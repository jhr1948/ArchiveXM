"""
Library Router - Handle local music library and playlists (Jukebox)
"""
from fastapi import APIRouter, HTTPException, Depends, Request, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import or_, func
from typing import List, Optional, Dict, Any
from pathlib import Path
from datetime import datetime, timezone, timedelta
import os
import mimetypes
import re
import asyncio
import httpx
import json
import difflib

from database import get_db, LocalTrack, Playlist, PlaylistTrack, Config, Channel, Download, Session as AuthSession
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


class PlaylistCoverUpdate(BaseModel):
    cover_image: Optional[str] = None



class CaptureTrackInfo(BaseModel):
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    timestamp_utc: Optional[str] = None
    image_url: Optional[str] = None
    started_at_ms: Optional[int] = None
    start_time_ms: Optional[int] = None
    startedAtMs: Optional[int] = None
    durationMs: Optional[int] = None
    imageUrl: Optional[str] = None


class CaptureCurrentRequest(BaseModel):
    channel_id: str
    channel_type: Optional[str] = None
    source: Optional[str] = None
    position_ms: Optional[int] = None
    track: Optional[CaptureTrackInfo] = None


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


class BulkDeleteTracksRequest(BaseModel):
    track_ids: List[int]
    delete_files: bool = True


class BulkRemoveFromPlaylistRequest(BaseModel):
    track_ids: List[int]


class RemoveCurrentPlaylistRequest(BaseModel):
    track_id: Optional[int] = None
    track_url: Optional[str] = None
    url: Optional[str] = None
    tvg_id: Optional[str] = None
    source: Optional[str] = None
    remove_all: bool = False
    track: Optional[Dict[str, Any]] = None



class MetadataApplyRequest(BaseModel):
    artist: Optional[str] = None
    title: Optional[str] = None
    album: Optional[str] = None
    year: Optional[str] = None
    cover_url: Optional[str] = None
    provider: Optional[str] = None
    recording_id: Optional[str] = None
    release_id: Optional[str] = None


def _first_text(*values, default: str = "") -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def _ms_to_iso_utc(ms_value) -> str | None:
    if ms_value in (None, ""):
        return None
    try:
        ms = int(ms_value)
        if ms <= 0:
            return None
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _capture_track_from_request(request: CaptureCurrentRequest) -> Dict[str, Any]:
    track = request.track
    if not track:
        return {}

    raw = track.dict(exclude_none=True)
    duration_ms = raw.get("duration_ms") or raw.get("durationMs") or 0
    timestamp_utc = raw.get("timestamp_utc") or _ms_to_iso_utc(
        raw.get("started_at_ms") or raw.get("startedAtMs") or raw.get("start_time_ms")
    )

    return {
        "artist": _first_text(raw.get("artist"), default="Unknown"),
        "title": _first_text(raw.get("title"), default="Unknown"),
        "album": _first_text(raw.get("album"), default=""),
        "duration_ms": int(duration_ms or 0),
        "timestamp_utc": timestamp_utc,
        "image_url": raw.get("image_url") or raw.get("imageUrl"),
    }


async def _resolve_capture_track(session_token: str, request: CaptureCurrentRequest) -> Dict[str, Any]:
    """Resolve the best track payload to hand to the existing download service.

    External players such as M3You usually know what ArchiveXM is displaying,
    but they should not be responsible for DVR timing. For linear live channels
    we prefer SiriusXM's timed station-history schedule so capture-current uses
    the original song start boundary instead of the time the user clicked +.
    """
    requested = _capture_track_from_request(request)
    channel_type = (request.channel_type or "").strip().lower()

    def _norm(value) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    def _item_payload(item: Dict[str, Any], fallback: Dict[str, Any] | None = None) -> Dict[str, Any]:
        fallback = fallback or {}
        ts = item.get("timestamp_utc") or item.get("timestamp") or fallback.get("timestamp_utc")
        dur = item.get("duration_ms") or item.get("durationMs") or item.get("duration") or fallback.get("duration_ms") or 0
        try:
            # Some APIs return duration in seconds. Anything small enough to look
            # like seconds gets converted to ms.
            dur_num = float(dur or 0)
            if 0 < dur_num < 2000:
                dur_num *= 1000
            dur = int(dur_num)
        except Exception:
            dur = int(fallback.get("duration_ms") or 0)

        return {
            "artist": _first_text(item.get("artist"), item.get("artistName"), fallback.get("artist"), default="Unknown"),
            "title": _first_text(item.get("title"), item.get("song"), item.get("name"), fallback.get("title"), default="Unknown"),
            "album": _first_text(item.get("album"), item.get("albumName"), fallback.get("album"), default=""),
            "duration_ms": dur,
            "timestamp_utc": ts,
            "image_url": item.get("image_url") or item.get("imageUrl") or item.get("cover") or fallback.get("image_url"),
        }

    # For normal live channels, use the raw timed station-history schedule.
    # get_current_track/metadata can be display-offset adjusted, which is great
    # for UI but bad for downloading because it can start at the click time and
    # save only a clipped tail. The schedule has the original start boundary.
    if channel_type not in {"channel-xtra", "xtra"}:
        try:
            from services.sxm_api import SiriusXMAPI
            api = SiriusXMAPI(session_token)

            # Keep get_current_track as a metadata fallback, but do not trust it
            # as the primary timing source.
            current = None
            try:
                current = await api.get_current_track(request.channel_id)
            except Exception as e:
                print(f"Capture current: current metadata lookup failed: {e}")

            requested_title = _norm(requested.get("title"))
            requested_artist = _norm(requested.get("artist"))
            current_title = _norm((current or {}).get("title"))
            current_artist = _norm((current or {}).get("artist"))

            schedule = []
            try:
                try:
                    schedule = await api.get_schedule(request.channel_id, hours_back=5, include_interstitials=True)
                except TypeError:
                    schedule = await api.get_schedule(request.channel_id, hours_back=5)
            except Exception as e:
                print(f"Capture current: schedule lookup failed: {e}")

            if isinstance(schedule, dict):
                schedule = schedule.get("items") or schedule.get("tracks") or schedule.get("episodes") or []

            timed_items = []
            for item in schedule or []:
                if not isinstance(item, dict):
                    continue
                ts = item.get("timestamp_utc") or item.get("timestamp")
                if not ts:
                    continue
                try:
                    item_time = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if item_time.tzinfo is None:
                        item_time = item_time.replace(tzinfo=timezone.utc)
                    timed_items.append((item_time, item))
                except Exception:
                    continue

            timed_items.sort(key=lambda pair: pair[0])
            now = datetime.now(timezone.utc)

            def _matches_requested(item: Dict[str, Any]) -> bool:
                title = _norm(item.get("title") or item.get("song") or item.get("name"))
                artist = _norm(item.get("artist") or item.get("artistName"))
                if requested_title and title and requested_title == title:
                    if not requested_artist or not artist or requested_artist == artist:
                        return True
                return False

            def _looks_like_match(item: Dict[str, Any]) -> bool:
                if _matches_requested(item):
                    return True
                title = _norm(item.get("title") or item.get("song") or item.get("name"))
                artist = _norm(item.get("artist") or item.get("artistName"))
                if current_title and title and current_title == title:
                    if not current_artist or not artist or current_artist == artist:
                        return True
                return False

            # First, prefer the timed item whose start/end window contains now.
            # This is the true current station-history item and should give the
            # full original song boundary even if the user clicks + mid-song.
            for idx, (start_time, item) in enumerate(timed_items):
                next_start = timed_items[idx + 1][0] if idx + 1 < len(timed_items) else None
                if start_time <= now and (next_start is None or now < next_start):
                    # If M3You is still displaying the previous song because of
                    # metadata offset or player polling, the true active SXM item
                    # may already be a short DJ/channel plug. In that case do not
                    # capture the plug; fall through to the matching history item
                    # for the song M3You actually asked us to bookmark.
                    if requested_title and not _matches_requested(item):
                        try:
                            window_sec = (next_start - start_time).total_seconds() if next_start else 0
                        except Exception:
                            window_sec = 0
                        is_interstitial = bool(item.get("is_interstitial") or item.get("isInterstitial") or item.get("interstitial"))
                        if is_interstitial or (0 < window_sec <= 75):
                            print(
                                "Capture current: active boundary does not match requested track; "
                                "skipping likely DJ/plug boundary and using matching history item"
                            )
                            break

                    payload = _item_payload(item, current or requested)
                    if next_start and not payload.get("duration_ms"):
                        payload["duration_ms"] = int((next_start - start_time).total_seconds() * 1000)
                    print(
                        "Capture current: using active schedule boundary "
                        f"{payload.get('artist')} - {payload.get('title')} @ {payload.get('timestamp_utc')}"
                    )
                    return payload

            # If clock/offset mismatch prevents the window check, fall back to the
            # most recent matching song by title/artist in the 5-hour history.
            matching = [(t, item) for t, item in timed_items if _looks_like_match(item)]
            if matching:
                start_time, item = matching[-1]
                payload = _item_payload(item, current or requested)
                print(
                    "Capture current: using matching schedule boundary "
                    f"{payload.get('artist')} - {payload.get('title')} @ {payload.get('timestamp_utc')}"
                )
                return payload

            if current:
                # Last resort: current metadata. This may still fail the full-window
                # guard rather than saving a clipped file.
                resolved = _item_payload(current, requested)
                print(
                    "Capture current: using current metadata fallback "
                    f"{resolved.get('artist')} - {resolved.get('title')} @ {resolved.get('timestamp_utc')}"
                )
                return resolved
        except Exception as e:
            print(f"Capture current: server-side live metadata lookup failed: {e}")

    # XTRA capture: use ArchiveXM's active XTRA queue when available. Unlike
    # live linear channels, XTRA tracks do not have station-history boundaries.
    # ArchiveXM's XTRA proxy already fetched a FULL 256k media playlist for the
    # currently playing XTRA item, so capture-current can download that exact
    # playlist from the beginning and add it to the selected Jukebox playlist.
    if channel_type in {"channel-xtra", "xtra"}:
        try:
            from routers import streams as streams_router

            cached = getattr(streams_router, "_xtra_sessions", {}).get(request.channel_id)
            xtra_track = None

            if cached:
                served = cached.get("served") or set()
                tracks = cached.get("tracks") or []

                # Pick the first queued XTRA item that has not been fully served.
                # This maps to the item the listener is currently hearing, while
                # still downloading its full playlist from the beginning.
                for candidate in tracks:
                    names = set(candidate.get("segment_names") or [])
                    if not names or not names.issubset(served):
                        xtra_track = candidate
                        break

                if not xtra_track and tracks:
                    xtra_track = tracks[-1]

            # If M3You calls + before ArchiveXM has an active proxy queue in this
            # process, try to fetch the current XTRA FULL item directly.
            if not xtra_track:
                fetcher = getattr(streams_router, "_fetch_xtra_256k_track", None)
                if fetcher:
                    xtra_track = await fetcher(
                        channel_id=request.channel_id,
                        bearer=session_token,
                        use_peek=True,
                    )

            if xtra_track:
                metadata = xtra_track.get("metadata") or {}
                duration_ms = metadata.get("durationMs")
                if not duration_ms:
                    try:
                        duration_ms = int(float(xtra_track.get("duration") or 0) * 1000)
                    except Exception:
                        duration_ms = requested.get("duration_ms") or 0

                timestamp_utc = _ms_to_iso_utc(metadata.get("startedAtMs"))
                if not timestamp_utc:
                    timestamp_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

                payload = {
                    "artist": _first_text(metadata.get("artist"), requested.get("artist"), default="Unknown"),
                    "title": _first_text(metadata.get("title"), requested.get("title"), default="Unknown"),
                    "album": _first_text(metadata.get("album"), requested.get("album"), default=""),
                    "duration_ms": int(duration_ms or requested.get("duration_ms") or 0),
                    "timestamp_utc": timestamp_utc,
                    "image_url": metadata.get("imageUrl") or requested.get("image_url"),
                    "is_xtra_capture": True,
                    "preserve_duration": True,
                    "_xtra_track": {
                        "playlist_text": xtra_track.get("playlist_text") or "",
                        "base_url": xtra_track.get("base_url") or "",
                        "path_dir": xtra_track.get("path_dir") or "",
                        "duration": xtra_track.get("duration"),
                        "path": xtra_track.get("path"),
                        "bearer": session_token,
                    },
                }
                print(
                    "Capture current: using active XTRA item "
                    f"{payload.get('artist')} - {payload.get('title')} "
                    f"duration={payload.get('duration_ms')}ms"
                )
                return payload

            print("Capture current: no active XTRA queue item found; using M3You metadata fallback")
        except Exception as e:
            print(f"Capture current: XTRA active item lookup failed: {e}")

    return requested


