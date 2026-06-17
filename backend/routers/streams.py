"""
Streams Router - Live streaming and DVR buffer access
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from typing import List, Optional
from datetime import datetime, timezone, timedelta

from database import get_db, Channel, Session as AuthSession, Config
from services.sxm_api import SiriusXMAPI
from services.hls_service import HLSService

router = APIRouter()


def resolve_channel_id(channel_id: str, db: DBSession) -> str:
    """Resolve either a playable UUID or a SiriusXM channel number to the playable UUID."""
    channel = db.query(Channel).filter(Channel.channel_id == channel_id).first()
    if channel:
        return channel.channel_id

    try:
        channel_number = int(str(channel_id).strip())
    except (TypeError, ValueError):
        return channel_id

    channel = db.query(Channel).filter(Channel.number == channel_number).first()
    if channel and channel.channel_id:
        return channel.channel_id

    return channel_id


def get_channel_type(channel_id: str, db: DBSession) -> str:
    """Return stored SiriusXM channel type for playback tuning."""
    resolved_channel_id = resolve_channel_id(channel_id, db)
    channel = db.query(Channel).filter(Channel.channel_id == resolved_channel_id).first()

    if channel and getattr(channel, "channel_type", None):
        return channel.channel_type

    return "channel-linear"



def _get_config_value(db: DBSession, key: str, default=None):
    item = db.query(Config).filter(Config.key == key).first()
    if item is None or item.value in (None, ""):
        return default
    return item.value


def _get_bool_config(db: DBSession, key: str, default: bool = False) -> bool:
    value = _get_config_value(db, key, None)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _get_live_metadata_settings(db: DBSession, channel_id: str) -> dict:
    import json

    global_offset = float(_get_config_value(db, "live_metadata_offset_seconds", "38") or 38)
    hide_short_cuts = _get_bool_config(db, "live_metadata_hide_short_cuts", False)
    short_cut_max_seconds = float(_get_config_value(db, "live_metadata_short_cut_max_seconds", "45") or 45)

    offsets_raw = _get_config_value(db, "live_metadata_channel_offsets", "{}") or "{}"
    try:
        channel_offsets = json.loads(offsets_raw)
        if not isinstance(channel_offsets, dict):
            channel_offsets = {}
    except Exception:
        channel_offsets = {}

    resolved_channel_id = resolve_channel_id(channel_id, db)
    offset = channel_offsets.get(resolved_channel_id, global_offset)
    try:
        offset = float(offset)
    except Exception:
        offset = global_offset

    return {
        "offset_seconds": max(-120.0, min(120.0, offset)),
        "hide_short_cuts": hide_short_cuts,
        "short_cut_max_seconds": max(1.0, min(300.0, short_cut_max_seconds)),
        "channel_offsets": channel_offsets,
    }


def _track_time(track: dict):
    try:
        timestamp = track.get("timestamp_utc") or track.get("timestamp")
        if not timestamp:
            return None
        return datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except Exception:
        return None


def _is_live_cut(track: dict, max_seconds: float) -> bool:
    if track.get("is_interstitial"):
        return True
    try:
        duration_ms = int(track.get("duration_ms") or 0)
    except Exception:
        duration_ms = 0
    return duration_ms > 0 and duration_ms <= int(max_seconds * 1000)


def _select_current_live_track(raw_tracks: list, settings: dict):
    if not raw_tracks:
        return None

    offset_seconds = float(settings.get("offset_seconds") or 0)
    # Positive offset delays metadata: choose the item active at now - offset.
    # Negative offset advances metadata: choose the item active at now + abs(offset).
    effective_now = datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)
    hide_short_cuts = bool(settings.get("hide_short_cuts"))
    max_seconds = float(settings.get("short_cut_max_seconds") or 45)

    selected = None
    for track in raw_tracks:
        t = _track_time(track)
        if t is None or t > effective_now:
            continue
        if hide_short_cuts and _is_live_cut(track, max_seconds):
            continue
        selected = track

    if selected is not None:
        return selected

    # Fallback: pick the latest eligible item if the offset window landed before
    # the first raw metadata item in the response.
    for track in reversed(raw_tracks):
        if hide_short_cuts and _is_live_cut(track, max_seconds):
            continue
        return track

    return raw_tracks[-1]


def _to_track_info(track: dict, now: datetime):
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
    except Exception:
        time_ago = None

    return TrackInfo(
        artist=track.get("artist", "Unknown"),
        title=track.get("title", "Unknown"),
        album=track.get("album"),
        timestamp_utc=track.get("timestamp_utc", ""),
        duration_ms=track.get("duration_ms", 0),
        time_ago=time_ago,
        image_url=track.get("image_url"),
        is_interstitial=bool(track.get("is_interstitial", False)),
    )


class TrackInfo(BaseModel):
    artist: str
    title: str
    album: str | None
    timestamp_utc: str
    duration_ms: int
    time_ago: str | None
    image_url: str | None
    is_interstitial: bool | None = False


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
        settings = _get_live_metadata_settings(db, channel_id)
        future_seconds = int(abs(float(settings.get("offset_seconds") or 0))) + 30

        # Raw metadata is used only for the current live item so optional DJ/plug
        # cuts and timing offsets can be honored. Station History remains clean
        # and excludes interstitials/short cuts.
        raw_tracks = await api.get_schedule(
            channel_id,
            hours_back,
            include_interstitials=True,
            future_seconds=future_seconds,
        )
        history_tracks = [track for track in raw_tracks if not track.get("is_interstitial")]
        
        now = datetime.now(timezone.utc)
        track_list = [_to_track_info(track, now) for track in history_tracks]

        current_raw = _select_current_live_track(raw_tracks, settings)
        current_track = _to_track_info(current_raw, now) if current_raw else (track_list[-1] if track_list else None)
        
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
        settings = _get_live_metadata_settings(db, channel_id)
        future_seconds = int(abs(float(settings.get("offset_seconds") or 0))) + 30
        tracks = await api.get_schedule(
            channel_id,
            hours_back=1,
            include_interstitials=True,
            future_seconds=future_seconds,
        )
        
        if not tracks:
            return {"channel_id": channel_id, "current_track": None}
        
        current = _select_current_live_track(tracks, settings)
        
        return {
            "channel_id": channel_id,
            "channel_name": channel.name if channel else "Unknown",
            "current_track": {
                "artist": current.get("artist", "Unknown"),
                "title": current.get("title", "Unknown"),
                "album": current.get("album"),
                "timestamp_utc": current.get("timestamp_utc"),
                "duration_ms": current.get("duration_ms", 0),
                "image_url": current.get("image_url"),
                "is_interstitial": bool(current.get("is_interstitial", False)),
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

# Store XTRA stitched EVENT playlists by channel.
# This is intentionally small/simple: keep the active item, append one or more
# future items, and never emit EXT-X-ENDLIST while the player is active.
_xtra_sessions = {}

# One-shot XTRA manual skip requests. When an app calls the Next endpoint,
# we prefetch the next XTRA item here, then the next playlist reload starts
# from that prefetched item instead of the normal tuneSource result.
_xtra_manual_start = {}

# One-track XTRA previous history, matching the official app's single Back behavior.
_xtra_previous_tracks = {}


def _is_xtra_media_playlist_path(path: str) -> bool:
    return path.endswith(".m3u8") and "_full_v3.m3u8" in path


def _is_audio_segment_path(path: str) -> bool:
    clean = path.split("?", 1)[0].lower()
    return clean.endswith(".aac")


def _extract_xtra_segment_key(path: str) -> str:
    return path.split("?", 1)[0].rsplit("/", 1)[-1]


def _playlist_duration_seconds(playlist_text: str) -> float:
    total = 0.0
    for line in playlist_text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            try:
                total += float(line.split(":", 1)[1].split(",", 1)[0])
            except Exception:
                pass
    return total


def _extract_target_duration(playlist_text: str) -> str:
    for line in playlist_text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-TARGETDURATION"):
            return line
    return "#EXT-X-TARGETDURATION:10"


def _extract_version(playlist_text: str) -> str:
    for line in playlist_text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-VERSION"):
            return line
    return "#EXT-X-VERSION:3"


def _extract_key_line(playlist_text: str) -> str | None:
    for line in playlist_text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-KEY:"):
            return line
    return None


def _rewrite_key_line_for_proxy(channel_id: str, line: str) -> str:
    if not line or 'URI="' not in line:
        return line

    import re
    import urllib.parse

    match = re.search(r'URI="([^"]+)"', line)
    if not match:
        return line

    key_url = match.group(1)
    encoded_key = urllib.parse.quote(key_url, safe='')
    proxy_key_url = f"/api/streams/{channel_id}/hls-key/{encoded_key}"
    return line.replace(f'URI="{key_url}"', f'URI="{proxy_key_url}"')


def _extract_segments_with_durations(playlist_text: str) -> list[tuple[str, str]]:
    pairs = []
    pending_duration = None

    for raw_line in playlist_text.splitlines():
        line = raw_line.strip()

        if line.startswith("#EXTINF:"):
            pending_duration = line
        elif line and not line.startswith("#"):
            # Media playlist segment line. Skip anything that is not audio-ish.
            if ".aac" in line or ".ts" in line or ".m4s" in line:
                pairs.append((pending_duration or "#EXTINF:10.0,", line))
            pending_duration = None

    return pairs


def _absolute_resource_url(base_url: str, path_dir: str, resource_line: str) -> str:
    """Resolve a media-playlist resource line to an absolute SiriusXM URL."""
    if resource_line.startswith("http://") or resource_line.startswith("https://"):
        return resource_line

    return base_url + path_dir + resource_line


def _proxied_segment_line(channel_id: str, base_url: str, path_dir: str, segment_line: str) -> str:
    import urllib.parse

    absolute_url = _absolute_resource_url(base_url, path_dir, segment_line)
    encoded_url = urllib.parse.quote(absolute_url, safe='')
    return f"/api/streams/{channel_id}/hls-xtra-resource/{encoded_url}"


def _track_from_playlist(path: str, playlist_text: str, base_url: str) -> dict:
    path_dir = path.rsplit("/", 1)[0] + "/" if "/" in path else ""
    segments = _extract_segments_with_durations(playlist_text)

    return {
        "path": path,
        "base_url": base_url,
        "path_dir": path_dir,
        "playlist_text": playlist_text,
        "segments": segments,
        "segment_names": [_extract_xtra_segment_key(seg) for _, seg in segments],
        "duration": _playlist_duration_seconds(playlist_text),
    }


def _build_xtra_event_playlist(channel_id: str, session: dict) -> str:
    tracks = session.get("tracks") or []

    if not tracks:
        return "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:0\n"

    first_text = tracks[0]["playlist_text"]
    lines = [
        "#EXTM3U",
        _extract_version(first_text),
        _extract_target_duration(first_text),
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-START:TIME-OFFSET=0,PRECISE=YES",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        "#EXT-X-DISCONTINUITY-SEQUENCE:0",
    ]

    for index, track in enumerate(tracks):
        if index > 0:
            lines.append("#EXT-X-DISCONTINUITY")

        key_line = _extract_key_line(track["playlist_text"])
        if key_line:
            lines.append(_rewrite_key_line_for_proxy(channel_id, key_line))

        for duration_line, segment_line in track["segments"]:
            lines.append(duration_line)
            lines.append(_proxied_segment_line(channel_id, track["base_url"], track["path_dir"], segment_line))

    # Do not emit EXT-X-ENDLIST. VLC/IPTV clients should keep refreshing the
    # playlist, letting us append more XTRA items when the queue is nearly used.
    return "\n".join(lines) + "\n"


def _first_present(data: dict, keys: list[str]):
    for key in keys:
        value = data.get(key) if isinstance(data, dict) else None
        if value:
            return value
    return None


def _deep_first_present(data, keys):
    """Find the first non-empty value for any key in nested SiriusXM JSON."""
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if value not in (None, ""):
                return value
        for value in data.values():
            found = _deep_first_present(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for value in data:
            found = _deep_first_present(value, keys)
            if found not in (None, ""):
                return found
    return None


def _format_sxm_image_url(image_key, width=800, height=800):
    """Convert SiriusXM artwork keys to imgsrv URLs, or pass through good HTTPS URLs."""
    import base64
    import json

    if not image_key:
        return ""
    image_key = str(image_key)
    if image_key.startswith("http://") or image_key.startswith("https://"):
        if ".m3u8" in image_key or "/audio/" in image_key:
            return ""
        return image_key
    if "/audio/" in image_key or image_key.endswith(".m3u8"):
        return ""

    logo_json = json.dumps({
        "key": image_key,
        "edits": [
            {"format": {"type": "jpeg"}},
            {"resize": {"width": int(width or 800), "height": int(height or 800)}},
        ],
    }, separators=(",", ":"))
    encoded = base64.b64encode(logo_json.encode("ascii")).decode("utf-8")
    return f"https://imgsrv-sxm-prod-device.streaming.siriusxm.com/{encoded}"


def _xtra_track_item_from_tune(tune_data):
    """SiriusXM XTRA tune/peek responses store song info under streams[].metadata.xtra.items."""
    if not isinstance(tune_data, dict):
        return None
    for stream in tune_data.get("streams", []) or []:
        if not isinstance(stream, dict):
            continue
        items = (((stream.get("metadata") or {}).get("xtra") or {}).get("items") or [])
        for item in items:
            if isinstance(item, dict) and item.get("type") == "xtra-channel-track":
                return item
        for item in items:
            if isinstance(item, dict) and (item.get("name") or item.get("artistName") or item.get("title")):
                return item
    return None


def _xtra_art_from_item(item):
    if not isinstance(item, dict):
        return ""
    images = item.get("images") or {}
    candidates = [
        (((images.get("tile") or {}).get("aspect_1x1") or {}).get("preferredImage") or {}),
        (((images.get("tile") or {}).get("aspect_1x1") or {}).get("defaultImage") or {}),
        (((images.get("cover") or {}).get("aspect_1x1") or {}).get("preferredImage") or {}),
        (((images.get("cover") or {}).get("aspect_1x1") or {}).get("defaultImage") or {}),
    ]
    for image in candidates:
        if isinstance(image, dict):
            url = image.get("url")
            if url:
                return _format_sxm_image_url(url, image.get("width", 800), image.get("height", 800))

    def walk(obj):
        if isinstance(obj, dict):
            url = obj.get("url")
            if url and "/artwork/" in str(url):
                return _format_sxm_image_url(url, obj.get("width", 800), obj.get("height", 800))
            for value in obj.values():
                found = walk(value)
                if found:
                    return found
        elif isinstance(obj, list):
            for value in obj:
                found = walk(value)
                if found:
                    return found
        return ""

    return walk(item)


def _extract_xtra_skip_limits(tune_data):
    def find_skip_limits(obj):
        if isinstance(obj, dict):
            if isinstance(obj.get("skipLimits"), dict):
                return obj.get("skipLimits")
            for value in obj.values():
                found = find_skip_limits(value)
                if found:
                    return found
        elif isinstance(obj, list):
            for value in obj:
                found = find_skip_limits(value)
                if found:
                    return found
        return None

    skip_limits = find_skip_limits(tune_data) or {}
    limited = skip_limits.get("limited") if isinstance(skip_limits, dict) else {}
    if not isinstance(limited, dict):
        limited = {}

    def to_int(value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    available_forward = to_int(
        limited.get("availableForwardSkips", skip_limits.get("availableForwardSkips") if isinstance(skip_limits, dict) else 0),
        0,
    )
    available_backward = to_int(
        limited.get("availableBackwardSkips", skip_limits.get("availableBackwardSkips") if isinstance(skip_limits, dict) else 0),
        0,
    )
    more_time = limited.get("moreSkipsAvailableTime") if isinstance(limited, dict) else None
    if more_time is None and isinstance(skip_limits, dict):
        more_time = skip_limits.get("moreSkipsAvailableTime")

    return {
        "availableForwardSkips": available_forward,
        "availableBackwardSkips": available_backward,
        "moreSkipsAvailableTime": more_time,
    }


def _extract_xtra_track_metadata(channel_id, tune_data):
    import time

    item = _xtra_track_item_from_tune(tune_data)
    sequence_token = _deep_first_present(tune_data, ["sequenceToken"])
    source_context_id = _deep_first_present(tune_data, ["sourceContextId"])

    if item:
        title = item.get("name") or item.get("title") or ""
        artist = item.get("artistName") or item.get("artist") or ""
        album = item.get("albumName") or item.get("albumTitle") or item.get("album") or ""
        duration_ms = item.get("duration") or item.get("durationMs") or item.get("trackDurationMs") or 0
        image_url = _xtra_art_from_item(item)
        track_id = item.get("id")
    else:
        title = _deep_first_present(tune_data, ["trackTitle", "songTitle", "cutTitle", "name", "title"]) or ""
        artist = _deep_first_present(tune_data, ["artistName", "artist", "artists", "subtitle", "secondaryTitle"]) or ""
        album = _deep_first_present(tune_data, ["albumName", "albumTitle", "album"]) or ""
        duration_ms = _deep_first_present(tune_data, ["durationMs", "duration", "trackDurationMs"]) or 0
        image_url = ""
        track_id = None

    try:
        duration_ms = int(duration_ms) if duration_ms is not None else 0
    except (TypeError, ValueError):
        duration_ms = 0

    metadata = {
        "channelId": channel_id,
        "title": title or "",
        "artist": artist or "",
        "album": album or "",
        "imageUrl": image_url or "",
        "durationMs": duration_ms,
        "startedAtMs": int(time.time() * 1000),
        "isXtra": True,
    }
    if sequence_token:
        metadata["sequenceToken"] = sequence_token
    if source_context_id:
        metadata["sourceContextId"] = source_context_id
    if track_id:
        metadata["trackId"] = track_id
    metadata.update(_extract_xtra_skip_limits(tune_data))
    return metadata


async def _xtra_tune_or_peek(
    channel_id: str,
    bearer: str,
    source_context_id: str | None = None,
    sequence_token: str | None = None,
    use_peek: bool = False,
) -> dict | None:
    import httpx

    url_name = "peek" if use_peek and source_context_id else "tuneSource"
    url = f"https://api.edge-gateway.siriusxm.com/playback/play/v1/{url_name}"
    payload = {
        "id": channel_id,
        "type": "channel-xtra",
        "hlsVersion": "V3",
        "mtcVersion": "V2",
        "trackResumeSupported": False,
    }

    if url_name == "peek":
        payload["sourceContextId"] = source_context_id
        if sequence_token:
            payload["sequenceToken"] = sequence_token
    else:
        payload["manifestVariant"] = "FULL"

    headers = {
        "Authorization": f"Bearer {bearer}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload, timeout=15)

    if response.status_code != 200:
        print(f"⚠️ XTRA {url_name} failed channel={channel_id} status={response.status_code} body={response.text[:200]}")
        return None

    data = response.json()
    streams = data.get("streams", [])
    stream_url = None
    if streams:
        stream_url = streams[0].get("urls", [{}])[0].get("url")
    stream_url = stream_url or data.get("hlsUrl") or data.get("primaryStreamUrl")

    if not stream_url:
        print(f"⚠️ XTRA {url_name} returned no stream URL for {channel_id}")
        return None

    return {
        "stream_url": stream_url,
        "raw_data": data,
        "source_context_id": _first_present(data, ["sourceContextId"]) or source_context_id,
        "sequence_token": _first_present(data, ["sequenceToken"]) or sequence_token,
        "method": url_name,
    }


async def _fetch_xtra_256k_track(
    channel_id: str,
    bearer: str,
    source_context_id: str | None = None,
    sequence_token: str | None = None,
    use_peek: bool = True,
) -> dict | None:
    """
    Fetch a 256k XTRA FULL media playlist.

    When possible this uses SiriusXM peek with sourceContextId/sequenceToken
    so the stitched queue advances to a fresh next XTRA item instead of
    restarting/resuming the current item near its end.
    """
    import httpx

    result = await _xtra_tune_or_peek(
        channel_id=channel_id,
        bearer=bearer,
        source_context_id=source_context_id,
        sequence_token=sequence_token,
        use_peek=use_peek,
    )

    if not result or not result.get("stream_url"):
        print(f"⚠️ XTRA continuation failed to get stream URL for {channel_id}")
        return None

    master_url = result["stream_url"]
    master_base = master_url.rsplit("/", 1)[0] + "/"

    async with httpx.AsyncClient() as client:
        master_response = await client.get(master_url, timeout=15)

        if master_response.status_code != 200:
            print(f"⚠️ XTRA continuation master fetch failed: {master_response.status_code}")
            return None

        master_lines = [line.strip() for line in master_response.text.splitlines()]
        variant_path = None

        i = 0
        while i < len(master_lines):
            line = master_lines[i]

            if line.startswith("#EXT-X-STREAM-INF"):
                variant_info = line
                variant_uri = master_lines[i + 1].strip() if i + 1 < len(master_lines) else ""

                if "_256k_" in variant_uri or "BANDWIDTH=281600" in variant_info:
                    variant_path = variant_uri
                    break

                i += 2
                continue

            i += 1

        if not variant_path:
            print(f"⚠️ XTRA continuation could not find 256k variant for {channel_id}")
            return None

        if variant_path.startswith("http"):
            variant_url = variant_path
            # Best effort for proxy path building with absolute URLs.
            proxy_path = variant_path.split(master_base, 1)[-1] if variant_path.startswith(master_base) else variant_path.rsplit("/", 2)[-2] + "/" + variant_path.rsplit("/", 1)[-1]
        else:
            variant_url = master_base + variant_path
            proxy_path = variant_path

        variant_response = await client.get(variant_url, timeout=20)

        if variant_response.status_code != 200:
            print(f"⚠️ XTRA continuation variant fetch failed: {variant_response.status_code}")
            return None

        track = _track_from_playlist(proxy_path, variant_response.text, master_base)
        track["source_context_id"] = result.get("source_context_id")
        track["sequence_token"] = result.get("sequence_token")
        track["method"] = result.get("method")
        track["metadata"] = _extract_xtra_track_metadata(channel_id, result.get("raw_data") or {})
        return track


async def _ensure_xtra_queue(channel_id: str, session_info: dict, requested_path: str, initial_playlist_text: str | None = None):
    import time

    now = time.time()
    cached = _xtra_sessions.get(channel_id)

    # Reset the queue when a new XTRA tune creates a different media playlist path.
    if not cached or cached.get("active_path") != requested_path:
        if initial_playlist_text is None:
            return None

        # Manual Next requests prefetch a fresh XTRA item and place it here.
        # The next player reload consumes it exactly once so playback starts
        # with the skipped-to item instead of the normal tuneSource result.
        manual_start = _xtra_manual_start.pop(channel_id, None)
        first_track = manual_start.get("track") if manual_start else None

        if manual_start:
            if manual_start.get("source_context_id"):
                session_info["source_context_id"] = manual_start.get("source_context_id")
            if manual_start.get("sequence_token"):
                session_info["sequence_token"] = manual_start.get("sequence_token")

        # Initial tuneSource can represent the current/resumed XTRA item and
        # may be near its end. Use its continuity context to start the local
        # stitched queue from a fresh peek item when possible.
        if not first_track:
            first_track = await _fetch_xtra_256k_track(
                channel_id=channel_id,
                bearer=session_info.get("bearer"),
                source_context_id=session_info.get("source_context_id"),
                sequence_token=session_info.get("sequence_token"),
                use_peek=True,
            )

        if not first_track:
            first_track = _track_from_playlist(requested_path, initial_playlist_text, session_info.get("base_url", ""))

        if first_track.get("source_context_id"):
            session_info["source_context_id"] = first_track.get("source_context_id")
        if first_track.get("sequence_token"):
            session_info["sequence_token"] = first_track.get("sequence_token")

        cached = {
            "active_path": requested_path,
            "tracks": [first_track],
            "served": set(),
            "created": now,
            "last_access": now,
            "append_in_progress": False,
        }
        _xtra_sessions[channel_id] = cached
        print(
            f"🎧 XTRA queue started channel={channel_id} "
            f"method={first_track.get('method', 'initial')} "
            f"segments={len(first_track['segments'])}"
        )

    cached["last_access"] = now

    # Keep a small look-ahead queue so the XTRA Queue UI can show
    # a deeper look-ahead buffer without changing playback. This still appends
    # lazily as the player accesses the proxy/queue UI, so it should not hammer SXM.
    # Keep several tracks ready because some HLS clients do not reliably reload
    # the EVENT playlist exactly at every XTRA song boundary.
    total_segments = sum(len(track.get("segments") or []) for track in cached.get("tracks", []))
    served_count = len(cached.get("served", set()))
    remaining = total_segments - served_count

    should_append = len(cached.get("tracks", [])) < 8 or remaining <= 30

    if should_append and not cached.get("append_in_progress"):
        cached["append_in_progress"] = True
        try:
            next_track = await _fetch_xtra_256k_track(
                channel_id=channel_id,
                bearer=session_info.get("bearer"),
                source_context_id=session_info.get("source_context_id"),
                sequence_token=session_info.get("sequence_token"),
                use_peek=True,
            )
            if next_track and next_track.get("segments"):
                if next_track.get("source_context_id"):
                    session_info["source_context_id"] = next_track.get("source_context_id")
                if next_track.get("sequence_token"):
                    session_info["sequence_token"] = next_track.get("sequence_token")
                # Avoid appending an identical playlist path twice in a row.
                existing_paths = {track.get("path") for track in cached.get("tracks", [])}
                if next_track.get("path") not in existing_paths:
                    cached["tracks"].append(next_track)
                    print(
                        f"🎧 XTRA queue appended channel={channel_id} "
                        f"tracks={len(cached['tracks'])} total_segments="
                        f"{sum(len(track.get('segments') or []) for track in cached['tracks'])}"
                    )
                else:
                    print(f"⚠️ XTRA continuation returned duplicate path for {channel_id}; not appending")
        finally:
            cached["append_in_progress"] = False

    return cached


async def _refresh_xtra_lookahead(channel_id: str, session_info: dict | None = None):
    """Refresh/prefetch the known XTRA queue without changing playback.

    HLS players can buffer ahead and may not reload the media playlist often
    enough after the first stitched playlist response. This helper lets segment
    requests and the Queue UI keep the lookahead warm so playback can continue
    beyond the first couple of XTRA items and the UI can show Coming Up.
    """
    cached = _xtra_sessions.get(channel_id)
    if not cached:
        return None

    if session_info is None:
        session_info = _stream_sessions.get(channel_id)
    if not session_info:
        return cached

    active_path = cached.get("active_path")
    tracks = cached.get("tracks") or []
    initial_text = tracks[0].get("playlist_text") if tracks else None
    if not active_path or not initial_text:
        return cached

    try:
        return await _ensure_xtra_queue(
            channel_id=channel_id,
            session_info=session_info,
            requested_path=active_path,
            initial_playlist_text=initial_text,
        )
    except Exception as e:
        print(f"⚠️ XTRA lookahead refresh failed channel={channel_id}: {e}")
        return cached


def _schedule_xtra_lookahead_refresh(channel_id: str, session_info: dict | None = None):
    """Fire-and-forget lookahead refresh used by segment/resource proxy routes."""
    cached = _xtra_sessions.get(channel_id)
    if not cached or cached.get("append_in_progress"):
        return

    try:
        import asyncio
        asyncio.create_task(_refresh_xtra_lookahead(channel_id, session_info))
    except RuntimeError:
        # No running loop; ignore. Queue endpoint/manual refresh can still append.
        return



def _get_current_xtra_track_for_previous(channel_id: str):
    """Return the best-known currently playing XTRA item for one-step Back."""
    cached = _xtra_sessions.get(channel_id)
    if cached and cached.get("tracks"):
        return cached.get("tracks")[0]

    manual_start = _xtra_manual_start.get(channel_id)
    if manual_start and manual_start.get("track"):
        return manual_start.get("track")

    return None


def _public_xtra_metadata_from_track(track: dict | None, channel_id: str) -> dict:
    metadata = dict((track or {}).get("metadata") or {})
    if metadata:
        metadata["channelId"] = channel_id
        metadata["isXtra"] = True
    return metadata


def _xtra_duration_ms_for_track(track: dict | None) -> int:
    metadata = (track or {}).get("metadata") or {}
    duration_ms = metadata.get("durationMs") or metadata.get("duration_ms") or 0
    try:
        duration_ms = int(duration_ms)
    except (TypeError, ValueError):
        duration_ms = 0

    if duration_ms <= 0:
        try:
            duration_ms = int(float((track or {}).get("duration") or 0) * 1000)
        except (TypeError, ValueError):
            duration_ms = 0
    return max(0, duration_ms)


def _xtra_track_summary(track: dict | None, channel_id: str, role: str = "queue", index: int | None = None) -> dict | None:
    metadata = _public_xtra_metadata_from_track(track, channel_id)
    if not metadata:
        return None

    duration_ms = metadata.get("durationMs") or _xtra_duration_ms_for_track(track)
    try:
        duration_ms = int(duration_ms or 0)
    except (TypeError, ValueError):
        duration_ms = 0

    return {
        "role": role,
        "index": index,
        "title": metadata.get("title") or "",
        "artist": metadata.get("artist") or "",
        "album": metadata.get("album") or "",
        "imageUrl": metadata.get("imageUrl") or metadata.get("image_url") or "",
        "durationMs": duration_ms,
        "startedAtMs": metadata.get("startedAtMs"),
        "trackId": metadata.get("trackId"),
        "sequenceToken": metadata.get("sequenceToken"),
        "sourceContextId": metadata.get("sourceContextId"),
        "isXtra": True,
    }


def _xtra_track_identity(track: dict | None) -> tuple:
    metadata = (track or {}).get("metadata") or {}
    title = str(metadata.get("title") or "").strip().lower()
    artist = str(metadata.get("artist") or "").strip().lower()
    track_id = str(metadata.get("trackId") or metadata.get("sequenceToken") or (track or {}).get("path") or "").strip().lower()
    duration = _xtra_duration_ms_for_track(track)
    return (track_id, title, artist, duration)


def _xtra_queue_snapshot(channel_id: str) -> dict:
    import time

    cached = _xtra_sessions.get(channel_id) or {}
    tracks = list(cached.get("tracks") or [])
    served = cached.get("served") or set()

    if not tracks:
        manual_start = _xtra_manual_start.get(channel_id) or {}
        if manual_start.get("track"):
            tracks = [manual_start.get("track")]

    current_index = 0
    elapsed_ms = None

    # Do not use the served-segment set to decide Now/Previous. Browsers can
    # buffer an entire XTRA item quickly, which made the Queue card advance
    # to the next song while the audible player was still on the first song.
    # Instead use elapsed wall-clock time since ArchiveXM created the stitched
    # XTRA session. This matches the same duration model used by /metadata.
    if tracks and cached.get("created"):
        try:
            elapsed_ms = max(0, int((time.time() - float(cached.get("created"))) * 1000))
            offset = 0
            for idx, track in enumerate(tracks):
                duration_ms = max(1, _xtra_duration_ms_for_track(track))
                next_offset = offset + duration_ms
                if elapsed_ms < next_offset:
                    current_index = idx
                    break
                current_index = idx
                offset = next_offset
        except Exception:
            current_index = 0

    current_track = tracks[current_index] if tracks else None
    current_identity = _xtra_track_identity(current_track)

    # Prefer the explicit one-track Back item stored by Next/Previous controls.
    # During normal continuous playback, expose the naturally completed item
    # immediately before Now so the Queue card keeps a useful Previous row.
    manual_previous = _xtra_previous_tracks.get(channel_id)
    previous_track = manual_previous if _xtra_track_identity(manual_previous) != current_identity else None
    if not previous_track and current_index > 0 and current_index - 1 < len(tracks):
        candidate_previous = tracks[current_index - 1]
        if _xtra_track_identity(candidate_previous) != current_identity:
            previous_track = candidate_previous

    upcoming_tracks = []
    seen = {current_identity}
    if previous_track:
        seen.add(_xtra_track_identity(previous_track))

    for track in tracks[current_index + 1:]:
        ident = _xtra_track_identity(track)
        if ident in seen:
            continue
        seen.add(ident)
        upcoming_tracks.append(track)

    return {
        "ok": True,
        "channelId": channel_id,
        "hasActiveQueue": bool(cached or tracks),
        "previous": _xtra_track_summary(previous_track, channel_id, "previous") if previous_track else None,
        "current": _xtra_track_summary(current_track, channel_id, "current", current_index) if current_track else None,
        "upcoming": [
            item for item in (
                _xtra_track_summary(track, channel_id, "upcoming", current_index + 1 + idx)
                for idx, track in enumerate(upcoming_tracks[:6])
            ) if item
        ],
        "tracksKnown": len(tracks),
        "servedSegments": len(served) if served else 0,
        "elapsedPositionMs": elapsed_ms,
        "availableBackwardSkips": 1 if channel_id in _xtra_previous_tracks else 0,
        "note": "XTRA upcoming is based on ArchiveXM's prefetched queue. More items appear after playback starts and the queue continues.",
    }


@router.get("/{channel_id}/xtra/queue")
async def xtra_queue(channel_id: str, db: DBSession = Depends(get_db)):
    """Return ArchiveXM's best-known XTRA previous/current/upcoming queue snapshot."""
    from fastapi.responses import JSONResponse

    requested_channel_id = channel_id
    channel_id = resolve_channel_id(channel_id, db)

    if get_channel_type(channel_id, db) != "channel-xtra":
        raise HTTPException(status_code=400, detail="XTRA queue is only supported for XTRA channels")

    # The Queue card polls this endpoint while an XTRA channel is playing. Use
    # that poll to keep ArchiveXM's stitched XTRA lookahead warm so playback can
    # continue past the currently buffered items and Coming Up stays populated.
    await _refresh_xtra_lookahead(channel_id)

    payload = _xtra_queue_snapshot(channel_id)
    payload["requestedChannelId"] = requested_channel_id
    return JSONResponse(content=payload, headers={"Access-Control-Allow-Origin": "*"})

