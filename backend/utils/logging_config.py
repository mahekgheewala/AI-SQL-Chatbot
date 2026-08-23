import os
import sys
import json
import logging
import logging.handlers
from datetime import datetime
from contextvars import ContextVar
from typing import Optional

# ─── ContextVars ──────────────────────────────────────────────────────────────
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
session_id_var: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
user_id_var: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
database_name_var: ContextVar[Optional[str]] = ContextVar("database_name", default=None)
selected_table_var: ContextVar[Optional[str]] = ContextVar("selected_table", default=None)
connection_id_var: ContextVar[Optional[str]] = ContextVar("connection_id", default=None)
metadata_version_var: ContextVar[Optional[int]] = ContextVar("metadata_version", default=None)

# Standard LogRecord attributes that should NOT be dumped into JSON's root/extra fields
_STANDARD_ATTRIBUTES = {
    'name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename',
    'module', 'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName',
    'created', 'msecs', 'relativeCreated', 'thread', 'threadName',
    'processName', 'process', 'message'
}

class SafeJSONFormatter(logging.Formatter):
    """
    Formatter that converts LogRecords into clean, structured JSON format.
    Safely handles serialization failures and intercepts request context variables.
    """
    def format(self, record: logging.LogRecord) -> str:
        try:
            # Build standard baseline JSON structure
            log_entry = {
                "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                # Correlation fields (fall back to null if not bound in context)
                "request_id": request_id_var.get(None),
                "session_id": session_id_var.get(None),
                "user_id": user_id_var.get(None),
                "database_name": database_name_var.get(None),
                "selected_table": selected_table_var.get(None),
                "connection_id": connection_id_var.get(None),
                "metadata_version": metadata_version_var.get(None)
            }

            # Inject any extra properties bound directly to the record (via extra=...)
            for key, val in record.__dict__.items():
                if key not in _STANDARD_ATTRIBUTES and not key.startswith('_'):
                    try:
                        # Test serialization, if it passes add it directly
                        json.dumps(val)
                        log_entry[key] = val
                    except (TypeError, ValueError):
                        # Serialization failed, convert to string
                        log_entry[key] = str(val)

            # Append structured exception traceback details if present
            if record.exc_info:
                log_entry["exception"] = {
                    "type": record.exc_info[0].__name__ if record.exc_info[0] else "UnknownException",
                    "message": str(record.exc_info[1]),
                    "stacktrace": self.formatException(record.exc_info)
                }

            return json.dumps(log_entry)
        except Exception as e:
            # Extreme fallback safety net to ensure logger never interrupts main thread execution
            fallback = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "level": "ERROR",
                "logger": "app.logging_fallback",
                "message": f"Logging formatter serialization failed: {str(e)}",
                "original_msg": str(getattr(record, "msg", "None"))
            }
            try:
                return json.dumps(fallback)
            except Exception:
                return '{"timestamp": "UTC", "level": "ERROR", "message": "Failed to format log record"}'

def setup_logging():
    """
    Initializes backend logging. Setup directories and log rotation files:
      - app.log (level INFO, 10MB x 5 backups)
      - error.log (level ERROR, 10MB x 5 backups)
      - audit.log (level INFO, 10MB x 10 backups, handles only audit logger)
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logs_dir = os.path.join(base_dir, "logs")
    
    try:
        os.makedirs(logs_dir, exist_ok=True)
    except Exception as e:
        sys.stderr.write(f"Logging setup warning: Could not create log directory '{logs_dir}': {e}\n")
        # Continue anyway, standard logger handles write failures gracefully

    # Create formatters
    json_formatter = SafeJSONFormatter()
    # Clean, human-readable stdout format
    console_formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 1. Unified app.log handler (level INFO)
    app_log = os.path.join(logs_dir, "app.log")
    app_handler = logging.handlers.RotatingFileHandler(
        app_log, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(json_formatter)

    # 2. error.log handler (level ERROR)
    error_log = os.path.join(logs_dir, "error.log")
    error_handler = logging.handlers.RotatingFileHandler(
        error_log, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(json_formatter)

    # 3. audit.log handler (level INFO, 10 backups)
    audit_log = os.path.join(logs_dir, "audit.log")
    audit_handler = logging.handlers.RotatingFileHandler(
        audit_log, maxBytes=10*1024*1024, backupCount=10, encoding="utf-8"
    )
    audit_handler.setLevel(logging.INFO)
    audit_handler.setFormatter(json_formatter)

    # 4. Console stream handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)

    # Setup loggers
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Clear any default handlers to avoid double logging
    root_logger.handlers.clear()
    root_logger.addHandler(app_handler)
    root_logger.addHandler(error_handler)
    root_logger.addHandler(console_handler)

    # Setup specialized audit logger that writes to audit.log AND propagates to root (thus app.log)
    audit_logger = logging.getLogger("app.audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.handlers.clear()
    audit_logger.addHandler(audit_handler)
    audit_logger.propagate = True # Propagates to root

# Expose specific logger handles for simplified imports across files
logger_http = logging.getLogger("app.http")
logger_query = logging.getLogger("app.query")
logger_ai = logging.getLogger("app.ai")
logger_audit = logging.getLogger("app.audit")
logger_db_schema = logging.getLogger("app.db_schema")
