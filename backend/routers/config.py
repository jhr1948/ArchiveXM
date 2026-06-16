"""
Configuration Router - App settings management
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from datetime import datetime
from typing import Optional, Dict
import os
import json

from database import get_db, Config, Credentials, Channel

router = APIRouter()


class ConfigUpdate(BaseModel):
    download_path: Optional[str] = None
    audio_quality: Optional[str] = None
    playlist_url_mode: Optional[str] = None
    playlist_local_base_url: Optional[str] = None
    playlist_public_base_url: Optional[str] = None
    playlist_url_style: Optional[str] = None
    playlist_auto_generate: Optional[bool] = None
    download_tail_pad_seconds: Optional[float] = None
    live_metadata_offset_seconds: Optional[float] = None
    live_metadata_hide_short_cuts: Optional[bool] = None
    live_metadata_short_cut_max_seconds: Optional[float] = None
    live_metadata_channel_offsets: Optional[Dict[str, float]] = None


class SetupRequest(BaseModel):
    username: str
    password: str
    download_path: str
    playlist_url_mode: str = "local"
    playlist_local_base_url: Optional[str] = None
    playlist_public_base_url: Optional[str] = None
    playlist_url_style: str = "listen"
    playlist_auto_generate: bool = True
    download_tail_pad_seconds: float = 2.0
    live_metadata_offset_seconds: float = 38.0
    live_metadata_hide_short_cuts: bool = False
    live_metadata_short_cut_max_seconds: float = 45.0
    live_metadata_channel_offsets: Dict[str, float] = {}


class ConfigResponse(BaseModel):
    is_configured: bool
    download_path: str | None
    audio_quality: str
    has_credentials: bool
    playlist_url_mode: str
    playlist_local_base_url: str | None
    playlist_public_base_url: str | None
    playlist_url_style: str
    playlist_auto_generate: bool
    download_tail_pad_seconds: float
    live_metadata_offset_seconds: float
    live_metadata_hide_short_cuts: bool
    live_metadata_short_cut_max_seconds: float
    live_metadata_channel_offsets: Dict[str, float]


PLAYLIST_CONFIG_KEYS = {
    "playlist_url_mode",
    "playlist_local_base_url",
    "playlist_public_base_url",
    "playlist_url_style",
    "playlist_auto_generate",
}


def _get_config_value(db: DBSession, key: str, default=None):
    item = db.query(Config).filter(Config.key == key).first()
    if item is None or item.value in (None, ""):
        return default
    return item.value


def _set_config_value(db: DBSession, key: str, value):
    if value is None:
        return
    if isinstance(value, bool):
        value = "true" if value else "false"
    else:
        value = str(value).strip()
    item = db.query(Config).filter(Config.key == key).first()
    if item:
        item.value = value
        item.updated_at = datetime.utcnow()
    else:
        db.add(Config(key=key, value=value))


def _get_bool_config(db: DBSession, key: str, default: bool = False) -> bool:
    value = _get_config_value(db, key, None)
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _get_json_config(db: DBSession, key: str, default=None):
    value = _get_config_value(db, key, None)
    if value is None:
        return default if default is not None else {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else (default if default is not None else {})
    except Exception:
        return default if default is not None else {}


def _set_json_config(db: DBSession, key: str, value):
    _set_config_value(db, key, json.dumps(value or {}))


def _default_local_base_url() -> str:
    scheme = os.getenv("PLAYLIST_SCHEME", "http").strip() or "http"
    host = os.getenv("PLAYLIST_HOST", "localhost").strip() or "localhost"
    port = os.getenv("PLAYLIST_PORT", "").strip()
    if port:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


async def _refresh_channels_for_setup(api, db: DBSession) -> int:
    channels_data = await api.fetch_all_channels()
    if not channels_data:
        return 0

    updated_count = 0
    for ch_data in channels_data:
        channel_type = ch_data.get("channel_type") or ch_data.get("type") or "channel-linear"
        existing = db.query(Channel).filter(Channel.channel_id == ch_data["id"]).first()
        images = ch_data.get("images", {}) or {}

        if existing:
            existing.channel_type = channel_type
            existing.name = ch_data.get("name", existing.name)
            existing.number = ch_data.get("number", existing.number)
            existing.category = ch_data.get("category", existing.category)
            existing.genre = ch_data.get("genre", existing.genre)
            existing.description = ch_data.get("description", existing.description)
            existing.image_url = images.get("thumbnail")
            existing.large_image_url = images.get("large")
            existing.updated_at = datetime.utcnow()
        else:
            db.add(Channel(
                channel_id=ch_data["id"],
                channel_type=channel_type,
                name=ch_data.get("name", "Unknown"),
                number=ch_data.get("number") or 0,
                category=ch_data.get("category"),
                genre=ch_data.get("genre"),
                description=ch_data.get("description"),
                image_url=images.get("thumbnail"),
                large_image_url=images.get("large"),
            ))

        updated_count += 1

    db.commit()
    return updated_count


@router.get("", response_model=ConfigResponse)
async def get_config(db: DBSession = Depends(get_db)):
    """
    Get current configuration
    """
    download_path_config = db.query(Config).filter(Config.key == "download_path").first()
    quality_config = db.query(Config).filter(Config.key == "audio_quality").first()
    creds = db.query(Credentials).first()

    is_configured = bool(download_path_config and creds)

    return ConfigResponse(
        is_configured=is_configured,
        download_path=download_path_config.value if download_path_config else None,
        audio_quality=quality_config.value if quality_config else "256k",
        has_credentials=bool(creds),
        playlist_url_mode=_get_config_value(db, "playlist_url_mode", os.getenv("PLAYLIST_URL_MODE", "local")),
        playlist_local_base_url=_get_config_value(db, "playlist_local_base_url", os.getenv("PLAYLIST_LOCAL_BASE_URL", _default_local_base_url())),
        playlist_public_base_url=_get_config_value(db, "playlist_public_base_url", os.getenv("PLAYLIST_PUBLIC_BASE_URL", "")),
        playlist_url_style=_get_config_value(db, "playlist_url_style", os.getenv("PLAYLIST_URL_STYLE", "listen")),
        playlist_auto_generate=_get_bool_config(db, "playlist_auto_generate", True),
        download_tail_pad_seconds=float(_get_config_value(db, "download_tail_pad_seconds", os.getenv("DOWNLOAD_TAIL_PAD_SECONDS", "2.0")) or 2.0),
        live_metadata_offset_seconds=float(_get_config_value(db, "live_metadata_offset_seconds", os.getenv("LIVE_METADATA_OFFSET_SECONDS", "38")) or 38),
        live_metadata_hide_short_cuts=_get_bool_config(db, "live_metadata_hide_short_cuts", False),
        live_metadata_short_cut_max_seconds=float(_get_config_value(db, "live_metadata_short_cut_max_seconds", os.getenv("LIVE_METADATA_SHORT_CUT_MAX_SECONDS", "45")) or 45),
        live_metadata_channel_offsets=_get_json_config(db, "live_metadata_channel_offsets", {}),
    )


@router.post("")
async def update_config(request: ConfigUpdate, db: DBSession = Depends(get_db)):
    """
    Update configuration settings
    """
    updates = {}

    if request.download_path:
        _set_config_value(db, "download_path", request.download_path)
        updates["download_path"] = request.download_path

    if request.audio_quality:
        _set_config_value(db, "audio_quality", request.audio_quality)
        updates["audio_quality"] = request.audio_quality

    if request.download_tail_pad_seconds is not None:
        # Conservative range: 0 disables padding, 5 seconds is the max.
        pad = max(0.0, min(5.0, float(request.download_tail_pad_seconds)))
        _set_config_value(db, "download_tail_pad_seconds", pad)
        updates["download_tail_pad_seconds"] = pad

    if request.live_metadata_offset_seconds is not None:
        offset = max(-120.0, min(120.0, float(request.live_metadata_offset_seconds)))
        _set_config_value(db, "live_metadata_offset_seconds", offset)
        updates["live_metadata_offset_seconds"] = offset

    if request.live_metadata_hide_short_cuts is not None:
        _set_config_value(db, "live_metadata_hide_short_cuts", request.live_metadata_hide_short_cuts)
        updates["live_metadata_hide_short_cuts"] = request.live_metadata_hide_short_cuts

    if request.live_metadata_short_cut_max_seconds is not None:
        max_cut = max(1.0, min(300.0, float(request.live_metadata_short_cut_max_seconds)))
        _set_config_value(db, "live_metadata_short_cut_max_seconds", max_cut)
        updates["live_metadata_short_cut_max_seconds"] = max_cut

    if request.live_metadata_channel_offsets is not None:
        clean_offsets = {}
        for key, value in (request.live_metadata_channel_offsets or {}).items():
            try:
                clean_offsets[str(key)] = max(-120.0, min(120.0, float(value)))
            except Exception:
                continue
        _set_json_config(db, "live_metadata_channel_offsets", clean_offsets)
        updates["live_metadata_channel_offsets"] = clean_offsets

    for key in PLAYLIST_CONFIG_KEYS:
        if hasattr(request, key):
            value = getattr(request, key)
            if value is not None:
                _set_config_value(db, key, value)
                updates[key] = value

    db.commit()

    playlist_result = None
    if request.playlist_auto_generate is not None or any(k in updates for k in PLAYLIST_CONFIG_KEYS):
        try:
            from routers.playlist import write_m3u_to_file
            playlist_result = write_m3u_to_file(db)
        except Exception as e:
            playlist_result = {"status": "error", "message": str(e)}

    return {"success": True, "updated": updates, "playlist": playlist_result}


@router.get("/setup-status")
async def get_setup_status(db: DBSession = Depends(get_db)):
    """
    Check if initial setup is complete
    """
    creds = db.query(Credentials).first()
    download_path = db.query(Config).filter(Config.key == "download_path").first()

    return {
        "needs_setup": not (creds and download_path),
        "has_credentials": bool(creds),
        "has_download_path": bool(download_path)
    }


@router.post("/setup")
async def initial_setup(request: SetupRequest, db: DBSession = Depends(get_db)):
    """
    Complete initial setup (credentials + download path + playlist settings)
    """
    from services.auth_service import AuthService
    from services.sxm_api import SiriusXMAPI
    import json

    try:
        auth_service = AuthService()
        result = await auth_service.authenticate(request.username, request.password)

        if not result["success"]:
            raise HTTPException(status_code=401, detail="Authentication failed")

        existing_creds = db.query(Credentials).first()
        if existing_creds:
            existing_creds.username = request.username
            existing_creds.password_encrypted = auth_service.encrypt_password(request.password)
            existing_creds.updated_at = datetime.utcnow()
        else:
            creds = Credentials(
                username=request.username,
                password_encrypted=auth_service.encrypt_password(request.password)
            )
            db.add(creds)

        from database import Session as AuthSession
        db.query(AuthSession).update({"is_valid": False})

        session = AuthSession(
            bearer_token=result["bearer_token"],
            cookies=json.dumps(result.get("cookies", {})),
            expires_at=result.get("expires_at"),
            is_valid=True
        )
        db.add(session)

        _set_config_value(db, "download_path", request.download_path)
        _set_config_value(db, "playlist_url_mode", request.playlist_url_mode or "local")
        if request.playlist_local_base_url:
            _set_config_value(db, "playlist_local_base_url", request.playlist_local_base_url)
        if request.playlist_public_base_url:
            _set_config_value(db, "playlist_public_base_url", request.playlist_public_base_url)
        _set_config_value(db, "playlist_url_style", request.playlist_url_style or "listen")
        _set_config_value(db, "playlist_auto_generate", request.playlist_auto_generate)
        _set_config_value(db, "download_tail_pad_seconds", max(0.0, min(5.0, float(request.download_tail_pad_seconds))))
        _set_config_value(db, "live_metadata_offset_seconds", max(-120.0, min(120.0, float(request.live_metadata_offset_seconds))))
        _set_config_value(db, "live_metadata_hide_short_cuts", request.live_metadata_hide_short_cuts)
        _set_config_value(db, "live_metadata_short_cut_max_seconds", max(1.0, min(300.0, float(request.live_metadata_short_cut_max_seconds))))
        _set_json_config(db, "live_metadata_channel_offsets", request.live_metadata_channel_offsets or {})

        db.commit()

        channels_refreshed = 0
        playlist_result = None
        setup_warnings = []

        if request.playlist_auto_generate:
            try:
                api = SiriusXMAPI(result["bearer_token"])
                channels_refreshed = await _refresh_channels_for_setup(api, db)
                from routers.playlist import write_m3u_to_file
                playlist_result = write_m3u_to_file(db)
            except Exception as e:
                setup_warnings.append(f"Playlist auto-generation failed: {str(e)}")

        return {
            "success": True,
            "message": "Setup complete! Channels refreshed and playlist generated." if request.playlist_auto_generate else "Setup complete! You can now browse channels.",
            "channels_refreshed": channels_refreshed,
            "playlist": playlist_result,
            "warnings": setup_warnings,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Setup error: {str(e)}")


@router.get("/download-paths")
async def get_download_paths():
    """
    Get available download path suggestions
    """
    base_paths = [
        "/downloads",
        "/app/downloads",
        os.path.expanduser("~/Music/ArchiveXM"),
        "/media",
        "/mnt"
    ]

    valid_paths = []
    for path in base_paths:
        if os.path.exists(os.path.dirname(path)) or os.path.exists(path):
            valid_paths.append(path)

    return {"paths": valid_paths}