async def _wait_until_capture_track_complete(track_payload: Dict[str, Any], max_wait_seconds: int = 900):
    """For current live captures, wait until the full song window exists.

    When a user clicks + during a live song, the station-history boundary gives
    the correct original start time, but the HLS/replay playlist may only expose
    media up to the current moment. Downloading immediately saves the beginning
    of the song and cuts off at "now". Waiting until the song end plus a small
    safety pad makes capture-current behave like downloading the item from
    Station History.
    """
    ts = track_payload.get("timestamp_utc") or track_payload.get("timestamp")
    duration_ms = track_payload.get("duration_ms") or track_payload.get("durationMs") or 0
    try:
        duration_ms = float(duration_ms or 0)
    except Exception:
        duration_ms = 0

    if not ts or duration_ms <= 0:
        return

    try:
        start = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
    except Exception:
        return

    # Use a generous publish-delay pad after the expected song end. The SiriusXM
    # replay/HLS playlist can lag the metadata boundary by more than one segment,
    # and starting the download too soon creates files that contain the beginning
    # of the song but miss the last few seconds. This is intentionally larger
    # than normal download tail padding because capture-current is a bookmark:
    # correctness is more important than immediate appearance in the playlist.
    safety_pad = 22.0
    try:
        from database import SessionLocal
        db_tmp = SessionLocal()
        try:
            item = db_tmp.query(Config).filter(Config.key == "download_tail_pad_seconds").first()
            configured_pad = float(item.value) if item and item.value not in (None, "") else 2.0
            safety_pad = max(18.0, min(35.0, configured_pad + 20.0))
        finally:
            db_tmp.close()
    except Exception:
        pass

    target = start + timedelta(milliseconds=duration_ms) + timedelta(seconds=safety_pad)
    now = datetime.now(timezone.utc)
    wait_seconds = (target - now).total_seconds()

    if wait_seconds > 1:
        capped = min(wait_seconds, float(max_wait_seconds))
        artist = track_payload.get("artist") or "Unknown"
        title = track_payload.get("title") or "Unknown"
        print(f"Capture current: waiting {capped:.1f}s for full live track window {artist} - {title}")
        await asyncio.sleep(capped)

async def _download_capture_and_add_to_playlist(
    download_id: int,
    channel_id: str,
    track_payload: Dict[str, Any],
    download_path: str,
    playlist_id: int,
    bearer_token: str,
):
    """Background task: download the captured track, import it, then add it."""
    from database import SessionLocal
    from services.download_service import DownloadService

    ok = False
    try:
        # If this is the currently airing live song, do not start downloading
        # until the full start-to-end replay window exists. Otherwise the file
        # imports as a clipped beginning that ends at the click time.
        if track_payload.get("require_full_window"):
            await _wait_until_capture_track_complete(track_payload)

        service = DownloadService(bearer_token)
        if track_payload.get("is_xtra_capture"):
            ok = await service.download_xtra_track(download_id, channel_id, track_payload, download_path)
        else:
            ok = await service.download_track(download_id, channel_id, track_payload, download_path)
    except Exception as e:
        print(f"Capture current download task failed: {e}")

    db = SessionLocal()
    try:
        download = db.query(Download).filter(Download.id == download_id).first()
        if not ok or not download or not download.file_path:
            return

        file_path = str(download.file_path)

        # Import the completed file into the Jukebox library. The scanner is
        # intentionally used instead of reaching into LibraryService internals so
        # this remains compatible with current and older service versions.
        try:
            library_service = LibraryService(db)
            await library_service.scan_library()
        except Exception as e:
            print(f"Capture current: library scan after download failed: {e}")

        local_track = db.query(LocalTrack).filter(LocalTrack.file_path == file_path).first()
        if not local_track:
            # Fallback: match by filename if paths differ because of Docker mount
            # normalization.
            local_track = db.query(LocalTrack).filter(LocalTrack.filename == Path(file_path).name).first()

        playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
        if not local_track or not playlist:
            print(f"Capture current: could not add to playlist local_track={bool(local_track)} playlist={bool(playlist)}")
            return

        existing = db.query(PlaylistTrack).filter(
            PlaylistTrack.playlist_id == playlist_id,
            PlaylistTrack.track_id == local_track.id,
        ).first()
        if not existing:
            max_pos = db.query(func.max(PlaylistTrack.position)).filter(
                PlaylistTrack.playlist_id == playlist_id
            ).scalar() or 0
            db.add(PlaylistTrack(
                playlist_id=playlist_id,
                track_id=local_track.id,
                position=max_pos + 1,
            ))
            db.flush()

        playlist.track_count = db.query(PlaylistTrack).filter(PlaylistTrack.playlist_id == playlist_id).count()
        db.commit()
        print(f"🎵 Capture current added library track {local_track.id} to playlist {playlist.name}")
    except Exception as e:
        db.rollback()
        print(f"Capture current add-to-playlist failed: {e}")
    finally:
        db.close()


def _normalize_song_lookup_text(value: str | None, *, keep_parenthetical: bool = True, drop_join_words: bool = True) -> str:
    """Normalize artist/title text for duplicate Jukebox lookups.

    Capture metadata is not always identical to tags scanned from downloaded
    files. For example SiriusXM may send ``(She's) Sexy & 17`` while a tagged
    file may scan as ``She's Sexy + 17`` or ``Sexy And 17``.  This normalizer
    keeps those as the same song without making broad playlist duplicates.
    """
    text = str(value or "").strip().lower()
    text = text.replace("’", "'").replace("‘", "'").replace("`", "'")
    text = text.replace("+", " and ").replace("&", " and ")
    text = re.sub(r"\b(feat|featuring|ft)\.?\b.*$", " ", text)
    if keep_parenthetical:
        # Keep the words inside parens/brackets; only remove the punctuation.
        text = re.sub(r"[\(\)\[\]{}]", " ", text)
    else:
        # Alternate key: drop parenthetical qualifiers entirely.
        text = re.sub(r"\([^)]*\)", " ", text)
        text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [tok for tok in text.split() if tok]
    if drop_join_words:
        tokens = [tok for tok in tokens if tok not in {"and"}]
    return " ".join(tokens).strip()


