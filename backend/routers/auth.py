from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from db.app_database import get_db
from models.domain import User
from services.authentication_service import AuthenticationService
from auth.dependencies import get_current_user
from auth.rate_limit import is_rate_limited, record_failure, clear_failures

router = APIRouter(tags=["auth"])

class UserCreate(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class RefreshRequest(BaseModel):
    refresh_token: str

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class UserResponse(BaseModel):
    id: int
    email: str
    role: str
    is_active: bool

@router.post("/register", response_model=UserResponse)
def register(user: UserCreate, db: Session = Depends(get_db)):
    auth_service = AuthenticationService(db)
    try:
        new_user = auth_service.register(email=user.email, password=user.password)
        return new_user
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/login", response_model=TokenResponse)
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    identifier = form_data.username # Using email as identifier for rate limiting
    
    if is_rate_limited(identifier):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Please try again later.")
        
    auth_service = AuthenticationService(db)
    
    try:
        user, access_token, refresh_token = auth_service.login(
            email=form_data.username,
            password=form_data.password,
            ip_address=client_ip,
            browser=request.headers.get("user-agent", "unknown")
        )
        clear_failures(identifier)
        return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}
    except ValueError as e:
        record_failure(identifier)
        raise HTTPException(status_code=401, detail=str(e))

@router.post("/refresh", response_model=TokenResponse)
def refresh(request: RefreshRequest, db: Session = Depends(get_db)):
    auth_service = AuthenticationService(db)
    try:
        _, access_token, refresh_token = auth_service.refresh_session(request.refresh_token)
        return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

@router.get("/verify-email")
def verify_email(token: str = Query(...), db: Session = Depends(get_db)):
    auth_service = AuthenticationService(db)
    try:
        user = auth_service.verify_email(token)
        return {"message": "Email verified successfully", "email": user.email}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/forgot-password")
def forgot_password(request: ForgotPasswordRequest, db: Session = Depends(get_db)):
    # Rate-limited per email (separate namespace from login's identifier) so
    # this can't be used to email-bomb an address with reset links. Silently
    # skipped once limited rather than returning 429 — a differing response
    # would itself be an account-enumeration/timing side channel.
    identifier = f"forgot_password:{request.email}"
    if not is_rate_limited(identifier):
        record_failure(identifier)
        auth_service = AuthenticationService(db)
        auth_service.forgot_password(str(request.email))
    # Always return the same generic message (no account enumeration).
    return {"message": "If that email is registered, a password reset link has been sent."}

@router.post("/reset-password")
def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    auth_service = AuthenticationService(db)
    try:
        auth_service.reset_password(request.token, request.new_password)
        return {"message": "Password reset successfully. You can now log in."}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/change-password")
def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    auth_service = AuthenticationService(db)
    try:
        auth_service.change_password(current_user, request.current_password, request.new_password)
        return {"message": "Password changed successfully. Please log in again."}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/logout")
def logout(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    auth_service = AuthenticationService(db)
    auth_service.logout(current_user.id)
    return {"message": "Logged out successfully"}

@router.get("/me", response_model=dict)
def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from services.connection_service import ConnectionService
    conn_service = ConnectionService(db)
    db_connected = conn_service.check_connection_health(current_user.id)
    
    return {
        "id": current_user.id,
        "email": current_user.email,
        "role": current_user.role.value,
        "is_verified": current_user.is_verified,
        "preferences": {
            "theme": current_user.preferences.theme if current_user.preferences else "light",
            "language": current_user.preferences.language if current_user.preferences else "en"
        },
        "database_connected": db_connected
    }
