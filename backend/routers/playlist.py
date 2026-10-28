"""
Playlist Router - Generate local M3U playlists for IPTV players
"""
import os
import re
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote, urlparse
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


def _playlist_output_path() -> Path:
    return Path(os.getenv("PLAYLIST_OUTPUT", "/app/output/siriusxm.m3u"))


def _channel_map_output_path() -> Path:
    configured = os.getenv("CHANNEL_MAP_OUTPUT", "").strip()
    if configured:
        return Path(configured)
    return _playlist_output_path().parent / "sxm_channel_map.json"


def _channels_xml_output_path() -> Path:
    configured = os.getenv("CHANNELS_XML_OUTPUT", "").strip()
    if configured:
        return Path(configured)
    return _playlist_output_path().parent / "siriusxm.channels_new.xml"


def _boss_mapping_output_path() -> Path:
    configured = os.getenv("BOSS_MAPPING_OUTPUT", "").strip()
    if configured:
        return Path(configured)
    return _playlist_output_path().parent / "boss.mapping.txt"


def _dated_backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%m.%d.%Y")
    backup_path = path.with_name(f"{path.name}.{stamp}.bak")
    if not backup_path.exists():
        return backup_path

    time_stamp = datetime.now().strftime("%H%M%S")
    backup_path = path.with_name(f"{path.name}.{stamp}-{time_stamp}.bak")
    counter = 2
    while backup_path.exists():
        backup_path = path.with_name(
            f"{path.name}.{stamp}-{time_stamp}-{counter}.bak"
        )
        counter += 1
    return backup_path


