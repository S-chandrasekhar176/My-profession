"""
Authentication routes for UltraBot Web.

Provides JWT-based login/logout for the admin user.
"""
from __future__ import annotations

import logging
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from api.dependencies import (
    verify_password,
    create_access_token,
    get_current_user,
    get_admin_credentials,
    _ADMIN_USERNAME,
    _ADMIN_PASSWORD_HASH,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Use a lightweight in-memory set to track tokens (for logout invalidation).
# In production, use Redis or a database-backed blocklist.
_revoked_tokens: set = set()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_hours: int = 24


class LogoutResponse(BaseModel):
    message: str


class UserInfo(BaseModel):
    username: str


@router.post("/login", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()) -> Dict:
    """Authenticate user with username/password and return JWT token.

    This endpoint does NOT require auth (it's the entry point).
    """
    username = form_data.username
    password = form_data.password

    expected_user, expected_hash = get_admin_credentials()

    # Check username
    if username != expected_user:
        logger.warning("Login attempt with unknown username: %s", username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check password
    if not verify_password(password, expected_hash):
        logger.warning("Login attempt with wrong password for user: %s", username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create JWT
    token = create_access_token(data={"sub": username})
    logger.info("User '%s' logged in successfully", username)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in_hours": 24,
    }


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    username: str = Depends(get_current_user),
) -> Dict:
    """Logout the current user by revoking their token.

    The token is extracted from the Authorization header and added to
    an in-memory revocation set.
    """
    try:
        auth_header = request.headers.get("authorization", "")
        token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else auth_header
        if token:
            _revoked_tokens.add(token)
            logger.info("User '%s' logged out, token revoked", username)
    except Exception as exc:
        logger.warning("Error during logout: %s", exc)

    return {"message": "Successfully logged out"}


@router.get("/me", response_model=UserInfo)
async def get_me(username: str = Depends(get_current_user)) -> Dict:
    """Return the currently authenticated user's info."""
    return {"username": username}


def revoke_token(token: str) -> None:
    """Add a token to the revoked tokens blocklist."""
    if token:
        _revoked_tokens.add(token)


def is_token_revoked(token: str) -> bool:
    """Check if a token has been revoked."""
    return token in _revoked_tokens
