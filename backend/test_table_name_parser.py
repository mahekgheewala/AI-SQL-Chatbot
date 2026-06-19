import sys
import os
import io

# Prevent Windows console UnicodeEncodeError for checkmark emoji (✅)
if sys.platform.startswith("win"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db.executor import extract_table_name, execute_sql
from models.schemas import ChatRequest
from routers.chat import chat_endpoint
from state.session_store import get_session, update_session
from state.metadata_store import clear as clear_meta, set_databases, refresh_routing_summaries

def test_parser_scenarios():
    print("--- Running Parser Extraction Unit Tests ---")
    
    # 1. Standard syntax, no space before parenthesis
    assert extract_table_name("CREATE TABLE demo(id integer)") == "demo"
    
    # 2. Standard syntax, space before parenthesis
    assert extract_table_name("CREATE TABLE demo (id integer)") == "demo"
    
    # 3. IF NOT EXISTS syntax
    assert extract_table_name("CREATE TABLE IF NOT EXISTS demo(id integer)") == "demo"
    
    # 4. Quoted identifier
    assert extract_table_name('CREATE TABLE IF NOT EXISTS "demo"(id integer)') == "demo"
    assert extract_table_name('CREATE TABLE `demo`(id integer)') == "demo"
    assert extract_table_name('CREATE TABLE [demo](id integer)') == "demo"
    
    # 5. Schema qualified identifiers
    assert extract_table_name("CREATE TABLE public.demo(id int)") == "demo"
    assert extract_table_name('CREATE TABLE "public"."demo"(id int)') == "demo"
    assert extract_table_name('CREATE TABLE public."demo"(id int)') == "demo"
    
    # 6. ALTER TABLE syntax
    assert extract_table_name("ALTER TABLE public.demo ADD COLUMN x int") == "demo"
    assert extract_table_name('ALTER TABLE "public"."demo" ADD COLUMN name text') == "demo"
    assert extract_table_name('ALTER TABLE IF EXISTS ONLY "public"."demo" ADD COLUMN name text') == "demo"
    
    # 7. Multiline SQL with comments
    sql_multiline = """
    -- setup comments
    CREATE TABLE demo (
        id int
    );
    """
    assert extract_table_name(sql_multiline) == "demo"
    
    # 8. Single quotes MUST NOT be recognized as valid PostgreSQL identifiers
    assert extract_table_name("CREATE TABLE 'products'(id int)") is None
    
    print("All Parser Extraction Unit Tests PASSED!\n")

async def test_integration_session_updates():
    print("--- Running Integration Session Update Tests ---")
    
    clear_meta()
    set_databases(["hr_database"])
    refresh_routing_summaries()
    
    session_id = "test-session-parser-999"
    update_session(session_id, selected_database="hr_database", selected_table=None) # Reset
    
    # Clean up test table to ensure CREATE TABLE runs successfully
    execute_sql("DROP TABLE IF EXISTS demo;", "hr_database")
    
    # Test 1: Chat execution table creation
    print("Executing CREATE TABLE demo(id int) via chat endpoint...")
    req = ChatRequest(message="create table demo(id integer)", session_id=session_id, history=[])
    res = await chat_endpoint(req)
    session = get_session(session_id)
    print("  Active selected_table in session:", session.get("selected_table"))
    assert session.get("selected_table") == "demo", "Failed to extract clean table name in integration!"
    
    # Test 2: Invalid single quote create table does not match or select
    update_session(session_id, selected_table="demo")
    print("Executing invalid CREATE TABLE 'products'(id int) via chat endpoint...")
    req = ChatRequest(message="create table 'products'(id integer)", session_id=session_id, history=[])
    res = await chat_endpoint(req)
    session = get_session(session_id)
    # The command should fail validation or be blocked, but selected_table should NOT become 'products' or 'products'(id
    print("  Active selected_table in session:", session.get("selected_table"))
    assert session.get("selected_table") != "'products'", "Single quotes incorrectly parsed as table name!"
    assert session.get("selected_table") != "products", "Single quotes incorrectly parsed as table name!"
    
    print("Integration Session Update Tests PASSED!\n")

async def run_all():
    print("====================================================")
    print("RUNNING TABLE NAME PARSER VERIFICATION TESTS")
    print("====================================================\n")
    
    test_parser_scenarios()
    await test_integration_session_updates()
    
    print("====================================================")
    print("ALL TABLE PARSER TESTS PASSED")
    print("====================================================")

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_all())