def _backup_file(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    backup_path = _dated_backup_path(path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    return str(backup_path)


def _mapping_xmltv_base(value: str, fallback: str = "") -> str:
    """Return an ASCII alphanumeric-only XMLTV id base."""
    text = str(value or "").strip()
    text = re.sub(r"^\s*Radio:\s*", "", text, flags=re.IGNORECASE)
    text = text.replace("&", "And")
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "", text)

    if not text:
        fallback_text = unicodedata.normalize("NFKD", str(fallback or ""))
        fallback_text = fallback_text.encode("ascii", "ignore").decode("ascii")
        text = re.sub(r"[^A-Za-z0-9]+", "", fallback_text)

    return text or "Channel"


def _mapping_xmltv_id(display_name: str, channel_uuid: str) -> str:
    return f"{_mapping_xmltv_base(display_name, channel_uuid)}.sxm"


def _sanitize_existing_mapping_xmltv_id(xmltv_id: str, channel_uuid: str) -> str:
    value = str(xmltv_id or "").strip()
    value = re.sub(r"\.sxm$", "", value, flags=re.IGNORECASE)
    return f"{_mapping_xmltv_base(value, channel_uuid)}.sxm"


def _mapping_name_for_compare(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def _load_existing_channel_map(path: Optional[Path] = None) -> dict:
    path = Path(path) if path else _channel_map_output_path()
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"Unable to parse existing channel map {path}: {exc}")
        return {}


def _mapping_display_name(channel: Channel) -> str:
    name = clean_m3u_text(channel.name or "Unknown")
    return f"Radio: {name}"


def _mapping_plain_name(display_name: str) -> str:
    return re.sub(
        r"^\s*Radio:\s*",
        "",
        str(display_name or ""),
        flags=re.IGNORECASE,
    ).strip()


def _mapping_uuid(channel: Channel) -> str:
    return str(
        getattr(channel, "public_uuid", None)
        or channel.channel_id
        or ""
    ).strip()


def generate_channel_mapping(db: DBSession, previous_map: Optional[dict] = None) -> dict:
    """Build one unique JSON mapping entry per current SiriusXM UUID.

    Existing aliases are preserved. Existing xmltvId values are retained when
    the channel name is unchanged, but sanitized to letters/numbers only. If a
    UUID's display name changes, xmltvId is regenerated from the new name so a
    reassigned/renamed channel cannot keep the old channel's XMLTV id.
    """
    previous_map = previous_map or {}
    mapping = {}

    channels = (
        db.query(Channel)
        .filter(Channel.channel_id.isnot(None))
        .filter(Channel.name.isnot(None))
        .order_by(Channel.id.asc())
        .all()
    )

    for channel in channels:
        channel_uuid = _mapping_uuid(channel)
        if not channel_uuid:
            continue

        display_name = _mapping_display_name(channel)
        previous = previous_map.get(channel_uuid)
        previous = previous if isinstance(previous, dict) else {}

        previous_xmltv_id = str(previous.get("xmltvId") or "").strip()
        previous_display_name = str(previous.get("displayName") or "").strip()

        if (
            previous_xmltv_id
            and _mapping_name_for_compare(previous_display_name)
            == _mapping_name_for_compare(display_name)
        ):
            # Preserve deliberate/custom XMLTV naming for unchanged channels,
            # while enforcing the alphanumeric-only ID rule.
            xmltv_id = _sanitize_existing_mapping_xmltv_id(
                previous_xmltv_id,
                channel_uuid,
            )
        else:
            # A changed display name on the same UUID means the channel was
            # renamed/reassigned; regenerate the XMLTV id from the new name.
            xmltv_id = _mapping_xmltv_id(display_name, channel_uuid)

        entry = {
            "xmltvId": xmltv_id,
            "displayName": display_name,
        }

        aliases = previous.get("aliases")
        clean_aliases = []
        seen_aliases = set()

        # Every mapping gets the plain current channel name as its first alias.
        # Preserve any additional curated aliases from the previous map.
        plain_name = _mapping_plain_name(display_name)
        alias_candidates = [plain_name]
        if isinstance(aliases, list):
            alias_candidates.extend(aliases)

        for alias in alias_candidates:
            cleaned = _mapping_plain_name(str(alias or "").strip())
            if not cleaned:
                continue
            key = _mapping_name_for_compare(cleaned)
            if key in seen_aliases:
                continue
            seen_aliases.add(key)
            clean_aliases.append(cleaned)

        if clean_aliases:
            entry["aliases"] = clean_aliases

        # JSON object keys must be unique. If SiriusXM reuses a UUID, the final
        # current channel row wins rather than emitting invalid duplicate keys.
        mapping[channel_uuid] = entry

    return mapping


def _mapping_entry_block(channel_uuid: str, entry: dict) -> list[str]:
    encoded = json.dumps(
        {channel_uuid: entry},
        indent=2,
        ensure_ascii=False,
    ).splitlines()
    if len(encoded) >= 2:
        return encoded[1:-1]
    return encoded


def write_channel_mapping_changes_report(previous_map: dict, current_map: dict) -> dict:
    report_path = _channel_map_output_path().parent / "sxm_channel_map_changes.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    old_keys = set(previous_map)
    new_keys = set(current_map)
    added_keys = [key for key in current_map if key not in old_keys]
    removed_keys = [key for key in previous_map if key not in new_keys]
    updated_keys = [
        key
        for key in current_map
        if key in previous_map and previous_map.get(key) != current_map.get(key)
    ]

    lines = [
        "ArchiveXM SiriusXM Channel Mapping Changes",
        f"Refresh: {datetime.now().strftime('%m/%d/%Y %I:%M %p')}",
        "",
        f"Previous mappings: {len(previous_map)}",
        f"Current mappings: {len(current_map)}",
        "",
        "====================",
        "Added Mappings",
        "====================",
        "",
    ]

    if added_keys:
        for key in added_keys:
            lines.extend(_mapping_entry_block(key, current_map[key]))
            lines.append("")
    else:
        lines.append("None")

    lines.extend([
        "",
        "====================",
        "Updated Mappings",
        "====================",
        "",
    ])
    if updated_keys:
        for key in updated_keys:
            lines.append(f"UUID: {key}")
            lines.append("OLD:")
            lines.extend(_mapping_entry_block(key, previous_map[key]))
            lines.append("NEW:")
            lines.extend(_mapping_entry_block(key, current_map[key]))
            lines.append("")
    else:
        lines.append("None")

    lines.extend([
        "",
        "====================",
        "Removed Mappings",
        "====================",
        "",
    ])
    if removed_keys:
        for key in removed_keys:
            lines.extend(_mapping_entry_block(key, previous_map[key]))
            lines.append("")
    else:
        lines.append("None")

    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {
        "path": str(report_path),
        "added": len(added_keys),
        "updated": len(updated_keys),
        "removed": len(removed_keys),
    }


def refresh_channel_mapping(db: DBSession) -> dict:
    output_path = _channel_map_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    previous_map = _load_existing_channel_map(output_path)
    backup_path = _backup_file(output_path)
    current_map = generate_channel_mapping(db, previous_map)

    output_path.write_text(
        json.dumps(current_map, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    changes = write_channel_mapping_changes_report(previous_map, current_map)

    return {
        "status": "ok",
        "path": str(output_path),
        "backup_path": backup_path,
        "count": len(current_map),
        "changes_path": changes["path"],
        "added": changes["added"],
        "updated": changes["updated"],
        "removed": changes["removed"],
    }



def _channel_xml_slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def _is_sports_play_by_play(channel_name: str, public_path: str = "") -> bool:
    haystack = f"{channel_name} {public_path}".lower()
    return bool(re.search(r"play[- ]by[- ]play", haystack))


def _channel_xml_section(channel: Channel, sports_path: str = "") -> str:
    channel_type = str(getattr(channel, "channel_type", None) or "channel-linear").lower()
    if channel_type == "channel-xtra":
        return "xtra"

    # Only actual SiriusXM team URLs use the special /sports/{league}/{team}
    # site_id form. Sports play-by-play channels otherwise behave like normal
    # ON NOW channels and retain their public web slug plus ##UUID.
    public_path = str(sports_path or getattr(channel, "public_path", None) or "").strip()
    if public_path.lower().startswith("/sports/"):
        return "sports"
    return "on_now"


def _sports_site_id(channel: Channel, sports_path: str = "") -> str:
    public_path = str(sports_path or getattr(channel, "public_path", None) or "").strip()
    if public_path.lower().startswith("/sports/"):
        sports_path = public_path.rstrip("/")
        channel_uuid = _mapping_uuid(channel)
        if channel_uuid:
            return f"{sports_path}##{channel_uuid}"
        return sports_path

    if public_path:
        clean_path = public_path.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        if clean_path.lower().startswith("/channels/"):
            return clean_path[len("/channels/"):]
        return clean_path.strip("/").rsplit("/", 1)[-1]

    return _channel_xml_slug(channel.name or "")


def _parse_existing_channels_xml(path: Optional[Path] = None) -> list[dict]:
    path = Path(path) if path else _channels_xml_output_path()
    if not path.exists():
        return []

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    entries = []
    channel_re = re.compile(
        r'^\s*<channel\s+([^>]+)>(.*?)</channel>\s*$',
        flags=re.IGNORECASE,
    )

    for line in lines:
        match = channel_re.match(line)
        if not match:
            continue

        attrs_text, display_text = match.groups()
        attrs = {
            attr.group(1): html_lib.unescape(attr.group(2))
            for attr in re.finditer(r'([A-Za-z0-9_-]+)="([^"]*)"', attrs_text)
        }
        if attrs.get("site") != "siriusxm":
            continue

        entries.append({
            "site_id": str(attrs.get("site_id") or "").strip(),
            "xmltv_id": str(attrs.get("xmltv_id") or "").strip(),
            "name": html_lib.unescape(display_text.strip()),
        })

    return entries


def _channel_xml_identity(entry: dict) -> str:
    site_id = str(entry.get("site_id") or "").strip()
    name = str(entry.get("name") or "").strip()
    lowered = site_id.lower()

    if lowered == "dummy":
        return f"xtra:{_mapping_name_for_compare(name)}"

    base_site_id = site_id.split("##", 1)[0]
    if base_site_id.lower().startswith("/sports/"):
        return f"sports:{base_site_id.lower().rstrip('/')}"

    uuid_match = re.search(
        r"##([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$",
        site_id,
    )
    if uuid_match:
        return f"uuid:{uuid_match.group(1).lower()}"

    if site_id:
        return f"site:{site_id.lower()}"

    return f"name:{_mapping_name_for_compare(name)}"


def _existing_xml_entry_for_channel(
    channel: Channel,
    existing_by_identity: dict,
    candidate_site_id: str,
    section: str,
) -> Optional[dict]:
    name = clean_m3u_text(channel.name or "Unknown")

    if section == "xtra":
        identity = f"xtra:{_mapping_name_for_compare(name)}"
    elif section == "sports":
        identity = _channel_xml_identity({
            "site_id": candidate_site_id,
            "name": name,
        })
    else:
        public_uuid = _mapping_uuid(channel)
        if public_uuid:
            identity = f"uuid:{public_uuid.lower()}"
        else:
            identity = _channel_xml_identity({
                "site_id": candidate_site_id,
                "name": name,
            })

    return existing_by_identity.get(identity)


def _channel_xml_xmltv_id(
    channel: Channel,
    existing_entry: Optional[dict],
    current_map: dict,
) -> str:
    name = clean_m3u_text(channel.name or "Unknown")
    current_uuid = _mapping_uuid(channel)

    # Prefer the freshly normalized JSON mapping when this UUID maps to the
    # same current channel name. This keeps the JSON and XML outputs aligned.
    map_entry = current_map.get(current_uuid)
    if isinstance(map_entry, dict):
        mapped_name = _mapping_plain_name(map_entry.get("displayName") or "")
        mapped_id = str(map_entry.get("xmltvId") or "").strip()
        if (
            mapped_id
            and _mapping_name_for_compare(mapped_name)
            == _mapping_name_for_compare(name)
        ):
            return mapped_id

    # For legacy sports/play-by-play entries that cannot be represented
    # uniquely by UUID, retain the established XMLTV id while the name is the
    # same.
    if existing_entry:
        old_name = str(existing_entry.get("name") or "").strip()
        old_id = str(existing_entry.get("xmltv_id") or "").strip()
        if (
            old_id
            and _mapping_name_for_compare(old_name)
            == _mapping_name_for_compare(name)
        ):
            return old_id

    return _mapping_xmltv_id(name, current_uuid or "channel")


def _format_channels_xml_line(entry: dict) -> str:
    site_id = html_lib.escape(str(entry.get("site_id") or ""), quote=True)
    xmltv_id = html_lib.escape(str(entry.get("xmltv_id") or ""), quote=True)
    name = html_lib.escape(str(entry.get("name") or "Unknown"), quote=False)
    return (
        f'  <channel update="i" site="siriusxm" site_id="{site_id}" '
        f'xmltv_id="{xmltv_id}">{name}</channel>'
    )


def generate_channels_xml_entries(db: DBSession, current_map: dict) -> dict:
    existing_entries = _parse_existing_channels_xml()
    existing_by_identity = {}
    for entry in existing_entries:
        existing_by_identity[_channel_xml_identity(entry)] = entry

    sections = {
        "on_now": [],
        "sports": [],
        "xtra": [],
    }

    channels = (
        db.query(Channel)
        .filter(Channel.channel_id.isnot(None))
        .filter(Channel.name.isnot(None))
        .order_by(Channel.number.asc(), Channel.id.asc())
        .all()
    )

    # SiriusXM can expose the same sports team twice: once as a normal channel
    # object and once as the canonical /sports/{league}/{team} public page.
    # Prefer the sports-path version and suppress the duplicate normal entry.
    #
    # Recover canonical team paths from the current XML OR any dated backup.
    # This lets ArchiveXM repair a currently-generated XML that already lost
    # the /sports/... paths during an earlier test refresh.
    existing_sports_path_by_name = {}
    xml_path = _channels_xml_output_path()
    candidate_xml_paths = [xml_path]
    candidate_xml_paths.extend(
        sorted(
            xml_path.parent.glob(f"{xml_path.name}.*.bak"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
            reverse=True,
        )
    )

    for candidate_path in candidate_xml_paths:
        for entry in _parse_existing_channels_xml(candidate_path):
            site_id = str(entry.get("site_id") or "").strip()
            base_site_id = site_id.split("##", 1)[0]
            if not base_site_id.lower().startswith("/sports/"):
                continue

            name_key = _mapping_name_for_compare(entry.get("name"))
            if name_key not in existing_sports_path_by_name:
                existing_sports_path_by_name[name_key] = base_site_id.rstrip("/")

    sports_team_names = {
        _mapping_name_for_compare(clean_m3u_text(channel.name or "Unknown"))
        for channel in channels
        if (
            str(getattr(channel, "public_path", None) or "").strip().lower().startswith("/sports/")
            or _mapping_name_for_compare(clean_m3u_text(channel.name or "Unknown"))
            in existing_sports_path_by_name
        )
    }

    seen_sports_paths = set()

    for channel in channels:
        name = clean_m3u_text(channel.name or "Unknown")
        name_key = _mapping_name_for_compare(name)

        current_public_path = str(getattr(channel, "public_path", None) or "").strip()
        sports_path = (
            current_public_path
            if current_public_path.lower().startswith("/sports/")
            else existing_sports_path_by_name.get(name_key, "")
        )
        section = _channel_xml_section(channel, sports_path)

        # If SiriusXM exposes both the canonical sports-path row and a normal
        # duplicate row for the same team, keep only the sports-path version.
        # If only the normal row survives DB refresh, the existing XML fallback
        # above upgrades that row back to the canonical sports path.
        if (
            section == "on_now"
            and name_key in sports_team_names
        ):
            continue

        if section == "xtra":
            site_id = "dummy"
        elif section == "sports":
            sports_key = sports_path.lower().rstrip("/")
            if sports_key in seen_sports_paths:
                continue
            seen_sports_paths.add(sports_key)
            site_id = _sports_site_id(channel, sports_path)
        else:
            snapshot = {
                "channel_id": str(channel.channel_id or ""),
                "channel_type": str(channel.channel_type or "channel-linear"),
                "public_uuid": str(getattr(channel, "public_uuid", None) or ""),
                "public_path": str(getattr(channel, "public_path", None) or ""),
                "name": name,
            }
            site_id = _site_id_for_channel(snapshot)

        existing_entry = _existing_xml_entry_for_channel(
            channel,
            existing_by_identity,
            site_id,
            section,
        )
        xmltv_id = _channel_xml_xmltv_id(
            channel,
            existing_entry,
            current_map,
        )

        entry = {
            "site_id": site_id,
            "xmltv_id": xmltv_id,
            "name": name,
            "section": section,
        }
        entry["identity"] = _channel_xml_identity(entry)
        sections[section].append(entry)

    return {
        "existing": existing_entries,
        "sections": sections,
        "all": sections["on_now"] + sections["sports"] + sections["xtra"],
    }


def _write_channels_xml_changes_report(old_entries: list[dict], new_entries: list[dict]) -> dict:
    report_path = _channels_xml_output_path().parent / "siriusxm_channels_changes.txt"

    old_by_identity = {}
    for entry in old_entries:
        old_by_identity[_channel_xml_identity(entry)] = entry
    new_by_identity = {}
    for entry in new_entries:
        new_by_identity[_channel_xml_identity(entry)] = entry

    added = [
        entry for key, entry in new_by_identity.items()
        if key not in old_by_identity
    ]
    removed = [
        entry for key, entry in old_by_identity.items()
        if key not in new_by_identity
    ]
    updated = [
        {"old": old_by_identity[key], "new": entry}
        for key, entry in new_by_identity.items()
        if key in old_by_identity
        and (
            old_by_identity[key].get("site_id") != entry.get("site_id")
            or old_by_identity[key].get("xmltv_id") != entry.get("xmltv_id")
            or _mapping_name_for_compare(old_by_identity[key].get("name"))
            != _mapping_name_for_compare(entry.get("name"))
        )
    ]

    lines = [
        "ArchiveXM SiriusXM WebGrab Channel List Changes",
        f"Refresh: {datetime.now().strftime('%m/%d/%Y %I:%M %p')}",
        "",
        f"Previous channels: {len(old_by_identity)}",
        f"Current channels: {len(new_by_identity)}",
        "",
        "====================",
        "Added Channels",
        "====================",
        "",
    ]
    if added:
        lines.extend(_format_channels_xml_line(entry) for entry in added)
    else:
        lines.append("None")

    lines.extend([
        "",
        "",
        "====================",
        "Updated Channels",
        "====================",
        "",
    ])
    if updated:
        for item in updated:
            lines.append("OLD:")
            lines.append(_format_channels_xml_line(item["old"]))
            lines.append("NEW:")
            lines.append(_format_channels_xml_line(item["new"]))
            lines.append("")
    else:
        lines.append("None")

    lines.extend([
        "",
        "====================",
        "Removed Channels",
        "====================",
        "",
    ])
    if removed:
        lines.extend(_format_channels_xml_line(entry) for entry in removed)
    else:
        lines.append("None")

    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {
        "path": str(report_path),
        "added": len(added),
        "updated": len(updated),
        "removed": len(removed),
    }


def write_boss_mapping(entries: list[dict]) -> str:
    output_path = _boss_mapping_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    seen = set()
    for entry in entries:
        xmltv_id = str(entry.get("xmltv_id") or "").strip()
        name = str(entry.get("name") or "").strip()
        if not xmltv_id or not name:
            continue
        key = (xmltv_id.casefold(), _mapping_name_for_compare(name))
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"{xmltv_id};;{name}::Radio: {name}")

    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return str(output_path)


def refresh_channels_xml_and_boss_mapping(db: DBSession) -> dict:
    xml_path = _channels_xml_output_path()
    xml_path.parent.mkdir(parents=True, exist_ok=True)

    old_entries = _parse_existing_channels_xml(xml_path)
    backup_path = _backup_file(xml_path)

    current_map = _load_existing_channel_map()
    generated = generate_channels_xml_entries(db, current_map)
    sections = generated["sections"]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<site generator-info-name="WebGrab+Plus/w MDB &amp; REX Postprocess -- version V5.2.2.0 -- Jan van Straaten" site="siriusxm">',
        '<channels>',
        '<!-- ON NOW MATCHED -->',
    ]
    lines.extend(_format_channels_xml_line(entry) for entry in sections["on_now"])
    lines.extend(_format_channels_xml_line(entry) for entry in sections["sports"])
    lines.extend([
        "",
        "<!-- XTRA Channels -->",
    ])
    lines.extend(_format_channels_xml_line(entry) for entry in sections["xtra"])
    lines.extend([
        "",
        "</channels>",
        "</site>",
    ])

    xml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    changes = _write_channels_xml_changes_report(
        old_entries,
        generated["all"],
    )
    boss_path = write_boss_mapping(generated["all"])

    return {
        "path": str(xml_path),
        "backup_path": backup_path,
        "changes_path": changes["path"],
        "added": changes["added"],
        "updated": changes["updated"],
        "removed": changes["removed"],
        "boss_mapping_path": boss_path,
        "count": len(generated["all"]),
    }


def _split_extinf_metadata_and_name(line: str) -> tuple[str, str]:
    """Split an EXTINF line at the first comma that is outside quotes."""
    in_quotes = False
    escape = False

    for index, char in enumerate(line):
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_quotes = not in_quotes
            continue
        if char == "," and not in_quotes:
            return line[:index], line[index + 1:]

    return line, ""


def read_existing_m3u_channels(
    db: Optional[DBSession] = None,
    output_path: Optional[Path] = None,
) -> list[dict]:
    """Parse the current generated M3U into a channel snapshot.

    The on-disk M3U is the authoritative "before" state for Refresh Channels.
    Database metadata is used only to enrich parsed entries with public UUID/path
    information needed for changes.txt.
    """
    output_path = Path(output_path) if output_path else _playlist_output_path()
    if not output_path.exists():
        return []

    db_metadata = {}
    if db is not None:
        for channel in db.query(Channel).all():
            channel_id = str(channel.channel_id or "")
            if not channel_id:
                continue
            db_metadata[channel_id] = {
                "public_uuid": str(getattr(channel, "public_uuid", None) or ""),
                "public_path": str(getattr(channel, "public_path", None) or ""),
                "number": channel.number,
                "category": channel.category,
                "genre": channel.genre,
            }

    try:
        lines = output_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    channels = []
    pending = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#EXTINF"):
            metadata_text, display_name = _split_extinf_metadata_and_name(line)
            attrs = {
                match.group(1): html_lib.unescape(match.group(2))
                for match in re.finditer(r'([A-Za-z0-9_-]+)="([^"]*)"', metadata_text)
            }
            name = html_lib.unescape(display_name.strip())
            if not name:
                name = attrs.get("tvg-name", "")

            pending = {
                "name": name or "Unknown",
                "channel_type": attrs.get("x-sxm-type", "channel-linear"),
            }
            continue

        if line.startswith("#") or pending is None:
            continue

        # Ignore ArchiveXM Jukebox playlist pseudo-channels; changes.txt is only
        # for SiriusXM channel lineup changes.
        if str(pending.get("channel_type") or "").lower() == "archivexm-playlist":
            pending = None
            continue

        channel_id = ""
        try:
            path = unquote(urlparse(line).path or "")
            listen_match = re.search(r"/listen/([^/?#]+)", path)
            api_match = re.search(r"/api/streams/([^/?#]+)/proxy-stream", path)
            match = listen_match or api_match
            if match:
                channel_id = match.group(1)
        except Exception:
            channel_id = ""

        if not channel_id:
            pending = None
            continue

        metadata = db_metadata.get(channel_id, {})
        channels.append({
            "channel_id": channel_id,
            "channel_type": pending.get("channel_type") or "channel-linear",
            "public_uuid": metadata.get("public_uuid") or "",
            "public_path": metadata.get("public_path") or "",
            "name": pending.get("name") or "Unknown",
            "number": metadata.get("number"),
            "category": metadata.get("category"),
            "genre": metadata.get("genre"),
        })
        pending = None

    return channels


def backup_existing_m3u(output_path: Optional[Path] = None) -> Optional[str]:
    """Preserve the currently generated M3U before a successful channel refresh."""
    output_path = Path(output_path) if output_path else _playlist_output_path()
    return _backup_file(output_path)


def _xmltv_id_for_channel(channel: dict) -> str:
    name = str(channel.get("name") or "")
    base = re.sub(r"[^A-Za-z0-9]+", "", name)
    if not base:
        base = re.sub(
            r"[^A-Za-z0-9]+",
            "",
            str(channel.get("public_uuid") or channel.get("channel_id") or "Channel"),
        )
    return f"{base}.sxm"


def _site_id_for_channel(channel: dict) -> str:
    channel_type = str(channel.get("channel_type") or "channel-linear").lower()
    if channel_type == "channel-xtra":
        return "dummy"

    public_path = str(channel.get("public_path") or "").strip()
    public_uuid = str(channel.get("public_uuid") or "").strip()

    if public_path.lower().startswith("/sports/"):
        return public_path

    slug = ""
    if public_path:
        clean_path = public_path.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        if clean_path.lower().startswith("/channels/"):
            slug = clean_path[len("/channels/"):]
        else:
            slug = clean_path.strip("/").rsplit("/", 1)[-1]

    if not slug:
        # Last-resort fallback for a public object that exposes a UUID/name but
        # no URL. Prefer the real public_path whenever SiriusXM provides it.
        slug = re.sub(r"[^a-z0-9]+", "-", str(channel.get("name") or "").lower()).strip("-")

    if public_uuid and slug:
        return f"{slug}##{public_uuid}"
    return public_path or slug or public_uuid or str(channel.get("channel_id") or "")


def format_webgrab_channel_line(channel: dict) -> str:
    site_id = html_lib.escape(_site_id_for_channel(channel), quote=True)
    xmltv_id = html_lib.escape(_xmltv_id_for_channel(channel), quote=True)
    name = html_lib.escape(str(channel.get("name") or "Unknown"), quote=False)
    return (
        f'<channel update="i" site="siriusxm" site_id="{site_id}" '
        f'xmltv_id="{xmltv_id}">{name}</channel>'
    )


def write_channel_changes_report(
    added: list[dict],
    renamed: list[dict],
    removed: list[dict],
    previous_count: int,
    current_count: int,
) -> str:
    """Write changes.txt beside the generated M3U after a successful refresh."""
    output_path = _playlist_output_path()
    report_path = output_path.parent / "changes.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "ArchiveXM SiriusXM Channel Changes",
        f"Refresh: {datetime.now().strftime('%m/%d/%Y %I:%M %p')}",
        "",
        f"Previous channels: {previous_count}",
        f"Current channels: {current_count}",
        "",
    ]

    lines.extend([
        "====================",
        "Added Channels",
        "====================",
        "",
    ])
    if added:
        for channel in added:
            lines.append(format_webgrab_channel_line(channel))
    else:
        lines.append("None")

    lines.extend([
        "",
        "",
        "====================",
        "Renamed Channels",
        "====================",
        "",
    ])
    if renamed:
        for item in renamed:
            old = item["old"]
            new = item["new"]
            lines.append(f"OLD: {old.get('name') or 'Unknown'}")
            lines.append(f"NEW: {new.get('name') or 'Unknown'}")
            identity = new.get("public_uuid") or new.get("channel_id")
            if identity:
                lines.append(f"UUID: {identity}")
            lines.append(format_webgrab_channel_line(new))
            lines.append("")
    else:
        lines.append("None")

    lines.extend([
        "",
        "====================",
        "Removed Channels",
        "====================",
        "",
    ])
    if removed:
        for channel in removed:
            lines.append(format_webgrab_channel_line(channel))
    else:
        lines.append("None")

    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return str(report_path)


def write_m3u_to_file(db: DBSession) -> dict:
    playlist = generate_m3u(db)

    output_path = _playlist_output_path()
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
