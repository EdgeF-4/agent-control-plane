"""Authentication routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models, schemas
from ..config import Settings
from ..deps import CurrentUser, get_current_user, get_session, get_settings
from ..security import mint_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=schemas.TokenResponse)
async def login(
    payload: schemas.LoginRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> schemas.TokenResponse:
    result = await session.execute(
        select(models.User).where(models.User.email == payload.email)
    )
    user = result.scalars().first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
    token = mint_access_token(settings.auth, subject=str(user.id), roles=[user.role])
    return schemas.TokenResponse(access_token=token, user=schemas.UserOut.model_validate(user))


@router.get("/me", response_model=schemas.UserOut)
async def me(current: CurrentUser = Depends(get_current_user)) -> schemas.UserOut:
    return schemas.UserOut.model_validate(current.user)
