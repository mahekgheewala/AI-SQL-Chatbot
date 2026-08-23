from fastapi import APIRouter, HTTPException, Depends
from models.schemas import (
    SelectDBRequest,
    SchemaResponse,
    CreateDatabaseRequest,
    CreateTableRequest,
)
from models.domain import User
from auth.dependencies import get_current_user
from db.schema_fetcher import fetch_all_databases, fetch_schema, fetch_table_schemas
from db.database_manager import create_database
from db.table_manager import fetch_tables, create_table as create_table_in_db
from state.metadata_store import (
    set_databases,
    sync_database_context,
    set_schema,
    set_tables,
    set_table_schemas,
    get_metadata,
    refresh_routing_summaries,
)

router = APIRouter()

@router.get("/databases")
async def list_databases(current_user: User = Depends(get_current_user)):
    try:
        dbs = fetch_all_databases()
        set_databases(dbs)
        return {"databases": dbs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection error: {str(e)}")

@router.post("/select-database", response_model=SchemaResponse)
async def select_database(request: SelectDBRequest, current_user: User = Depends(get_current_user)):
    try:
        schema = fetch_schema(request.database)
        tables = fetch_tables(request.database)
        table_schemas = fetch_table_schemas(request.database)
        
        sync_database_context(request.database)
        
        return {"selected": request.database, "schema": schema}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not fetch schema: {str(e)}")

@router.get("/schema")
async def get_current_schema(current_user: User = Depends(get_current_user)):
    meta = get_metadata()
    if not meta["selected_db"]:
        raise HTTPException(status_code=404, detail="No database selected")
    return {"selected": meta["selected_db"], "schema": meta["schema"]}

@router.post("/create-database")
async def create_new_database(request: CreateDatabaseRequest, current_user: User = Depends(get_current_user)):
    try:
        create_database(request.database_name)
        dbs = fetch_all_databases()
        set_databases(dbs)
        refresh_routing_summaries()
        return {
            "message": f"Database '{request.database_name}' created successfully.",
            "databases": dbs,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not create database: {str(e)}")

@router.get("/tables/{database_name}")
async def list_tables(database_name: str, current_user: User = Depends(get_current_user)):
    try:
        tables = fetch_tables(database_name)
        set_tables(tables)
        return {"tables": tables}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not fetch tables: {str(e)}")

@router.post("/create-table")
async def create_new_table(request: CreateTableRequest, current_user: User = Depends(get_current_user)):
    try:
        columns = [{"name": col.name, "type": col.type} for col in request.columns]
        create_table_in_db(request.database, request.table_name, columns)

        tables = fetch_tables(request.database)
        schema = fetch_schema(request.database)
        set_tables(tables)
        set_schema(schema)
        refresh_routing_summaries()

        return {
            "message": f"Table '{request.table_name}' created successfully.",
            "tables": tables,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not create table: {str(e)}")
