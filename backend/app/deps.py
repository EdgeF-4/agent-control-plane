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


async def get_ingest_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    """Authenticate a run-ingest request as either a project API key or a user.

    A project API key is tried first (via the gateway-composed authenticator on
    the hub); it yields a principal scoped to that one project and attributed to
    an ``agent`` actor. If no key matches, this falls back to the normal user
    JWT, so the dashboard and existing tooling keep working unchanged.
    """
    hub: EngineHub = request.app.state.hub
    client_id = hub.authenticate_ingest(request.headers)
    if client_id is not None:
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
    return await get_current_user(request, credentials, session)
