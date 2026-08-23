import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from utils.config_loader import init_env

# Ensure environment variables are loaded
init_env()

# Use SQLite by default if APP_DB_URL is not provided, 
# although in production this should be a PostgreSQL URL.
APP_DB_URL = os.getenv("APP_DB_URL", "sqlite:///./sql_assistant_app.db")

# SQLite needs check_same_thread=False for FastAPI concurrency
connect_args = {"check_same_thread": False} if APP_DB_URL.startswith("sqlite") else {}

engine = create_engine(
    APP_DB_URL, connect_args=connect_args
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """FastAPI Dependency for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