@router.get("/{channel_id}/xtra/next")
@router.post("/{channel_id}/xtra/next")
async def xtra_next(channel_id: str, db: DBSession = Depends(get_db)):
    """
    Skip an XTRA channel to the next item.

    Client behavior:
      1. POST this endpoint.
      2. Stop/flush the current player buffer.
      3. Reload the returned streamUrl.

    M3U/HLS players cannot skip inside a static M3U entry by themselves, so the
    endpoint prepares the next XTRA item server-side and tells the client to
    reload the normal proxy-stream URL.
    """
    from fastapi.responses import JSONResponse

    requested_channel_id = channel_id
    channel_id = resolve_channel_id(channel_id, db)

    if get_channel_type(channel_id, db) != "channel-xtra":
        raise HTTPException(status_code=400, detail="Next is only supported for XTRA channels")

    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    stream_info = _stream_sessions.get(channel_id)

    if not stream_info:
        api = SiriusXMAPI(session.bearer_token)
        result = await api.get_stream_url(channel_id, "channel-xtra")
        if not result or not result.get("stream_url"):
            raise HTTPException(status_code=500, detail="Failed to get XTRA stream URL")

        raw_data = result.get("raw_data") or {}
        stream_info = {
            "base_url": result["stream_url"].rsplit("/", 1)[0] + "/",
            "bearer": session.bearer_token,
            "source_context_id": raw_data.get("sourceContextId"),
            "sequence_token": raw_data.get("sequenceToken"),
        }
        _stream_sessions[channel_id] = stream_info
    else:
        # Keep bearer current in case the session was created before a token refresh.
        stream_info["bearer"] = session.bearer_token

    previous_track = _get_current_xtra_track_for_previous(channel_id)

    next_track = await _fetch_xtra_256k_track(
        channel_id=channel_id,
        bearer=stream_info.get("bearer"),
        source_context_id=stream_info.get("source_context_id"),
        sequence_token=stream_info.get("sequence_token"),
        use_peek=True,
    )

    if not next_track or not next_track.get("segments"):
        raise HTTPException(status_code=500, detail="Failed to prepare next XTRA item")

    if previous_track and previous_track.get("segments"):
        _xtra_previous_tracks[channel_id] = previous_track

    if next_track.get("source_context_id"):
        stream_info["source_context_id"] = next_track.get("source_context_id")
    if next_track.get("sequence_token"):
        stream_info["sequence_token"] = next_track.get("sequence_token")

    _xtra_sessions.pop(channel_id, None)
    _xtra_manual_start[channel_id] = {
        "track": next_track,
        "source_context_id": next_track.get("source_context_id"),
        "sequence_token": next_track.get("sequence_token"),
    }

    print(
        f"⏭️ XTRA manual next prepared channel={channel_id} "
        f"segments={len(next_track.get('segments') or [])}"
    )

    stream_url = f"/api/streams/{channel_id}/proxy-stream"
    return JSONResponse(
        content={
            "ok": True,
            "action": "reload",
            "direction": "next",
            "channelId": channel_id,
            "requestedChannelId": requested_channel_id,
            "streamUrl": stream_url,
            "metadata": _public_xtra_metadata_from_track(next_track, channel_id),
            "availableBackwardSkips": 1 if channel_id in _xtra_previous_tracks else 0,
            "message": "Stop/flush the current player buffer, then reload streamUrl.",
        },
        headers={"Access-Control-Allow-Origin": "*"},
    )



