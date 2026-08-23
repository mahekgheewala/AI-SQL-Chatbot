from datetime import datetime
from sqlalchemy.orm import Session
from models.domain import UserSession, LoginHistory

class SessionRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_user_session(self, session: UserSession) -> UserSession:
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_user_session(self, user_id: int) -> UserSession | None:
        return self.db.query(UserSession).filter(UserSession.user_id == user_id).first()

    def get_user_session_by_token_hash(self, user_id: int, token_hash: str) -> UserSession | None:
        return self.db.query(UserSession).filter(
            UserSession.user_id == user_id,
            UserSession.hashed_refresh_token == token_hash
        ).first()

    def update_refresh_token(self, user_session: UserSession, new_token_hash: str, new_expires_at: datetime) -> UserSession:
        """Rotate the refresh token in place (single session per user)."""
        user_session.hashed_refresh_token = new_token_hash
        user_session.expires_at = new_expires_at
        self.db.commit()
        self.db.refresh(user_session)
        return user_session

    def delete_user_session(self, user_id: int):
        self.db.query(UserSession).filter(UserSession.user_id == user_id).delete()
        self.db.commit()

    def delete_session_by_id(self, session_id: int):
        self.db.query(UserSession).filter(UserSession.id == session_id).delete()
        self.db.commit()

    def create_login_history(self, history: LoginHistory) -> LoginHistory:
        self.db.add(history)
        self.db.commit()
        self.db.refresh(history)
        return history
