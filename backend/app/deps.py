"""FastAPI dependencies: settings, db session, engine hub, bus, and identity."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models
from .config import Settings
from .engines import EngineHub
from .live import EventBus
from .security import verify_access_token

_bearer = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_hub(request: Request) -> EngineHub:
    return request.app.state.hub


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.sessionmaker() as session:
        yield session


@dataclass
class CurrentUser:
    user: models.User
    tenant: models.Tenant
    # "user" for a human/JWT caller, "api_key" for an agent authenticating with a
    # project ingest key. An api_key principal is scoped to exactly one project.
    kind: str = "user"
    scoped_project_id: uuid.UUID | None = None

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.id

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"

    @property
    def is_superadmin(self) -> bool:
        return bool(getattr(self.user, "superadmin", False))


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    settings: Settings = request.app.state.settings
    identity = verify_access_token(settings.auth, credentials.credentials)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    try:
        user_id = uuid.UUID(identity.client_id)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "malformed token subject")
    user = await session.get(models.User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown subject")
    tenant = await session.get(models.Tenant, user.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown tenant")
    return CurrentUser(user=user, tenant=tenant)


async def require_admin(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not current.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
    return current


async def require_superadmin(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not current.is_superadmin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "superadmin role required")
    return current


async def get_ingest_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    """Authenticate a run-ingest request as a project API key, an IdP JWT, or a user.

    The hub's gateway-composed authenticator is tried first: a per-project API
    key yields a principal scoped to that one project, and an external-IdP JWT
    (when OAuth2/JWKS ingest is enabled) yields a principal in the configured
    tenant — scoped to the token's project claim if it carries one. Both are
    attributed to an ``agent`` actor. If neither matches, this falls back to the
    normal user JWT, so the dashboard and existing tooling keep working.
    """
    hub: EngineHub = request.app.state.hub
    ident = hub.authenticate_ingest(request.headers)
    if ident is not None:
        if ident.auth_method == "api_key":
            return await _api_key_principal(session, ident.client_id)
        if ident.auth_method == "oauth2":
            return await _oauth2_principal(request, session, ident)
    return await get_current_user(request, credentials, session)


async def _api_key_principal(session: AsyncSession, client_id: str) -> CurrentUser:
    try:
        key_id = uuid.UUID(client_id)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid api key identity")
    api_key = await session.get(models.ApiKey, key_id)
    if api_key is None or not api_key.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown or revoked api key")
    tenant = await session.get(models.Tenant, api_key.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown tenant")
    api_key.last_used_at = models.utcnow()
    await session.commit()
    # A transient actor for attribution; never persisted as a user row.
    agent_user = models.User(
        id=api_key.id, tenant_id=api_key.tenant_id,
        email=f"agentkey:{api_key.key_prefix}", name=api_key.name or "agent key",
        role="agent", password_hash="",
    )
    return CurrentUser(
        user=agent_user, tenant=tenant, kind="api_key",
        scoped_project_id=api_key.project_id,
    )


async def _oauth2_principal(request: Request, session: AsyncSession, ident) -> CurrentUser:
    settings: Settings = request.app.state.settings
    cfg = settings.ingest.oauth2
    if not cfg.tenant_slug:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "oauth2 ingest is not bound to a tenant (set ingest.oauth2.tenant_slug)",
        )
    result = await session.execute(
        select(models.Tenant).where(models.Tenant.slug == cfg.tenant_slug)
    )
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown ingest tenant")

    scoped_project_id: uuid.UUID | None = None
    if ident.project_hint:
        proj = await session.execute(
            select(models.Project).where(
                models.Project.tenant_id == tenant.id,
                models.Project.slug == ident.project_hint,
            )
        )
        project = proj.scalar_one_or_none()
        if project is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"token project claim '{ident.project_hint}' does not match any project",
            )
        scoped_project_id = project.id

    # A transient actor for attribution; never persisted as a user row. The
    # id is synthetic; the IdP subject is carried in the actor's email/name.
    agent_user = models.User(
        id=uuid.uuid4(), tenant_id=tenant.id,
        email=f"oauth2:{ident.client_id}", name=ident.client_id or "oauth2 agent",
        role="agent", password_hash="",
    )
    return CurrentUser(
        user=agent_user, tenant=tenant, kind="oauth2",
        scoped_project_id=scoped_project_id,
    )
