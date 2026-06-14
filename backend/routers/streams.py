"""
Streams Router - Live streaming and DVR buffer access
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from typing import List, Optional
from datetime import datetime, timezone, timedelta

from database import get_db, Channel, Session as AuthSession
from services.sxm_api import SiriusXMAPI
from services.hls_service import HLSService

router = APIRouter()


def get_channel_type(channel_id: str, db: DBSession) -> str:
    """Return stored SiriusXM channel type for playback tuning."""
    channel = db.query(Channel).filter(Channel.channel_id == channel_id).first()

    if channel and getattr(channel, "channel_type", None):
        return channel.channel_type

    return "channel-linear"



class TrackInfo(BaseModel):
    artist: str
    title: str
    album: str | None
    timestamp_utc: str
    duration_ms: int
    time_ago: str | None
    image_url: str | None


class ScheduleResponse(BaseModel):
    channel_id: str
    channel_name: str
    current_track: TrackInfo | None
    tracks: List[TrackInfo]
    total: int


class StreamUrlResponse(BaseModel):
    channel_id: str
    stream_url: str
    expires_at: str | None


@router.get("/{channel_id}/schedule", response_model=ScheduleResponse)
async def get_schedule(
    channel_id: str,
    hours_back: int = Query(5, ge=1, le=5, description="Hours of history (1-5)"),
    db: DBSession = Depends(get_db)
):
    """
    Get track schedule for a channel (DVR buffer - up to 5 hours)
    """
    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    channel = db.query(Channel).filter(Channel.channel_id == channel_id).first()
    channel_name = channel.name if channel else "Unknown Channel"
    
    try:
        api = SiriusXMAPI(session.bearer_token)
        tracks = await api.get_schedule(channel_id, hours_back)
        
        now = datetime.now(timezone.utc)
        track_list = []
        
        for track in tracks:
            # Calculate time ago
            try:
                track_time = datetime.fromisoformat(track["timestamp_utc"].replace("Z", "+00:00"))
                delta = now - track_time
                
                if delta.total_seconds() < 60:
                    time_ago = "just now"
                elif delta.total_seconds() < 3600:
                    mins = int(delta.total_seconds() / 60)
                    time_ago = f"{mins} min ago"
                else:
                    hours = int(delta.total_seconds() / 3600)
                    time_ago = f"{hours}h ago"
            except:
                time_ago = None
            
            # Get image URL (now directly from track data)
            image_url = track.get("image_url")
            
            track_list.append(TrackInfo(
                artist=track.get("artist", "Unknown"),
                title=track.get("title", "Unknown"),
                album=track.get("album"),
                timestamp_utc=track.get("timestamp_utc", ""),
                duration_ms=track.get("duration_ms", 0),
                time_ago=time_ago,
                image_url=image_url
            ))
        
        # Current track is the last one
        current_track = track_list[-1] if track_list else None
        
        return ScheduleResponse(
            channel_id=channel_id,
            channel_name=channel_name,
            current_track=current_track,
            tracks=track_list,
            total=len(track_list)
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching schedule: {str(e)}")


@router.get("/{channel_id}/now-playing")
async def get_now_playing(channel_id: str, db: DBSession = Depends(get_db)):
    """
    Get currently playing track for a channel
    """
    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    channel = db.query(Channel).filter(Channel.channel_id == channel_id).first()
    
    try:
        api = SiriusXMAPI(session.bearer_token)
        tracks = await api.get_schedule(channel_id, hours_back=1)
        
        if not tracks:
            return {"channel_id": channel_id, "current_track": None}
        
        current = tracks[-1]
        images = current.get("images", {})
        
        return {
            "channel_id": channel_id,
            "channel_name": channel.name if channel else "Unknown",
            "current_track": {
                "artist": current.get("artist", "Unknown"),
                "title": current.get("title", "Unknown"),
                "album": current.get("album"),
                "timestamp_utc": current.get("timestamp_utc"),
                "duration_ms": current.get("duration_ms", 0),
                "image_url": images.get("default", {}).get("url") if isinstance(images, dict) else None
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.get("/{channel_id}/stream-url", response_model=StreamUrlResponse)
async def get_stream_url(channel_id: str, db: DBSession = Depends(get_db)):
    """
    Get HLS stream URL for a channel
    """
    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        api = SiriusXMAPI(session.bearer_token)
        result = await api.get_stream_url(channel_id, get_channel_type(channel_id, db))
        
        if not result or not result.get('stream_url'):
            raise HTTPException(status_code=500, detail="Failed to get stream URL")
        
        return StreamUrlResponse(
            channel_id=channel_id,
            stream_url=result['stream_url'],
            expires_at=None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# Store active stream sessions for proxy
_stream_sessions = {}

@router.get("/{channel_id}/proxy-stream")
async def proxy_stream(channel_id: str, db: DBSession = Depends(get_db)):
    """
    Proxy HLS master playlist - rewrites URLs to go through our proxy
    """
    import httpx
    from fastapi.responses import Response
    
    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        api = SiriusXMAPI(session.bearer_token)
        result = await api.get_stream_url(channel_id, get_channel_type(channel_id, db))
        
        if not result or not result.get('stream_url'):
            raise HTTPException(status_code=500, detail="Failed to get stream URL")
        
        master_url = result['stream_url']
        base_url = master_url.rsplit('/', 1)[0] + '/'
        
        # Store base URL for this channel's proxy requests
        _stream_sessions[channel_id] = {
            'base_url': base_url,
            'bearer': session.bearer_token
        }
        
        # Fetch the master playlist
        async with httpx.AsyncClient() as client:
            response = await client.get(master_url, timeout=15)
            
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to fetch stream")
            
            content = response.text
            
            # Rewrite URLs to go through our proxy endpoint.
            # For XTRA channels, expose only the 256k variant to avoid
            # player startup switching from 32k to 256k after the first segment.
            channel_type = get_channel_type(channel_id, db)
            source_lines = content.split('\n')

            if channel_type == "channel-xtra":
                filtered_lines = ["#EXTM3U"]
                i = 0

                while i < len(source_lines):
                    line = source_lines[i].strip()

                    if line.startswith("#EXT-X-STREAM-INF"):
                        variant_info = line
                        variant_uri = source_lines[i + 1].strip() if i + 1 < len(source_lines) else ""

                        if "_256k_" in variant_uri or "BANDWIDTH=281600" in variant_info:
                            filtered_lines.append(variant_info)
                            filtered_lines.append(variant_uri)

                        i += 2
                        continue

                    if line.startswith("#EXT-X-") and not line.startswith("#EXT-X-STREAM-INF"):
                        filtered_lines.append(line)
                        i += 1
                        continue

                    i += 1

                source_lines = filtered_lines

            rewritten_lines = []
            for line in source_lines:
                line = line.strip()
                if line and not line.startswith('#'):
                    rewritten_lines.append(f"/api/streams/{channel_id}/hls-proxy/{line}")
                else:
                    rewritten_lines.append(line)

            rewritten_content = '\n'.join(rewritten_lines)
            
            return Response(
                content=rewritten_content,
                media_type="application/vnd.apple.mpegurl",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "no-cache"
                }
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.get("/{channel_id}/hls-proxy/{path:path}")
async def hls_proxy(channel_id: str, path: str, db: DBSession = Depends(get_db)):
    """
    Proxy any HLS resource (variant playlists, segments, keys)
    """
    import httpx
    from fastapi.responses import Response
    
    # Get stored session info
    stream_info = _stream_sessions.get(channel_id)
    if not stream_info:
        # Try to get fresh session
        session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")
        
        api = SiriusXMAPI(session.bearer_token)
        result = await api.get_stream_url(channel_id, get_channel_type(channel_id, db))
        
        if result and result.get('stream_url'):
            base_url = result['stream_url'].rsplit('/', 1)[0] + '/'
            stream_info = {
                'base_url': base_url,
                'bearer': session.bearer_token
            }
            _stream_sessions[channel_id] = stream_info
        else:
            raise HTTPException(status_code=500, detail="No stream session")
    
    base_url = stream_info['base_url']
    bearer = stream_info['bearer']
    
    # Build full URL
    full_url = base_url + path
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Add auth for key requests
        if '/key/' in path or 'key' in path.lower():
            headers['Authorization'] = f'Bearer {bearer}'
        
        async with httpx.AsyncClient() as client:
            response = await client.get(full_url, headers=headers, timeout=30)
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail="Proxy fetch failed")
            
            content = response.content
            content_type = response.headers.get('content-type', 'application/octet-stream')
            
            # If it's a playlist, rewrite the URLs
            if '.m3u8' in path or 'mpegurl' in content_type.lower():
                text_content = content.decode('utf-8')
                rewritten_lines = []
                
                for line in text_content.split('\n'):
                    line = line.strip()
                    
                    # Handle key URLs in EXT-X-KEY tag
                    if line.startswith('#EXT-X-KEY:') and 'URI="' in line:
                        import re
                        match = re.search(r'URI="([^"]+)"', line)
                        if match:
                            key_url = match.group(1)
                            # Encode the key URL and proxy it
                            import urllib.parse
                            encoded_key = urllib.parse.quote(key_url, safe='')
                            proxy_key_url = f"/api/streams/{channel_id}/hls-key/{encoded_key}"
                            line = line.replace(f'URI="{key_url}"', f'URI="{proxy_key_url}"')
                        rewritten_lines.append(line)
                    elif line and not line.startswith('#'):
                        # Get the directory of current path for relative resolution
                        if '/' in path:
                            path_dir = path.rsplit('/', 1)[0] + '/'
                        else:
                            path_dir = ''
                        
                        if line.startswith('http'):
                            # Absolute URL - extract path and proxy it
                            line = f"/api/streams/{channel_id}/hls-proxy/{path_dir}{line.split('/')[-1]}"
                        else:
                            # Relative URL
                            line = f"/api/streams/{channel_id}/hls-proxy/{path_dir}{line}"
                        rewritten_lines.append(line)
                    else:
                        rewritten_lines.append(line)
                
                content = '\n'.join(rewritten_lines).encode('utf-8')
                content_type = 'application/vnd.apple.mpegurl'
            
            return Response(
                content=content,
                media_type=content_type,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "no-cache" if '.m3u8' in path else "max-age=3600"
                }
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")


@router.get("/{channel_id}/hls-key/{encoded_key:path}")
async def hls_key_proxy(channel_id: str, encoded_key: str, db: DBSession = Depends(get_db)):
    """
    Proxy HLS decryption key requests
    SiriusXM returns JSON with base64 key - we need to extract and return raw bytes
    """
    import httpx
    import urllib.parse
    import base64
    from fastapi.responses import Response
    
    # Decode the key URL
    key_url = urllib.parse.unquote(encoded_key)
    
    # Get bearer token
    stream_info = _stream_sessions.get(channel_id)
    if stream_info:
        bearer = stream_info['bearer']
    else:
        session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")
        bearer = session.bearer_token
    
    try:
        headers = {
            'Authorization': f'Bearer {bearer}',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(key_url, headers=headers, timeout=15)
            
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail="Key fetch failed")
            
            # SiriusXM returns JSON like {"keyId":"...", "key":"base64encodedkey"}
            # HLS.js needs raw 16-byte key
            content = response.content
            content_type = response.headers.get('content-type', '')
            
            if 'json' in content_type or content.startswith(b'{'):
                try:
                    import json
                    key_data = json.loads(content)
                    if 'key' in key_data:
                        # Decode base64 key to raw bytes
                        raw_key = base64.b64decode(key_data['key'])
                        content = raw_key
                except:
                    pass  # Return as-is if parsing fails
            
            return Response(
                content=content,
                media_type='application/octet-stream',
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "max-age=300"
                }
            )
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Key proxy error: {str(e)}")


@router.get("/{channel_id}/hls-playlist")
async def get_hls_playlist(
    channel_id: str,
    quality: str = Query("256k", description="Audio quality"),
    db: DBSession = Depends(get_db)
):
    """
    Get HLS variant playlist with segments (for DVR operations)
    """
    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        hls_service = HLSService(session.bearer_token)
        playlist_data = await hls_service.get_variant_playlist(channel_id, quality)
        
        return {
            "channel_id": channel_id,
            "quality": quality,
            "segments": playlist_data.get("segments", []),
            "total_segments": playlist_data.get("total_segments", 0),
            "duration_seconds": playlist_data.get("duration_seconds", 0)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
