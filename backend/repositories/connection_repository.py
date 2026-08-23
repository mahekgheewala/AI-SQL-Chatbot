from sqlalchemy.orm import Session
from models.domain import PostgresConnection

class ConnectionRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_connection_by_user_id(self, user_id: int) -> PostgresConnection | None:
        return self.db.query(PostgresConnection).filter(PostgresConnection.user_id == user_id).first()

    def create_connection(self, connection: PostgresConnection) -> PostgresConnection:
        self.db.add(connection)
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def update_connection(self, connection: PostgresConnection) -> PostgresConnection:
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def delete_connection(self, user_id: int):
        self.db.query(PostgresConnection).filter(PostgresConnection.user_id == user_id).delete()
        self.db.commit()
