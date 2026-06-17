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

The implementation delegates to routers.streams.xtra_next/xtra_previous so both the
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


def _duration_ms_for_track(track: dict) -> int:
    metadata = track.get("metadata") or {}
    duration_ms = metadata.get("durationMs") or 0
    try:
        duration_ms = int(duration_ms)
    except (TypeError, ValueError):
        duration_ms = 0

    if duration_ms <= 0:
        try:
            duration_ms = int(float(track.get("duration") or 0) * 1000)
        except (TypeError, ValueError):
            duration_ms = 0

    return max(0, duration_ms)


def _xtra_metadata_from_queue(channel_id: str, requested_channel_id: str, position_ms: int | None = None) -> dict | None:
    cached = getattr(streams, "_xtra_sessions", {}).get(channel_id)
    if not cached:
        manual_start = getattr(streams, "_xtra_manual_start", {}).get(channel_id)
        if manual_start and manual_start.get("track"):
            track = manual_start.get("track")
            metadata = dict(track.get("metadata") or {})
            if metadata:
                metadata["channelId"] = requested_channel_id
                metadata["isXtra"] = True
                metadata["availableBackwardSkips"] = 1 if channel_id in getattr(streams, "_xtra_previous_tracks", {}) else 0
                return metadata
        return None

    tracks = cached.get("tracks") or []
    if not tracks:
        return None

    selected = tracks[0]
    start_offset_ms = 0
    end_offset_ms = _duration_ms_for_track(selected)

    if position_ms is not None:
        offset = 0
        for track in tracks:
            duration_ms = _duration_ms_for_track(track)
            next_offset = offset + duration_ms
            if offset <= position_ms < next_offset:
                selected = track
                start_offset_ms = offset
                end_offset_ms = next_offset
                break
            if position_ms >= next_offset:
                selected = track
                start_offset_ms = offset
                end_offset_ms = next_offset
            offset = next_offset

    metadata = dict(selected.get("metadata") or {})
    if not metadata:
        return None

    if not metadata.get("durationMs"):
        metadata["durationMs"] = _duration_ms_for_track(selected)

    try:
        created_ms = int(float(cached.get("created") or 0) * 1000)
    except (TypeError, ValueError):
        created_ms = 0
    if created_ms and position_ms is not None:
        metadata["startedAtMs"] = created_ms + start_offset_ms
    elif not metadata.get("startedAtMs") and created_ms:
        metadata["startedAtMs"] = created_ms + start_offset_ms

    metadata["channelId"] = requested_channel_id
    metadata["isXtra"] = True
    metadata["availableBackwardSkips"] = 1 if channel_id in getattr(streams, "_xtra_previous_tracks", {}) else 0
    metadata["startOffsetMs"] = start_offset_ms
    metadata["endOffsetMs"] = end_offset_ms
    if position_ms is not None:
        metadata["positionMs"] = position_ms
        metadata["resolvedBy"] = "position"
    return metadata


@router.get("/metadata/{channel_id}")
async def legacy_metadata(channel_id: str, positionMs: int | None = None, db: DBSession = Depends(get_db)):
    """M3You/m3u8XM-compatible metadata endpoint.

    XTRA metadata comes from ArchiveXM's active XTRA queue, because M3You
    polls /metadata/<uuid> for XTRA tracks instead of using normal SXM live metadata.
    """
    resolved_channel_id = _resolve_channel_id(channel_id, db)
    channel = db.query(Channel).filter(Channel.channel_id == resolved_channel_id).first()
    if not channel:
        return JSONResponse(status_code=404, content={"ok": False}, headers={"Access-Control-Allow-Origin": "*"})

    is_xtra = getattr(channel, "channel_type", None) == "channel-xtra"

    if is_xtra:
        queue_metadata = _xtra_metadata_from_queue(resolved_channel_id, channel_id, positionMs)
        if queue_metadata and (queue_metadata.get("title") or queue_metadata.get("artist")):
            queue_metadata["ok"] = True
            if not queue_metadata.get("imageUrl"):
                queue_metadata["imageUrl"] = channel.large_image_url or channel.image_url or ""
            return JSONResponse(content=queue_metadata, headers={"Access-Control-Allow-Origin": "*"})

    return JSONResponse(
        content={
            "ok": True,
            "channelId": channel_id,
            "title": channel.name or "",
            "artist": "",
            "album": "",
            "imageUrl": channel.large_image_url or channel.image_url or "",
            "durationMs": 0,
            "startedAtMs": 0,
            "isXtra": is_xtra,
        },
        headers={"Access-Control-Allow-Origin": "*"},
    )



@router.get("/xtra/{channel_id}/queue")
async def legacy_xtra_queue(channel_id: str, db: DBSession = Depends(get_db)):
    resolved_channel_id = _resolve_channel_id(channel_id, db)
    return await streams.xtra_queue(resolved_channel_id, db)

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


async def _legacy_previous_response(channel_id: str, db: DBSession) -> JSONResponse:
    response = await streams.xtra_previous(channel_id, db)

    try:
        payload = json.loads(response.body.decode("utf-8"))
    except Exception:
        payload = {
            "ok": False,
            "action": "error",
            "direction": "previous",
            "channelId": channel_id,
            "message": "Unable to prepare XTRA previous item.",
        }

    resolved_channel_id = payload.get("channelId") or channel_id
    stream_url = payload.get("streamUrl") or f"/api/streams/{resolved_channel_id}/proxy-stream"
    listen_url = f"/listen/{resolved_channel_id}"

    payload["listenUrl"] = listen_url
    payload["streamUrl"] = stream_url
    payload["metadataUrl"] = f"/metadata/{resolved_channel_id}"
    payload["legacyRoute"] = True

    return JSONResponse(
        content=payload,
        status_code=response.status_code,
        headers={"Access-Control-Allow-Origin": "*"},
    )


@router.get("/xtra/{channel_id}/previous")
@router.post("/xtra/{channel_id}/previous")
@router.get("/xtra/{channel_id}/back")
@router.post("/xtra/{channel_id}/back")
async def legacy_xtra_previous(channel_id: str, db: DBSession = Depends(get_db)):
    return await _legacy_previous_response(channel_id, db)
