import os
from dotenv import load_dotenv

def init_env(override: bool = False):
    """
    Centralized environment variable loader.
    Resolves the absolute path to backend/.env and loads it.
    """
    utils_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(utils_dir)
    env_path = os.path.join(backend_dir, ".env")

    load_dotenv(dotenv_path=env_path, override=override)


def get_superdb_name() -> str:
    """The always-available PostgreSQL maintenance database used for
    server-scoped operations (CREATE DATABASE, DROP DATABASE, listing
    databases). Single source of truth for the DB_SUPERDB fallback, which
    was previously repeated as os.getenv("DB_SUPERDB", "postgres") at half a
    dozen call sites."""
    return os.getenv("DB_SUPERDB", "postgres")
