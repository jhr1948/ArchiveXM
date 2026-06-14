"""
Playlist Router - Generate local M3U playlists for IPTV players
"""
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session as DBSession

from database import Channel, get_db

# Register this router in main.py with:
# app.include_router(playlist.router)
#
# Endpoints:
#   GET  /api/playlist.m3u
#   GET  /api/playlist/m3u
#   POST /api/playlist/generate
router = APIRouter(prefix="/api", tags=["Playlist"])


def build_playlist_base_url() -> str:
    scheme = os.getenv("PLAYLIST_SCHEME", "http").strip() or "http"
    host = os.getenv("PLAYLIST_HOST", "localhost").strip() or "localhost"
    port = os.getenv("PLAYLIST_PORT", "").strip()

    if port:
        return f"{scheme}://{host}:{port}"

    return f"{scheme}://{host}"


def escape_m3u_attr(value: Optional[object]) -> str:
    if value is None:
        return ""

    return (
        str(value)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _group_title_for_channel(channel: Channel) -> str:
    channel_type = getattr(channel, "channel_type", None) or "channel-linear"
    group = channel.category or channel.genre or "SiriusXM"

    if channel_type == "channel-xtra":
        if group.strip().lower() == "all xtra":
            return "All XTRA"
        if not group.upper().endswith("XTRA"):
            return f"{group} XTRA"

    return group


def generate_m3u(db: DBSession) -> str:
    base_url = build_playlist_base_url()

    channels = (
        db.query(Channel)
        .filter(Channel.channel_id.isnot(None))
        .filter(Channel.name.isnot(None))
        .order_by(Channel.id.asc())
        .all()
    )

    lines = ["#EXTM3U"]

    for channel in channels:
        channel_id = escape_m3u_attr(channel.channel_id)
        name = escape_m3u_attr(channel.name)
        group = escape_m3u_attr(_group_title_for_channel(channel))
        logo = escape_m3u_attr(channel.large_image_url or channel.image_url or "")
        channel_type = escape_m3u_attr(
            getattr(channel, "channel_type", None) or "channel-linear"
        )

        channel_number = ""
        if channel.number is not None:
            channel_number = f' tvg-chno="{escape_m3u_attr(channel.number)}"'

        stream_path_channel_id = quote(str(channel.channel_id), safe="")
        stream_url = f"{base_url}/api/streams/{stream_path_channel_id}/proxy-stream"

        lines.append(
            f'#EXTINF:-1 tvg-id="{channel_id}" '
            f'tvg-name="{name}"'
            f'{channel_number} '
            f'tvg-logo="{logo}" '
            f'group-title="{group}" '
            f'x-sxm-type="{channel_type}",{name}'
        )
        lines.append(stream_url)

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
