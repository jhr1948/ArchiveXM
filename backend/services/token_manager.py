"""
Token Manager Service - Centralized token management with auto-refresh

Handles:
- Token retrieval with automatic refresh on expiry
- Auto-retry on 401/403 errors (like m3u8XM approach)
- Proactive refresh before token expires
- Background refresh scheduling
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable, TypeVar, Any
from functools import wraps
import httpx

# Singleton instance
_token_manager: Optional['TokenManager'] = None


class TokenManager:
    """
    Centralized token management with automatic refresh
    
    Uses stored credentials to re-authenticate when token expires or API returns 401/403
    """
    
    # Refresh token when within this many minutes of expiry
    REFRESH_THRESHOLD_MINUTES = 30
    
    # Maximum retry attempts for auto-refresh
    MAX_RETRIES = 2
    
    def __init__(self):
        self._bearer_token: Optional[str] = None
        self._expires_at: Optional[datetime] = None
        self._refresh_lock = asyncio.Lock()
        self._last_refresh_attempt: Optional[datetime] = None
        self._refresh_cooldown_seconds = 30  # Minimum time between refresh attempts
    
    @property
    def bearer_token(self) -> Optional[str]:
        return self._bearer_token
    
    @property
    def expires_at(self) -> Optional[datetime]:
        return self._expires_at
    
    @property
    def is_expired(self) -> bool:
        """Check if token is expired or close to expiry"""
        if not self._expires_at:
            return self._bearer_token is None
        
        threshold = datetime.now(timezone.utc) + timedelta(minutes=self.REFRESH_THRESHOLD_MINUTES)
        return self._expires_at < threshold
    
    @property
    def time_until_expiry(self) -> Optional[timedelta]:
        """Get time remaining until token expires"""
        if not self._expires_at:
            return None
        return self._expires_at - datetime.now(timezone.utc)
    
    def load_from_db(self):
        """Load current token from database"""
        from database import get_db_session, Session as AuthSession
        
        with get_db_session() as db:
            session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
            
            if session:
                self._bearer_token = session.bearer_token
                self._expires_at = session.expires_at
                if self._expires_at and self._expires_at.tzinfo is None:
                    # Make timezone-aware if needed
                    self._expires_at = self._expires_at.replace(tzinfo=timezone.utc)
                
                print(f"🔑 Token loaded from DB, expires: {self._expires_at}")
                return True
            
            print("⚠️ No valid session in database")
            return False
    
    async def get_valid_token(self) -> Optional[str]:
        """
        Get a valid bearer token, refreshing if necessary
        
        This is the main method to call - it handles all refresh logic
        Uses in-memory cache to avoid DB hits on every call
        """
        # Only load from DB if we don't have a token at all
        if not self._bearer_token:
            self.load_from_db()
        
        # If we still don't have a token after loading, return None
        if not self._bearer_token:
            return None
        
        # If expired or close to expiry, refresh
        if self.is_expired:
            print(f"🔄 Token expired or expiring soon, refreshing...")
            success = await self.refresh_token()
            if not success:
                # Return existing token anyway - it might still work
                # The API will retry with refresh on 401/403
                return self._bearer_token
        
        return self._bearer_token
    
    async def refresh_token(self) -> bool:
        """
        Refresh the authentication token using stored credentials
        
        Returns True on success, False on failure
        """
        # Prevent concurrent refresh attempts
        async with self._refresh_lock:
            # Check cooldown to prevent refresh spam
            if self._last_refresh_attempt:
                elapsed = (datetime.now(timezone.utc) - self._last_refresh_attempt).total_seconds()
                if elapsed < self._refresh_cooldown_seconds:
                    print(f"⏳ Refresh cooldown active ({self._refresh_cooldown_seconds - elapsed:.0f}s remaining)")
                    return self._bearer_token is not None
            
            self._last_refresh_attempt = datetime.now(timezone.utc)
            
            print("🔐 Refreshing authentication token...")
            
            try:
                from database import get_db_session, Credentials, Session as AuthSession
                from services.auth_service import AuthService
                import json
                
                with get_db_session() as db:
                    creds = db.query(Credentials).first()
                    
                    if not creds:
                        print("❌ No stored credentials for refresh")
                        return False
                    
                    auth_service = AuthService()
                    password = auth_service.decrypt_password(creds.password_encrypted)
                    
                    result = await auth_service.authenticate(creds.username, password)
                    
                    if not result["success"]:
                        print(f"❌ Token refresh failed: {result.get('error')}")
                        return False
                    
                    # Invalidate old sessions
                    db.query(AuthSession).update({"is_valid": False})
                    
                    # Create new session
                    new_session = AuthSession(
                        bearer_token=result["bearer_token"],
                        cookies=json.dumps(result.get("cookies", {})),
                        expires_at=result.get("expires_at"),
                        is_valid=True
                    )
                    db.add(new_session)
                    db.commit()
                    
                    # Update local state
                    self._bearer_token = result["bearer_token"]
                    self._expires_at = result.get("expires_at")
                    if self._expires_at and self._expires_at.tzinfo is None:
                        self._expires_at = self._expires_at.replace(tzinfo=timezone.utc)
                    
                    print(f"✅ Token refreshed successfully! Expires: {self._expires_at}")
                    return True
                    
            except Exception as e:
                print(f"❌ Token refresh error: {e}")
                import traceback
                traceback.print_exc()
                return False
    
    async def execute_with_retry(
        self,
        request_func: Callable,
        *args,
        max_retries: int = None,
        **kwargs
    ) -> Any:
        """
        Execute an async function with automatic token refresh on 401/403
        
        This mimics the m3u8XM approach: retry with re-auth on 4xx errors
        
        Args:
            request_func: Async function to execute
            max_retries: Maximum retry attempts (default: MAX_RETRIES)
            
        Returns:
            Result of request_func
        """
        if max_retries is None:
            max_retries = self.MAX_RETRIES
        
        for attempt in range(max_retries + 1):
            try:
                result = await request_func(*args, **kwargs)
                return result
                
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                
                if status_code in (401, 403) and attempt < max_retries:
                    print(f"⚠️ Got {status_code}, attempting token refresh (attempt {attempt + 1}/{max_retries})")
                    
                    success = await self.refresh_token()
                    if success:
                        # Update token in kwargs if present
                        if 'headers' in kwargs and 'Authorization' in kwargs['headers']:
                            kwargs['headers']['Authorization'] = f'Bearer {self._bearer_token}'
                        continue
                    else:
                        raise
                else:
                    raise
                    
            except Exception as e:
                # For non-HTTP errors, check if it looks like an auth issue
                error_str = str(e).lower()
                if ('401' in error_str or '403' in error_str or 'unauthorized' in error_str) and attempt < max_retries:
                    print(f"⚠️ Possible auth error: {e}, attempting refresh (attempt {attempt + 1}/{max_retries})")
                    await self.refresh_token()
                    continue
                raise
        
        return None
    
    def invalidate(self):
        """Invalidate current token (forces refresh on next use)"""
        self._bearer_token = None
        self._expires_at = None
        print("🔒 Token invalidated")


def get_token_manager() -> TokenManager:
    """Get the singleton TokenManager instance"""
    global _token_manager
    if _token_manager is None:
        _token_manager = TokenManager()
    return _token_manager


async def start_background_refresh(check_interval_minutes: int = 10):
    """
    Background task that proactively refreshes token before expiry
    
    This runs continuously and checks token expiry periodically
    """
    manager = get_token_manager()
    
    print(f"🔄 Starting background token refresh (checking every {check_interval_minutes} min)")
    
    while True:
        try:
            await asyncio.sleep(check_interval_minutes * 60)
            
            # Load latest from DB
            manager.load_from_db()
            
            if manager.is_expired:
                print("🔄 Background refresh: Token expiring soon, refreshing...")
                await manager.refresh_token()
            else:
                remaining = manager.time_until_expiry
                if remaining:
                    print(f"✓ Token valid, expires in {remaining}")
                    
        except asyncio.CancelledError:
            print("🛑 Background token refresh stopped")
            break
        except Exception as e:
            print(f"⚠️ Background refresh error: {e}")
            # Continue running despite errors
            await asyncio.sleep(60)
