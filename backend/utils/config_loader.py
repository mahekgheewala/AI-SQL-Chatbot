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


def get_db_connect_timeout_seconds() -> int:
    """How long establishing a new PostgreSQL connection (TCP + auth
    handshake) may take before giving up. This is network-level, not
    query-level — should normally complete in well under a second; a
    generous-but-bounded default guards against a hung/unreachable host
    without being so tight it false-positives on a slow network."""
    return int(os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "10"))


def get_max_result_rows() -> int:
    """Maximum rows a single interactive SELECT will return to the app.
    Does not rewrite the query (a blind LIMIT would change COUNT/GROUP BY/
    aggregate semantics) — the full query still runs and PostgreSQL still
    computes the real aggregate/grouped result server-side; this only caps
    how many result rows get pulled into Python process memory and sent
    back in the HTTP response. A genuinely large export is a different,
    not-yet-built feature (see README's "What's left"), not something this
    cap tries to replace."""
    return int(os.getenv("DB_MAX_RESULT_ROWS", "5000"))


def get_db_statement_timeout_ms() -> int:
    """Maximum time a single SQL statement may run on the server before
    PostgreSQL itself cancels it (the `statement_timeout` GUC). This is an
    interactive chat assistant — legitimate ad hoc aggregate queries
    against a user's own database should finish in low single-digit
    seconds; 30s leaves real headroom for a heavier-than-usual query while
    still bounding a runaway one (accidental cross join, missing WHERE).
    Configurable per deployment since actual data sizes vary."""
    return int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))