def _song_lookup_keys(value: str | None) -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()

    values = {raw}
    # Some scanned titles or filenames include "Artist - Title".  Keep both.
    if " - " in raw:
        values.add(raw.split(" - ", 1)[1])

    keys: set[str] = set()
    for val in values:
        for keep_parenthetical in (True, False):
            for drop_join_words in (True, False):
                key = _normalize_song_lookup_text(
                    val,
                    keep_parenthetical=keep_parenthetical,
                    drop_join_words=drop_join_words,
                )
                if key:
                    keys.add(key)
    return keys


def _artist_lookup_keys(value: str | None) -> set[str]:
    keys = _song_lookup_keys(value)
    expanded = set(keys)
    for key in keys:
        if key.startswith("the "):
            expanded.add(key[4:])
        else:
            expanded.add(f"the {key}")
    return {key for key in expanded if key and key not in {"unknown", "various artists"}}


def _lookup_keys_match(left: set[str], right: set[str], *, fuzzy: bool = False) -> bool:
    if not left or not right:
        return False
    if left.intersection(right):
        return True
    if not fuzzy:
        return False

    for a in left:
        for b in right:
            shorter = min(len(a), len(b))
            if shorter < 8:
                continue
            if a in b or b in a:
                return True
            # Conservative fuzzy fallback for small tag differences like
            # "shes sexy 17" vs "sexy 17" or apostrophe/plus/ampersand variants.
            if difflib.SequenceMatcher(None, a, b).ratio() >= 0.88:
                return True
    return False


def _find_existing_jukebox_track(
    db: DBSession,
    artist: str | None,
    title: str | None,
    duration_ms: int | None = None,
    exclude_track_id: int | None = None,
) -> LocalTrack | None:
    """Return an existing library track that looks like the same song.

    The match is title-first, with artist required when known.  It tolerates
    common metadata differences from SiriusXM, filenames, and music tags
    without treating unrelated same-title songs by different artists as dupes.
    """
    title_keys = _song_lookup_keys(title)
    artist_keys = _artist_lookup_keys(artist)
    if not title_keys:
        return None

    query = db.query(LocalTrack).filter(LocalTrack.title.isnot(None))
    if exclude_track_id is not None:
        query = query.filter(LocalTrack.id != exclude_track_id)

    matches = []
    for track in query.all():
        track_title_keys = _song_lookup_keys(track.title) | _song_lookup_keys(track.filename)
        if not _lookup_keys_match(title_keys, track_title_keys, fuzzy=True):
            continue

        if artist_keys:
            track_artist_keys = _artist_lookup_keys(track.artist)
            # Last fallback: filename often contains "Artist - Title" even when
            # tags are sparse or slightly different.
            track_filename_keys = _song_lookup_keys(track.filename)
            if not (
                _lookup_keys_match(artist_keys, track_artist_keys, fuzzy=True)
                or any(artist_key in filename_key for artist_key in artist_keys for filename_key in track_filename_keys)
            ):
                continue

        matches.append(track)

    if not matches:
        return None

    try:
        target_ms = int(duration_ms or 0)
    except Exception:
        target_ms = 0

    if target_ms > 0:
        with_duration = []
        for track in matches:
            try:
                track_ms = int(float(track.duration_seconds or 0) * 1000)
            except Exception:
                track_ms = 0
            if track_ms > 0:
                tolerance = max(15000, min(45000, int(target_ms * 0.12)))
                with_duration.append((abs(track_ms - target_ms), tolerance, track))
        close = [item for item in with_duration if item[0] <= item[1]]
        if close:
            close.sort(key=lambda item: item[0])
            return close[0][2]

    return matches[0]


def _get_config_value(db: DBSession, key: str, default=None):
    item = db.query(Config).filter(Config.key == key).first()
    if item is None or item.value in (None, ""):
        return default
    return item.value


def _normalize_base_url(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value.rstrip("/")


def _library_base_url(db: DBSession) -> str:
    mode = str(_get_config_value(db, "playlist_url_mode", "local") or "local").strip().lower()
    if mode == "public":
        base = _normalize_base_url(_get_config_value(db, "playlist_public_base_url", ""))
        if base:
            return base
    base = _normalize_base_url(_get_config_value(db, "playlist_local_base_url", ""))
    if base:
        return base
    return ""


def _m3u_escape(value) -> str:
    text = "" if value is None else str(value)
    return text.replace('"', "'").replace("\r", " ").replace("\n", " ").strip()


def _m3u_plain(value) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"[\r\n]+", " ", text)
    return text.strip()


def _track_display_name(track: LocalTrack) -> str:
    title = _m3u_plain(track.title or track.filename or f"Track {track.id}")
    artist = _m3u_plain(track.artist or "")
    if artist and title and artist.lower() not in title.lower():
        return f"{artist} - {title}"
    return title or artist or f"Track {track.id}"


def _track_file_ext(track: LocalTrack) -> str:
    """Return a safe file extension for external players.

    Some IPTV players are stricter than VLC and use the URL extension as part
    of format detection. Keep the extension on the public play URL even though
    the normal /play alias still works.
    """
    suffix = Path(track.file_path or track.filename or "").suffix.lower().lstrip(".")
    if suffix in {"mp3", "m4a", "mp4", "aac", "flac", "ogg", "wav"}:
        return suffix
    fmt = (track.format or "").lower().strip().lstrip(".")
    if fmt in {"mp3", "m4a", "mp4", "aac", "flac", "ogg", "wav"}:
        return fmt
    return "m4a"


def _track_play_url(db: DBSession, track: LocalTrack) -> str:
    base = _library_base_url(db)
    return f"{base}/api/library/files/{track.id}/play.{_track_file_ext(track)}"




def _download_path(db: DBSession) -> Path:
    path = _get_config_value(db, "download_path", os.getenv("DOWNLOAD_PATH", "/downloads")) or "/downloads"
    return Path(str(path))




def _musicbrainz_artist_credit(recording: Dict[str, Any]) -> str:
    parts = []
    for credit in recording.get("artist-credit") or []:
        if isinstance(credit, dict):
            name = credit.get("name") or (credit.get("artist") or {}).get("name")
            if name:
                parts.append(str(name))
        elif isinstance(credit, str):
            parts.append(credit)
    return "".join(parts).strip()


