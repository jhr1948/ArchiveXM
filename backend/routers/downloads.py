"""
Downloads Router - Download tracks from DVR buffer
"""
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime
import os

from database import get_db, Channel, Download, Session as AuthSession, Config, LocalTrack, Playlist, PlaylistTrack
from services.download_service import DownloadService
from routers.library import _find_existing_jukebox_track

router = APIRouter()


class TrackDownloadRequest(BaseModel):
    channel_id: str
    artist: str
    title: str
    album: str | None = None
    timestamp_utc: str
    duration_ms: int
    image_url: str | None = None
    playlist_id: int | None = None
    playlist_name: str | None = None


class BulkDownloadRequest(BaseModel):
    channel_id: str
    tracks: List[TrackDownloadRequest]
    playlist_id: int | None = None
    playlist_name: str | None = None


class DownloadResponse(BaseModel):
    success: bool
    message: str
    download_id: int | None = None
    file_path: str | None = None
    already_in_jukebox: bool = False
    already_in_playlist: bool = False
    added_to_playlist: bool = False
    local_track_id: int | None = None
    playlist_id: int | None = None
    playlist_name: str | None = None


def _resolve_playlist_for_download(db: DBSession, playlist_id: int | None = None, playlist_name: str | None = None) -> Playlist | None:
    playlist = None
    if playlist_id:
        playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()

    target_name = (playlist_name or "").strip()
    if not playlist and target_name:
        playlist = db.query(Playlist).filter(Playlist.name == target_name).first()
        if not playlist:
            playlist = Playlist(name=target_name, description="Created from download")
            db.add(playlist)
            db.flush()

    return playlist


def _add_existing_track_to_playlist(db: DBSession, playlist: Playlist | None, track: LocalTrack) -> dict:
    if not playlist:
        return {
            "added_to_playlist": False,
            "already_in_playlist": False,
            "playlist_id": None,
            "playlist_name": None,
        }

    existing = db.query(PlaylistTrack).filter(
        PlaylistTrack.playlist_id == playlist.id,
        PlaylistTrack.track_id == track.id,
    ).first()

    added = False
    if not existing:
        max_pos = db.query(func.max(PlaylistTrack.position)).filter(
            PlaylistTrack.playlist_id == playlist.id
        ).scalar() or 0
        db.add(PlaylistTrack(
            playlist_id=playlist.id,
            track_id=track.id,
            position=max_pos + 1,
        ))
        added = True

    playlist.track_count = db.query(PlaylistTrack).filter(
        PlaylistTrack.playlist_id == playlist.id
    ).count()
    return {
        "added_to_playlist": added,
        "already_in_playlist": bool(existing),
        "playlist_id": playlist.id,
        "playlist_name": playlist.name,
    }


class DownloadHistoryItem(BaseModel):
    id: int
    channel_name: str
    artist: str
    title: str
    album: str | None
    duration_ms: int
    file_path: str
    downloaded_at: str
    status: str

    class Config:
        from_attributes = True


