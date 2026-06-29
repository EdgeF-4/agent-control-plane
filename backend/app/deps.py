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