def _metadata_clean_text(value: str) -> str:
    text = str(value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\bfeat(?:uring)?\.?\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _metadata_words(value: str) -> List[str]:
    stop = {"the", "a", "an", "and", "or"}
    return [w for w in _metadata_clean_text(value).split() if len(w) > 1 and w not in stop]


def _metadata_word_set(value: str) -> set:
    return set(_metadata_words(value))


def _metadata_similarity(a: str, b: str) -> float:
    aw = _metadata_word_set(a)
    bw = _metadata_word_set(b)
    if not aw or not bw:
        return 0.0
    inter = len(aw & bw)
    union = len(aw | bw)
    if union <= 0:
        return 0.0
    # Blend Jaccard with containment so titles with extra parenthetical wording
    # like "(You Gotta) Fight for Your Right (to Party!)" still score high
    # against SXM's simplified display title.
    containment = inter / max(1, min(len(aw), len(bw)))
    return (inter / union * 0.55) + (containment * 0.45)


def _metadata_artist_variants(artist: str) -> List[str]:
    """Return search-friendly artist variants.

    MusicBrainz commonly stores punk/new-wave artists without a leading
    article, e.g. SXM/Jukebox may show "The Ramones" while MusicBrainz uses
    "Ramones". Search both forms so strict artist+title queries do not return
    zero candidates.
    """
    raw = re.sub(r"\s+", " ", str(artist or "").strip())
    if not raw:
        return []

    variants = []

    def add(value: str):
        value = re.sub(r"\s+", " ", str(value or "").strip())
        if value and value.lower() not in {v.lower() for v in variants}:
            variants.append(value)

    add(raw)
    no_article = re.sub(r"^(the|a|an)\s+", "", raw, flags=re.I).strip()
    add(no_article)
    if no_article and no_article.lower() == raw.lower():
        add(f"The {raw}")

    return variants


def _metadata_artist_similarity(a: str, b: str) -> float:
    variants_a = _metadata_artist_variants(a) or [a]
    variants_b = _metadata_artist_variants(b) or [b]

    best = 0.0
    for va in variants_a:
        for vb in variants_b:
            ac = _metadata_clean_text(va)
            bc = _metadata_clean_text(vb)
            if not ac or not bc:
                continue
            if ac == bc:
                best = max(best, 1.0)
            elif ac in bc or bc in ac:
                best = max(best, 0.90)
            else:
                best = max(best, _metadata_similarity(ac, bc))
    return best


def _musicbrainz_release_artist(release: Dict[str, Any], fallback: str = "") -> str:
    parts = []
    for credit in release.get("artist-credit") or []:
        if isinstance(credit, dict):
            name = credit.get("name") or (credit.get("artist") or {}).get("name")
            if name:
                parts.append(str(name))
        elif isinstance(credit, str):
            parts.append(credit)
    return "".join(parts).strip() or fallback


def _candidate_key(candidate: Dict[str, Any]) -> tuple:
    return (
        str(candidate.get("artist") or "").lower(),
        str(candidate.get("title") or "").lower(),
        str(candidate.get("album") or "").lower(),
        str(candidate.get("year") or ""),
        str(candidate.get("release_id") or ""),
    )


def _metadata_title_variants(title: str) -> List[str]:
    """Return search-friendly variants for SXM/XTRA titles.

    SXM often appends the release year in parentheses, e.g.
    "Push It (88)". MusicBrainz generally stores the canonical title without
    that suffix, so search both the displayed title and cleaned variants.
    """
    raw = (title or "").strip()
    if not raw:
        return []

    variants = []

    def add(value: str):
        value = re.sub(r"\s+", " ", str(value or "").strip())
        if value and value.lower() not in {v.lower() for v in variants}:
            variants.append(value)

    add(raw)

    # Remove SXM year suffixes like (87), (1987), [87], or [1987].
    cleaned = re.sub(r"\s*[\(\[]\s*(?:19|20)?\d{2}\s*[\)\]]\s*$", "", raw).strip()
    add(cleaned)

    # Drop common mix/remaster/radio edit suffixes only as fallback variants.
    cleaned2 = re.sub(
        r"\s*[\(\[]\s*(?:remaster(?:ed)?|radio edit|single version|album version|extended(?: mix)?|mono|stereo)\s*[\)\]]\s*$",
        "",
        cleaned,
        flags=re.I,
    ).strip()
    add(cleaned2)

    # A loose punctuation-normalized fallback helps titles like
    # "You Gotta Fight For Your Right To Party" match MusicBrainz's
    # "(You Gotta) Fight for Your Right (To Party!)".
    loose = re.sub(r"[^A-Za-z0-9]+", " ", cleaned2).strip()
    add(loose)

    # Common MusicBrainz/SXM wording variants. Covers and older titles often
    # differ by colloquial spelling: "Wanna" vs "Want to", "Gonna" vs
    # "Going to". Keep both so tracks like "Do You Wanna Dance?" can find
    # canonical entries stored as "Do You Want to Dance".
    swaps = [
        (r"\bwanna\b", "want to"),
        (r"\bwant to\b", "wanna"),
        (r"\bgonna\b", "going to"),
        (r"\bgoing to\b", "gonna"),
        (r"\bgotta\b", "got to"),
        (r"\bgot to\b", "gotta"),
        (r"\byou gotta\b", "you got to"),
    ]
    for source in list(variants):
        lower = source.lower()
        for pattern, replacement in swaps:
            swapped = re.sub(pattern, replacement, lower, flags=re.I)
            if swapped != lower:
                add(swapped.title())

    # Extra useful variants for common SXM display titles that include leading
    # informal wording. These are still searched with the artist, so they do not
    # become dangerously broad, but they help canonical titles rank correctly.
    words = _metadata_words(cleaned2)
    if len(words) >= 5:
        for marker in ("fight", "love", "dance", "walk", "shake", "dream", "want", "need"):
            if marker in words and words.index(marker) > 0:
                add(" ".join(words[words.index(marker):]))
                break

    return variants


def _metadata_query_word_clause(value: str, field: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", value or "") if len(w) > 1]
    # Keep enough words to be useful without over-constraining long titles.
    words = words[:8]
    if not words:
        return ""
    return " AND ".join(f'{field}:{word}' for word in words)


def _metadata_release_flags(release: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    rg = (release or {}).get("release-group") or {}
    primary = str(rg.get("primary-type") or "").strip().lower()
    secondary = {str(t).strip().lower() for t in (rg.get("secondary-types") or []) if str(t).strip()}
    bad_secondary = {"compilation", "soundtrack", "live", "remix", "dj-mix", "mixtape/street", "demo"}
    return {
        "primary": primary,
        "secondary": secondary,
        "is_album": primary == "album",
        "is_single": primary == "single",
        "is_bad_secondary": bool(secondary & bad_secondary),
        "is_compilation": "compilation" in secondary,
        "is_live": "live" in secondary,
    }


def _metadata_release_year(release: Optional[Dict[str, Any]]) -> int | None:
    date = str((release or {}).get("date") or "")
    try:
        year = int(date[:4])
        if 1900 <= year <= 2035:
            return year
    except Exception:
        pass
    return None


def _metadata_title_mentions_live(title: str) -> bool:
    text = f" {_metadata_clean_text(title)} "
    return any(marker in text for marker in (
        " live ",
        " in concert ",
        " concert version ",
        " live at ",
        " live from ",
        " live in ",
    ))


def _metadata_release_type_rank(release_type: str, requested_title: str = "") -> int:
    """Human-friendly release ordering for metadata search results.

    Prefer the canonical studio album first, then singles, then compilations.
    Live albums are useful only for explicitly live track searches; otherwise
    they are pushed below normal metadata cleanup candidates.
    """
    reltype = str(release_type or "").lower().strip()
    is_live_query = _metadata_title_mentions_live(requested_title)

    is_album = reltype.startswith("album")
    is_single = reltype.startswith("single")
    is_compilation = "compilation" in reltype
    is_live = "live" in reltype
    is_bad_album = any(bad in reltype for bad in ("soundtrack", "remix", "dj-mix", "mixtape/street", "demo"))

    if is_album and not is_compilation and not is_live and not is_bad_album:
        return 0
    if is_single:
        return 1
    if is_album and is_compilation and not is_live:
        return 2
    if is_album and is_live:
        return 3 if is_live_query else 9
    if is_album:
        return 4
    if reltype == "recording":
        return 8
    return 7


def _metadata_release_score(
    recording_score: int,
    requested_artist: str,
    requested_title: str,
    rec_artist: str,
    rec_title: str,
    release: Optional[Dict[str, Any]],
) -> float:
    """Score MusicBrainz release candidates for a human-friendly picker.

    The MusicBrainz search score is only one ingredient. For Jukebox cleanup we
    usually want the original/studio album before singles, compilations, live
    releases, and soundtracks.
    """
    score = max(0.0, min(1.0, (recording_score or 0) / 100.0)) * 0.30
    score += _metadata_artist_similarity(requested_artist, rec_artist) * 0.27
    score += _metadata_similarity(requested_title, rec_title) * 0.20

    if not release:
        return max(0.0, min(1.0, score))

    release_artist = _musicbrainz_release_artist(release, rec_artist)
    score += _metadata_artist_similarity(requested_artist, release_artist) * 0.10

    status = str(release.get("status") or "").lower()
    if status == "official":
        score += 0.04

    flags = _metadata_release_flags(release)
    release_artist_clean = _metadata_clean_text(release_artist)

    # Strongly prefer a clean studio album. This is the common desired answer
    # for cleanup, e.g. Rebel Yell should show the Rebel Yell album before the
    # 1985 single or later compilation/live releases.
    if flags["is_album"] and not flags["is_bad_secondary"]:
        score += 0.28
    elif flags["is_album"]:
        score += 0.08
    elif flags["is_single"]:
        score += 0.02
    elif flags["primary"]:
        score += 0.01

    is_live_query = _metadata_title_mentions_live(requested_title)
    if flags["is_compilation"]:
        score -= 0.28
    if flags["is_live"]:
        score += 0.03 if is_live_query else -0.42
    if flags["is_bad_secondary"] and not (flags["is_compilation"] or flags["is_live"]):
        score -= 0.18

    if release_artist_clean in {"various artists", "various"}:
        score -= 0.30

    # Prefer older/original releases over later reissues when all else is close,
    # but do not let a random early compilation beat a later official album.
    year = _metadata_release_year(release)
    if year:
        if flags["is_album"] and not flags["is_bad_secondary"]:
            score += max(0.0, min(0.06, (2035 - year) / 1200.0))
        else:
            score += max(0.0, min(0.02, (2035 - year) / 3000.0))

    return max(0.0, min(1.0, score))


async def _search_musicbrainz_candidates(artist: str, title: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Find likely album/cover candidates using MusicBrainz + Cover Art Archive.

    This is deliberately user-triggered. Music metadata is not always unique,
    especially for singles, remasters, compilations, and live versions, so the
    frontend presents candidates and lets the user choose.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not title:
        return []

    headers = {
        "User-Agent": "ArchiveXM/1.0 (metadata lookup; local user initiated)",
        "Accept": "application/json",
    }

    query_attempts = []
    seen_queries = set()

    def add_query(query: str):
        query = re.sub(r"\s+", " ", str(query or "").strip())
        if query and query.lower() not in seen_queries:
            seen_queries.add(query.lower())
            query_attempts.append(query)

    title_variants = _metadata_title_variants(title)
    artist_variants = _metadata_artist_variants(artist)
    artist_known = bool(artist_variants) and artist.lower() != "unknown"

    for variant in title_variants:
        quoted_title = variant.replace('"', '\\"')
        if artist_known:
            for artist_variant in artist_variants:
                quoted_artist = artist_variant.replace('"', '\\"')
                add_query(f'artist:"{quoted_artist}" AND recording:"{quoted_title}"')
                add_query(f'artistname:"{quoted_artist}" AND recording:"{quoted_title}"')
        else:
            add_query(f'recording:"{quoted_title}"')

    # Fallback to word-based recording clauses. This catches SXM display titles
    # that differ from canonical MusicBrainz titles, especially XTRA tracks with
    # extra year suffixes or missing punctuation.
    for variant in title_variants:
        word_clause = _metadata_query_word_clause(variant, "recording")
        if not word_clause:
            continue
        if artist_known:
            for artist_variant in artist_variants:
                quoted_artist = artist_variant.replace('"', '\\"')
                add_query(f'artist:"{quoted_artist}" AND {word_clause}')
                add_query(f'artistname:"{quoted_artist}" AND {word_clause}')
        else:
            add_query(word_clause)

    # Last-resort title-only search. Results are still ranked by artist
    # similarity, so the requested artist rises above covers by other artists.
    # This helps cover songs or artist-name variants that MusicBrainz stores
    # differently than SXM.
    for variant in title_variants[:4]:
        quoted_title = variant.replace('"', '\\"')
        add_query(f'recording:"{quoted_title}"')

    async def run_query(client: httpx.AsyncClient, query: str) -> List[Dict[str, Any]]:
        params = {
            "query": query,
            "fmt": "json",
            "limit": "25",
            # Keep this include list conservative. MusicBrainz can reject the
            # entire request with HTTP 400 when unsupported includes are used
            # for the recording endpoint, which made the lookup modal fail
            # instantly instead of trying the next query variant.
            "inc": "artist-credits+releases",
        }
        response = await client.get("https://musicbrainz.org/ws/2/recording/", params=params, headers=headers)
        response.raise_for_status()
        return response.json().get("recordings") or []

    async def hydrate_recording(client: httpx.AsyncClient, recording_id: str) -> Dict[str, Any] | None:
        if not recording_id:
            return None
        params = {
            "fmt": "json",
            # The search response can return only a shallow/short release list.
            # Fetching the recording directly gives a better chance of seeing
            # the original studio album before we rank candidates.
            "inc": "artist-credits+releases+release-groups",
        }
        response = await client.get(f"https://musicbrainz.org/ws/2/recording/{recording_id}", params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None

    async def run_release_query(client: httpx.AsyncClient, query: str) -> List[Dict[str, Any]]:
        params = {
            "query": query,
            "fmt": "json",
            "limit": "10",
            "inc": "artist-credits+release-groups",
        }
        response = await client.get("https://musicbrainz.org/ws/2/release/", params=params, headers=headers)
        response.raise_for_status()
        return response.json().get("releases") or []

    async def hydrate_release(client: httpx.AsyncClient, release_id: str) -> Dict[str, Any] | None:
        if not release_id:
            return None
        params = {
            "fmt": "json",
            "inc": "artist-credits+release-groups+media+recordings",
        }
        response = await client.get(f"https://musicbrainz.org/ws/2/release/{release_id}", params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None

    recordings_by_id: Dict[str, Dict[str, Any]] = {}
    extra_releases_by_id: Dict[str, Dict[str, Any]] = {}
    query_errors: List[str] = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for query in query_attempts:
            try:
                found = await run_query(client, query)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response is not None else "unknown"
                msg = f"status={status} query={query[:140]}"
                query_errors.append(msg)
                print(f"Metadata lookup query skipped: {msg}")
                continue
            except httpx.HTTPError as e:
                msg = f"{type(e).__name__}: {str(e)[:180]} query={query[:140]}"
                query_errors.append(msg)
                print(f"Metadata lookup query skipped: {msg}")
                continue
            except Exception as e:
                msg = f"{type(e).__name__}: {str(e)[:180]} query={query[:140]}"
                query_errors.append(msg)
                print(f"Metadata lookup query skipped: {msg}")
                continue

            for recording in found:
                rid = recording.get("id") or json.dumps(recording, sort_keys=True)[:80]
                if rid not in recordings_by_id:
                    recordings_by_id[rid] = recording
            if len(recordings_by_id) >= 40:
                break

        # Hydrate the best recordings so their full release lists are available
        # for ranking. Keep this small to stay friendly to MusicBrainz.
        for rid in list(recordings_by_id.keys())[:12]:
            try:
                hydrated = await hydrate_recording(client, rid)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response is not None else "unknown"
                print(f"Metadata lookup hydrate skipped: status={status} recording={rid}")
                continue
            except Exception as e:
                print(f"Metadata lookup hydrate skipped: {type(e).__name__}: {str(e)[:160]} recording={rid}")
                continue
            if hydrated:
                base = recordings_by_id.get(rid) or {}
                # Preserve MusicBrainz search relevance score; direct lookup does
                # not include the search score.
                if "score" not in hydrated and "score" in base:
                    hydrated["score"] = base.get("score")
                recordings_by_id[rid] = hydrated

        # Recording searches can still miss the canonical studio album,
        # especially when MusicBrainz has several similarly named recordings or
        # a long list of singles/compilations. Add a small targeted release
        # search for same-named official albums, then verify that the release
        # contains a matching recording before presenting it as a candidate.
        release_queries: List[str] = []
        def add_release_query(query: str):
            if query and query not in release_queries:
                release_queries.append(query)

        for variant in title_variants[:4]:
            quoted_title = variant.replace('"', '\"')
            if artist_known:
                for artist_variant in artist_variants[:4]:
                    quoted_artist = artist_variant.replace('"', '\"')
                    add_release_query(f'artist:"{quoted_artist}" AND release:"{quoted_title}" AND type:album AND status:official')
                    add_release_query(f'artistname:"{quoted_artist}" AND release:"{quoted_title}" AND type:album')
            else:
                add_release_query(f'release:"{quoted_title}" AND type:album AND status:official')

        for query in release_queries[:10]:
            try:
                found_releases = await run_release_query(client, query)
            except httpx.HTTPStatusError as e:
                status = e.response.status_code if e.response is not None else "unknown"
                print(f"Metadata release lookup skipped: status={status} query={query[:140]}")
                continue
            except Exception as e:
                print(f"Metadata release lookup skipped: {type(e).__name__}: {str(e)[:160]} query={query[:140]}")
                continue

            for release in found_releases[:6]:
                rid = release.get("id")
                if not rid or rid in extra_releases_by_id:
                    continue
                flags = _metadata_release_flags(release)
                if not (flags["is_album"] and not flags["is_bad_secondary"]):
                    continue
                release_artist = _musicbrainz_release_artist(release, artist)
                if artist_known and _metadata_artist_similarity(artist, release_artist) < 0.72:
                    continue
                if _metadata_similarity(title, release.get("title") or "") < 0.72:
                    continue
                try:
                    hydrated_release = await hydrate_release(client, rid)
                except Exception as e:
                    print(f"Metadata release hydrate skipped: {type(e).__name__}: {str(e)[:160]} release={rid}")
                    continue
                if hydrated_release:
                    hydrated_release.setdefault("score", release.get("score") or 100)
                    extra_releases_by_id[rid] = hydrated_release
            if len(extra_releases_by_id) >= 8:
                break

    if not recordings_by_id and query_errors:
        print(f"Metadata lookup found no usable MusicBrainz responses; skipped {len(query_errors)} failed queries")

    candidates: List[Dict[str, Any]] = []
    seen = set()

    for recording in recordings_by_id.values():
        rec_title = recording.get("title") or title
        rec_artist = _musicbrainz_artist_credit(recording) or artist or "Unknown"
        try:
            mb_score = int(recording.get("score") or 0)
        except Exception:
            mb_score = 0

        releases = recording.get("releases") or []
        if not releases:
            confidence = _metadata_release_score(mb_score, artist, title, rec_artist, rec_title, None)
            candidate = {
                "provider": "musicbrainz",
                "recording_id": recording.get("id"),
                "release_id": None,
                "artist": rec_artist,
                "title": rec_title,
                "album": "",
                "year": "",
                "cover_url": None,
                "confidence": round(confidence, 3),
                "release_type": "Recording",
                "label": f"{rec_artist} - {rec_title}",
            }
            key = _candidate_key(candidate)
            if key not in seen:
                seen.add(key)
                candidates.append(candidate)
            continue

        for release in releases[:20]:
            release_id = release.get("id")
            album = release.get("title") or ""
            date = str(release.get("date") or "")
            year = date[:4] if date else ""
            rg = release.get("release-group") or {}
            primary_type = rg.get("primary-type") or ""
            secondary_types = rg.get("secondary-types") or []
            release_type_parts = []
            if primary_type:
                release_type_parts.append(primary_type)
            release_type_parts.extend([str(t) for t in secondary_types if t])
            release_type = ", ".join(release_type_parts) or "Release"
            confidence = _metadata_release_score(mb_score, artist, title, rec_artist, rec_title, release)

            candidate = {
                "provider": "musicbrainz",
                "recording_id": recording.get("id"),
                "release_id": release_id,
                "artist": rec_artist,
                "title": rec_title,
                "album": album,
                "year": year,
                "cover_url": f"https://coverartarchive.org/release/{release_id}/front-250" if release_id else None,
                "confidence": round(confidence, 3),
                "release_type": release_type,
                "label": f"{album or rec_title}" + (f" ({year})" if year else "") + (f" · {release_type}" if release_type else ""),
            }
            key = _candidate_key(candidate)
            if key not in seen:
                seen.add(key)
                candidates.append(candidate)

    for release in extra_releases_by_id.values():
        release_id = release.get("id")
        album = release.get("title") or ""
        date = str(release.get("date") or "")
        year = date[:4] if date else ""
        release_artist = _musicbrainz_release_artist(release, artist or "Unknown")
        matched_recording = None
        for medium in release.get("media") or []:
            for track_item in medium.get("tracks") or []:
                rec = track_item.get("recording") or {}
                rec_title = rec.get("title") or track_item.get("title") or ""
                if _metadata_similarity(title, rec_title) >= 0.72:
                    matched_recording = rec
                    break
            if matched_recording:
                break
        if not matched_recording:
            continue

        rg = release.get("release-group") or {}
        primary_type = rg.get("primary-type") or ""
        secondary_types = rg.get("secondary-types") or []
        release_type_parts = []
        if primary_type:
            release_type_parts.append(primary_type)
        release_type_parts.extend([str(t) for t in secondary_types if t])
        release_type = ", ".join(release_type_parts) or "Release"
        confidence = _metadata_release_score(
            int(release.get("score") or 100),
            artist,
            title,
            _musicbrainz_artist_credit(matched_recording) or release_artist,
            matched_recording.get("title") or title,
            release,
        )
        # Same-named verified studio albums are usually the canonical result;
        # give them enough confidence to appear in the first page without
        # bypassing the normal release-type ordering.
        confidence = max(confidence, 0.96)
        candidate = {
            "provider": "musicbrainz",
            "recording_id": matched_recording.get("id"),
            "release_id": release_id,
            "artist": release_artist,
            "title": matched_recording.get("title") or title,
            "album": album,
            "year": year,
            "cover_url": f"https://coverartarchive.org/release/{release_id}/front-250" if release_id else None,
            "confidence": round(confidence, 3),
            "release_type": release_type,
            "label": f"{album or title}" + (f" ({year})" if year else "") + (f" · {release_type}" if release_type else ""),
        }
        key = _candidate_key(candidate)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)

    def sort_key(c: Dict[str, Any]):
        album = str(c.get("album") or "")
        reltype = str(c.get("release_type") or "")
        has_album = 0 if album else 1
        year = 9999
        try:
            y = int(str(c.get("year") or "")[:4])
            if 1900 <= y <= 2035:
                year = y
        except Exception:
            pass
        # Explicit user-facing order:
        # Album, Single, Album/Compilation, then Album/Live only for live
        # track queries. Confidence is still used inside each bucket.
        return (
            _metadata_release_type_rank(reltype, title),
            has_album,
            -float(c.get("confidence") or 0),
            year,
            str(c.get("album") or "").lower(),
        )

    candidates.sort(key=sort_key)
    return candidates[:limit]

async def _save_track_cover_from_url(db: DBSession, track: LocalTrack, cover_url: str | None) -> str | None:
    cover_url = (cover_url or "").strip()
    if not cover_url:
        return None
    if not cover_url.lower().startswith(("http://", "https://")):
        return None

    headers = {
        "User-Agent": "ArchiveXM/1.0 (cover art lookup; local user initiated)",
        "Accept": "image/*,*/*",
    }
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        response = await client.get(cover_url, headers=headers)
    if response.status_code >= 400 or not response.content:
        raise Exception(f"Cover fetch failed status={response.status_code}")

    content_type = (response.headers.get("content-type") or "").lower()
    suffix = ".jpg"
    if "png" in content_type:
        suffix = ".png"
    elif "webp" in content_type:
        suffix = ".webp"
    elif "jpeg" in content_type or "jpg" in content_type:
        suffix = ".jpg"
    else:
        path_suffix = Path(str(response.url).split("?", 1)[0]).suffix.lower()
        if path_suffix in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = path_suffix

    cover_dir = _download_path(db) / ".archivexm" / "covers"
    cover_dir.mkdir(parents=True, exist_ok=True)
    cover_path = cover_dir / _safe_cover_filename(track.id, suffix)
    cover_path.write_bytes(response.content)
    track.cover_art_path = str(cover_path)
    return str(cover_path)


def _safe_cover_filename(track_id: int, suffix: str) -> str:
    suffix = (suffix or ".jpg").lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    return f"track_{int(track_id)}{suffix}"


def _is_url(value: str | None) -> bool:
    return bool(value and str(value).strip().lower().startswith(("http://", "https://")))


def _playlist_custom_cover_path(playlist: Playlist) -> Path | None:
    cover = (playlist.cover_image or "").strip()
    if not cover or _is_url(cover):
        return None
    path = Path(cover)
    return path if path.exists() else None


def _playlist_fallback_cover_track(db: DBSession, playlist_id: int) -> LocalTrack | None:
    return (
        db.query(LocalTrack)
        .join(PlaylistTrack, PlaylistTrack.track_id == LocalTrack.id)
        .filter(PlaylistTrack.playlist_id == playlist_id)
        .filter(LocalTrack.cover_art_path.isnot(None))
        .order_by(PlaylistTrack.position.asc())
        .first()
    )


def _playlist_cover_public_url(db: DBSession, playlist: Playlist) -> str:
    cover = (playlist.cover_image or "").strip()
    if _is_url(cover):
        return cover

    base = _library_base_url(db)
    if cover and Path(cover).exists():
        return f"{base}/api/library/playlists/{playlist.id}/cover"

    fallback = _playlist_fallback_cover_track(db, playlist.id)
    if fallback and fallback.cover_art_path and Path(fallback.cover_art_path).exists():
        return f"{base}/api/library/files/{fallback.id}/cover"

    return ""


def _m3u_for_tracks(db: DBSession, tracks, group_title: str) -> str:
    base = _library_base_url(db)
    lines = ["#EXTM3U"]
    for track in tracks:
        name = _track_display_name(track)
        duration = int(track.duration_seconds or -1)
        attrs = {
            "tvg-id": f"archivexm-{track.id}",
            "tvg-name": name,
            "group-title": group_title,
        }
        if track.cover_art_path and Path(track.cover_art_path).exists():
            attrs["tvg-logo"] = f"{base}/api/library/files/{track.id}/cover"
        attr_text = " ".join(f'{key}="{_m3u_escape(value)}"' for key, value in attrs.items())
        lines.append(f"#EXTINF:{duration} {attr_text},{name}")
        lines.append(_track_play_url(db, track))
    lines.append("")
    return "\n".join(lines)




def _playlist_tracks(db: DBSession, playlist_id: int):
    return db.query(PlaylistTrack, LocalTrack).join(
        LocalTrack, PlaylistTrack.track_id == LocalTrack.id
    ).filter(
        PlaylistTrack.playlist_id == playlist_id
    ).order_by(PlaylistTrack.position).all()


def _hls_for_tracks(db: DBSession, tracks, playlist_name: str) -> str:
    """Return a simple HLS VOD playlist for M3You-style players.

    The regular .m3u endpoint is still best when adding a playlist as its own
    source. For a playlist-as-channel inside the main ArchiveXM M3U, many IPTV
    players expect the URL to be playable media, not another nested M3U. This
    HLS wrapper gives them a media-playlist URL while each item still streams
    from ArchiveXM's local file endpoint.
    """
    base = _library_base_url(db)
    durations = []
    for track in tracks:
        try:
            durations.append(float(track.duration_seconds or 0) or 1.0)
        except Exception:
            durations.append(1.0)

    target_duration = max(1, int(max(durations, default=1.0) + 0.999))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        f"#EXT-X-SESSION-DATA:DATA-ID=\"com.archivexm.playlist\",VALUE=\"{_m3u_escape(playlist_name)}\"",
    ]

    for track, duration in zip(tracks, durations):
        name = _track_display_name(track)
        lines.append(f"#EXTINF:{duration:.3f},{_m3u_plain(name)}")
        lines.append(_track_play_url(db, track))

    lines.append("#EXT-X-ENDLIST")
    lines.append("")
    return "\n".join(lines)

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


@router.get("/downloads.m3u")
async def get_downloads_m3u(db: DBSession = Depends(get_db)):
    """
    Export all downloaded/library tracks as an M3U playlist for external players.
    """
    tracks = db.query(LocalTrack).order_by(LocalTrack.artist.asc(), LocalTrack.title.asc(), LocalTrack.filename.asc()).all()
    return PlainTextResponse(
        _m3u_for_tracks(db, tracks, "ArchiveXM Downloads"),
        media_type="audio/x-mpegurl; charset=utf-8",
        headers={"Content-Disposition": "inline; filename=archivexm-downloads.m3u"},
    )


@router.get("/files/{track_id}/metadata")
async def get_file_metadata(track_id: int, db: DBSession = Depends(get_db)):
    """
    Public-friendly metadata endpoint for a saved local track.
    """
    track = db.query(LocalTrack).filter(LocalTrack.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    base = _library_base_url(db)
    return {
        "id": track.id,
        "title": track.title or track.filename,
        "artist": track.artist or "Unknown",
        "album": track.album,
        "genre": track.genre,
        "durationSeconds": track.duration_seconds,
        "durationMs": int((track.duration_seconds or 0) * 1000) if track.duration_seconds else None,
        "imageUrl": f"{base}/api/library/files/{track.id}/cover" if track.cover_art_path else None,
        "streamUrl": _track_play_url(db, track),
        "isLocalTrack": True,
    }


@router.get("/files/{track_id}/play")
async def play_file_alias(track_id: int, request: Request, db: DBSession = Depends(get_db)):
    """
    External-player friendly alias for local track streaming.
    """
    return await stream_track(track_id, request, db)


@router.get("/files/{track_id}/play.{ext}")
async def play_file_alias_with_extension(track_id: int, ext: str, request: Request, db: DBSession = Depends(get_db)):
    """
    Extension-bearing alias for stricter IPTV players.

    VLC is happy with /play, but some players decide how to initialize the
    decoder from the URL suffix before trusting Content-Type. The ext value is
    intentionally not used for lookup; the database track id remains the source
    of truth.
    """
    return await stream_track(track_id, request, db)


@router.get("/files/{track_id}/cover")
async def cover_file_alias(track_id: int, db: DBSession = Depends(get_db)):
    """
    External-player friendly alias for local track cover art.
    """
    return await get_track_cover(track_id, db)



@router.post("/tracks/bulk-delete")
async def bulk_delete_tracks(
    request: BulkDeleteTracksRequest,
    db: DBSession = Depends(get_db)
):
    """
    Remove multiple tracks from the Jukebox, all playlists, and optionally delete files.
    This is used by the Jukebox bulk-select UI.
    """
    track_ids = []
    seen = set()
    for track_id in request.track_ids or []:
        try:
            track_id = int(track_id)
        except Exception:
            continue
        if track_id > 0 and track_id not in seen:
            seen.add(track_id)
            track_ids.append(track_id)

    if not track_ids:
        raise HTTPException(status_code=400, detail="No valid track IDs supplied")

    tracks = db.query(LocalTrack).filter(LocalTrack.id.in_(track_ids)).all()
    found_ids = {track.id for track in tracks}
    missing_ids = [track_id for track_id in track_ids if track_id not in found_ids]

    affected_playlist_ids = [
        row[0] for row in db.query(PlaylistTrack.playlist_id)
        .filter(PlaylistTrack.track_id.in_(track_ids))
        .distinct()
        .all()
    ]

    db.query(PlaylistTrack).filter(PlaylistTrack.track_id.in_(track_ids)).delete(synchronize_session=False)

    deleted_files = []
    file_errors = []
    if request.delete_files:
        for track in tracks:
            try:
                file_path = Path(track.file_path)
                if file_path.exists():
                    file_path.unlink()
                    deleted_files.append(str(file_path))
            except Exception as e:
                file_errors.append({"track_id": track.id, "error": str(e)})

    for track in tracks:
        db.delete(track)

    for playlist_id in affected_playlist_ids:
        playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
        if playlist:
            playlist.track_count = db.query(PlaylistTrack).filter(
                PlaylistTrack.playlist_id == playlist_id
            ).count()

    db.commit()

    return {
        "success": True,
        "deleted": len(tracks),
        "deleted_ids": sorted(found_ids),
        "missing_ids": missing_ids,
        "affected_playlists": affected_playlist_ids,
        "deleted_files": deleted_files,
        "file_errors": file_errors,
    }


@router.get("/metadata/search")
async def search_metadata_candidates(
    artist: Optional[str] = None,
    title: Optional[str] = None,
):
    """
    Search external metadata candidates directly by artist/title.

    This helper is mostly for diagnostics and frontend recovery paths; the
    Jukebox UI usually calls the track-specific endpoint below.
    """
    search_artist = _first_text(artist, default="")
    search_title = _first_text(title, default="")
    if not search_title:
        raise HTTPException(status_code=400, detail="Track title is required for metadata search")

    try:
        candidates = await _search_musicbrainz_candidates(search_artist, search_title)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"MusicBrainz lookup failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Metadata lookup failed: {e}")

    return {
        "query": {"artist": search_artist, "title": search_title},
        "candidates": candidates,
    }


@router.get("/tracks/{track_id}/metadata/search")
async def search_track_metadata(
    track_id: int,
    artist: Optional[str] = None,
    title: Optional[str] = None,
    db: DBSession = Depends(get_db),
):
    """
    Search external metadata candidates for a local Jukebox track.

    Uses MusicBrainz for release/album candidates and Cover Art Archive URLs for
    cover previews. This is user-triggered so ambiguous matches can be reviewed
    before applying.
    """
    track = db.query(LocalTrack).filter(LocalTrack.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    search_artist = _first_text(artist, track.artist, default="")
    search_title = _first_text(title, track.title, track.filename, default="")
    if not search_title:
        raise HTTPException(status_code=400, detail="Track title is required for metadata search")

    try:
        candidates = await _search_musicbrainz_candidates(search_artist, search_title)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"MusicBrainz lookup failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Metadata lookup failed: {e}")

    return {
        "track_id": track.id,
        "query": {"artist": search_artist, "title": search_title},
        "candidates": candidates,
    }


@router.post("/tracks/{track_id}/metadata/apply")
async def apply_track_metadata(
    track_id: int,
    request: MetadataApplyRequest,
    db: DBSession = Depends(get_db),
):
    """
    Apply user-selected metadata to a local Jukebox track.
    """
    track = db.query(LocalTrack).filter(LocalTrack.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    if request.artist is not None and str(request.artist).strip():
        track.artist = str(request.artist).strip()
    if request.title is not None and str(request.title).strip():
        track.title = str(request.title).strip()
    if request.album is not None:
        track.album = str(request.album).strip() or None

    cover_error = None
    if request.cover_url:
        try:
            await _save_track_cover_from_url(db, track, request.cover_url)
        except Exception as e:
            cover_error = str(e)
            print(f"Metadata apply: cover download failed for track {track.id}: {e}")

    db.commit()
    db.refresh(track)

    return {
        "success": True,
        "track": TrackResponse.from_orm(track),
        "cover_error": cover_error,
        "metadata": {
            "provider": request.provider,
            "recording_id": request.recording_id,
            "release_id": request.release_id,
            "year": request.year,
        },
    }


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



@router.post("/playlists/{playlist_id}/capture-current")
async def capture_current_to_playlist(
    playlist_id: int,
    request: CaptureCurrentRequest,
    background_tasks: BackgroundTasks,
    db: DBSession = Depends(get_db),
):
    """
    Queue the currently playing ArchiveXM Live/XTRA track for download and add
    it to a Jukebox playlist when the download completes.

    This is intended for external players like M3You. M3You should show a +
    button while a Live or XTRA ArchiveXM track is playing, let the user choose
    a playlist, and POST the current channel/track metadata here.
    """
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    channel = db.query(Channel).filter(Channel.channel_id == request.channel_id).first()

    track_payload = await _resolve_capture_track(session.bearer_token, request)
    if not track_payload.get("title") or track_payload.get("title") == "Unknown":
        raise HTTPException(status_code=400, detail="Could not resolve current track title")
    if not track_payload.get("artist"):
        track_payload["artist"] = "Unknown"
    if not track_payload.get("timestamp_utc"):
        raise HTTPException(
            status_code=400,
            detail="Could not resolve track start time. Send track.timestamp_utc or started_at_ms, or try again while ArchiveXM live metadata is available.",
        )
    if not track_payload.get("duration_ms"):
        # A conservative fallback. The downloader will still try to refine live
        # duration from the next timed schedule item when possible.
        track_payload["duration_ms"] = 240000

    config = db.query(Config).filter(Config.key == "download_path").first()
    download_path = config.value if config else os.getenv("DOWNLOAD_PATH", "/downloads")

    download = Download(
        channel_id=request.channel_id,
        channel_name=channel.name if channel else "Unknown",
        artist=track_payload.get("artist") or "Unknown",
        title=track_payload.get("title") or "Unknown",
        album=track_payload.get("album") or None,
        duration_ms=int(track_payload.get("duration_ms") or 0),
        timestamp_utc=track_payload.get("timestamp_utc"),
        status="pending",
    )
    db.add(download)
    db.commit()
    db.refresh(download)

    is_xtra_capture = bool(track_payload.get("is_xtra_capture")) or (request.channel_type or "").strip().lower() in {"channel-xtra", "xtra"}

    if not is_xtra_capture:
        # For external "capture current" requests, never save a clipped tail of a
        # song. If the SiriusXM/HLS buffer no longer contains the real start boundary,
        # the download service should fail the job instead of importing a partial file.
        track_payload["require_full_window"] = True

        # Preserve the station-history/API duration for captures. Normal downloads
        # can stop at the next raw metadata boundary to avoid DJ bleed, but capture
        # bookmarks were losing the last few seconds because those boundaries can be
        # early. The user's configured tail pad still applies to the audio segments.
        track_payload["preserve_duration"] = True
    else:
        # XTRA captures use the active FULL XTRA media playlist and do not have
        # linear replay-window boundaries. Do not apply the live DVR full-window
        # guard or wait-until-song-end logic.
        track_payload["is_xtra_capture"] = True
        track_payload["preserve_duration"] = True

    background_tasks.add_task(
        _download_capture_and_add_to_playlist,
        download.id,
        request.channel_id,
        track_payload,
        download_path,
        playlist_id,
        session.bearer_token,
    )

    return {
        "success": True,
        "message": "Queued current track for download and playlist add",
        "playlist_id": playlist_id,
        "playlist_name": playlist.name,
        "download_id": download.id,
        "channel_id": request.channel_id,
        "channel_type": request.channel_type,
        "track": {
            "artist": track_payload.get("artist"),
            "title": track_payload.get("title"),
            "album": track_payload.get("album"),
            "duration_ms": track_payload.get("duration_ms"),
            "timestamp_utc": track_payload.get("timestamp_utc"),
            "image_url": track_payload.get("image_url"),
        },
    }


@router.get("/playlists/{playlist_id}.m3u")
async def get_playlist_m3u(playlist_id: int, db: DBSession = Depends(get_db)):
    """
    Export one Jukebox playlist as a normal M3U playlist for external players.
    """
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    playlist_tracks = _playlist_tracks(db, playlist_id)
    tracks = [track for _, track in playlist_tracks]
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", playlist.name or f"playlist_{playlist_id}").strip("_") or f"playlist_{playlist_id}"
    return PlainTextResponse(
        _m3u_for_tracks(db, tracks, f"ArchiveXM - {playlist.name}"),
        media_type="audio/x-mpegurl; charset=utf-8",
        headers={"Content-Disposition": f"inline; filename={safe_name}.m3u"},
    )


@router.get("/playlists/{playlist_id}/channel.m3u8")
async def get_playlist_channel_hls(playlist_id: int, db: DBSession = Depends(get_db)):
    """
    Export a Jukebox playlist as a simple HLS VOD media playlist.

    This is used when a Jukebox playlist is listed as a virtual channel inside
    the main ArchiveXM M3U. M3You is more likely to treat this as playable
    media than a nested .m3u playlist URL.
    """
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    playlist_tracks = _playlist_tracks(db, playlist_id)
    tracks = [track for _, track in playlist_tracks]
    if not tracks:
        raise HTTPException(status_code=404, detail="Playlist has no tracks")

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", playlist.name or f"playlist_{playlist_id}").strip("_") or f"playlist_{playlist_id}"
    return PlainTextResponse(
        _hls_for_tracks(db, tracks, playlist.name or f"Playlist {playlist_id}"),
        media_type="application/vnd.apple.mpegurl; charset=utf-8",
        headers={
            "Content-Disposition": f"inline; filename={safe_name}.m3u8",
            "Cache-Control": "no-store",
        },
    )


@router.get("/playlists/{playlist_id}/channel.m3u")
async def get_playlist_channel_hls_alt(playlist_id: int, db: DBSession = Depends(get_db)):
    # Some parsers are happier discovering a .m3u URL even when the content is
    # HLS. Keep this alias for compatibility.
    return await get_playlist_channel_hls(playlist_id, db)




@router.get("/playlists/{playlist_id}/cover")
async def get_playlist_cover(playlist_id: int, db: DBSession = Depends(get_db)):
    """
    Return a playlist cover image. Custom URL covers redirect; uploaded covers
    serve from disk; otherwise the first track cover is used as a fallback.
    """
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    cover = (playlist.cover_image or "").strip()
    if _is_url(cover):
        return RedirectResponse(cover)

    custom_path = _playlist_custom_cover_path(playlist)
    if custom_path:
        return FileResponse(str(custom_path))

    fallback = _playlist_fallback_cover_track(db, playlist_id)
    if fallback and fallback.cover_art_path and Path(fallback.cover_art_path).exists():
        return FileResponse(fallback.cover_art_path)

    raise HTTPException(status_code=404, detail="No playlist cover available")


@router.post("/playlists/{playlist_id}/cover-url")
async def set_playlist_cover_url(
    playlist_id: int,
    request: PlaylistCoverUpdate,
    db: DBSession = Depends(get_db),
):
    """Set a playlist cover to a remote image URL."""
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    cover = (request.cover_image or "").strip()
    if cover and not _is_url(cover):
        raise HTTPException(status_code=400, detail="Cover image must be an http:// or https:// URL")

    playlist.cover_image = cover or None
    db.commit()
    db.refresh(playlist)

    return {"success": True, "cover_image": playlist.cover_image, "cover_url": _playlist_cover_public_url(db, playlist)}


@router.post("/playlists/{playlist_id}/cover-upload")
async def upload_playlist_cover(
    playlist_id: int,
    file: UploadFile = File(...),
    db: DBSession = Depends(get_db),
):
    """Upload a local playlist cover image into the downloads folder."""
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    content_type = (file.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded cover must be an image")

    original_suffix = Path(file.filename or "").suffix.lower()
    if original_suffix not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if "png" in content_type:
            original_suffix = ".png"
        elif "webp" in content_type:
            original_suffix = ".webp"
        elif "gif" in content_type:
            original_suffix = ".gif"
        else:
            original_suffix = ".jpg"

    cover_dir = _download_path(db) / ".playlist_covers"
    cover_dir.mkdir(parents=True, exist_ok=True)
    target = cover_dir / f"playlist_{playlist_id}{original_suffix}"

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded cover is empty")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Uploaded cover is too large")

    target.write_bytes(data)
    playlist.cover_image = str(target)
    db.commit()
    db.refresh(playlist)

    return {"success": True, "cover_image": playlist.cover_image, "cover_url": _playlist_cover_public_url(db, playlist)}


@router.delete("/playlists/{playlist_id}/cover")
async def clear_playlist_cover(playlist_id: int, db: DBSession = Depends(get_db)):
    """Clear a custom playlist cover and fall back to the first track cover."""
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    old_path = _playlist_custom_cover_path(playlist)
    playlist.cover_image = None
    db.commit()

    if old_path:
        try:
            old_path.unlink()
        except Exception:
            pass

    return {"success": True, "cover_image": None, "cover_url": _playlist_cover_public_url(db, playlist)}


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
        "cover_url": _playlist_cover_public_url(db, playlist),
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



def _extract_track_id_from_remove_request(request: RemoveCurrentPlaylistRequest) -> int | None:
    if request.track_id is not None:
        try:
            return int(request.track_id)
        except Exception:
            return None

    candidates = [request.track_url, request.url, request.tvg_id]

    if request.track:
        for key in ("track_id", "id", "local_track_id", "localTrackId", "tvg_id", "url", "track_url"):
            value = request.track.get(key)
            if value not in (None, ""):
                candidates.append(str(value))

    patterns = [
        r"archivexm-track-(\d+)",
        r"/api/library/files/(\d+)/(?:play|metadata|cover)(?:\.[A-Za-z0-9]+)?",
        r"/library/files/(\d+)/(?:play|metadata|cover)(?:\.[A-Za-z0-9]+)?",
        r"/api/library/tracks/(\d+)/(?:stream|cover)(?:\.[A-Za-z0-9]+)?",
        r"/library/tracks/(\d+)/(?:stream|cover)(?:\.[A-Za-z0-9]+)?",
        r"/api/library/tracks/(\d+)(?:\b|$)",
        r"/library/tracks/(\d+)(?:\b|$)",
    ]

    for candidate in candidates:
        if not candidate:
            continue
        text = str(candidate)
        if text.isdigit():
            return int(text)
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    return int(match.group(1))
                except Exception:
                    continue

    return None


def _compact_playlist_positions(db: DBSession, playlist_id: int):
    rows = db.query(PlaylistTrack).filter(
        PlaylistTrack.playlist_id == playlist_id
    ).order_by(PlaylistTrack.position, PlaylistTrack.id).all()

    for index, row in enumerate(rows, 1):
        if row.position != index:
            row.position = index

    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if playlist:
        playlist.track_count = len(rows)



@router.post("/playlists/{playlist_id}/tracks/bulk-remove")
async def bulk_remove_tracks_from_playlist(
    playlist_id: int,
    request: BulkRemoveFromPlaylistRequest,
    db: DBSession = Depends(get_db)
):
    """
    Remove multiple tracks from one playlist only. Does not delete files or library rows.
    """
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    track_ids = []
    seen = set()
    for track_id in request.track_ids or []:
        try:
            track_id = int(track_id)
        except Exception:
            continue
        if track_id > 0 and track_id not in seen:
            seen.add(track_id)
            track_ids.append(track_id)

    if not track_ids:
        raise HTTPException(status_code=400, detail="No valid track IDs supplied")

    deleted = db.query(PlaylistTrack).filter(
        PlaylistTrack.playlist_id == playlist_id,
        PlaylistTrack.track_id.in_(track_ids)
    ).delete(synchronize_session=False)

    playlist.track_count = db.query(PlaylistTrack).filter(
        PlaylistTrack.playlist_id == playlist_id
    ).count()
    db.commit()

    return {
        "success": True,
        "removed": deleted,
        "playlist_id": playlist_id,
        "track_ids": track_ids,
        "track_count": playlist.track_count,
    }


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



@router.post("/playlists/{playlist_id}/remove-current")
async def remove_current_track_from_playlist(
    playlist_id: int,
    request: RemoveCurrentPlaylistRequest,
    db: DBSession = Depends(get_db)
):
    """
    Remove the currently playing ArchiveXM playlist/VOD item from a playlist.

    This is intended for external players such as M3You. They can pass either
    track_id directly or any URL/tvg-id that contains the LocalTrack id, such as:
      /api/library/files/91/play.m4a
      /api/library/files/91/metadata
      archivexm-track-91

    This removes the song from the playlist only. It does not delete the local
    audio file or remove the song from the Jukebox library.
    """
    playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    track_id = _extract_track_id_from_remove_request(request)
    if not track_id:
        raise HTTPException(status_code=400, detail="track_id or a recognizable ArchiveXM track URL is required")

    track = db.query(LocalTrack).filter(LocalTrack.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    query = db.query(PlaylistTrack).filter(
        PlaylistTrack.playlist_id == playlist_id,
        PlaylistTrack.track_id == track_id
    )

    removed = 0
    if request.remove_all:
        removed = query.count()
        query.delete(synchronize_session=False)
    else:
        pt = query.order_by(PlaylistTrack.position, PlaylistTrack.id).first()
        if pt:
            db.delete(pt)
            removed = 1

    if removed <= 0:
        raise HTTPException(status_code=404, detail="Track not in playlist")

    db.flush()
    _compact_playlist_positions(db, playlist_id)
    db.commit()

    return {
        "success": True,
        "message": "Track removed from playlist",
        "playlist_id": playlist_id,
        "playlist_name": playlist.name,
        "track_id": track_id,
        "removed": removed,
        "track": {
            "id": track.id,
            "artist": track.artist,
            "title": track.title,
            "album": track.album,
            "filename": track.filename,
        },
        "track_count": playlist.track_count,
    }

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
