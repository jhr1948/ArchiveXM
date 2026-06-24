"""
Credential Manager Service - Handle multiple credentials with load balancing
"""
from datetime import datetime, timedelta
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import func
from typing import Optional, Dict, Any
import json

from database import Credentials, Session as AuthSession, ActiveStream, get_db_session
from services.auth_service import AuthService


class CredentialManager:
    """
    Manages multiple SiriusXM credentials with automatic load balancing.
    Tracks active streams per credential and selects the best available credential.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.auth_service = AuthService()
    
    def get_available_credential(self, db: DBSession, stream_type: str = 'live') -> Optional[Dict[str, Any]]:
        """
        Get the best available credential for a new stream.
        Returns credential info with valid bearer token, or None if all credentials are maxed out.
        """
        # Clean up stale streams first (older than 5 minutes without heartbeat)
        stale_threshold = datetime.utcnow() - timedelta(minutes=5)
        db.query(ActiveStream).filter(ActiveStream.last_heartbeat < stale_threshold).delete()
        db.commit()
        
        # Get all active credentials ordered by priority
        credentials = db.query(Credentials).filter(
            Credentials.is_active == True
        ).order_by(Credentials.priority).all()
        
        if not credentials:
            return None
        
        # Find credential with available capacity
        for cred in credentials:
            active_count = db.query(func.count(ActiveStream.id)).filter(
                ActiveStream.credential_id == cred.id
            ).scalar() or 0
            
            if active_count < cred.max_streams:
                # This credential has capacity - get or create session
                session = self._get_valid_session(db, cred)
                if session:
                    return {
                        'credential_id': cred.id,
                        'credential_name': cred.name,
                        'username': cred.username,
                        'bearer_token': session.bearer_token,
                        'active_streams': active_count,
                        'max_streams': cred.max_streams
                    }
        
        return None
    
    def _get_valid_session(self, db: DBSession, credential: Credentials) -> Optional[AuthSession]:
        """Get a valid session for a credential, refreshing if needed."""
        session = db.query(AuthSession).filter(
            AuthSession.credential_id == credential.id,
            AuthSession.is_valid == True
        ).first()
        
        # Check if session exists and is not expired
        if session and session.expires_at and session.expires_at > datetime.utcnow():
            return session
        
        # Need to authenticate
        try:
            password = self.auth_service.decrypt_password(credential.password_encrypted)
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context, need to run sync
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, self.auth_service.authenticate(credential.username, password))
                    result = future.result()
            else:
                result = asyncio.run(self.auth_service.authenticate(credential.username, password))
            
            if not result.get("success"):
                return None
            
            # Invalidate old sessions for this credential
            db.query(AuthSession).filter(
                AuthSession.credential_id == credential.id
            ).update({"is_valid": False})
            
            # Create new session
            new_session = AuthSession(
                credential_id=credential.id,
                bearer_token=result["bearer_token"],
                cookies=json.dumps(result.get("cookies", {})),
                expires_at=result.get("expires_at"),
                is_valid=True
            )
            db.add(new_session)
            db.commit()
            db.refresh(new_session)
            
            return new_session
            
        except Exception as e:
            print(f"Error authenticating credential {credential.name}: {e}")
            return None
    
    def register_stream(self, db: DBSession, credential_id: int, stream_type: str, channel_id: str) -> int:
        """Register a new active stream. Returns stream ID."""
        stream = ActiveStream(
            credential_id=credential_id,
            stream_type=stream_type,
            channel_id=channel_id
        )
        db.add(stream)
        db.commit()
        db.refresh(stream)
        return stream.id
    
    def unregister_stream(self, db: DBSession, stream_id: int):
        """Unregister a stream when it ends."""
        db.query(ActiveStream).filter(ActiveStream.id == stream_id).delete()
        db.commit()
    
    def heartbeat_stream(self, db: DBSession, stream_id: int):
        """Update heartbeat for an active stream."""
        db.query(ActiveStream).filter(ActiveStream.id == stream_id).update({
            "last_heartbeat": datetime.utcnow()
        })
        db.commit()
    
    def get_stream_stats(self, db: DBSession) -> Dict[str, Any]:
        """Get statistics about credential usage."""
        credentials = db.query(Credentials).filter(Credentials.is_active == True).all()
        
        stats = {
            'credentials': [],
            'total_active_streams': 0,
            'total_capacity': 0
        }
        
        for cred in credentials:
            active_count = db.query(func.count(ActiveStream.id)).filter(
                ActiveStream.credential_id == cred.id
            ).scalar() or 0
            
            # Get session status
            session = db.query(AuthSession).filter(
                AuthSession.credential_id == cred.id,
                AuthSession.is_valid == True
            ).first()
            
            has_valid_session = session is not None and (
                session.expires_at is None or session.expires_at > datetime.utcnow()
            )
            
            # Calculate session expiration info
            session_expires_at = None
            session_expires_in = None
            if session and session.expires_at:
                session_expires_at = session.expires_at.isoformat()
                remaining = session.expires_at - datetime.utcnow()
                if remaining.total_seconds() > 0:
                    hours = int(remaining.total_seconds() // 3600)
                    minutes = int((remaining.total_seconds() % 3600) // 60)
                    if hours > 24:
                        days = hours // 24
                        session_expires_in = f"{days}d {hours % 24}h"
                    elif hours > 0:
                        session_expires_in = f"{hours}h {minutes}m"
                    else:
                        session_expires_in = f"{minutes}m"
                else:
                    session_expires_in = "Expired"
            
            stats['credentials'].append({
                'id': cred.id,
                'name': cred.name,
                'username': cred.username,
                'active_streams': active_count,
                'max_streams': cred.max_streams,
                'priority': cred.priority,
                'has_valid_session': has_valid_session,
                'session_expires_at': session_expires_at,
                'session_expires_in': session_expires_in,
                'available_capacity': cred.max_streams - active_count
            })
            
            stats['total_active_streams'] += active_count
            stats['total_capacity'] += cred.max_streams
        
        stats['available_capacity'] = stats['total_capacity'] - stats['total_active_streams']
        
        return stats


def get_credential_manager() -> CredentialManager:
    """Get the singleton credential manager instance."""
    return CredentialManager()
