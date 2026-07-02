"""Admin routes: manage tenants (superadmin) and users within a tenant (admin).

Project and API-key administration live on the projects router; the eval gate
lives on the evals router. Together these back the dashboard's admin view.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models, schemas
from ..deps import CurrentUser, get_current_user, get_session, require_admin, require_superadmin
from ..security import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])


# ------------------------------- tenants ----------------------------------- #
@router.get("/tenants", response_model=list[schemas.TenantOut])
async def list_tenants(
    current: CurrentUser = Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
) -> list[schemas.TenantOut]:
    tenants = (await session.execute(select(models.Tenant).order_by(models.Tenant.created_at))).scalars().all()
    out: list[schemas.TenantOut] = []
    for t in tenants:
        users = await session.scalar(
            select(func.count()).select_from(models.User).where(models.User.tenant_id == t.id)
        )
        projects = await session.scalar(
            select(func.count()).select_from(models.Project).where(models.Project.tenant_id == t.id)
        )
        out.append(schemas.TenantOut(
            id=t.id, slug=t.slug, name=t.name, user_count=users or 0, project_count=projects or 0,
        ))
    return out


@router.post("/tenants", response_model=schemas.TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: schemas.TenantCreate,
    current: CurrentUser = Depends(require_superadmin),
    session: AsyncSession = Depends(get_session),
) -> schemas.TenantOut:
    exists = await session.scalar(select(models.Tenant).where(models.Tenant.slug == payload.slug))
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "tenant slug already exists")
    tenant = models.Tenant(slug=payload.slug, name=payload.name)
    session.add(tenant)
    await session.flush()
    session.add(models.User(
        tenant_id=tenant.id, email=payload.admin_email,
        password_hash=hash_password(payload.admin_password),
        name="Administrator", role="admin",
    ))
    await session.commit()
    return schemas.TenantOut(id=tenant.id, slug=tenant.slug, name=tenant.name,
                             user_count=1, project_count=0)


# -------------------------------- users ------------------------------------ #
@router.get("/users", response_model=list[schemas.UserOut])
async def list_users(
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[models.User]:
    result = await session.execute(
        select(models.User)
        .where(models.User.tenant_id == current.tenant_id)
        .order_by(models.User.created_at)
    )
    return list(result.scalars().all())


@router.post("/users", response_model=schemas.UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: schemas.UserCreate,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> models.User:
    if payload.role not in ("admin", "member"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "role must be admin or member")
    dupe = await session.scalar(
        select(models.User).where(
            models.User.tenant_id == current.tenant_id, models.User.email == payload.email
        )
    )
    if dupe is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "a user with that email already exists")
    user = models.User(
        tenant_id=current.tenant_id, email=payload.email,
        password_hash=hash_password(payload.password), name=payload.name, role=payload.role,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=schemas.UserOut)
async def update_user(
    user_id: uuid.UUID,
    payload: schemas.UserUpdate,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> models.User:
    user = await session.get(models.User, user_id)
    if user is None or user.tenant_id != current.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if payload.role is not None:
        if payload.role not in ("admin", "member"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "role must be admin or member")
        if user.id == current.user.id and payload.role != "admin":
            raise HTTPException(status.HTTP_409_CONFLICT, "you cannot demote yourself")
        user.role = payload.role
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    await session.commit()
    await session.refresh(user)
    return user


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await session.get(models.User, user_id)
    if user is None or user.tenant_id != current.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if user.id == current.user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "you cannot delete your own account")
    await session.delete(user)
    await session.commit()
    return {"deleted": True, "id": str(user_id)}
