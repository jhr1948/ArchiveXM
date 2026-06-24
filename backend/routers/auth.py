"""
Authentication Router - Handle SiriusXM login and token management
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from datetime import datetime, timezone
import json

from database import get_db, Credentials, Session as AuthSession
from services.auth_service import AuthService

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    message: str
    expires_at: str | None = None


class StatusResponse(BaseModel):
    authenticated: bool
    username: str | None = None
    expires_at: str | None = None
    message: str


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, db: DBSession = Depends(get_db)):
    """
    Authenticate with SiriusXM and store session
    """
    try:
        auth_service = AuthService()
        result = await auth_service.authenticate(request.username, request.password)
        
        if not result["success"]:
            raise HTTPException(status_code=401, detail=result.get("error", "Authentication failed"))
        
        # Store credentials (encrypted) - check if this username already exists
        existing = db.query(Credentials).filter(Credentials.username == request.username).first()
        if existing:
            existing.password_encrypted = auth_service.encrypt_password(request.password)
            existing.updated_at = datetime.utcnow()
            credential_id = existing.id
        else:
            # Count existing credentials to set name
            cred_count = db.query(Credentials).count()
            creds = Credentials(
                name=f"Account {cred_count + 1}" if cred_count > 0 else "Primary",
                username=request.username,
                password_encrypted=auth_service.encrypt_password(request.password),
                is_active=True,
                max_streams=3,
                priority=cred_count
            )
            db.add(creds)
            db.commit()
            db.refresh(creds)
            credential_id = creds.id
        
        # Store session linked to credential
        db.query(AuthSession).filter(AuthSession.credential_id == credential_id).update({"is_valid": False})
        
        session = AuthSession(
            credential_id=credential_id,
            bearer_token=result["bearer_token"],
            cookies=json.dumps(result.get("cookies", {})),
            expires_at=result.get("expires_at"),
            is_valid=True
        )
        db.add(session)
        db.commit()
        
        return LoginResponse(
            success=True,
            message="Authentication successful",
            expires_at=result.get("expires_at").isoformat() if result.get("expires_at") else None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Authentication error: {str(e)}")


@router.get("/status", response_model=StatusResponse)
async def auth_status(db: DBSession = Depends(get_db)):
    """
    Check current authentication status
    """
    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    creds = db.query(Credentials).first()
    
    if not session:
        return StatusResponse(
            authenticated=False,
            message="Not authenticated"
        )
    
    # Check if expired
    if session.expires_at and session.expires_at < datetime.utcnow():
        session.is_valid = False
        db.commit()
        return StatusResponse(
            authenticated=False,
            username=creds.username if creds else None,
            message="Session expired"
        )
    
    return StatusResponse(
        authenticated=True,
        username=creds.username if creds else None,
        expires_at=session.expires_at.isoformat() if session.expires_at else None,
        message="Authenticated"
    )


@router.post("/refresh")
async def refresh_token(db: DBSession = Depends(get_db)):
    """
    Refresh authentication token using stored credentials
    """
    creds = db.query(Credentials).first()
    
    if not creds:
        raise HTTPException(status_code=401, detail="No stored credentials")
    
    try:
        auth_service = AuthService()
        password = auth_service.decrypt_password(creds.password_encrypted)
        result = await auth_service.authenticate(creds.username, password)
        
        if not result["success"]:
            raise HTTPException(status_code=401, detail="Token refresh failed")
        
        # Update session
        db.query(AuthSession).update({"is_valid": False})
        
        session = AuthSession(
            bearer_token=result["bearer_token"],
            cookies=json.dumps(result.get("cookies", {})),
            expires_at=result.get("expires_at"),
            is_valid=True
        )
        db.add(session)
        db.commit()
        
        return {"success": True, "message": "Token refreshed"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Refresh error: {str(e)}")


@router.post("/logout")
async def logout(db: DBSession = Depends(get_db)):
    """
    Clear authentication session
    """
    db.query(AuthSession).update({"is_valid": False})
    db.commit()
    return {"success": True, "message": "Logged out"}


@router.get("/token")
async def get_bearer_token(db: DBSession = Depends(get_db)):
    """
    Get current bearer token (for internal use)
    """
    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if session.expires_at and session.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Session expired")
    
    return {"bearer_token": session.bearer_token}


@router.get("/token-status")
async def token_status(db: DBSession = Depends(get_db)):
    """
    Get detailed token status including time until expiry and auto-refresh info
    """
    from datetime import timedelta
    from services.token_manager import get_token_manager
    
    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    creds = db.query(Credentials).first()
    token_manager = get_token_manager()
    
    if not session:
        return {
            "has_token": False,
            "has_stored_credentials": creds is not None,
            "auto_refresh_enabled": True,
            "message": "No active session. Please log in."
        }
    
    now = datetime.now(timezone.utc)
    expires_at = session.expires_at
    
    # Make timezone aware if needed
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    
    # Calculate time remaining
    time_remaining = None
    time_remaining_str = None
    is_expired = False
    will_refresh_soon = False
    
    if expires_at:
        time_remaining = expires_at - now
        is_expired = time_remaining.total_seconds() <= 0
        
        if is_expired:
            time_remaining_str = "Expired"
        else:
            hours = int(time_remaining.total_seconds() // 3600)
            minutes = int((time_remaining.total_seconds() % 3600) // 60)
            time_remaining_str = f"{hours}h {minutes}m"
            
            # Will refresh if within 30 minutes of expiry
            will_refresh_soon = time_remaining.total_seconds() <= 1800
    
    return {
        "has_token": True,
        "has_stored_credentials": creds is not None,
        "username": creds.username if creds else None,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "time_remaining": time_remaining_str,
        "is_expired": is_expired,
        "will_auto_refresh_soon": will_refresh_soon,
        "auto_refresh_enabled": True,
        "auto_refresh_threshold_minutes": 30,
        "message": "Token expired - will auto-refresh on next API call" if is_expired else 
                   "Token will auto-refresh soon" if will_refresh_soon else
                   f"Token valid for {time_remaining_str}"
    }
