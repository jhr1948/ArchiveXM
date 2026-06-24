"""
Recording Router - Handle live recording operations
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from typing import Optional
from pathlib import Path
import os

from database import get_db, Session as AuthSession, Config
from services.live_recorder import LiveRecorder

router = APIRouter()

# Global recorder instance (in production, use proper state management)
active_recorder: Optional[LiveRecorder] = None


class StartRecordingRequest(BaseModel):
    channel_id: str


class CurrentTrackInfo(BaseModel):
    artist: Optional[str] = None
    title: Optional[str] = None
    image_url: Optional[str] = None


class RecordingStatusResponse(BaseModel):
    recording: bool
    channel_id: Optional[str] = None
    start_time: Optional[str] = None
    elapsed_seconds: Optional[float] = None
    tracks_recorded: Optional[int] = None
    current_track: Optional[CurrentTrackInfo] = None
    stopping: Optional[bool] = None
    stopping_in_seconds: Optional[float] = None


@router.post("/start")
async def start_recording(
    request: StartRecordingRequest,
    db: DBSession = Depends(get_db)
):
    """
    Start live recording for a channel
    """
    global active_recorder
    
    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if active_recorder and active_recorder.is_recording:
        raise HTTPException(status_code=400, detail="Already recording")
    
    # Get download path
    config = db.query(Config).filter(Config.key == "download_path").first()
    download_path = config.value if config else os.getenv("DOWNLOAD_PATH", "/downloads")
    
    try:
        active_recorder = LiveRecorder(session.bearer_token)
        result = await active_recorder.start_recording(
            request.channel_id,
            Path(download_path) / "recordings"
        )
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to start recording"))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
async def stop_recording(
    wait_for_track: bool = True,
    db: DBSession = Depends(get_db)
):
    """
    Stop live recording
    
    Args:
        wait_for_track: If True, wait for current track to finish
    """
    global active_recorder
    
    if not active_recorder or not active_recorder.is_recording:
        raise HTTPException(status_code=400, detail="Not recording")
    
    try:
        result = await active_recorder.stop_recording(wait_for_track_end=wait_for_track)
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status", response_model=RecordingStatusResponse)
async def recording_status():
    """
    Get current recording status
    """
    global active_recorder
    
    if not active_recorder:
        return RecordingStatusResponse(recording=False)
    
    status = active_recorder.get_status()
    
    # Build current track info if available
    current_track_info = None
    if status.get("current_track"):
        ct = status["current_track"]
        current_track_info = CurrentTrackInfo(
            artist=ct.get("artist"),
            title=ct.get("title"),
            image_url=ct.get("image_url")
        )
    
    return RecordingStatusResponse(
        recording=status.get("recording", False),
        channel_id=status.get("channel_id"),
        start_time=status.get("start_time"),
        elapsed_seconds=status.get("elapsed_seconds"),
        tracks_recorded=status.get("tracks_recorded"),
        current_track=current_track_info,
        stopping=status.get("stopping"),
        stopping_in_seconds=status.get("stopping_in_seconds")
    )


@router.post("/force-stop")
async def force_stop_recording():
    """
    Force stop recording - use if normal stop fails
    """
    global active_recorder
    
    if not active_recorder:
        return {"success": True, "message": "No active recorder"}
    
    try:
        # Force flags
        active_recorder.is_recording = False
        
        # Cancel task forcefully
        if active_recorder.recording_task:
            active_recorder.recording_task.cancel()
        
        # Clear the recorder
        tracks = active_recorder.tracks_recorded.copy() if active_recorder.tracks_recorded else []
        active_recorder = None
        
        return {
            "success": True, 
            "message": "Recording force stopped",
            "tracks_recovered": len(tracks)
        }
        
    except Exception as e:
        # Nuclear option - just clear everything
        active_recorder = None
        return {
            "success": True,
            "message": f"Force cleared with error: {str(e)}"
        }
