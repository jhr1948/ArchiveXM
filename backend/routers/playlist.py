"""
Playlist Router - Generate local M3U playlists for IPTV players
"""
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote
import html as html_lib
import unicodedata

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import func

from database import Channel, Config, LocalTrack, Playlist, PlaylistTrack, get_db

# Register this router in main.py with:
# app.include_router(playlist.router)
#
# Endpoints:
#   GET  /api/playlist.m3u
#   GET  /api/playlist/m3u
#   POST /api/playlist/generate
router = APIRouter(prefix="/api", tags=["Playlist"])


# Fallback group overrides. The main copy of these lives in sxm_api.py so the
# database/UI are corrected at refresh time, but keeping them here protects M3U
# output if the playlist is generated before the next channel refresh.
CHANNEL_GROUP_OVERRIDES = {
    "1308": "Workout",
    "1302": "Party",
    "1085": "The 70s Decade",
    "1177": "The 70s Decade",
    "739": "Country",
}


def _get_config_value(db: DBSession, key: str, default=None):
    item = db.query(Config).filter(Config.key == key).first()
    if item is None or item.value in (None, ""):
        return default
    return item.value


def _normalize_base_url(value: Optional[str]) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value.rstrip("/")


def _default_env_base_url() -> str:
    public_base_url = os.getenv("PLAYLIST_PUBLIC_BASE_URL", "").strip()
    if public_base_url:
        return _normalize_base_url(public_base_url)

    scheme = os.getenv("PLAYLIST_SCHEME", "http").strip() or "http"
    host = os.getenv("PLAYLIST_HOST", "localhost").strip() or "localhost"
    port = os.getenv("PLAYLIST_PORT", "").strip()

    if port:
        return f"{scheme}://{host}:{port}"

    return f"{scheme}://{host}"


def build_playlist_base_url(db: Optional[DBSession] = None) -> str:
    """Return the base URL used inside generated M3U files.

    Prefer the saved ArchiveXM playlist settings so generated files follow the
    same Local/Public URL mode used by the UI. Fall back to env vars for older
    setups or during early setup before config rows exist.
    """
    if db is not None:
        mode = str(_get_config_value(db, "playlist_url_mode", os.getenv("PLAYLIST_URL_MODE", "local")) or "local").strip().lower()
        if mode == "public":
            public_base = _normalize_base_url(_get_config_value(db, "playlist_public_base_url", os.getenv("PLAYLIST_PUBLIC_BASE_URL", "")))
            if public_base:
                return public_base

        local_base = _normalize_base_url(_get_config_value(db, "playlist_local_base_url", os.getenv("PLAYLIST_LOCAL_BASE_URL", "")))
        if local_base:
            return local_base

    return _default_env_base_url()


def _playlist_url_style(db: Optional[DBSession] = None) -> str:
    if db is not None:
        return str(_get_config_value(db, "playlist_url_style", os.getenv("PLAYLIST_URL_STYLE", "listen")) or "listen").strip().lower()
    return os.getenv("PLAYLIST_URL_STYLE", "listen").strip().lower()


def clean_m3u_text(value: Optional[object]) -> str:
    """Return plain M3U text without HTML/XML entity escaping.

    IPTV players generally expect normal display text in EXTINF fields, e.g.
    Dance/R&B instead of Dance/R&amp;B. Keep values on one line and avoid raw
    double quotes because EXTINF attributes are quoted.
    """
    if value is None:
        return ""

    text = html_lib.unescape(str(value))

    try:
        if any(marker in text for marker in ("\u00c3", "\u00c2", "\u00e2")):
            repaired = text.encode("latin-1").decode("utf-8")
            if repaired:
                text = repaired
        text = unicodedata.normalize("NFC", text)
    except Exception:
        pass

    return (
        text
        .replace("\n", " ")
        .replace("\r", " ")
        .replace('"', "'")
        .strip()
    )


def _group_title_for_channel(channel: Channel) -> str:
    channel_type = getattr(channel, "channel_type", None) or "channel-linear"
    number_key = str(channel.number) if channel.number is not None else ""
    group = CHANNEL_GROUP_OVERRIDES.get(number_key) or channel.category or channel.genre or "SiriusXM"

    if channel_type == "channel-xtra":
        if group.strip().lower() == "all xtra":
            return "All XTRA"
        if not group.upper().endswith("XTRA"):
            return f"{group} XTRA"

    return group


def _playlist_cover_url(db: DBSession, playlist_id: int, base_url: str) -> str:
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if playlist and playlist.cover_image:
        cover = str(playlist.cover_image).strip()
        if cover.startswith(("http://", "https://")):
            return clean_m3u_text(cover)
        if Path(cover).exists():
            return f"{base_url}/api/library/playlists/{playlist_id}/cover"

    first_track = (
        db.query(LocalTrack)
        .join(PlaylistTrack, PlaylistTrack.track_id == LocalTrack.id)
        .filter(PlaylistTrack.playlist_id == playlist_id)
        .filter(LocalTrack.cover_art_path.isnot(None))
        .order_by(PlaylistTrack.position.asc())
        .first()
    )
    if first_track and first_track.cover_art_path and Path(first_track.cover_art_path).exists():
        return f"{base_url}/api/library/files/{first_track.id}/cover"
    return ""


