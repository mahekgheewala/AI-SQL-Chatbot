import sys
from datetime import datetime
from utils.logging_config import logger_ai

def log_gemini_transaction(selected_db: str, selected_table: str, intent: str, user_question: str, generated_sql: str):
    """
    Logs the exact transaction details for Phase 3 requirements.
    Also routes structured details to the app.log file using JSON.
    """
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    
    # Handle None values elegantly
    db_display = selected_db if selected_db else "None"
    table_display = selected_table if selected_table else "None"
    sql_display = generated_sql if generated_sql else "None"

    # 1. Human-readable console print (exactly as Phase 3 required)
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
    sys.stdout.write(log_block + "\n")
    sys.stdout.flush()

    # 2. Structured JSON logging via logger_ai (under app.log)
    try:
        logger_ai.info("Gemini transaction details", extra={
            "category": "ai",
            "operation_type": "SQL_GENERATION",
            "intent": intent,
            "user_question": user_question,
            "generated_sql": sql_display,
            "selected_db": db_display,
            "selected_table": table_display,
        })
    except Exception:
        pass # Silently handle any logging failure