@router.get("/{channel_id}/xtra/previous")
@router.post("/{channel_id}/xtra/previous")
@router.get("/{channel_id}/xtra/back")
@router.post("/{channel_id}/xtra/back")
async def xtra_previous(channel_id: str, db: DBSession = Depends(get_db)):
    """
    Move an XTRA channel back to the previous item, limited to one item.

    This mirrors the official SiriusXM behavior: Back is available after at
    least one manual Next/skip and only remembers the immediately previous
    item. The client should stop/flush the player and reload streamUrl.
    """
    from fastapi.responses import JSONResponse

    requested_channel_id = channel_id
    channel_id = resolve_channel_id(channel_id, db)

    if get_channel_type(channel_id, db) != "channel-xtra":
        raise HTTPException(status_code=400, detail="Previous is only supported for XTRA channels")

    previous_track = _xtra_previous_tracks.pop(channel_id, None)
    if not previous_track or not previous_track.get("segments"):
        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "action": "unavailable",
                "direction": "previous",
                "channelId": channel_id,
                "requestedChannelId": requested_channel_id,
                "availableBackwardSkips": 0,
                "message": "No previous XTRA item is available.",
            },
            headers={"Access-Control-Allow-Origin": "*"},
        )

    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    stream_info = _stream_sessions.get(channel_id) or {}
    stream_info["bearer"] = session.bearer_token
    if previous_track.get("source_context_id"):
        stream_info["source_context_id"] = previous_track.get("source_context_id")
    if previous_track.get("sequence_token"):
        stream_info["sequence_token"] = previous_track.get("sequence_token")
    _stream_sessions[channel_id] = stream_info

    _xtra_sessions.pop(channel_id, None)
    _xtra_manual_start[channel_id] = {
        "track": previous_track,
        "source_context_id": previous_track.get("source_context_id"),
        "sequence_token": previous_track.get("sequence_token"),
    }

    print(
        f"⏮️ XTRA manual previous prepared channel={channel_id} "
        f"segments={len(previous_track.get('segments') or [])}"
    )

    stream_url = f"/api/streams/{channel_id}/proxy-stream"
    return JSONResponse(
        content={
            "ok": True,
            "action": "reload",
            "direction": "previous",
            "channelId": channel_id,
            "requestedChannelId": requested_channel_id,
            "streamUrl": stream_url,
            "metadata": _public_xtra_metadata_from_track(previous_track, channel_id),
            "availableBackwardSkips": 0,
            "message": "Stop/flush the current player buffer, then reload streamUrl.",
        },
        headers={"Access-Control-Allow-Origin": "*"},
    )

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
        
        raw_data = result.get('raw_data') or {}

        # Store base URL and XTRA continuity context for this channel's proxy requests
        _stream_sessions[channel_id] = {
            'base_url': base_url,
            'bearer': session.bearer_token,
            'source_context_id': raw_data.get('sourceContextId'),
            'sequence_token': raw_data.get('sequenceToken'),
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
    Proxy any HLS resource (variant playlists, segments, keys).

    For XTRA media playlists, this returns a small stitched EVENT playlist that
    appends the next XTRA item before the current queue runs out.
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
            raw_data = result.get('raw_data') or {}
            stream_info = {
                'base_url': base_url,
                'bearer': session.bearer_token,
                'source_context_id': raw_data.get('sourceContextId'),
                'sequence_token': raw_data.get('sequenceToken'),
            }
            _stream_sessions[channel_id] = stream_info
        else:
            raise HTTPException(status_code=500, detail="No stream session")

    base_url = stream_info['base_url']
    bearer = stream_info['bearer']
    channel_type = get_channel_type(channel_id, db)

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

            # Track XTRA segment consumption so playlist refreshes know when to append.
            if channel_type == "channel-xtra" and _is_audio_segment_path(path):
                cached = _xtra_sessions.get(channel_id)
                if cached:
                    served = cached.setdefault("served", set())
                    served.add(_extract_xtra_segment_key(path))
                    _schedule_xtra_lookahead_refresh(channel_id, stream_info)

            # If this is an XTRA FULL media playlist, serve an ongoing EVENT
            # playlist instead of the finite playlist ending in EXT-X-ENDLIST.
            if channel_type == "channel-xtra" and _is_xtra_media_playlist_path(path):
                playlist_text = content.decode('utf-8')
                cached = await _ensure_xtra_queue(
                    channel_id=channel_id,
                    session_info=stream_info,
                    requested_path=path,
                    initial_playlist_text=playlist_text,
                )

                if cached:
                    content = _build_xtra_event_playlist(channel_id, cached).encode('utf-8')
                    content_type = 'application/vnd.apple.mpegurl'

                    return Response(
                        content=content,
                        media_type=content_type,
                        headers={
                            "Access-Control-Allow-Origin": "*",
                            "Cache-Control": "no-cache"
                        }
                    )

            # If it's a non-XTRA playlist, or an XTRA playlist we did not handle
            # above, rewrite URLs through the normal proxy path.
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


@router.get("/{channel_id}/hls-xtra-resource/{encoded_url:path}")
async def hls_xtra_resource_proxy(channel_id: str, encoded_url: str, db: DBSession = Depends(get_db)):
    """
    Proxy an absolute SiriusXM XTRA media segment URL.

    XTRA continuation can stitch together tracks whose media segments live under
    different SiriusXM base URLs. The normal hls-proxy endpoint is relative to
    one active base URL, so stitched XTRA segments use this absolute-resource
    proxy instead.
    """
    import httpx
    import urllib.parse
    from fastapi.responses import Response

    absolute_url = urllib.parse.unquote(encoded_url)

    if not absolute_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="Invalid XTRA resource URL")

    # Mark stitched XTRA segment consumption. These segments bypass the normal
    # hls-proxy route, so the queue needs to count them here.
    cached = _xtra_sessions.get(channel_id)
    if cached and _is_audio_segment_path(absolute_url):
        served = cached.setdefault("served", set())
        served.add(_extract_xtra_segment_key(absolute_url))
        _schedule_xtra_lookahead_refresh(channel_id)

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(absolute_url, headers=headers, timeout=30)

            if response.status_code != 200:
                print(f"⚠️ XTRA resource fetch failed status={response.status_code} url={absolute_url}")
                raise HTTPException(status_code=response.status_code, detail="XTRA resource fetch failed")

            content_type = response.headers.get('content-type', 'application/octet-stream')

            return Response(
                content=response.content,
                media_type=content_type,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "max-age=3600"
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"XTRA resource proxy error: {str(e)}")


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
