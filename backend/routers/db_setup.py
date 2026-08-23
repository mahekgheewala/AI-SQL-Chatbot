from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.orm import Session
from db.app_database import get_db
from models.domain import User
from auth.dependencies import get_current_user
from services.connection_service import ConnectionService

router = APIRouter(tags=["db_setup"])

class ConnectionRequest(BaseModel):
    host: str
    port: int = 5432
    username: str
    password: str
    database: str
    remember: bool = True
    # Phase 9.6: Optional per-user database ACL (JSON list). Empty/null means
    # the user may only access their default database.
    allowed_databases: Optional[List[str]] = None

@router.post("/test-connection")
def test_connection(req: ConnectionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    conn_service = ConnectionService(db)
    success = conn_service.validate_connection(
        host=req.host,
        port=req.port,
        username=req.username,
        password=req.password,
        database=req.database
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to connect to the database. Please check your credentials.")
    return {"message": "Connection successful"}

@router.post("/save-connection")
def save_connection(req: ConnectionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    conn_service = ConnectionService(db)
    
    # Optional: validate before saving
    success = conn_service.validate_connection(
        host=req.host,
        port=req.port,
        username=req.username,
        password=req.password,
        database=req.database
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to connect to the database. Configuration not saved.")
        
    conn_service.save_connection(
        user_id=current_user.id,
        host=req.host,
        port=req.port,
        username=req.username,
        password=req.password,
        database=req.database,
        remember=req.remember,
        allowed_databases=req.allowed_databases,
    )
    
    return {"message": "Database connection saved successfully"}
