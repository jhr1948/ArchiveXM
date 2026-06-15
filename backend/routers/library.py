"""
Library Router - Handle local music library and playlists (Jukebox)
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import or_, func
from typing import List, Optional
from pathlib import Path
from datetime import datetime
import os
import mimetypes

from database import get_db, LocalTrack, Playlist, PlaylistTrack
from services.library_service import LibraryService

router = APIRouter()


class TrackResponse(BaseModel):
    id: int
    file_path: str
    filename: str
    artist: Optional[str]
    title: Optional[str]
    album: Optional[str]
    genre: Optional[str]
    duration_seconds: Optional[float]
    file_size: Optional[int]
    format: Optional[str]
    cover_art_path: Optional[str]
    play_count: int
    
    class Config:
        from_attributes = True


class PlaylistCreate(BaseModel):
    name: str
    description: Optional[str] = None


class PlaylistResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    cover_image: Optional[str]
    track_count: int
    created_at: datetime
    
    class Config:
        from_attributes = True


class AddToPlaylistRequest(BaseModel):
    track_ids: List[int]


def _playlist_count(db: DBSession, playlist_id: int) -> int:
    return db.query(PlaylistTrack).filter(PlaylistTrack.playlist_id == playlist_id).count()


def _playlist_response_with_count(db: DBSession, playlist: Playlist) -> PlaylistResponse:
    count = _playlist_count(db, playlist.id)
    if playlist.track_count != count:
        playlist.track_count = count
        db.flush()
    return PlaylistResponse(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        cover_image=playlist.cover_image,
        track_count=count,
        created_at=playlist.created_at,
    )


# ============ Library Scanning ============

@router.post("/scan")
async def scan_library(db: DBSession = Depends(get_db)):
    """
    Scan the downloads directory for audio files and update the library
    """
    try:
        service = LibraryService(db)
        result = await service.scan_library()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def library_stats(db: DBSession = Depends(get_db)):
    """
    Get library statistics
    """
    total_tracks = db.query(LocalTrack).count()
    total_playlists = db.query(Playlist).count()
    
    # Get unique artists and albums
    artists = db.query(func.count(func.distinct(LocalTrack.artist))).scalar() or 0
    albums = db.query(func.count(func.distinct(LocalTrack.album))).scalar() or 0
    
    # Total duration
    total_duration = db.query(func.sum(LocalTrack.duration_seconds)).scalar() or 0
    
    # Total size
    total_size = db.query(func.sum(LocalTrack.file_size)).scalar() or 0
    
    return {
        "total_tracks": total_tracks,
        "total_playlists": total_playlists,
        "unique_artists": artists,
        "unique_albums": albums,
        "total_duration_seconds": total_duration,
        "total_size_bytes": total_size
    }


# ============ Tracks ============

@router.get("/tracks", response_model=List[TrackResponse])
async def get_tracks(
    search: Optional[str] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    genre: Optional[str] = None,
    sort_by: str = "artist",
    sort_order: str = "asc",
    limit: int = 100,
    offset: int = 0,
    db: DBSession = Depends(get_db)
):
    """
    Get tracks from the library with optional filtering
    """
    query = db.query(LocalTrack)
    
    # Apply filters
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                LocalTrack.artist.ilike(search_term),
                LocalTrack.title.ilike(search_term),
                LocalTrack.album.ilike(search_term)
            )
        )
    
    if artist:
        query = query.filter(LocalTrack.artist.ilike(f"%{artist}%"))
    
    if album:
        query = query.filter(LocalTrack.album.ilike(f"%{album}%"))
    
    if genre:
        query = query.filter(LocalTrack.genre.ilike(f"%{genre}%"))
    
    # Apply sorting
    sort_column = getattr(LocalTrack, sort_by, LocalTrack.artist)
    if sort_order == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())
    
    # Apply pagination
    tracks = query.offset(offset).limit(limit).all()
    
    return tracks


@router.get("/tracks/{track_id}")
async def get_track(track_id: int, db: DBSession = Depends(get_db)):
    """
    Get a single track by ID
    """
    track = db.query(LocalTrack).filter(LocalTrack.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    return track


@router.get("/tracks/{track_id}/stream")
async def stream_track(
    track_id: int,
    request: Request,
    db: DBSession = Depends(get_db)
):
    """
    Stream an audio file with byte-range support so browser seeking works.
    """
    track = db.query(LocalTrack).filter(LocalTrack.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    file_path = Path(track.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    file_size = file_path.stat().st_size

    # Update play count
    track.play_count += 1
    track.last_played = datetime.utcnow()
    db.commit()

    # Determine mime type
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if not mime_type:
        mime_type = "audio/mp4" if file_path.suffix.lower() in (".m4a", ".mp4") else "audio/mpeg"

    range_header = request.headers.get("range")

    def iter_file(start: int = 0, end: int = file_size - 1, chunk_size: int = 1024 * 1024):
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    if range_header:
        try:
            range_value = range_header.strip().lower().replace("bytes=", "", 1)
            start_text, _, end_text = range_value.partition("-")
            start = int(start_text) if start_text else 0
            end = int(end_text) if end_text else file_size - 1
            start = max(0, start)
            end = min(file_size - 1, end)

            if start > end or start >= file_size:
                raise ValueError("Invalid range")
        except Exception:
            raise HTTPException(status_code=416, detail="Invalid range request")

        content_length = end - start + 1
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Cache-Control": "no-cache",
        }
        return StreamingResponse(
            iter_file(start, end),
            status_code=206,
            media_type=mime_type,
            headers=headers,
        )

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Cache-Control": "no-cache",
    }
    return StreamingResponse(
        iter_file(),
        media_type=mime_type,
        headers=headers,
    )


@router.get("/tracks/{track_id}/cover")
async def get_track_cover(track_id: int, db: DBSession = Depends(get_db)):
    """
    Get cover art for a track
    """
    track = db.query(LocalTrack).filter(LocalTrack.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    if track.cover_art_path and Path(track.cover_art_path).exists():
        return FileResponse(track.cover_art_path)
    
    raise HTTPException(status_code=404, detail="No cover art available")


@router.delete("/tracks/{track_id}")
async def delete_track(
    track_id: int, 
    delete_file: bool = False,
    db: DBSession = Depends(get_db)
):
    """
    Remove a track from the library (optionally delete the file)
    """
    track = db.query(LocalTrack).filter(LocalTrack.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    # Remove from playlists and remember affected playlists so counts stay correct
    affected_playlist_ids = [
        row[0] for row in db.query(PlaylistTrack.playlist_id)
        .filter(PlaylistTrack.track_id == track_id)
        .distinct()
        .all()
    ]
    db.query(PlaylistTrack).filter(PlaylistTrack.track_id == track_id).delete(synchronize_session=False)
    
    # Delete file if requested
    if delete_file:
        try:
            file_path = Path(track.file_path)
            if file_path.exists():
                file_path.unlink()
        except Exception as e:
            print(f"Error deleting file: {e}")
    
    # Remove from database
    db.delete(track)

    # Update cached playlist counts for any playlist that contained this track
    for playlist_id in affected_playlist_ids:
        playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
        if playlist:
            playlist.track_count = db.query(PlaylistTrack).filter(
                PlaylistTrack.playlist_id == playlist_id
            ).count()

    db.commit()
    
    return {"success": True, "message": "Track removed", "affected_playlists": affected_playlist_ids}


# ============ Artists & Albums ============

@router.get("/artists")
async def get_artists(db: DBSession = Depends(get_db)):
    """
    Get list of unique artists
    """
    artists = db.query(
        LocalTrack.artist,
        func.count(LocalTrack.id).label("track_count")
    ).filter(
        LocalTrack.artist.isnot(None),
        LocalTrack.artist != ""
    ).group_by(LocalTrack.artist).order_by(LocalTrack.artist).all()
    
    return [{"name": a[0], "track_count": a[1]} for a in artists]


@router.get("/albums")
async def get_albums(db: DBSession = Depends(get_db)):
    """
    Get list of unique albums
    """
    albums = db.query(
        LocalTrack.album,
        LocalTrack.artist,
        func.count(LocalTrack.id).label("track_count")
    ).filter(
        LocalTrack.album.isnot(None),
        LocalTrack.album != ""
    ).group_by(LocalTrack.album, LocalTrack.artist).order_by(LocalTrack.album).all()
    
    return [{"name": a[0], "artist": a[1], "track_count": a[2]} for a in albums]


# ============ Playlists ============

@router.get("/playlists", response_model=List[PlaylistResponse])
async def get_playlists(db: DBSession = Depends(get_db)):
    """
    Get all playlists with live track counts.
    """
    playlists = db.query(Playlist).order_by(Playlist.name).all()
    responses = [_playlist_response_with_count(db, playlist) for playlist in playlists]
    db.commit()
    return responses


@router.post("/playlists", response_model=PlaylistResponse)
async def create_playlist(
    playlist: PlaylistCreate,
    db: DBSession = Depends(get_db)
):
    """
    Create a new playlist
    """
    new_playlist = Playlist(
        name=playlist.name,
        description=playlist.description
    )
    db.add(new_playlist)
    db.commit()
    db.refresh(new_playlist)
    return _playlist_response_with_count(db, new_playlist)


@router.get("/playlists/{playlist_id}")
async def get_playlist(playlist_id: int, db: DBSession = Depends(get_db)):
    """
    Get a playlist with its tracks
    """
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    # Get tracks in order
    playlist_tracks = db.query(PlaylistTrack, LocalTrack).join(
        LocalTrack, PlaylistTrack.track_id == LocalTrack.id
    ).filter(
        PlaylistTrack.playlist_id == playlist_id
    ).order_by(PlaylistTrack.position).all()
    
    tracks = []
    for pt, track in playlist_tracks:
        tracks.append({
            "position": pt.position,
            "track": {
                "id": track.id,
                "file_path": track.file_path,
                "filename": track.filename,
                "artist": track.artist,
                "title": track.title,
                "album": track.album,
                "duration_seconds": track.duration_seconds,
                "cover_art_path": track.cover_art_path
            }
        })
    
    live_count = len(tracks)
    if playlist.track_count != live_count:
        playlist.track_count = live_count
        db.commit()

    return {
        "id": playlist.id,
        "name": playlist.name,
        "description": playlist.description,
        "cover_image": playlist.cover_image,
        "track_count": live_count,
        "created_at": playlist.created_at,
        "tracks": tracks
    }


@router.put("/playlists/{playlist_id}")
async def update_playlist(
    playlist_id: int,
    playlist: PlaylistCreate,
    db: DBSession = Depends(get_db)
):
    """
    Update a playlist
    """
    existing = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    existing.name = playlist.name
    existing.description = playlist.description
    db.commit()
    
    return {"success": True, "message": "Playlist updated"}


@router.delete("/playlists/{playlist_id}")
async def delete_playlist(playlist_id: int, db: DBSession = Depends(get_db)):
    """
    Delete a playlist
    """
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    # Remove playlist tracks
    db.query(PlaylistTrack).filter(PlaylistTrack.playlist_id == playlist_id).delete()
    
    # Remove playlist
    db.delete(playlist)
    db.commit()
    
    return {"success": True, "message": "Playlist deleted"}


@router.post("/playlists/{playlist_id}/tracks")
async def add_tracks_to_playlist(
    playlist_id: int,
    request: AddToPlaylistRequest,
    db: DBSession = Depends(get_db)
):
    """
    Add tracks to a playlist
    """
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    # Get current max position
    max_pos = db.query(func.max(PlaylistTrack.position)).filter(
        PlaylistTrack.playlist_id == playlist_id
    ).scalar() or 0
    
    added = 0
    for track_id in request.track_ids:
        # Check if track exists
        track = db.query(LocalTrack).filter(LocalTrack.id == track_id).first()
        if not track:
            continue
        
        # Check if already in playlist
        existing = db.query(PlaylistTrack).filter(
            PlaylistTrack.playlist_id == playlist_id,
            PlaylistTrack.track_id == track_id
        ).first()
        
        if not existing:
            max_pos += 1
            pt = PlaylistTrack(
                playlist_id=playlist_id,
                track_id=track_id,
                position=max_pos
            )
            db.add(pt)
            added += 1
    
    db.flush()
    # Update track count
    playlist.track_count = db.query(PlaylistTrack).filter(
        PlaylistTrack.playlist_id == playlist_id
    ).count()
    
    db.commit()
    
    return {"success": True, "added": added}


@router.delete("/playlists/{playlist_id}/tracks/{track_id}")
async def remove_track_from_playlist(
    playlist_id: int,
    track_id: int,
    db: DBSession = Depends(get_db)
):
    """
    Remove a track from a playlist
    """
    pt = db.query(PlaylistTrack).filter(
        PlaylistTrack.playlist_id == playlist_id,
        PlaylistTrack.track_id == track_id
    ).first()
    
    if not pt:
        raise HTTPException(status_code=404, detail="Track not in playlist")
    
    db.delete(pt)
    
    db.flush()
    # Update track count
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if playlist:
        playlist.track_count = db.query(PlaylistTrack).filter(
            PlaylistTrack.playlist_id == playlist_id
        ).count()
    
    db.commit()
    
    return {"success": True, "message": "Track removed from playlist"}


@router.put("/playlists/{playlist_id}/reorder")
async def reorder_playlist(
    playlist_id: int,
    track_ids: List[int],
    db: DBSession = Depends(get_db)
):
    """
    Reorder tracks in a playlist
    """
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")
    
    for position, track_id in enumerate(track_ids, 1):
        db.query(PlaylistTrack).filter(
            PlaylistTrack.playlist_id == playlist_id,
            PlaylistTrack.track_id == track_id
        ).update({"position": position})
    
    db.commit()
    
    return {"success": True, "message": "Playlist reordered"}