def _playlist_entries(db: DBSession, base_url: str, start_channel_number: int) -> list[str]:
    playlists = (
        db.query(
            Playlist,
            func.count(PlaylistTrack.id).label("live_track_count"),
        )
        .outerjoin(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id)
        .group_by(Playlist.id)
        .order_by(Playlist.name.asc())
        .all()
    )

    lines: list[str] = []
    channel_number = start_channel_number

    for playlist, live_track_count in playlists:
        if not live_track_count:
            continue

        channel_number += 1
        playlist_id = f"archivexm-playlist-{playlist.id}"
        name = clean_m3u_text(playlist.name or f"Playlist {playlist.id}")
        group = "Playlists"
        logo = clean_m3u_text(_playlist_cover_url(db, playlist.id, base_url))
        playlist_url = f"{base_url}/api/library/playlists/{quote(str(playlist.id), safe='')}.m3u"

        # This is intentionally a virtual/VOD-style channel entry. Players that
        # support nested M3U/VOD sources can open the playlist URL and then play
        # its saved local tracks. The direct playlist M3U endpoint remains
        # available too for apps that prefer adding each playlist as a source.
        lines.append(
            f'#EXTINF:-1 tvg-id="{clean_m3u_text(playlist_id)}" '
            f'tvg-name="{name}" '
            f'tvg-chno="{clean_m3u_text(channel_number)}" '
            f'tvg-logo="{logo}" '
            f'group-title="{group}" '
            f'x-sxm-type="archivexm-playlist" '
            f',{name}'
        )
        lines.append(playlist_url)

    return lines


def generate_m3u(db: DBSession) -> str:
    base_url = build_playlist_base_url(db)

    channels = (
        db.query(Channel)
        .filter(Channel.channel_id.isnot(None))
        .filter(Channel.name.isnot(None))
        .order_by(Channel.id.asc())
        .all()
    )

    lines = ["#EXTM3U"]

    for index, channel in enumerate(channels, start=1):
        channel_id = clean_m3u_text(channel.channel_id)
        tvg_id = clean_m3u_text(channel.number if channel.number is not None else channel.channel_id)
        tvg_chno = clean_m3u_text(index)
        name = clean_m3u_text(channel.name)
        group = clean_m3u_text(_group_title_for_channel(channel))
        logo = clean_m3u_text(channel.large_image_url or channel.image_url or "")
        channel_type = clean_m3u_text(
            getattr(channel, "channel_type", None) or "channel-linear"
        )

        channel_number = f' tvg-chno="{tvg_chno}"'

        stream_path_channel_id = quote(str(channel.channel_id), safe="")
        url_style = _playlist_url_style(db)
        if url_style in ("api", "archivexm"):
            stream_url = f"{base_url}/api/streams/{stream_path_channel_id}/proxy-stream"
        else:
            # m3u8XM/M3You-compatible shape: the app extracts the XTRA UUID from /listen/<uuid>
            stream_url = f"{base_url}/listen/{stream_path_channel_id}"

        lines.append(
            f'#EXTINF:-1 tvg-id="{tvg_id}" '
            f'tvg-name="{name}"'
            f'{channel_number} '
            f'tvg-logo="{logo}" '
            f'group-title="{group}" '
            f'x-sxm-type="{channel_type}",{name}'
        )
        lines.append(stream_url)

    lines.extend(_playlist_entries(db, base_url, start_channel_number=len(channels)))

    return "\n".join(lines) + "\n"


def write_m3u_to_file(db: DBSession) -> dict:
    playlist = generate_m3u(db)

    output_path = Path(os.getenv("PLAYLIST_OUTPUT", "/app/output/siriusxm.m3u"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(playlist, encoding="utf-8")

    return {
        "status": "ok",
        "path": str(output_path),
        "channel_count": playlist.count("#EXTINF"),
    }


@router.get("/playlist.m3u")
def get_playlist_m3u(db: DBSession = Depends(get_db)):
    playlist = generate_m3u(db)

    return Response(
        content=playlist,
        media_type="audio/x-mpegurl",
        headers={
            "Content-Disposition": 'inline; filename="siriusxm.m3u"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/playlist/m3u")
def get_playlist_m3u_alt(db: DBSession = Depends(get_db)):
    playlist = generate_m3u(db)

    return Response(
        content=playlist,
        media_type="audio/x-mpegurl",
        headers={
            "Content-Disposition": 'inline; filename="siriusxm.m3u"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/playlist/generate")
def write_playlist_file(db: DBSession = Depends(get_db)):
    return write_m3u_to_file(db)
