"""FastAPI routes for authentication — register, login, user info."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ane.api.schemas import RegisterRequest, LoginRequest, AuthResponse
from ane.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user,
)
from ane.database.engine import get_db
from ane.database.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user account."""
    # Check if username already exists
    result = await db.execute(
        select(User).where(User.username == req.username)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        )

    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        display_name=req.display_name or req.username,
        is_adult=req.is_adult,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token({"sub": user.id, "username": user.username, "is_adult": bool(user.is_adult or False)})
    logger.info(f"User registered: {user.username} ({user.id})" + (" [+18]" if user.is_adult else " [minor]"))
    return AuthResponse(
        token=token,
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_adult=bool(user.is_adult or False),
    )


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Log in and receive a JWT token."""
    result = await db.execute(
        select(User).where(User.username == req.username)
    )
    user = result.scalar_one_or_none()
    if not user:
        logger.warning(f"Login failed: username not found — {req.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not verify_password(req.password, user.password_hash):
        logger.warning(f"Login failed: wrong password — {req.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account deactivated",
        )

    token = create_access_token({"sub": user.id, "username": user.username, "is_adult": bool(user.is_adult or False)})
    logger.info(f"User logged in: {user.username}" + (" [+18]" if user.is_adult else " [minor]"))
    return AuthResponse(
        token=token,
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_adult=bool(user.is_adult or False),
    )


@router.get("/me", response_model=AuthResponse)
async def get_me(user: User = Depends(get_current_user)):
    """Return the current user's info."""
    return AuthResponse(
        token="",
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_adult=bool(user.is_adult or False),
    )
