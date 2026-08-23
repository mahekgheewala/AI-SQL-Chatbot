import json
import psycopg2
from sqlalchemy.orm import Session
from models.domain import PostgresConnection
from repositories.connection_repository import ConnectionRepository
from auth.hashing import encrypt_data, decrypt_data

class ConnectionService:
    def __init__(self, db: Session):
        self.repo = ConnectionRepository(db)

    def validate_connection(self, host: str, port: int, username: str, password: str, database: str) -> bool:
        """Attempt to connect to the database to validate credentials."""
        try:
            conn = psycopg2.connect(
                host=host,
                port=port,
                user=username,
                password=password,
                dbname=database,
                connect_timeout=5
            )
            conn.close()
            return True
        except Exception as e:
            print(f"Connection validation failed: {e}")
            return False

    def save_connection(self, user_id: int, host: str, port: int, username: str, password: str, database: str, remember: bool, allowed_databases: list[str] | None = None) -> PostgresConnection:
        existing = self.repo.get_connection_by_user_id(user_id)

        enc_pass = encrypt_data(password) if remember else None
        # Phase 9.6: persist the per-user database ACL (JSON list).
        allowed_json = json.dumps(list(allowed_databases)) if allowed_databases else None

        if existing:
            existing.host = host
            existing.port = port
            existing.username = username
            existing.encrypted_password = enc_pass
            existing.default_database = database
            existing.remember_password = remember
            # Only overwrite the ACL when the caller explicitly provides one.
            if allowed_databases is not None:
                existing.allowed_databases = allowed_json
            return self.repo.update_connection(existing)
        else:
            new_conn = PostgresConnection(
                user_id=user_id,
                host=host,
                port=port,
                username=username,
                encrypted_password=enc_pass,
                default_database=database,
                remember_password=remember,
                allowed_databases=allowed_json,
            )
            return self.repo.create_connection(new_conn)

    def get_user_connection(self, user_id: int) -> PostgresConnection | None:
        return self.repo.get_connection_by_user_id(user_id)

    def grant_database_access(self, user_id: int, database_name: str) -> None:
        """
        Phase 9.6 ACL: add `database_name` to the user's allowed_databases list.

        Without this, a database created through the app (CREATE DATABASE) is
        immediately unreachable by its own creator, since ConnectionManager
        enforces allowed_databases on every subsequent connection and nothing
        else ever grows that list after connection setup.
        """
        conn_model = self.get_user_connection(user_id)
        if not conn_model or not database_name:
            return

        try:
            allowed = json.loads(conn_model.allowed_databases) if conn_model.allowed_databases else []
            if not isinstance(allowed, list):
                allowed = []
        except (ValueError, TypeError):
            allowed = []

        if not any(str(d).lower() == database_name.lower() for d in allowed):
            allowed.append(database_name)
            conn_model.allowed_databases = json.dumps(allowed)
            self.repo.update_connection(conn_model)
        
    def check_connection_health(self, user_id: int) -> bool:
        """Check if the saved connection is still valid."""
        conn_model = self.get_user_connection(user_id)
        if not conn_model:
            return False
            
        password = decrypt_data(conn_model.encrypted_password) if conn_model.encrypted_password else ""
        if not password and conn_model.remember_password:
             return False # Missing password when it should be remembered
             
        return self.validate_connection(
            conn_model.host, 
            conn_model.port, 
            conn_model.username, 
            password, 
            conn_model.default_database
        )
