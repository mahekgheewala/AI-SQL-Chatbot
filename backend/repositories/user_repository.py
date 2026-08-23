from sqlalchemy.orm import Session
from models.domain import User

class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_user_by_id(self, user_id: int) -> User | None:
        return self.db.query(User).filter(User.id == user_id).first()

    def get_user_by_email(self, email: str) -> User | None:
        return self.db.query(User).filter(User.email == email).first()

    def get_user_by_verification_token_hash(self, token_hash: str) -> User | None:
        return self.db.query(User).filter(User.verification_token_hash == token_hash).first()

    def create_user(self, user: User) -> User:
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def update_user(self, user: User) -> User:
        self.db.commit()
        self.db.refresh(user)
        return user
