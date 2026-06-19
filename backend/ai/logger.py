import logging
import sys
from datetime import datetime

# Configure standard logger
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",  # We handle the formatting manually for this specific requirement
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

gemini_logger = logging.getLogger("gemini_logger")

def log_gemini_transaction(selected_db: str, selected_table: str, intent: str, user_question: str, generated_sql: str):
    """
    Logs the exact transaction details for Phase 3 requirements.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Handle None values elegantly
    db_display = selected_db if selected_db else "None"
    table_display = selected_table if selected_table else "None"
    sql_display = generated_sql if generated_sql else "None"

    log_block = f"""
==================================================
[PHASE 3 - GEMINI]
==================================================
Timestamp:
{timestamp}

Selected Database:
{db_display}

Selected Table:
{table_display}

User Question:
{user_question}

Detected Intent:
{intent}

Generated SQL:
{sql_display}
==================================================
"""
    gemini_logger.info(log_block)