@router.post("/track", response_model=DownloadResponse)
async def download_track(
    request: TrackDownloadRequest,
    background_tasks: BackgroundTasks,
    db: DBSession = Depends(get_db)
):
    """
    Download a single track from DVR buffer
    """
    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    channel = db.query(Channel).filter(Channel.channel_id == request.channel_id).first()
    
    # Get download path from config
    config = db.query(Config).filter(Config.key == "download_path").first()
    download_path = config.value if config else os.getenv("DOWNLOAD_PATH", "/downloads")
    
    try:
        existing_library_track = _find_existing_jukebox_track(
            db,
            request.artist,
            request.title,
            request.duration_ms,
        )
        if existing_library_track:
            playlist = _resolve_playlist_for_download(db, request.playlist_id, request.playlist_name)
            playlist_result = _add_existing_track_to_playlist(db, playlist, existing_library_track)
            db.commit()

            if playlist_result["already_in_playlist"]:
                message = f"Already in Jukebox and already in {playlist_result['playlist_name']}."
            elif playlist_result["added_to_playlist"]:
                message = f"Already in Jukebox; added to {playlist_result['playlist_name']}."
            else:
                message = "Already in Jukebox; download skipped."

            return DownloadResponse(
                success=True,
                message=message,
                download_id=None,
                already_in_jukebox=True,
                already_in_playlist=playlist_result["already_in_playlist"],
                added_to_playlist=playlist_result["added_to_playlist"],
                local_track_id=existing_library_track.id,
                playlist_id=playlist_result["playlist_id"],
                playlist_name=playlist_result["playlist_name"],
            )

        # Create download record
        download = Download(
            channel_id=request.channel_id,
            channel_name=channel.name if channel else "Unknown",
            artist=request.artist,
            title=request.title,
            album=request.album,
            duration_ms=request.duration_ms,
            timestamp_utc=request.timestamp_utc,
            status="pending"
        )
        db.add(download)
        db.commit()
        db.refresh(download)
        
        # Start download in background
        download_service = DownloadService(session.bearer_token)
        background_tasks.add_task(
            download_service.download_track,
            download.id,
            request.channel_id,
            request.dict(),
            download_path,
            playlist_id=request.playlist_id,
            playlist_name=request.playlist_name
        )
        
        return DownloadResponse(
            success=True,
            message="Download started",
            download_id=download.id
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download error: {str(e)}")


@router.post("/bulk", response_model=DownloadResponse)
async def download_bulk(
    request: BulkDownloadRequest,
    background_tasks: BackgroundTasks,
    db: DBSession = Depends(get_db)
):
    """
    Download multiple tracks from DVR buffer
    """
    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    channel = db.query(Channel).filter(Channel.channel_id == request.channel_id).first()
    
    config = db.query(Config).filter(Config.key == "download_path").first()
    download_path = config.value if config else os.getenv("DOWNLOAD_PATH", "/downloads")
    
    try:
        download_ids = []
        
        for track in request.tracks:
            download = Download(
                channel_id=request.channel_id,
                channel_name=channel.name if channel else "Unknown",
                artist=track.artist,
                title=track.title,
                album=track.album,
                duration_ms=track.duration_ms,
                timestamp_utc=track.timestamp_utc,
                status="pending"
            )
            db.add(download)
            db.commit()
            db.refresh(download)
            download_ids.append(download.id)
        
        # Start bulk download in background
        download_service = DownloadService(session.bearer_token)
        background_tasks.add_task(
            download_service.download_bulk,
            download_ids,
            request.channel_id,
            [t.dict() for t in request.tracks],
            download_path,
            playlist_id=request.playlist_id,
            playlist_name=request.playlist_name
        )
        
        return DownloadResponse(
            success=True,
            message=f"Started downloading {len(request.tracks)} tracks"
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Bulk download error: {str(e)}")


@router.get("/history")
async def get_download_history(
    limit: int = 50,
    offset: int = 0,
    db: DBSession = Depends(get_db)
):
    """
    Get download history
    """
    downloads = db.query(Download).order_by(
        Download.downloaded_at.desc()
    ).offset(offset).limit(limit).all()
    
    total = db.query(Download).count()
    
    return {
        "downloads": [
            DownloadHistoryItem(
                id=d.id,
                channel_name=d.channel_name,
                artist=d.artist,
                title=d.title,
                album=d.album,
                duration_ms=d.duration_ms,
                file_path=d.file_path or "",
                downloaded_at=d.downloaded_at.isoformat() if d.downloaded_at else "",
                status=d.status
            ) for d in downloads
        ],
        "total": total,
        "limit": limit,
        "offset": offset
    }


@router.post("/{download_id}/cancel")
async def cancel_download(download_id: int, db: DBSession = Depends(get_db)):
    """
    Cancel a pending/downloading job. This stops queued/deferred jobs cleanly and
    asks in-progress jobs to stop before they save/import anything.
    """
    download = db.query(Download).filter(Download.id == download_id).first()
    if not download:
        raise HTTPException(status_code=404, detail="Download not found")

    if download.status == "completed":
        raise HTTPException(status_code=400, detail="Completed downloads cannot be cancelled")

    if not str(download.status or "").startswith("cancelled"):
        download.status = "cancelled"
        db.commit()

    return {"success": True, "message": "Download cancelled", "download_id": download_id}


@router.post("/clear-history")
async def clear_download_history(db: DBSession = Depends(get_db)):
    """
    Clear completed/failed/cancelled download history records. Does not delete audio files.
    Active pending/downloading jobs are kept.
    """
    rows = db.query(Download).filter(~Download.status.in_(["pending", "downloading"])).all()
    cleared = len(rows)
    for row in rows:
        db.delete(row)
    db.commit()
    return {"success": True, "cleared": cleared}


@router.get("/{download_id}/status")
async def get_download_status(download_id: int, db: DBSession = Depends(get_db)):
    """
    Get status of a specific download
    """
    download = db.query(Download).filter(Download.id == download_id).first()
    
    if not download:
        raise HTTPException(status_code=404, detail="Download not found")
    
    return {
        "id": download.id,
        "status": download.status,
        "file_path": download.file_path,
        "file_size": download.file_size
    }


@router.delete("/{download_id}")
async def delete_download_record(download_id: int, db: DBSession = Depends(get_db)):
    """
    Delete a download record (not the file)
    """
    download = db.query(Download).filter(Download.id == download_id).first()
    
    if not download:
        raise HTTPException(status_code=404, detail="Download not found")
    
    db.delete(download)
    db.commit()
    
    return {"success": True, "message": "Download record deleted"}
