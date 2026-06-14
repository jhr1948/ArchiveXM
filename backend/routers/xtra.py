"""
Legacy XTRA controls for m3u8XM-compatible clients.

Register in backend/main.py with:
    from routers import xtra
    app.include_router(xtra.router)

Routes:
    GET      /listen/{channel_id}
    GET/POST /xtra/{channel_id}/next
    GET/POST /xtra/{channel_id}/previous
    GET/POST /xtra/{channel_id}/back
    GET      /metadata/{channel_id}

The implementation delegates to routers.streams.xtra_next so both the
ArchiveXM REST route and the old m3u8XM route share the same skip logic.
"""
import json

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session as DBSession

from database import get_db, Channel
from routers import streams

router = APIRouter(tags=["XTRA Legacy Controls"])


def _resolve_channel_id(channel_id: str, db: DBSession) -> str:
    """Accept either ArchiveXM/SXM UUID or old m3u8XM tvg-id channel number."""
    channel = db.query(Channel).filter(Channel.channel_id == channel_id).first()
    if channel:
        return channel.channel_id

    try:
        channel_number = int(str(channel_id).strip())
    except (TypeError, ValueError):
        return channel_id

    channel = db.query(Channel).filter(Channel.number == channel_number).first()
    if channel:
        return channel.channel_id

    return channel_id


@router.get("/listen/{channel_id}")
async def legacy_listen(channel_id: str, db: DBSession = Depends(get_db)):
    """m3u8XM-compatible playback URL used by M3You: /listen/<uuid>."""
    resolved_channel_id = _resolve_channel_id(channel_id, db)
    return await streams.proxy_stream(resolved_channel_id, db)


@router.get("/metadata/{channel_id}")
async def legacy_metadata(channel_id: str, db: DBSession = Depends(get_db)):
    """Small compatibility metadata endpoint for clients that probe m3u8XM metadata."""
    resolved_channel_id = _resolve_channel_id(channel_id, db)
    channel = db.query(Channel).filter(Channel.channel_id == resolved_channel_id).first()
    if not channel:
        return JSONResponse(status_code=404, content={"ok": False}, headers={"Access-Control-Allow-Origin": "*"})
    return JSONResponse(
        content={
            "ok": True,
            "channelId": resolved_channel_id,
            "title": channel.name or "",
            "artist": "",
            "album": "",
            "imageUrl": channel.large_image_url or channel.image_url or "",
            "durationMs": 0,
            "startedAtMs": 0,
            "isXtra": (getattr(channel, "channel_type", None) == "channel-xtra"),
        },
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def _legacy_next_response(channel_id: str, db: DBSession) -> JSONResponse:
    response = await streams.xtra_next(channel_id, db)

    try:
        payload = json.loads(response.body.decode("utf-8"))
    except Exception:
        payload = {
            "ok": False,
            "action": "error",
            "direction": "next",
            "channelId": channel_id,
            "message": "Unable to prepare XTRA next item.",
        }

    resolved_channel_id = payload.get("channelId") or channel_id
    stream_url = payload.get("streamUrl") or f"/api/streams/{resolved_channel_id}/proxy-stream"
    listen_url = f"/listen/{resolved_channel_id}"

    # Old m3u8XM-style clients expect /listen/<uuid>. New ArchiveXM clients can use streamUrl.
    payload["listenUrl"] = listen_url
    payload["streamUrl"] = stream_url
    payload["metadataUrl"] = f"/metadata/{resolved_channel_id}"
    payload["legacyRoute"] = True

    return JSONResponse(
        content=payload,
        status_code=response.status_code,
        headers={"Access-Control-Allow-Origin": "*"},
    )


@router.get("/xtra/{channel_id}/next")
@router.post("/xtra/{channel_id}/next")
async def legacy_xtra_next(channel_id: str, db: DBSession = Depends(get_db)):
    return await _legacy_next_response(channel_id, db)


@router.get("/xtra/{channel_id}/previous")
@router.post("/xtra/{channel_id}/previous")
@router.get("/xtra/{channel_id}/back")
@router.post("/xtra/{channel_id}/back")
async def legacy_xtra_previous(channel_id: str):
    return JSONResponse(
        status_code=501,
        content={
            "ok": False,
            "action": "unsupported",
            "direction": "previous",
            "channelId": channel_id,
            "message": "Previous/back is not implemented server-side yet. Use forward skip first.",
        },
        headers={"Access-Control-Allow-Origin": "*"},
    )
