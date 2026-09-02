"""
FastAPI dependencies for UltraBot Web.

Provides dependency injection for:
  - JWT-based authentication
  - Engine instance access
  - Repository instance access

Usage in app.py:
  from api.dependencies import set_engine, set_repository
  set_engine(engine)
  set_repository(repo)
"""
from __future__ import annotations

import os
import logging
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from config.settings import settings
from db.repository import Repository
from core.engine import UltraBotEngine

logger = logging.getLogger(__name__)

# OAuth2 scheme – expects Bearer token in Authorization header
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# Module-level singletons – set by app.py after instantiation
_engine_instance: Optional[UltraBotEngine] = None
_repo_instance: Optional[Repository] = None

def set_engine(eng: UltraBotEngine) -> None:
    """Set the global engine instance. Called once from app.py."""
    global _engine_instance
    _engine_instance = eng
    logger.info("Engine instance registered in dependencies")


def set_repository(repo: Repository) -> None:
    """Set the global repository instance. Called once from app.py."""
    global _repo_instance
    _repo_instance = repo
    logger.info("Repository instance registered in dependencies")


def get_engine() -> UltraBotEngine:
    """FastAPI dependency that returns the global engine instance."""
    if _engine_instance is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Engine not initialized",
        )
    return _engine_instance


async def get_repository():
    """FastAPI dependency that yields a request-scoped repository session."""
    from db.database import async_session_factory
    async with async_session_factory() as session:
        yield Repository(session)


ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# Admin credentials defaults
_ADMIN_USERNAME = "admin"
# bcrypt hash of "admin" – generated with: bcrypt.hashpw(b"admin", bcrypt.gensalt())
_ADMIN_PASSWORD_HASH = (
    "$2b$12$Wb5rmUCZlzToN7WVIzbJX.409papQngxfB/vb7LjQdUumBzErPbhG"
)


def get_admin_credentials() -> tuple[str, str]:
    """Retrieve active admin credentials from settings or defaults."""
    auth_cfg = settings.get("auth", default={})
    username = auth_cfg.get("username") or settings.auth_username or _ADMIN_USERNAME
    password_hash = auth_cfg.get("password_hash") or settings.auth_password_hash or _ADMIN_PASSWORD_HASH
    return username, password_hash


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a bcrypt hash (or fallback constant-time match with warning)."""
    if not plain_password or not hashed_password:
        return False
    try:
        import bcrypt
        # If hashed_password is a bcrypt hash
        if hashed_password.startswith(("$2b$", "$2a$", "$2y$")):
            return bcrypt.checkpw(
                plain_password.encode("utf-8"),
                hashed_password.encode("utf-8"),
            )
    except Exception as exc:
        logger.warning("Bcrypt password verification failed with exception: %s", exc)
        return False

    # Plaintext fallback for legacy/testing configs
    import hmac
    logger.warning("Using plaintext password verification fallback. Please migrate to bcrypt hashed passwords.")
    return hmac.compare_digest(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[int] = None) -> str:
    """Create a unique JWT access token."""
    import uuid
    from datetime import datetime, timedelta, timezone
    to_encode = data.copy()
    expire_minutes = (expires_delta or ACCESS_TOKEN_EXPIRE_HOURS) * 60
    expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
    if "jti" not in to_encode:
        to_encode["jti"] = uuid.uuid4().hex
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    """Verify JWT token and return the username.

    Raises:
        HTTPException(401) if token is invalid, expired, or revoked.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        from api.routes.auth import is_token_revoked
        if is_token_revoked(token):
            logger.warning("Access attempted with revoked token")
            raise credentials_exception

        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        username: Optional[str] = payload.get("sub")
        if username is None:
            raise credentials_exception
        return username
    except JWTError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise credentials_exception from exc
