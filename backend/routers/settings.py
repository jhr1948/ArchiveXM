"""
Settings Router - Manage application settings and credentials
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from typing import Optional, List
from datetime import datetime

from database import get_db, Credentials, Session as AuthSession, ActiveStream, Channel, Config
from services.auth_service import AuthService
from services.sxm_api import SiriusXMAPI
from services.credential_manager import get_credential_manager
from services.token_manager import get_token_manager

router = APIRouter()


async def _probe_playback(db: DBSession, bearer_token: str, lineup_id: Optional[str] = None):
    """Verify playback with a real tuneSource request instead of lineup-id presence."""
    channel = (
        db.query(Channel)
        .filter(Channel.channel_type == "channel-linear")
        .order_by(Channel.number.asc(), Channel.id.asc())
        .first()
    )
    if channel is None:
        channel = db.query(Channel).order_by(Channel.id.asc()).first()

    if channel is None:
        return {
            "status": "valid",
            "available": None,
            "message": "Login is valid; playback was not tested because no channels are loaded",
        }

    api = SiriusXMAPI(bearer_token=bearer_token, lineup_id=lineup_id)
    result = await api.get_stream_url(
        channel.channel_id,
        channel.channel_type or "channel-linear",
        ensure_valid_token=False,
        load_lineup_from_db=False,
    )
    if result and result.get("stream_url"):
        return {
            "status": "ready",
            "available": True,
            "message": "Playback verified",
        }

    error = api.last_stream_error or {}
    description = str(error.get("description") or "").strip()
    if error.get("kind") == "entitlement":
        return {
            "status": "login_only",
            "available": False,
            "message": "Login is valid, but SiriusXM rejected playback entitlement",
        }

    return {
        "status": "playback_error",
        "available": None,
        "message": f"Login is valid, but the playback check failed{': ' + description if description else ''}",
    }


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
    playback_status: str
    status_message: str
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
            playback_status=cred_stat.get('playback_status', 'needs_auth'),
            status_message=cred_stat.get('status_message', 'Authentication required'),
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
    
    playback_check = await _probe_playback(
        db, result["bearer_token"], result.get("lineup_id")
    )

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
        lineup_id=result.get("lineup_id"),
        playback_status=playback_check["status"],
        playback_message=playback_check["message"],
        expires_at=result.get("expires_at"),
        is_valid=True
    )
    db.add(session)
    db.commit()
    
    return {
        "success": True,
        "message": f"Credential '{request.name}' added successfully",
        "credential_id": credential.id,
        "status": playback_check["status"],
        "playback_available": playback_check["available"],
    }


@router.put("/credentials/{credential_id}")
async def update_credential(
    credential_id: int,
    request: CredentialUpdate,
    db: DBSession = Depends(get_db)
):
    """Update an existing credential, including its SiriusXM login."""
    credential = db.query(Credentials).filter(Credentials.id == credential_id).first()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")

    auth_service = AuthService()
    requested_username = request.username.strip() if request.username is not None else credential.username
    requested_password = request.password if request.password not in (None, "") else None
    username_changed = request.username is not None and requested_username != credential.username
    password_changed = requested_password is not None
    auth_changed = username_changed or password_changed
    auth_result = None

    if auth_changed:
        try:
            password_to_test = (
                requested_password
                if password_changed
                else auth_service.decrypt_password(credential.password_encrypted)
            )
            auth_result = await auth_service.authenticate(requested_username, password_to_test)
            if not auth_result.get("success"):
                raise HTTPException(
                    status_code=401,
                    detail=auth_result.get("error") or "Invalid SiriusXM username or password"
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Could not verify updated SiriusXM login: {str(e)}")

    if request.name is not None:
        credential.name = request.name
    if username_changed:
        credential.username = requested_username
    if password_changed:
        credential.password_encrypted = auth_service.encrypt_password(requested_password)
    if request.max_streams is not None:
        credential.max_streams = request.max_streams
    if request.priority is not None:
        credential.priority = request.priority
    if request.is_active is not None:
        credential.is_active = request.is_active

    playback_check = None
    if auth_result:
        playback_check = await _probe_playback(
            db, auth_result["bearer_token"], auth_result.get("lineup_id")
        )

        # Keep historical sessions for diagnostics, but make only the newly verified
        # login session valid. This prevents a username/password edit from continuing
        # to use an older bearer token.
        db.query(AuthSession).filter(
            AuthSession.credential_id == credential_id
        ).update({"is_valid": False})

        import json
        db.add(AuthSession(
            credential_id=credential.id,
            bearer_token=auth_result["bearer_token"],
            cookies=json.dumps(auth_result.get("cookies", {})),
            lineup_id=auth_result.get("lineup_id"),
            playback_status=playback_check["status"],
            playback_message=playback_check["message"],
            expires_at=auth_result.get("expires_at"),
            is_valid=True
        ))

    credential.updated_at = datetime.utcnow()
    db.commit()

    if auth_changed:
        # The singleton may still hold the old account token in memory. Force the
        # next API request to reload the newly-created session.
        get_token_manager().invalidate()

    return {
        "success": True,
        "message": playback_check["message"] if playback_check else "Credential updated successfully",
        "status": playback_check["status"] if playback_check else None,
        "playback_available": playback_check["available"] if playback_check else None,
    }


@router.delete("/credentials/{credential_id}")
async def delete_credential(credential_id: int, db: DBSession = Depends(get_db)):
    """Delete a credential and all of its dependent session/stream rows."""
    credential = db.query(Credentials).filter(Credentials.id == credential_id).first()
    if not credential:
        raise HTTPException(status_code=404, detail="Credential not found")

    # Account deletion is an explicit reset operation. Remove dependent rows first
    # instead of blocking deletion because stale stream/session records exist.
    deleted_streams = db.query(ActiveStream).filter(
        ActiveStream.credential_id == credential_id
    ).delete(synchronize_session=False)
    deleted_sessions = db.query(AuthSession).filter(
        AuthSession.credential_id == credential_id
    ).delete(synchronize_session=False)
    db.delete(credential)
    db.commit()

    # A deleted account's bearer may still be cached by the global token manager.
    get_token_manager().invalidate()

    return {
        "success": True,
        "message": "Credential deleted successfully",
        "deleted_sessions": deleted_sessions,
        "deleted_active_streams": deleted_streams
    }


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
            playback_check = await _probe_playback(
                db, result["bearer_token"], result.get("lineup_id")
            )

            # Update session only after login and playback probing are complete.
            db.query(AuthSession).filter(AuthSession.credential_id == credential_id).update({"is_valid": False})
            import json
            session = AuthSession(
                credential_id=credential.id,
                bearer_token=result["bearer_token"],
                cookies=json.dumps(result.get("cookies", {})),
                lineup_id=result.get("lineup_id"),
                playback_status=playback_check["status"],
                playback_message=playback_check["message"],
                expires_at=result.get("expires_at"),
                is_valid=True
            )
            db.add(session)
            db.commit()
            get_token_manager().invalidate()

            return {
                "success": True,
                "status": playback_check["status"],
                "playback_available": playback_check["available"],
                "message": playback_check["message"],
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
