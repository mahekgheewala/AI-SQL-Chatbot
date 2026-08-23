from sqlalchemy.orm import Session
from models.domain import User, UserPreference
from repositories.user_repository import UserRepository

class UserService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = UserRepository(db)

    def get_user_by_id(self, user_id: int) -> User | None:
        return self.repo.get_user_by_id(user_id)
        
    def get_user_by_email(self, email: str) -> User | None:
        return self.repo.get_user_by_email(email)

    def update_preferences(self, user_id: int, theme: str = None, language: str = None, remember_db: bool = None) -> UserPreference:
        user = self.repo.get_user_by_id(user_id)
        if not user:
            raise ValueError("User not found")
            
        if not user.preferences:
            user.preferences = UserPreference(user_id=user_id)
            self.db.add(user.preferences)
            
        if theme is not None:
            user.preferences.theme = theme
        if language is not None:
            user.preferences.language = language
        if remember_db is not None:
            user.preferences.remember_db = remember_db
            
        self.db.commit()
        self.db.refresh(user.preferences)
        return user.preferences
