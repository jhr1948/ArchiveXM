"""
Authentication Service - Handle SiriusXM login via API
Based on m3u8XM approach - pure API, no browser needed
"""
import httpx
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
import json
import os
from cryptography.fernet import Fernet


class AuthService:
    """Handle SiriusXM authentication via API"""
    
    USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
    API_BASE = 'https://api.edge-gateway.siriusxm.com'
    
    def __init__(self):
        self.encryption_key = self._get_or_create_key()
        self.fernet = Fernet(self.encryption_key)
    
    def _get_or_create_key(self) -> bytes:
        """Get or create encryption key for passwords"""
        key_file = "/app/data/.encryption_key"
        
        if os.path.exists(key_file):
            with open(key_file, "rb") as f:
                return f.read()
        
        key = Fernet.generate_key()
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, "wb") as f:
            f.write(key)
        
        return key
    
    def encrypt_password(self, password: str) -> str:
        """Encrypt password for storage"""
        return self.fernet.encrypt(password.encode()).decode()
    
    def decrypt_password(self, encrypted: str) -> str:
        """Decrypt stored password"""
        return self.fernet.decrypt(encrypted.encode()).decode()
    
    async def authenticate(self, username: str, password: str) -> Dict:
        """
        Authenticate with SiriusXM using pure API calls
        Based on m3u8XM approach - no browser needed!
        
        Flow:
        1. Register device -> get grant token
        2. Create anonymous session -> get access token  
        3. Authenticate with password -> get auth token
        4. Create authenticated session -> get final bearer token
        
        Returns:
            Dict with success, bearer_token, expires_at
        """
        print(f"🔐 Starting API authentication for {username}")
        
        try:
            async with httpx.AsyncClient() as client:
                headers = {
                    'User-Agent': self.USER_AGENT,
                    'Content-Type': 'application/json'
                }
                
                # Step 1: Register device
                print("📱 Registering device...")
                device_payload = {
                    'devicePlatform': 'web-desktop',
                    'deviceAttributes': {
                        'browser': {
                            'browserVersion': '7.74.0',
                            'userAgent': self.USER_AGENT,
                            'sdk': 'web',
                            'app': 'web',
                            'sdkVersion': '7.74.0',
                            'appVersion': '7.74.0'
                        }
                    },
                    'grantVersion': 'v2'
                }
                
                device_headers = {**headers, 'x-sxm-tenant': 'sxm'}
                response = await client.post(
                    f'{self.API_BASE}/device/v1/devices',
                    json=device_payload,
                    headers=device_headers,
                    timeout=30
                )
                
                if response.status_code not in [200, 201]:
                    print(f"❌ Device registration failed: {response.status_code}")
                    return {"success": False, "error": f"Device registration failed: {response.status_code}"}
                
                data = response.json()
                grant_token = data.get('grant')
                if grant_token:
                    headers['Authorization'] = f'Bearer {grant_token}'
                print("✅ Device registered")
                
                # Step 2: Create anonymous session
                print("🔓 Creating anonymous session...")
                response = await client.post(
                    f'{self.API_BASE}/session/v1/sessions/anonymous',
                    json={},
                    headers={**headers, 'x-sxm-tenant': 'sxm'},
                    timeout=30
                )
                
                if response.status_code not in [200, 201]:
                    print(f"❌ Anonymous session failed: {response.status_code}")
                    return {"success": False, "error": f"Anonymous session failed: {response.status_code}"}
                
                data = response.json()
                access_token = data.get('accessToken')
                if access_token:
                    headers['Authorization'] = f'Bearer {access_token}'
                print("✅ Anonymous session created")
                
                # Step 3: Authenticate with password
                print("🔐 Authenticating with credentials...")
                auth_payload = {
                    'handle': username,
                    'password': password
                }
                
                response = await client.post(
                    f'{self.API_BASE}/identity/v1/identities/authenticate/password',
                    json=auth_payload,
                    headers=headers,
                    timeout=30
                )
                
                if response.status_code not in [200, 201]:
                    print(f"❌ Password auth failed: {response.status_code}")
                    error_msg = "Invalid credentials"
                    try:
                        err_data = response.json()
                        error_msg = err_data.get('message', error_msg)
                    except:
                        pass
                    return {"success": False, "error": error_msg}
                
                data = response.json()
                # Update token if provided
                if 'grant' in data:
                    headers['Authorization'] = f'Bearer {data["grant"]}'
                elif 'accessToken' in data:
                    headers['Authorization'] = f'Bearer {data["accessToken"]}'
                print("✅ Password authenticated")
                
                # Step 4: Create authenticated session
                print("🎫 Creating authenticated session...")
                response = await client.post(
                    f'{self.API_BASE}/session/v1/sessions/authenticated',
                    json={},
                    headers=headers,
                    timeout=30
                )
                
                if response.status_code not in [200, 201]:
                    print(f"❌ Authenticated session failed: {response.status_code}")
                    return {"success": False, "error": f"Authenticated session failed: {response.status_code}"}
                
                data = response.json()
                
                # Get final bearer token
                bearer_token = data.get('accessToken') or data.get('grant')
                session_type = data.get('sessionType')
                
                if not bearer_token:
                    return {"success": False, "error": "No bearer token in response"}
                
                if session_type != 'authenticated':
                    print(f"⚠️ Session type: {session_type}")
                
                # Use actual token expiration if provided, otherwise default to 24 hours
                expires_at = None
                if 'accessTokenExpiresAt' in data:
                    try:
                        expires_at = datetime.fromisoformat(data['accessTokenExpiresAt'].replace('Z', '+00:00'))
                    except:
                        pass
                if not expires_at:
                    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
                
                print(f"✅ Authentication successful!")
                print(f"🎫 Bearer token: {bearer_token[:20]}...")
                print(f"⏰ Token expires: {expires_at}")
                
                return {
                    "success": True,
                    "bearer_token": bearer_token,
                    "cookies": {},  # No cookies needed with API auth
                    "expires_at": expires_at
                }
                
        except httpx.TimeoutException:
            print("❌ Authentication timed out")
            return {"success": False, "error": "Request timed out"}
        except Exception as e:
            print(f"❌ Authentication error: {e}")
            return {"success": False, "error": str(e)}
