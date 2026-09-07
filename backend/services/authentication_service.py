import secrets
from datetime import datetime, timedelta
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from models.domain import User, UserSession, LoginHistory
from repositories.user_repository import UserRepository
from repositories.session_repository import SessionRepository
from repositories.password_reset_repository import PasswordResetRepository
from repositories.chat_session_repository import ChatSessionRepository
from auth.hashing import get_password_hash, verify_password, hash_token
from auth.jwt import create_access_token, create_refresh_token, SECRET_KEY, ALGORITHM, REFRESH_TOKEN_EXPIRE_DAYS
from mail.email_service import EmailService

VERIFICATION_TOKEN_EXPIRE_HOURS = 24
RESET_TOKEN_EXPIRE_MINUTES = 30

class AuthenticationService:
    def __init__(self, db: Session):
        self.db = db
        self.user_repo = UserRepository(db)
        self.session_repo = SessionRepository(db)
        self.reset_repo = PasswordResetRepository(db)
        self.chat_session_repo = ChatSessionRepository(db)
        self.email_service = EmailService()

    # ── Registration ──────────────────────────────────────────────────────────
    def register(self, email: str, password: str, role: str = "USER") -> User:
        if self.user_repo.get_user_by_email(email):
            raise ValueError("Email already registered")

        hashed_password = get_password_hash(password)
        user = User(email=email, password_hash=hashed_password, role=role)
        self.user_repo.create_user(user)

        # Phase 9.4: Persist ONLY the hashed verification token with an expiry.
        verification_token = secrets.token_urlsafe(32)
        user.verification_token_hash = hash_token(verification_token)
        user.verification_expires_at = datetime.utcnow() + timedelta(hours=VERIFICATION_TOKEN_EXPIRE_HOURS)
        self.user_repo.update_user(user)

        self.email_service.send_verification_email(user.email, verification_token)
        return user

    def verify_email(self, token: str) -> User:
        if not token:
            raise ValueError("Invalid verification token")
        token_hash = hash_token(token)
        user = self.user_repo.get_user_by_verification_token_hash(token_hash)
        if user is None:
            raise ValueError("Invalid verification token")
        if user.verification_expires_at is None or user.verification_expires_at < datetime.utcnow():
            raise ValueError("Verification token expired")

        # Reuses this same token pair for an email-CHANGE confirmation, not
        # just initial signup verification — change_email() below sets
        # pending_email and issues a token exactly the same way register()
        # does. If a pending_email is waiting, this redemption swaps it in;
        # otherwise it's a normal signup verification.
        if user.pending_email:
            if self.user_repo.get_user_by_email(user.pending_email):
                raise ValueError("That email is no longer available")
            user.email = user.pending_email
            user.pending_email = None

        # Mark verified and consume the token (prevents reuse).
        user.is_verified = True
        user.verification_token_hash = None
        user.verification_expires_at = None
        self.user_repo.update_user(user)
        return user

    # ── Login ─────────────────────────────────────────────────────────────────
    def login(self, email: str, password: str, ip_address: str = None, browser: str = None) -> tuple[User, str, str]:
        user = self.user_repo.get_user_by_email(email)

        history = LoginHistory(
            user_id=user.id if user else None,
            ip_address=ip_address,
            browser=browser,
            success=False
        )

        if not user:
            raise ValueError("Invalid email or password")

        if not verify_password(password, user.password_hash):
            history.failure_reason = "Invalid password"
            self.session_repo.create_login_history(history)
            raise ValueError("Invalid email or password")

        if not user.is_active:
            history.failure_reason = "Account disabled"
            self.session_repo.create_login_history(history)
            raise ValueError("Account disabled")

        # Success
        history.success = True
        self.session_repo.create_login_history(history)

        access_token, refresh_token = self._issue_tokens(user)
        # Persist the refresh token hash so POST /api/auth/refresh can validate it.
        self._store_refresh_token(user.id, refresh_token)
        return user, access_token, refresh_token

    # ── Refresh token rotation ────────────────────────────────────────────────
    def refresh_session(self, refresh_token: str) -> tuple[User, str, str]:
        """Validate a refresh token, rotate it, and issue fresh tokens."""
        if not refresh_token:
            raise ValueError("Invalid refresh token")
        try:
            payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        except JWTError:
            raise ValueError("Invalid refresh token")

        if payload.get("type") != "refresh":
            raise ValueError("Invalid refresh token")

        try:
            user_id = int(payload.get("sub"))
        except (TypeError, ValueError):
            raise ValueError("Invalid refresh token")

        user = self.user_repo.get_user_by_id(user_id)
        if user is None or not user.is_active:
            raise ValueError("Invalid refresh token")

        token_hash = hash_token(refresh_token)
        user_session = self.session_repo.get_user_session_by_token_hash(user_id, token_hash)
        if user_session is None:
            raise ValueError("Invalid refresh token")

        if user_session.expires_at is None or user_session.expires_at < datetime.utcnow():
            self.session_repo.delete_session_by_id(user_session.id)
            raise ValueError("Refresh token expired")

        # Rotation: issue new tokens and invalidate the presented one.
        access_token, new_refresh_token = self._issue_tokens(user)
        new_expires_at = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        self.session_repo.update_refresh_token(
            user_session, hash_token(new_refresh_token), new_expires_at
        )
        return user, access_token, new_refresh_token

    # ── Logout ────────────────────────────────────────────────────────────────
    def logout(self, user_id: int, refresh_token: str = None):
        # With multi-device sessions, a logout should end only the calling
        # device by default — ending every device's session just because
        # one of them logged out would defeat the point of allowing
        # multiple concurrent sessions at all. Falls back to ending every
        # session (the old behavior) only when no refresh token is given,
        # so an older client that doesn't send one still gets a full
        # logout instead of silently doing nothing.
        if refresh_token:
            token_hash = hash_token(refresh_token)
            deleted = self.session_repo.delete_session_by_token_hash(user_id, token_hash)
            if deleted:
                # Targeted, single-device logout — there's no reliable way
                # to know which chat/conversation sessions belong to just
                # THIS device (a device can have several open chat tabs,
                # each its own session_id, with no link back to the login
                # session), so leave chat state alone here rather than
                # wiping the other devices' active conversations too.
                return
            self.session_repo.delete_user_session(user_id)
        else:
            self.session_repo.delete_user_session(user_id)
        self._revoke_chat_sessions(user_id)

    # ── Forgot password ───────────────────────────────────────────────────────
    def forgot_password(self, email: str) -> bool:
        """Issue a reset token for an existing account.

        Returns True if an email was sent, False if the account does not exist.
        The endpoint ALWAYS returns the same generic message regardless, so the
        caller cannot distinguish account existence (no enumeration).
        """
        user = self.user_repo.get_user_by_email(email)
        if user is None or not user.is_active:
            return False

        reset_token = secrets.token_urlsafe(32)
        token_hash = hash_token(reset_token)
        self.reset_repo.invalidate_all_for_user(user.id)
        self.reset_repo.create(
            user.id,
            token_hash,
            datetime.utcnow() + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES),
        )
        self.email_service.send_password_reset_email(user.email, reset_token)
        return True

    def reset_password(self, token: str, new_password: str) -> User:
        if not token:
            raise ValueError("Invalid reset token")
        token_hash = hash_token(token)
        reset = self.reset_repo.get_by_token_hash(token_hash)
        if reset is None or reset.used:
            raise ValueError("Invalid or already used reset token")
        if reset.expires_at is None or reset.expires_at < datetime.utcnow():
            raise ValueError("Reset token expired")

        user = self.user_repo.get_user_by_id(reset.user_id)
        if user is None or not user.is_active:
            raise ValueError("Invalid reset token")

        user.password_hash = get_password_hash(new_password)
        self.user_repo.update_user(user)

        # Consume this token, invalidate any other outstanding ones, revoke sessions.
        self.reset_repo.mark_used(reset)
        self.reset_repo.invalidate_all_for_user(user.id)
        self.session_repo.delete_user_session(user.id)
        self._revoke_chat_sessions(user.id)
        return user

    # ── Change password ───────────────────────────────────────────────────────
    def change_password(self, user: User, current_password: str, new_password: str) -> User:
        if not verify_password(current_password, user.password_hash):
            raise ValueError("Current password is incorrect")

        user.password_hash = get_password_hash(new_password)
        self.user_repo.update_user(user)

        # Revoke all existing sessions so other devices must log in again.
        self.session_repo.delete_user_session(user.id)
        self._revoke_chat_sessions(user.id)
        return user

    # ── Change email ──────────────────────────────────────────────────────────
    def change_email(self, user: User, new_email: str, current_password: str) -> None:
        if not verify_password(current_password, user.password_hash):
            raise ValueError("Current password is incorrect")
        if new_email.lower() == user.email.lower():
            raise ValueError("That is already your current email")
        if self.user_repo.get_user_by_email(new_email):
            raise ValueError("Email already registered")

        # The address in `email` does NOT change until the new one is
        # confirmed — otherwise a stolen password alone would let an
        # attacker immediately take over the account under an email only
        # they control, before the real owner could ever notice.
        user.pending_email = new_email
        verification_token = secrets.token_urlsafe(32)
        user.verification_token_hash = hash_token(verification_token)
        user.verification_expires_at = datetime.utcnow() + timedelta(hours=VERIFICATION_TOKEN_EXPIRE_HOURS)
        self.user_repo.update_user(user)

        self.email_service.send_verification_email(new_email, verification_token)

    # ── Delete account ────────────────────────────────────────────────────────
    def delete_account(self, user: User, current_password: str) -> None:
        if not verify_password(current_password, user.password_hash):
            raise ValueError("Current password is incorrect")

        user_id = user.id
        self._revoke_chat_sessions(user_id)
        # Sessions, connections, preferences, login history, and chat
        # sessions are all declared cascade="all, delete-orphan" on User's
        # relationships (models/domain.py), so deleting the user row alone
        # is sufficient — no separate cleanup calls needed per table.
        self.db.delete(user)
        self.db.commit()

    # ── Internals ─────────────────────────────────────────────────────────────
    def _issue_tokens(self, user: User) -> tuple[str, str]:
        access_token = create_access_token(data={"sub": str(user.id)})
        refresh_token = create_refresh_token(data={"sub": str(user.id)})
        return access_token, refresh_token

    def _store_refresh_token(self, user_id: int, refresh_token: str) -> None:
        """Store the hashed refresh token as a NEW session for the user —
        multiple concurrent sessions are allowed, so logging in on a second
        device must not delete the first device's still-valid session."""
        session = UserSession(
            user_id=user_id,
            hashed_refresh_token=hash_token(refresh_token),
            expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        )
        self.session_repo.create_user_session(session)

    def _revoke_chat_sessions(self, user_id: int):
        try:
            from state.session_store import store as session_store
            session_store.clear_user(user_id)
        except Exception:
            pass
        try:
            self.chat_session_repo.deactivate_all_for_user(user_id)
        except Exception:
            pass
