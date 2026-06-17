"""
Download Service - Download and process tracks from DVR buffer
"""
import asyncio
import subprocess
import os
import tempfile
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
import httpx
import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

from services.hls_service import HLSService
from services.sxm_api import SiriusXMAPI


class DownloadService:
    """Handle track downloads from DVR buffer"""
    
    def __init__(self, bearer_token: str):
        self.bearer_token = bearer_token
        self.hls_service = HLSService(bearer_token)
        self.api = SiriusXMAPI(bearer_token)

    def _get_download_tail_pad_seconds(self, db=None) -> float:
        """Return extra seconds to keep after the next SXM metadata boundary.

        The raw metadata boundary prevents long DJ/talk bleed. A tiny pad keeps
        natural fades from sounding clipped when SXM timestamps are slightly
        early. Clamped to a safe range so this cannot recreate long downloads.
        """
        default_value = os.getenv("DOWNLOAD_TAIL_PAD_SECONDS", "2.0")
        value = default_value

        if db is not None:
            try:
                from database import Config
                item = db.query(Config).filter(Config.key == "download_tail_pad_seconds").first()
                if item and item.value not in (None, ""):
                    value = item.value
            except Exception:
                value = default_value

        try:
            pad = float(value)
        except Exception:
            pad = 2.0

        # Keep this intentionally conservative. 0 disables padding, 5 is the
        # upper bound to avoid pulling in whole DJ/talk breaks.
        if pad < 0:
            pad = 0.0
        if pad > 5:
            pad = 5.0
        return pad

    def _apply_tail_pad_ms(self, duration_ms: int, db=None, log_prefix: str = "   " ) -> int:
        pad_seconds = self._get_download_tail_pad_seconds(db)
        if pad_seconds <= 0:
            return duration_ms
        padded_ms = duration_ms + int(pad_seconds * 1000)
        print(f"{log_prefix}Tail pad: +{pad_seconds:.1f}s -> {padded_ms/1000:.1f}s")
        return padded_ms
    

    def _extend_segments_for_tail_pad(
        self,
        track_segments: List[Dict],
        all_segments: List[Dict],
        db=None,
        log_prefix: str = "   ",
    ) -> List[Dict]:
        """Ensure padding has actual audio available after the selected window.

        The normal HLS segment filter can stop on the same last segment even when
        the trim duration is padded. If the padded tail falls into the next HLS
        segment, ffmpeg cannot keep those extra seconds unless we also download
        that following segment. Append a couple of following segments and let
        ffmpeg's exact -t trim decide the final cut.
        """
        if not track_segments or not all_segments:
            return track_segments

        pad_seconds = self._get_download_tail_pad_seconds(db)
        if pad_seconds <= 0:
            return track_segments

        last = track_segments[-1]
        last_url = last.get("url")
        last_index = None

        for idx, segment in enumerate(all_segments):
            if segment is last or (last_url and segment.get("url") == last_url):
                last_index = idx
                break

        if last_index is None:
            return track_segments

        extended = list(track_segments)
        seen_urls = {seg.get("url") for seg in extended if seg.get("url")}

        # Add enough following audio for the pad. SiriusXM HLS segments are often
        # around 9-10 seconds, so one segment is usually enough, but two keeps
        # this robust for unusual segment durations. ffmpeg still trims exactly.
        added = 0
        added_duration = 0.0
        for segment in all_segments[last_index + 1:]:
            url = segment.get("url")
            if url and url in seen_urls:
                continue
            extended.append(segment)
            if url:
                seen_urls.add(url)
            added += 1
            try:
                added_duration += float(segment.get("duration") or 0)
            except Exception:
                pass
            if added >= 2 or added_duration >= pad_seconds + 2.0:
                break

        if added:
            print(f"{log_prefix}Tail pad audio: appended {added} following HLS segment(s) so padding can be kept")

        return extended

    async def download_track(
        self,
        download_id: int,
        channel_id: str,
        track: Dict,
        download_path: str,
        next_track_timestamp: str = None,
        playlist_id: Optional[int] = None,
        playlist_name: Optional[str] = None,
        create_playlist_name: Optional[str] = None,
        **kwargs
    ) -> bool:
        """
        Download a single track from DVR buffer
        
        Steps:
        1. Get HLS playlist with all segments
        2. Filter segments for track time window
        3. Download and decrypt segments
        4. Concatenate into single file
        5. Add metadata and cover art
        
        Args:
            next_track_timestamp: If provided, use this to calculate exact duration
        """
        from database import SessionLocal, Download
        
        db = SessionLocal()
        download_record = db.query(Download).filter(Download.id == download_id).first()
        
        try:
            print(f"📥 Starting download: {track['artist']} - {track['title']}")
            
            # Update status
            download_record.status = "downloading"
            db.commit()
            
            preserve_duration = bool(track.get("preserve_duration"))

            # If no next_track_timestamp provided, try to get it from schedule.
            # For external capture-current bookmarks, preserve the station-history
            # item's own duration. Some SXM raw metadata boundaries fire a few
            # seconds before the audible song end; recalculating from that next
            # boundary is what caused captured files to lose their final seconds.
            if not next_track_timestamp and not preserve_duration:
                next_track_timestamp = await self._get_next_track_timestamp(
                    channel_id, track["timestamp_utc"]
                )
            elif preserve_duration:
                try:
                    print(f"   Preserving capture duration from schedule/API: {float(track.get('duration_ms') or 0)/1000:.1f}s")
                except Exception:
                    print("   Preserving capture duration from schedule/API")
            
            # Calculate actual duration from next track if available
            if next_track_timestamp and not preserve_duration:
                try:
                    track_start = datetime.fromisoformat(track["timestamp_utc"].replace('Z', '+00:00'))
                    next_start = datetime.fromisoformat(next_track_timestamp.replace('Z', '+00:00'))
                    actual_duration_ms = int((next_start - track_start).total_seconds() * 1000)
                    if actual_duration_ms > 0:
                        actual_duration_ms = self._apply_tail_pad_ms(actual_duration_ms, db, log_prefix="   ")
                    print(f"   Duration from next track: {actual_duration_ms/1000:.1f}s (API said {track['duration_ms']/1000:.1f}s)")
                    track["duration_ms"] = actual_duration_ms
                except Exception as e:
                    print(f"   Could not calculate duration from next track: {e}")
            
            # Get variant playlist
            playlist_data = await self.hls_service.get_variant_playlist(channel_id)
            
            if "error" in playlist_data:
                raise Exception(playlist_data["error"])
            
            segments = playlist_data.get("segments", [])
            key_url = playlist_data.get("key_url")
            
            if not segments:
                raise Exception("No segments found in playlist")
            
            # Filter segments for this track
            track_segments = self.hls_service.filter_segments_for_track(
                segments,
                track["timestamp_utc"],
                track["duration_ms"]
            )
            
            track_segments = self._extend_segments_for_tail_pad(
                track_segments,
                segments,
                db=db,
                log_prefix="   ",
            )

            require_full_window = bool(track.get("require_full_window"))

            # If no segments found by timestamp, try using duration-based estimation
            # for normal/manual downloads only. External "capture current" requests
            # must not fall back to latest segments because that creates clipped song
            # tails that look successful but are only a few seconds long.
            if not track_segments and segments and not require_full_window:
                print(f"   ⚠️ No segments by timestamp, using duration estimation")
                duration_sec = track["duration_ms"] / 1000
                num_segments = max(1, int(duration_sec / 9.75) + 2)  # ~9.75s per segment
                # Get latest segments as fallback
                track_segments = segments[-min(num_segments, len(segments)):]
            
            if not track_segments:
                raise Exception("No segments found for track time window")

            if require_full_window and track_segments and track_segments[0].get("timestamp"):
                try:
                    first_seg_time = datetime.fromisoformat(track_segments[0]["timestamp"].replace('Z', '+00:00'))
                    track_start_time = datetime.fromisoformat(track["timestamp_utc"].replace('Z', '+00:00'))
                    missing_start_sec = (first_seg_time - track_start_time).total_seconds()
                    if missing_start_sec > 5:
                        raise Exception(
                            f"Track start is no longer in the HLS/DVR buffer; first available segment is {missing_start_sec:.1f}s after track start. Refusing to save a partial capture."
                        )

                    # Also require the end of the song to be available. Without
                    # this guard, capture-current can save only the beginning of
                    # a song when the user clicks + before it has finished airing.
                    last_seg = track_segments[-1]
                    last_ts = last_seg.get("timestamp")
                    if last_ts and track.get("duration_ms"):
                        last_seg_time = datetime.fromisoformat(str(last_ts).replace('Z', '+00:00'))
                        last_seg_duration = float(last_seg.get("duration") or 0)
                        last_end_time = last_seg_time + timedelta(seconds=last_seg_duration)
                        track_end_time = track_start_time + timedelta(milliseconds=float(track.get("duration_ms") or 0))
                        missing_end_sec = (track_end_time - last_end_time).total_seconds()
                        # Be strict for capture-current: even 3-7 seconds missing is
                        # audible and shows up as a short Jukebox duration. Normal
                        # history downloads can be more tolerant, but external +
                        # captures should either download the complete song or fail.
                        if missing_end_sec > 2.0:
                            raise Exception(
                                f"Track end is not fully available yet; last available segment ends {missing_end_sec:.1f}s before track end. Refusing to save a partial capture."
                            )
                except Exception as e:
                    # Re-raise deliberate partial-capture failures, but keep normal
                    # timestamp parsing errors explicit too so they are visible in logs.
                    raise
            
            print(f"   Found {len(track_segments)} segments for track")
            
            # Log segment details for debugging exact timing
            if track_segments:
                print(f"   Segments timeline:")
                for i, seg in enumerate(track_segments[:3]):  # Show first 3
                    print(f"     [{i}] {seg.get('timestamp', 'no ts')} dur={seg.get('duration', 0):.2f}s")
                if len(track_segments) > 3:
                    print(f"     ... and {len(track_segments) - 3} more")
            
            # Get decryption key
            key_bytes = None
            if key_url:
                key_bytes = await self.hls_service.get_decryption_key(key_url)
            
            if not key_bytes:
                raise Exception("Could not get decryption key")
            
            # Create output directory: /downloads/STATION/DATE/
            from database import SessionLocal, Channel
            db_temp = SessionLocal()
            channel_record = db_temp.query(Channel).filter(Channel.channel_id == channel_id).first()
            station_name = self._sanitize_filename(channel_record.name if channel_record else "Unknown")
            db_temp.close()
            
            # Parse date from track timestamp
            try:
                track_date = datetime.fromisoformat(track["timestamp_utc"].replace('Z', '+00:00'))
                date_folder = track_date.strftime("%Y-%m-%d")
            except:
                date_folder = datetime.now().strftime("%Y-%m-%d")
            
            safe_artist = self._sanitize_filename(track["artist"])
            safe_title = self._sanitize_filename(track["title"])
            
            output_dir = Path(download_path) / station_name / date_folder
            output_dir.mkdir(parents=True, exist_ok=True)
            
            output_file = output_dir / f"{safe_artist} - {safe_title}.m4a"
            
            # Calculate precise trim points based on segment timestamps
            start_offset_sec = 0.0
            duration_sec = track["duration_ms"] / 1000.0
            
            # Calculate start offset: how far into the concatenated audio does the track start?
            if track_segments and track_segments[0].get("timestamp"):
                try:
                    first_seg_time = datetime.fromisoformat(
                        track_segments[0]["timestamp"].replace('Z', '+00:00')
                    )
                    track_start_time = datetime.fromisoformat(
                        track["timestamp_utc"].replace('Z', '+00:00')
                    )
                    
                    # Calculate offset from first segment to track start
                    # This is how many seconds into the downloaded audio the track begins
                    if track_start_time > first_seg_time:
                        start_offset_sec = (track_start_time - first_seg_time).total_seconds()
                    
                    # Debug info
                    print(f"   First segment: {first_seg_time.strftime('%H:%M:%S.%f')}")
                    print(f"   Track start:   {track_start_time.strftime('%H:%M:%S.%f')}")
                    print(f"   Trim: skip {start_offset_sec:.3f}s, keep {duration_sec:.3f}s (exact)")
                except Exception as e:
                    print(f"   Warning: Could not calculate precise offset: {e}")
            else:
                print(f"   Warning: No segment timestamps, using full duration without offset")
            
            # Download and decrypt segments
            temp_dir = Path(tempfile.mkdtemp())
            
            try:
                decrypted_files = await self._download_segments(
                    track_segments,
                    key_bytes,
                    temp_dir
                )
                
                if not decrypted_files:
                    raise Exception("No segments downloaded")
                
                # Concatenate segments with precise trimming
                await self._concatenate_segments(
                    decrypted_files, 
                    output_file,
                    start_offset_sec=start_offset_sec,
                    duration_sec=duration_sec
                )
                
                # Add metadata
                await self._add_metadata(
                    output_file,
                    track,
                    track.get("image_url")
                )
                
                # Update download record
                file_size = output_file.stat().st_size if output_file.exists() else 0
                download_record.file_path = str(output_file)
                download_record.file_size = file_size
                download_record.status = "completed"
                db.commit()

                # Some routers call DownloadService.download_track with playlist
                # kwargs so a Station History download can also be added to a
                # Jukebox playlist. Keep this support here so older/newer router
                # call styles do not crash with unexpected keyword arguments.
                await self._add_completed_download_to_playlist_if_requested(
                    db,
                    output_file,
                    playlist_id=playlist_id,
                    playlist_name=playlist_name,
                    create_playlist_name=create_playlist_name,
                )
                
                print(f"   ✅ Downloaded: {output_file}")
                return True
                
            finally:
                # Cleanup temp directory
                if temp_dir.exists():
                    shutil.rmtree(temp_dir)
            
        except Exception as e:
            print(f"   ❌ Download error: {e}")
            download_record.status = f"failed: {str(e)[:100]}"
            db.commit()
            return False
        finally:
            db.close()
    

    async def _add_completed_download_to_playlist_if_requested(
        self,
        db,
        output_file: Path,
        playlist_id: Optional[int] = None,
        playlist_name: Optional[str] = None,
        create_playlist_name: Optional[str] = None,
    ) -> None:
        """Import a completed download and optionally add it to a playlist.

        This is used by Channel History / download-and-add flows. It is kept
        tolerant on purpose: a normal download should still succeed even if the
        playlist add step cannot complete.
        """
        target_playlist_name = (create_playlist_name or playlist_name or "").strip()
        if not playlist_id and not target_playlist_name:
            return

        try:
            from sqlalchemy import func
            from database import LocalTrack, Playlist, PlaylistTrack
            from services.library_service import LibraryService

            try:
                library_service = LibraryService(db)
                await library_service.scan_library()
            except Exception as scan_error:
                print(f"   Warning: playlist add scan failed: {scan_error}")

            output_path = str(output_file)
            local_track = db.query(LocalTrack).filter(LocalTrack.file_path == output_path).first()
            if not local_track:
                local_track = db.query(LocalTrack).filter(LocalTrack.filename == output_file.name).first()

            if not local_track:
                print(f"   Warning: playlist add skipped; library track not found for {output_file.name}")
                return

            playlist = None
            if playlist_id:
                playlist = db.query(Playlist).filter(Playlist.id == playlist_id).first()

            if not playlist and target_playlist_name:
                playlist = db.query(Playlist).filter(Playlist.name == target_playlist_name).first()
                if not playlist and create_playlist_name:
                    playlist = Playlist(name=target_playlist_name, description="Created from download")
                    db.add(playlist)
                    db.flush()

            if not playlist:
                print(f"   Warning: playlist add skipped; playlist not found id={playlist_id} name={target_playlist_name!r}")
                return

            existing = db.query(PlaylistTrack).filter(
                PlaylistTrack.playlist_id == playlist.id,
                PlaylistTrack.track_id == local_track.id,
            ).first()

            if not existing:
                max_pos = db.query(func.max(PlaylistTrack.position)).filter(
                    PlaylistTrack.playlist_id == playlist.id
                ).scalar() or 0
                db.add(PlaylistTrack(
                    playlist_id=playlist.id,
                    track_id=local_track.id,
                    position=max_pos + 1,
                ))

            playlist.track_count = db.query(PlaylistTrack).filter(
                PlaylistTrack.playlist_id == playlist.id
            ).count()
            db.commit()
            print(f"   🎵 Added downloaded track {local_track.id} to playlist: {playlist.name}")
        except Exception as e:
            db.rollback()
            print(f"   Warning: playlist add failed: {e}")


    async def _local_xtra_playlist_text(self, xtra_track: Dict, temp_dir: Path) -> str:
        """Build a standalone XTRA playlist for ffmpeg without using live proxy routes.

        The earlier proxy-based capture path could interfere with active XTRA
        playback. This safer path keeps capture isolated: segment URLs remain
        direct SiriusXM URLs, while decryption keys are fetched once by
        ArchiveXM and written to temporary local key files for ffmpeg.
        """
        import base64
        import json
        import re
        import httpx

        playlist_text = xtra_track.get("playlist_text") or ""
        base_url = xtra_track.get("base_url") or ""
        path_dir = xtra_track.get("path_dir") or ""
        bearer = xtra_track.get("bearer") or ""

        def absolute_url(value: str) -> str:
            value = str(value or "").strip()
            if not value:
                return value
            if value.startswith(("http://", "https://")):
                return value
            return f"{base_url}{path_dir}{value}"

        key_cache = {}

        async def fetch_key_to_file(key_url: str) -> str:
            if key_url in key_cache:
                return key_cache[key_url]

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "*/*",
            }
            if bearer:
                headers["Authorization"] = f"Bearer {bearer}"

            async with httpx.AsyncClient() as client:
                response = await client.get(key_url, headers=headers, timeout=20)

            if response.status_code != 200:
                raise Exception(f"XTRA key fetch failed status={response.status_code}")

            content = response.content
            content_type = response.headers.get("content-type", "")

            # SiriusXM key endpoints commonly return JSON {"key":"base64..."};
            # ffmpeg needs the raw 16-byte AES key.
            if "json" in content_type.lower() or content.strip().startswith(b"{"):
                try:
                    data = json.loads(content.decode("utf-8"))
                    if isinstance(data, dict) and data.get("key"):
                        content = base64.b64decode(data["key"])
                except Exception as e:
                    raise Exception(f"XTRA key JSON decode failed: {e}")

            if not content:
                raise Exception("XTRA key fetch returned empty content")

            key_path = temp_dir / f"xtra_key_{len(key_cache)}.bin"
            key_path.write_bytes(content)
            key_cache[key_url] = str(key_path)
            return str(key_path)

        out = []
        for raw_line in playlist_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("#EXT-X-KEY"):
                match = re.search(r'URI="([^"]+)"', line)
                if match:
                    key_url = absolute_url(match.group(1))
                    local_key = await fetch_key_to_file(key_url)
                    line = re.sub(r'URI="([^"]+)"', f'URI="{local_key}"', line)
                out.append(line)
                continue

            if line.startswith("#"):
                out.append(line)
                continue

            out.append(absolute_url(line))

        if not any(line.strip() == "#EXT-X-ENDLIST" for line in out):
            out.append("#EXT-X-ENDLIST")

        return "\n".join(out) + "\n"

    async def download_xtra_track(
        self,
        download_id: int,
        channel_id: str,
        track: Dict,
        download_path: str,
    ) -> bool:
        """Download a full XTRA track from ArchiveXM's active XTRA FULL playlist.

        XTRA items are not linear DVR history entries, so timestamp-based HLS
        filtering is the wrong tool. The XTRA proxy already holds a FULL media
        playlist for the active item; this method remuxes that item into an M4A,
        tags it, and lets the capture-current background task import/add it.
        """
        from database import SessionLocal, Download, Channel

        db = SessionLocal()
        download_record = db.query(Download).filter(Download.id == download_id).first()

        try:
            print(f"📥 Starting XTRA capture: {track.get('artist', 'Unknown')} - {track.get('title', 'Unknown')}")
            if download_record:
                download_record.status = "downloading"
                db.commit()

            xtra_track = track.get("_xtra_track") or {}
            playlist_text = xtra_track.get("playlist_text") or ""
            if not playlist_text:
                raise Exception("No active XTRA media playlist available for capture")

            channel_record = db.query(Channel).filter(Channel.channel_id == channel_id).first()
            station_name = self._sanitize_filename(channel_record.name if channel_record else "XTRA")

            try:
                ts = track.get("timestamp_utc") or datetime.utcnow().isoformat()
                track_date = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
                date_folder = track_date.strftime("%Y-%m-%d")
            except Exception:
                date_folder = datetime.utcnow().strftime("%Y-%m-%d")

            safe_artist = self._sanitize_filename(track.get("artist") or "Unknown")
            safe_title = self._sanitize_filename(track.get("title") or "Unknown")
            output_dir = Path(download_path) / station_name / date_folder
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file = output_dir / f"{safe_artist} - {safe_title}.m4a"

            temp_dir = Path(tempfile.mkdtemp())
            try:
                m3u8_file = temp_dir / "xtra_capture.m3u8"
                m3u8_file.write_text(await self._local_xtra_playlist_text(xtra_track, temp_dir))

                # Do not pass -headers here. For encrypted HLS ffmpeg opens
                # segments through the crypto protocol, and http-only header
                # options can get forwarded to crypto and fail with
                # "Option not found" after the local key is opened. The isolated
                # playlist already contains direct media URLs and local key files.
                ffmpeg_cmd = [
                    "ffmpeg", "-y",
                    "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
                    "-allowed_extensions", "ALL",
                    "-i", str(m3u8_file),
                    "-c:a", "aac",
                    "-b:a", "256k",
                    "-movflags", "+faststart",
                    str(output_file),
                ]
                print(f"   FFmpeg XTRA: {' '.join(ffmpeg_cmd[-8:])}")
                result = subprocess.run(ffmpeg_cmd, capture_output=True)
                if result.returncode != 0 or not output_file.exists():
                    err = result.stderr.decode(errors="ignore")[-1200:]
                    raise Exception(f"ffmpeg XTRA capture failed: {err}")

                await self._add_metadata(output_file, track, track.get("image_url"))

                file_size = output_file.stat().st_size if output_file.exists() else 0
                if download_record:
                    download_record.file_path = str(output_file)
                    download_record.file_size = file_size
                    download_record.status = "completed"
                    db.commit()

                print(f"   ✅ XTRA downloaded: {output_file}")
                return True
            finally:
                if temp_dir.exists():
                    shutil.rmtree(temp_dir)

        except Exception as e:
            print(f"   ❌ XTRA download error: {e}")
            if download_record:
                download_record.status = f"failed: {str(e)[:100]}"
                db.commit()
            return False
        finally:
            db.close()

    async def download_bulk(
        self,
        download_ids: List[int],
        channel_id: str,
        tracks: List[Dict],
        download_path: str
    ) -> Dict:
        """
        Download multiple tracks efficiently
        
        Bulk download is more efficient - we get the playlist once
        and download segments for all tracks
        """
        from database import SessionLocal, Download
        
        db = SessionLocal()
        
        try:
            print(f"📥 Starting bulk download: {len(tracks)} tracks")
            
            # Get variant playlist once
            playlist_data = await self.hls_service.get_variant_playlist(channel_id)
            
            if "error" in playlist_data:
                raise Exception(playlist_data["error"])
            
            segments = playlist_data.get("segments", [])
            key_url = playlist_data.get("key_url")
            
            # Get decryption key once
            key_bytes = None
            if key_url:
                key_bytes = await self.hls_service.get_decryption_key(key_url)
            
            if not key_bytes:
                raise Exception("Could not get decryption key")

            # Fetch raw timed metadata once for boundary calculations.
            # This includes DJ/talk/interstitial cuts, so downloads stop at the
            # next real timestamp instead of running until the next selected song.
            raw_schedule = await self.api.get_schedule(
                channel_id,
                hours_back=5,
                include_interstitials=True,
            )
            print(f"   Loaded {len(raw_schedule)} raw metadata boundary items")
            
            successful = 0
            failed = 0
            
            # Sort tracks by timestamp for accurate next-track duration calculation
            sorted_tracks = sorted(
                list(zip(download_ids, tracks)), 
                key=lambda x: x[1].get("timestamp_utc", "")
            )
            
            # Download each track
            for i, (download_id, track) in enumerate(sorted_tracks):
                try:
                    download_record = db.query(Download).filter(Download.id == download_id).first()
                    
                    print(f"   [{i+1}/{len(tracks)}] {track['artist']} - {track['title']}")
                    
                    download_record.status = "downloading"
                    db.commit()
                    
                    # Calculate duration from the next raw timed metadata boundary.
                    # This is more accurate than API duration and avoids recording
                    # through DJ/talk plugs when those plugs have timestamps.
                    actual_duration_ms = track["duration_ms"]
                    next_boundary_ts = self._find_next_timestamp_from_schedule(
                        raw_schedule,
                        track["timestamp_utc"],
                    )

                    if not next_boundary_ts and i + 1 < len(sorted_tracks):
                        # Fallback only: use the next selected/downloaded song if
                        # the raw schedule did not provide a boundary.
                        next_boundary_ts = sorted_tracks[i + 1][1].get("timestamp_utc")

                    if next_boundary_ts:
                        try:
                            current_start = datetime.fromisoformat(track["timestamp_utc"].replace('Z', '+00:00'))
                            next_start = datetime.fromisoformat(next_boundary_ts.replace('Z', '+00:00'))
                            calculated_ms = int((next_start - current_start).total_seconds() * 1000)
                            if calculated_ms > 0:
                                actual_duration_ms = self._apply_tail_pad_ms(calculated_ms, db, log_prefix="      ")
                                print(f"      Duration from raw boundary: {actual_duration_ms/1000:.1f}s (API: {track['duration_ms']/1000:.1f}s)")
                        except Exception as e:
                            print(f"      Could not calculate raw-boundary duration: {e}")
                    
                    # Filter segments for this track using actual duration
                    track_segments = self.hls_service.filter_segments_for_track(
                        segments,
                        track["timestamp_utc"],
                        actual_duration_ms
                    )
                    
                    track_segments = self._extend_segments_for_tail_pad(
                        track_segments,
                        segments,
                        db=db,
                        log_prefix="      ",
                    )

                    if not track_segments:
                        download_record.status = "failed: no segments"
                        db.commit()
                        failed += 1
                        continue
                    
                    # Create output path: /downloads/STATION/DATE/
                    from database import Channel
                    channel_record = db.query(Channel).filter(Channel.channel_id == channel_id).first()
                    station_name = self._sanitize_filename(channel_record.name if channel_record else "Unknown")
                    
                    try:
                        track_date = datetime.fromisoformat(track["timestamp_utc"].replace('Z', '+00:00'))
                        date_folder = track_date.strftime("%Y-%m-%d")
                    except:
                        date_folder = datetime.now().strftime("%Y-%m-%d")
                    
                    safe_artist = self._sanitize_filename(track["artist"])
                    safe_title = self._sanitize_filename(track["title"])
                    
                    output_dir = Path(download_path) / station_name / date_folder
                    output_dir.mkdir(parents=True, exist_ok=True)
                    
                    output_file = output_dir / f"{safe_artist} - {safe_title}.m4a"
                    
                    # Calculate precise trim points
                    start_offset_sec = 0.0
                    duration_sec = actual_duration_ms / 1000.0
                    
                    if track_segments and track_segments[0].get("timestamp"):
                        try:
                            first_seg_time = datetime.fromisoformat(
                                track_segments[0]["timestamp"].replace('Z', '+00:00')
                            )
                            track_start_time = datetime.fromisoformat(
                                track["timestamp_utc"].replace('Z', '+00:00')
                            )
                            if track_start_time > first_seg_time:
                                start_offset_sec = (track_start_time - first_seg_time).total_seconds()
                        except:
                            pass
                    
                    # Download segments
                    temp_dir = Path(tempfile.mkdtemp())
                    
                    try:
                        decrypted_files = await self._download_segments(
                            track_segments,
                            key_bytes,
                            temp_dir
                        )
                        
                        if decrypted_files:
                            await self._concatenate_segments(
                                decrypted_files, 
                                output_file,
                                start_offset_sec=start_offset_sec,
                                duration_sec=duration_sec
                            )
                            await self._add_metadata(output_file, track, track.get("image_url"))
                            
                            download_record.file_path = str(output_file)
                            download_record.file_size = output_file.stat().st_size
                            download_record.status = "completed"
                            successful += 1
                        else:
                            download_record.status = "failed: download error"
                            failed += 1
                        
                        db.commit()
                        
                    finally:
                        if temp_dir.exists():
                            shutil.rmtree(temp_dir)
                    
                except Exception as e:
                    print(f"   ❌ Error: {e}")
                    failed += 1
                    continue
            
            print(f"✅ Bulk download complete: {successful} successful, {failed} failed")
            
            return {
                "success": True,
                "successful": successful,
                "failed": failed,
                "total": len(tracks)
            }
            
        except Exception as e:
            print(f"❌ Bulk download error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            db.close()
    
    async def _download_segments(
        self,
        segments: List[Dict],
        key_bytes: bytes,
        temp_dir: Path
    ) -> List[Path]:
        """Download and decrypt HLS segments"""
        decrypted_files = []
        
        async with httpx.AsyncClient() as client:
            for i, segment in enumerate(segments):
                try:
                    # Download encrypted segment
                    response = await client.get(segment["url"], timeout=30)
                    
                    if response.status_code != 200:
                        continue
                    
                    encrypted_data = response.content
                    
                    # Decrypt (AES-128-CBC)
                    # IV is typically the segment sequence number (16 bytes, zero-padded)
                    iv = bytes([0] * 16)  # Default IV
                    
                    # Try to extract IV from segment URL or use sequence number
                    try:
                        # Use segment index as IV
                        iv = i.to_bytes(16, byteorder='big')
                    except:
                        pass
                    
                    decrypted_data = self._decrypt_segment(encrypted_data, key_bytes, iv)
                    
                    if decrypted_data:
                        dec_file = temp_dir / f"seg_{i:04d}.aac"
                        dec_file.write_bytes(decrypted_data)
                        decrypted_files.append(dec_file)
                        
                except Exception as e:
                    print(f"   Segment {i} error: {e}")
                    continue
        
        return sorted(decrypted_files)
    
    def _decrypt_segment(self, data: bytes, key: bytes, iv: bytes) -> Optional[bytes]:
        """Decrypt AES-128-CBC encrypted segment"""
        try:
            cipher = Cipher(
                algorithms.AES(key),
                modes.CBC(iv),
                backend=default_backend()
            )
            decryptor = cipher.decryptor()
            decrypted = decryptor.update(data) + decryptor.finalize()
            
            # Remove PKCS7 padding
            padding_length = decrypted[-1]
            if padding_length <= 16:
                decrypted = decrypted[:-padding_length]
            
            return decrypted
            
        except Exception as e:
            print(f"Decryption error: {e}")
            return None
    
    def _find_next_timestamp_from_schedule(
        self,
        schedule: List[Dict],
        current_track_timestamp: str,
        min_gap_seconds: float = 1.0,
        max_reasonable_seconds: float = 30 * 60,
    ) -> Optional[str]:
        """Return the next timed SXM metadata boundary after current_track_timestamp.

        This intentionally uses raw timed metadata, including DJ plugs, bumpers,
        and interstitial/talk cuts. Those short cuts are still real boundaries
        and should stop the previous downloaded song.
        """
        if not schedule:
            return None

        try:
            current_time = datetime.fromisoformat(current_track_timestamp.replace('Z', '+00:00'))
        except Exception:
            return None

        candidates = []
        for item in schedule:
            ts = item.get("timestamp_utc") or item.get("timestamp")
            if not ts:
                continue
            try:
                item_time = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
            except Exception:
                continue

            delta = (item_time - current_time).total_seconds()
            if delta >= min_gap_seconds and delta <= max_reasonable_seconds:
                candidates.append((item_time, ts, item))

        if not candidates:
            return None

        candidates.sort(key=lambda row: row[0])
        next_time, next_ts, next_item = candidates[0]
        print(
            "   Next raw metadata boundary: "
            f"{next_ts} ({(next_time - current_time).total_seconds():.1f}s) "
            f"title={next_item.get('title') or next_item.get('name') or 'Unknown'} "
            f"interstitial={next_item.get('is_interstitial', False)}"
        )
        return str(next_ts)

    async def _get_next_track_timestamp(self, channel_id: str, current_track_timestamp: str) -> Optional[str]:
        """Get the next raw timed metadata item after the selected track.

        Normal schedule display filters out interstitials, but downloads must not:
        DJ plugs/talk cuts often have their own timestamps and are the correct
        end boundary for the previous song.
        """
        try:
            schedule = await self.api.get_schedule(
                channel_id,
                hours_back=5,
                include_interstitials=True,
            )
            return self._find_next_timestamp_from_schedule(schedule, current_track_timestamp)
        except Exception as e:
            print(f"   Could not get next track timestamp: {e}")
            return None
    
    async def _concatenate_segments(
        self, 
        segment_files: List[Path], 
        output_file: Path,
        start_offset_sec: float = 0.0,
        duration_sec: float = None
    ):
        """
        Concatenate decrypted segments and trim to exact timestamps
        
        Args:
            segment_files: List of decrypted segment files
            output_file: Final output path
            start_offset_sec: Seconds to skip from start of first segment
            duration_sec: Exact duration to keep (for precise trimming)
        """
        try:
            # Simple concatenation for AAC
            concat_file = output_file.with_suffix('.concat.aac')
            with open(concat_file, 'wb') as outfile:
                for seg_file in segment_files:
                    outfile.write(seg_file.read_bytes())
            
            # Build ffmpeg command with precise trimming
            # Use output seeking (-ss after -i) for frame-accurate cuts
            ffmpeg_cmd = ['ffmpeg', '-y', '-i', str(concat_file)]
            
            # Add start offset if needed (trim beginning) - output seeking for accuracy
            if start_offset_sec > 0.1:  # Only trim if offset is significant
                ffmpeg_cmd.extend(['-ss', f'{start_offset_sec:.3f}'])
            
            # Add duration limit if specified (trim end)
            if duration_sec and duration_sec > 0:
                ffmpeg_cmd.extend(['-t', f'{duration_sec:.3f}'])
            
            # Output options - re-encode for precise cuts at frame boundaries
            ffmpeg_cmd.extend([
                '-c:a', 'aac',
                '-b:a', '256k',
                '-movflags', '+faststart',
                str(output_file)
            ])
            
            print(f"   FFmpeg: {' '.join(ffmpeg_cmd[-6:])}")
            
            result = subprocess.run(ffmpeg_cmd, capture_output=True)
            
            # Cleanup concat file
            if concat_file.exists():
                concat_file.unlink()
            
            if result.returncode != 0:
                print(f"FFmpeg error: {result.stderr.decode()[:200]}")
                # Fallback: just copy without trimming
                if concat_file.exists():
                    shutil.copy(concat_file, output_file)
                
        except Exception as e:
            print(f"Concatenation error: {e}")
    
    async def _add_metadata(
        self,
        file_path: Path,
        track: Dict,
        cover_url: Optional[str] = None
    ):
        """Add ID3 metadata and cover art"""
        try:
            from mutagen.mp4 import MP4, MP4Cover
            
            audio = MP4(str(file_path))
            
            # Add tags
            audio['\xa9nam'] = track.get('title', 'Unknown')  # Title
            audio['\xa9ART'] = track.get('artist', 'Unknown')  # Artist
            
            if track.get('album'):
                audio['\xa9alb'] = track['album']  # Album
            
            # Download and add cover art
            if cover_url:
                print(f"   Cover URL: {cover_url[:80]}...")
                try:
                    async with httpx.AsyncClient() as client:
                        response = await client.get(cover_url, timeout=10)
                        if response.status_code == 200:
                            cover_data = response.content
                            print(f"   Cover downloaded: {len(cover_data)} bytes")
                            
                            # Determine format
                            if cover_url.lower().endswith('.png'):
                                cover_format = MP4Cover.FORMAT_PNG
                            else:
                                cover_format = MP4Cover.FORMAT_JPEG
                            
                            audio['covr'] = [MP4Cover(cover_data, imageformat=cover_format)]
                        else:
                            print(f"   Cover download failed: HTTP {response.status_code}")
                except Exception as e:
                    print(f"   Cover art error: {e}")
            else:
                print(f"   No cover URL provided for track")
            
            audio.save()
            
        except Exception as e:
            print(f"Metadata error: {e}")
    
    def _sanitize_filename(self, name: str) -> str:
        """Sanitize string for use as filename"""
        # Remove/replace invalid characters
        invalid_chars = '<>:"/\\|?*'
        result = name
        
        for char in invalid_chars:
            result = result.replace(char, '_')
        
        # Limit length
        return result[:100].strip()
