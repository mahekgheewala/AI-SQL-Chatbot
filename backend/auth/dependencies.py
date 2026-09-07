import os
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from db.app_database import get_db
from models.domain import User, Role
from repositories.user_repository import UserRepository
from auth.jwt import SECRET_KEY, ALGORITHM

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        # Refresh tokens are signed with the same secret/algorithm as access
        # tokens, so without this check one would pass every check below and
        # authenticate normal API calls — a token meant only for "get me a
        # new access token" would work as a full 7-day bearer credential
        # instead, widening the damage if a refresh token ever leaks.
        if payload.get("type") != "access":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user_repo = UserRepository(db)
    user = user_repo.get_user_by_id(int(user_id))
    if user is None:
        raise credentials_exception
    # A disabled account's still-valid access token (issued before the
    # account was disabled) would otherwise keep working for up to its
    # remaining 30-minute lifetime — checked on every request instead.
    if not user.is_active:
        raise credentials_exception
    return user

def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in [Role.ADMIN, Role.SUPER_ADMIN]:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return current_user
