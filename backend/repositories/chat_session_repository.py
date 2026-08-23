from datetime import datetime
from sqlalchemy.orm import Session
from models.domain import ChatSession

class ChatSessionRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_session_id(self, session_id: str) -> ChatSession | None:
        return self.db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()

    def create(self, session_id: str, user_id: int, expires_at: datetime | None = None) -> ChatSession:
        session = ChatSession(
            session_id=session_id,
            user_id=user_id,
            expires_at=expires_at,
            is_active=True,
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def touch(self, session: ChatSession) -> None:
        session.last_active_at = datetime.utcnow()
        self.db.commit()

    def deactivate_all_for_user(self, user_id: int) -> None:
        self.db.query(ChatSession).filter(
            ChatSession.user_id == user_id,
            ChatSession.is_active == True
        ).update({"is_active": False})
        self.db.commit()
