"""
Downloads Router - Download tracks from DVR buffer
"""
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from typing import List, Optional
from datetime import datetime
import os

from database import get_db, Channel, Download, Session as AuthSession, Config
from services.download_service import DownloadService

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
