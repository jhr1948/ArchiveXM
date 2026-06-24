"""
HLS Service - Parse and manage HLS playlists and segments
"""
import httpx
import m3u8
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple
import re
import base64


class HLSService:
    """Handle HLS playlist parsing and segment management"""
    
    MAX_RETRIES = 2
    
    def __init__(self, bearer_token: str = None):
        self._token_manager = None
        self.bearer_token = bearer_token
        self._update_headers()
    
    def _update_headers(self):
        """Update headers with current bearer token"""
        self.headers = {
            'Authorization': f'Bearer {self.bearer_token}' if self.bearer_token else '',
            'User-Agent': 'Mozilla/5.0'
        }
    
    async def _ensure_valid_token(self) -> bool:
        """Ensure we have a valid token, refreshing if needed"""
        from services.token_manager import get_token_manager
        
        if self._token_manager is None:
            self._token_manager = get_token_manager()
        
        token = await self._token_manager.get_valid_token()
        if token and token != self.bearer_token:
            self.bearer_token = token
            self._update_headers()
        
        return self.bearer_token is not None
    
    async def _refresh_and_retry(self) -> bool:
        """Refresh token after a 401/403 error"""
        from services.token_manager import get_token_manager
        
        if self._token_manager is None:
            self._token_manager = get_token_manager()
        
        success = await self._token_manager.refresh_token()
        if success:
            self.bearer_token = self._token_manager.bearer_token
            self._update_headers()
        return success
    
    async def get_master_playlist(self, master_url: str) -> Dict:
        """
        Parse HLS master playlist
        
        Returns available qualities/variants
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(master_url, timeout=10)
                
                if response.status_code != 200:
                    return {"error": f"HTTP {response.status_code}"}
                
                playlist = m3u8.loads(response.text)
                base_url = master_url.rsplit('/', 1)[0] + '/'
                
                variants = []
                for p in playlist.playlists:
                    bandwidth = p.stream_info.bandwidth if p.stream_info else 0
                    
                    # Determine quality name
                    if bandwidth >= 250000:
                        quality = "256k"
                    elif bandwidth >= 120000:
                        quality = "128k"
                    elif bandwidth >= 60000:
                        quality = "64k"
                    else:
                        quality = "32k"
                    
                    variants.append({
                        "quality": quality,
                        "bandwidth": bandwidth,
                        "uri": base_url + p.uri if not p.uri.startswith('http') else p.uri
                    })
                
                return {
                    "master_url": master_url,
                    "variants": sorted(variants, key=lambda x: x["bandwidth"], reverse=True)
                }
                
        except Exception as e:
            return {"error": str(e)}
    
    async def get_variant_playlist(self, channel_id: str, quality: str = "256k") -> Dict:
        """
        Get variant playlist with all segments (includes 5-hour DVR buffer)
        
        Each segment has:
        - timestamp (EXT-X-PROGRAM-DATE-TIME)
        - duration (EXTINF)
        - url
        """
        from services.sxm_api import SiriusXMAPI
        
        try:
            # Get master playlist URL
            api = SiriusXMAPI(self.bearer_token)
            result = await api.get_stream_url(channel_id)
            
            if not result or not result.get('stream_url'):
                return {"error": "Could not get stream URL"}
            
            master_url = result['stream_url']
            
            # Get master playlist
            master_info = await self.get_master_playlist(master_url)
            
            if "error" in master_info:
                return master_info
            
            # Find requested quality
            variant_url = None
            for v in master_info.get("variants", []):
                if v["quality"] == quality:
                    variant_url = v["uri"]
                    break
            
            if not variant_url and master_info.get("variants"):
                variant_url = master_info["variants"][0]["uri"]
            
            if not variant_url:
                return {"error": "No variant playlist found"}
            
            # Get variant playlist
            async with httpx.AsyncClient() as client:
                response = await client.get(variant_url, timeout=15)
                
                if response.status_code != 200:
                    return {"error": f"HTTP {response.status_code}"}
                
                return self.parse_variant_playlist(response.text, variant_url)
                
        except Exception as e:
            return {"error": str(e)}
    
    def parse_variant_playlist(self, playlist_text: str, base_url: str) -> Dict:
        """
        Parse variant playlist into structured segments
        
        The playlist contains:
        - #EXT-X-PROGRAM-DATE-TIME: exact UTC timestamp per segment
        - #EXTINF: segment duration
        - Segment filename (relative URL)
        - #EXT-X-KEY: decryption key info
        """
        base_path = base_url.rsplit('/', 1)[0] + '/'
        
        segments = []
        current_timestamp = None
        current_duration = None
        key_url = None
        
        lines = playlist_text.split('\n')
        
        for line in lines:
            line = line.strip()
            
            if line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
                timestamp_str = line.split(':', 1)[1]
                try:
                    current_timestamp = datetime.fromisoformat(
                        timestamp_str.replace('Z', '+00:00')
                    )
                except:
                    current_timestamp = None
            
            elif line.startswith('#EXTINF:'):
                try:
                    duration_str = line.split(':')[1].rstrip(',')
                    current_duration = float(duration_str)
                except:
                    current_duration = 9.75  # Default segment duration
            
            elif line.startswith('#EXT-X-KEY:'):
                match = re.search(r'URI="([^"]+)"', line)
                if match:
                    key_url = match.group(1)
            
            elif line and not line.startswith('#') and '.aac' in line:
                # This is a segment URL
                segment_url = line if line.startswith('http') else base_path + line
                
                segments.append({
                    "url": segment_url,
                    "timestamp": current_timestamp.isoformat() if current_timestamp else None,
                    "duration": current_duration or 9.75
                })
        
        # Calculate total duration
        total_duration = sum(s["duration"] for s in segments)
        
        return {
            "segments": segments,
            "total_segments": len(segments),
            "duration_seconds": total_duration,
            "key_url": key_url,
            "base_url": base_path
        }
    
    async def get_decryption_key(self, key_url: str) -> Optional[bytes]:
        """
        Get AES-128 decryption key from SiriusXM
        """
        await self._ensure_valid_token()
        
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        key_url,
                        headers=self.headers,
                        timeout=10
                    )
                    
                    # Auto-retry on 401/403
                    if response.status_code in (401, 403) and attempt < self.MAX_RETRIES:
                        print(f"⚠️ Key API got {response.status_code}, refreshing token (attempt {attempt + 1})")
                        if await self._refresh_and_retry():
                            continue
                        return None
                    
                    if response.status_code == 200:
                        data = response.json()
                        key_b64 = data.get('key', '')
                        return base64.b64decode(key_b64)
                    
                    return None
                    
            except Exception as e:
                print(f"Error getting decryption key: {e}")
                if attempt < self.MAX_RETRIES:
                    await self._refresh_and_retry()
                    continue
                return None
        
        return None
    
    def filter_segments_for_track(
        self,
        segments: List[Dict],
        track_start: str,
        duration_ms: int
    ) -> List[Dict]:
        """
        Filter segments that fall within a track's time window
        
        This is the core DVR functionality - get exact segments for a track
        """
        try:
            start = datetime.fromisoformat(track_start.replace('Z', '+00:00'))
            end = start + timedelta(milliseconds=duration_ms)
            
            track_segments = []
            
            for seg in segments:
                if not seg.get("timestamp"):
                    continue
                
                seg_time = datetime.fromisoformat(seg["timestamp"].replace('Z', '+00:00'))
                seg_end = seg_time + timedelta(seconds=seg.get("duration", 9.75))
                
                # Include if segment overlaps with track window
                if seg_time < end and seg_end > start:
                    track_segments.append(seg)
            
            return track_segments
            
        except Exception as e:
            print(f"Error filtering segments: {e}")
            return []
    
    def filter_segments_for_time_range(
        self,
        segments: List[Dict],
        hours_back: int
    ) -> List[Dict]:
        """
        Filter segments for a time range (e.g., last 3 hours)
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        
        return [
            seg for seg in segments
            if seg.get("timestamp") and
            datetime.fromisoformat(seg["timestamp"].replace('Z', '+00:00')) >= cutoff
        ]
