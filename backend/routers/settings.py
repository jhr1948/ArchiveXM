"""
Settings Router - Manage application settings and credentials
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from typing import Optional, List
from datetime import datetime

from database import get_db, Credentials, Session as AuthSession, ActiveStream, Config
from services.auth_service import AuthService
from services.credential_manager import get_credential_manager

router = APIRouter()


class CredentialCreate(BaseModel):
    name: str
    username: str
    password: str
    max_streams: int = 3
    priority: int = 0


class CredentialUpdate(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    max_streams: Optional[int] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None


class CredentialResponse(BaseModel):
    id: int
    name: str
    username: str
    is_active: bool
    max_streams: int
    priority: int
    active_streams: int
    has_valid_session: bool
    session_expires_at: Optional[str] = None
    session_expires_in: Optional[str] = None
    created_at: str


class CredentialListResponse(BaseModel):
    credentials: List[CredentialResponse]
    total_capacity: int
    total_active_streams: int


@router.get("/credentials", response_model=CredentialListResponse)
async def list_credentials(db: DBSession = Depends(get_db)):
    """List all credentials with their status."""
    credential_manager = get_credential_manager()
    stats = credential_manager.get_stream_stats(db)
    
    credentials = []
    for cred_stat in stats['credentials']:
        cred = db.query(Credentials).filter(Credentials.id == cred_stat['id']).first()
        credentials.append(CredentialResponse(
            id=cred.id,
            name=cred.name or f"Account {cred.id}",
            username=cred.username,
            is_active=cred.is_active,
            max_streams=cred.max_streams,
            priority=cred.priority,
            active_streams=cred_stat['active_streams'],
            has_valid_session=cred_stat['has_valid_session'],
            session_expires_at=cred_stat.get('session_expires_at'),
            session_expires_in=cred_stat.get('session_expires_in'),
            created_at=cred.created_at.isoformat() if cred.created_at else ""
        ))
    
    return CredentialListResponse(
        credentials=credentials,
        total_capacity=stats['total_capacity'],
        total_active_streams=stats['total_active_streams']
    )


@router.post("/credentials")
async def add_credential(request: CredentialCreate, db: DBSession = Depends(get_db)):
    """Add a new credential."""
    auth_service = AuthService()
    
    # Test the credential first
    try:
        result = await auth_service.authenticate(request.username, request.password)
        if not result.get("success"):
            raise HTTPException(status_code=401, detail="Invalid credentials - authentication failed")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Could not verify credentials: {str(e)}")
    
    # Create credential
    credential = Credentials(
        name=request.name,
        username=request.username,
        password_encrypted=auth_service.encrypt_password(request.password),
        max_streams=request.max_streams,
        priority=request.priority,
        is_active=True
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)
    
    # Create initial session
    import json
    session = AuthSession(
        credential_id=credential.id,
        bearer_token=result["bearer_token"],
        cookies=json.dumps(result.get("cookies", {})),
        expires_at=result.get("expires_at"),
        is_valid=True
    )
    db.add(session)
    db.commit()
    
    return {
        "success": True,
        "message": f"Credential '{request.name}' added successfully",
        "credential_id": credential.id
    }


@router.put("/credentials/{credential_id}")
async def update_credential(
    credential_id: int, 
    request: CredentialUpdate, 
    db: DBSession = Depends(get_db)
):
    """Update an existing credential."""
    credential = db.query(Credentials).filter(Credentials.id == credential_id).first()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")
    
    auth_service = AuthService()
    
    # Update fields
    if request.name is not None:
        credential.name = request.name
    if request.username is not None:
        credential.username = request.username
    if request.password is not None:
        # Test new password
        try:
            result = await auth_service.authenticate(
                request.username or credential.username, 
                request.password
            )
            if not result.get("success"):
                raise HTTPException(status_code=401, detail="Invalid password")
            credential.password_encrypted = auth_service.encrypt_password(request.password)
            
            # Invalidate old sessions and create new one
            db.query(AuthSession).filter(AuthSession.credential_id == credential_id).update({"is_valid": False})
            import json
            session = AuthSession(
                credential_id=credential.id,
                bearer_token=result["bearer_token"],
                cookies=json.dumps(result.get("cookies", {})),
                expires_at=result.get("expires_at"),
                is_valid=True
            )
            db.add(session)
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Could not verify new password: {str(e)}")
    
    if request.max_streams is not None:
        credential.max_streams = request.max_streams
    if request.priority is not None:
        credential.priority = request.priority
    if request.is_active is not None:
        credential.is_active = request.is_active
    
    credential.updated_at = datetime.utcnow()
    db.commit()
    
    return {"success": True, "message": "Credential updated successfully"}


@router.delete("/credentials/{credential_id}")
async def delete_credential(credential_id: int, db: DBSession = Depends(get_db)):
    """Delete a credential."""
    credential = db.query(Credentials).filter(Credentials.id == credential_id).first()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")
    
    # Check if this is the last credential
    cred_count = db.query(Credentials).count()
    if cred_count <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last credential")
    
    # Check for active streams
    active_streams = db.query(ActiveStream).filter(ActiveStream.credential_id == credential_id).count()
    if active_streams > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete - credential has {active_streams} active streams")
    
    # Delete sessions
    db.query(AuthSession).filter(AuthSession.credential_id == credential_id).delete()
    
    # Delete credential
    db.query(Credentials).filter(Credentials.id == credential_id).delete()
    db.commit()
    
    return {"success": True, "message": "Credential deleted successfully"}


@router.post("/credentials/{credential_id}/test")
async def test_credential(credential_id: int, db: DBSession = Depends(get_db)):
    """Test a credential by attempting to authenticate."""
    credential = db.query(Credentials).filter(Credentials.id == credential_id).first()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")
    
    auth_service = AuthService()
    
    try:
        password = auth_service.decrypt_password(credential.password_encrypted)
        result = await auth_service.authenticate(credential.username, password)
        
        if result.get("success"):
            # Update session
            db.query(AuthSession).filter(AuthSession.credential_id == credential_id).update({"is_valid": False})
            import json
            session = AuthSession(
                credential_id=credential.id,
                bearer_token=result["bearer_token"],
                cookies=json.dumps(result.get("cookies", {})),
                expires_at=result.get("expires_at"),
                is_valid=True
            )
            db.add(session)
            db.commit()
            
            return {
                "success": True,
                "message": "Credential is valid",
                "expires_at": result.get("expires_at").isoformat() if result.get("expires_at") else None
            }
        else:
            return {"success": False, "message": "Authentication failed"}
    except Exception as e:
        return {"success": False, "message": f"Error: {str(e)}"}


@router.get("/stream-stats")
async def get_stream_stats(db: DBSession = Depends(get_db)):
    """Get current stream usage statistics."""
    credential_manager = get_credential_manager()
    return credential_manager.get_stream_stats(db)


@router.get("/active-streams")
async def list_active_streams(db: DBSession = Depends(get_db)):
    """List all active streams."""
    streams = db.query(ActiveStream).all()
    
    result = []
    for stream in streams:
        cred = db.query(Credentials).filter(Credentials.id == stream.credential_id).first()
        result.append({
            "id": stream.id,
            "credential_name": cred.name if cred else "Unknown",
            "stream_type": stream.stream_type,
            "channel_id": stream.channel_id,
            "started_at": stream.started_at.isoformat() if stream.started_at else None,
            "last_heartbeat": stream.last_heartbeat.isoformat() if stream.last_heartbeat else None
        })
    
    return {"active_streams": result}
