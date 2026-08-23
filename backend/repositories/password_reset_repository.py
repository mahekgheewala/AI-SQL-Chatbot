from datetime import datetime
from sqlalchemy.orm import Session
from models.domain import PasswordResetToken

class PasswordResetRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, user_id: int, token_hash: str, expires_at: datetime) -> PasswordResetToken:
        token = PasswordResetToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            used=False,
        )
        self.db.add(token)
        self.db.commit()
        self.db.refresh(token)
        return token

    def get_by_token_hash(self, token_hash: str) -> PasswordResetToken | None:
        return self.db.query(PasswordResetToken).filter(
            PasswordResetToken.token_hash == token_hash
        ).first()

    def invalidate_all_for_user(self, user_id: int) -> None:
        """Mark all outstanding reset tokens for a user as used."""
        self.db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used == False
        ).update({"used": True})
        self.db.commit()

    def mark_used(self, token: PasswordResetToken) -> None:
        token.used = True
        self.db.commit()
