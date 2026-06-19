from fastapi import APIRouter, HTTPException
from models.schemas import (
    SelectDBRequest,
    SchemaResponse,
    CreateDatabaseRequest,
    CreateTableRequest,
)
from db.schema_fetcher import fetch_all_databases, fetch_schema, fetch_table_schemas
from db.database_manager import create_database
from db.table_manager import fetch_tables, create_table as create_table_in_db
from state.metadata_store import (
    set_databases,
    set_selected_db,
    set_schema,
    set_tables,
    set_table_schemas,
    get_metadata,
    refresh_routing_summaries,   # Phase 7
)

router = APIRouter()


# ─── Phase 2 Base — Database & Schema Endpoints ──────────────────────────────

@router.get("/databases")
async def list_databases():
    """Return all available PostgreSQL databases and cache them in memory."""
    try:
        dbs = fetch_all_databases()
        set_databases(dbs)
        refresh_routing_summaries()  # Phase 7: rebuild cross-DB routing index on startup
        return {"databases": dbs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection error: {str(e)}")


@router.post("/select-database", response_model=SchemaResponse)
async def select_database(request: SelectDBRequest):
    """
    Select an active database: fetch + cache its schema AND its table list.
    Both are stored in metadata_store so Phase 3 AI can read them without
    re-querying PostgreSQL on every request.
    """
    try:
        schema = fetch_schema(request.database)
        tables = fetch_tables(request.database)
        table_schemas = fetch_table_schemas(request.database)
        set_selected_db(request.database)
        set_schema(schema)
        set_tables(tables)
        set_table_schemas(table_schemas)
        return {"selected": request.database, "schema": schema}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not fetch schema: {str(e)}")


@router.get("/schema")
async def get_current_schema():
    """Return the cached schema for the currently selected database."""
    meta = get_metadata()
    if not meta["selected_db"]:
        raise HTTPException(status_code=404, detail="No database selected")
    return {"selected": meta["selected_db"], "schema": meta["schema"]}


# ─── Phase 2 Additional — Dynamic Database Management ────────────────────────

@router.post("/create-database")
async def create_new_database(request: CreateDatabaseRequest):
    """
    Create a new PostgreSQL database dynamically.

    Flow:
      1. Validate the name (letters/numbers/underscores, starts with letter).
      2. Run CREATE DATABASE with autocommit=True (required by PostgreSQL).
      3. Refresh the databases list in metadata_store.
      4. Return the updated database list so the frontend can refresh its dropdown.
    """
    try:
        create_database(request.database_name)
        dbs = fetch_all_databases()
        set_databases(dbs)
        refresh_routing_summaries()  # Phase 7: new DB added, rebuild routing index
        return {
            "message": f"Database '{request.database_name}' created successfully.",
            "databases": dbs,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not create database: {str(e)}")


# ─── Phase 2 Additional — Dynamic Table Management ───────────────────────────

@router.get("/tables/{database_name}")
async def list_tables(database_name: str):
    """
    Return all table names in the public schema of a given database.
    Also updates the tables cache in metadata_store.
    """
    try:
        tables = fetch_tables(database_name)
        set_tables(tables)
        return {"tables": tables}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not fetch tables: {str(e)}")


@router.post("/create-table")
async def create_new_table(request: CreateTableRequest):
    """
    Create a new table in the specified database.

    Flow:
      1. Validate table name and all column names/types.
      2. Execute CREATE TABLE (auto-adding id SERIAL PRIMARY KEY).
      3. Refresh both the tables list and the schema in metadata_store.
      4. Return the updated tables list so the frontend can refresh its dropdown.
    """
    try:
        columns = [{"name": col.name, "type": col.type} for col in request.columns]
        create_table_in_db(request.database, request.table_name, columns)

        # Refresh tables + schema cache so metadata_store stays in sync
        tables = fetch_tables(request.database)
        schema = fetch_schema(request.database)
        set_tables(tables)
        set_schema(schema)
        refresh_routing_summaries()  # Phase 7: new table added, rebuild routing index

        return {
            "message": f"Table '{request.table_name}' created successfully.",
            "tables": tables,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not create table: {str(e)}")
