import os
import secrets
from datetime import datetime, timedelta
from typing import Optional
from jose import jwt
from utils.config_loader import init_env

init_env()

# Hard-fail at import time if the JWT signing secret is absent or still set to
# the well-known FastAPI documentation example. Silently falling back to an
# insecure default would allow arbitrary token forgery (including admin roles).
_INSECURE_DEFAULTS = {
    "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7",
    "changeme",
    "secret",
}

_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "").strip()
if not _SECRET_KEY or _SECRET_KEY.lower() in _INSECURE_DEFAULTS:
    raise RuntimeError(
        "JWT_SECRET_KEY is not configured or is set to an insecure default. "
        "Set a strong, random value in backend/.env before starting the server."
    )

SECRET_KEY = _SECRET_KEY
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    to_encode["type"] = "access"
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    to_encode["type"] = "refresh"
    # Unique per issuance so rotation can always produce a distinct token, even
    # when two tokens are minted within the same second (JWT exp is second-granular).
    to_encode["jti"] = secrets.token_urlsafe(16)
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
