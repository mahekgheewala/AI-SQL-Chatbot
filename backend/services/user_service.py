import json

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

    def list_users_with_database_counts(self) -> list[dict]:
        """One row per user for the admin panel: identity, status, and how
        many databases they've created through the app.

        "Databases they've made" is counted from PostgresConnection.
        allowed_databases — the per-connection ACL list that
        ConnectionService.grant_database_access() appends to specifically
        when a CREATE DATABASE request succeeds (see db/executor.py) — not
        the connection's own default_database, which is just whatever
        pre-existing database the user pointed their credentials at when
        setting up the connection, not something they created via this app.
        """
        users = self.repo.list_users()
        result = []
        for user in users:
            db_count = 0
            for conn in user.connections:
                if conn.allowed_databases:
                    try:
                        db_count += len(json.loads(conn.allowed_databases))
                    except (TypeError, ValueError):
                        pass
            result.append({
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role.value,
                "is_active": user.is_active,
                "is_verified": user.is_verified,
                "created_at": user.created_at,
                "database_count": db_count,
            })
        return result

    def set_user_status(self, user_id: int, is_active: bool = None, role: str = None) -> User:
        """Admin action: suspend/reactivate a user and/or change their role."""
        user = self.repo.get_user_by_id(user_id)
        if not user:
            raise ValueError("User not found")

        if is_active is not None:
            user.is_active = is_active
            if not is_active:
                # Suspending must actually end access immediately, not just
                # block future logins — revoke every active session too.
                from repositories.session_repository import SessionRepository
                SessionRepository(self.db).delete_user_session(user_id)
                try:
                    from state.session_store import store as session_store
                    session_store.clear_user(user_id)
                except Exception:
                    pass

        if role is not None:
            from models.domain import Role
            if role not in (r.value for r in Role):
                raise ValueError(f"Invalid role: {role}")
            user.role = Role(role)

        return self.repo.update_user(user)

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
