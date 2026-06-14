"""
SiriusXM API Service - Handle all API interactions
"""
import httpx
import base64
import json
import html as html_lib
import re
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
import asyncio


class SiriusXMAPI:
    """SiriusXM API client"""
    
    BASE_URL = 'https://api.edge-gateway.siriusxm.com'
    
    CDN_BASE = "https://imgsrv-sxm-prod-device.streaming.siriusxm.com/"
    
    MAX_RETRIES = 2

    # Optional channel-number based group/category overrides. These match the
    # old m3u8XM script and are applied during channel refresh so both the
    # ArchiveXM UI and generated M3U use the preferred groups.
    CHANNEL_GROUP_OVERRIDES = {
        "1308": "Workout",          # Alt Workout
        "1302": "Party",            # Oldies Party
        "1085": "The 70s Decade",   # 70s on 7 Dance/R&B
        "1177": "The 70s Decade",   # 70s on 7 Just Music
        "739": "Country",           # Savior Sunday Daily by Carrie's Country
    }

    # Optional UUID-based type overrides for rare cases where both public and
    # authenticated metadata disagree with reality.
    CHANNEL_TYPE_OVERRIDES = {}
    
    def __init__(self, bearer_token: str = None):
        self._token_manager = None
        self.bearer_token = bearer_token
        self._update_headers()
    
    def _update_headers(self):
        """Update headers with current bearer token"""
        self.headers = {
            'Authorization': f'Bearer {self.bearer_token}' if self.bearer_token else '',
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
    
    async def _ensure_valid_token(self) -> bool:
        """Ensure we have a valid token, refreshing if needed"""
        from services.token_manager import get_token_manager
        
        if self._token_manager is None:
            self._token_manager = get_token_manager()
        
        token = await self._token_manager.get_valid_token()
        if token and token != self.bearer_token:
            self.bearer_token = token
            self._update_headers()
        
        return self.bearer_token is not None
    
    async def _refresh_and_retry(self) -> bool:
        """Refresh token after a 401/403 error"""
        from services.token_manager import get_token_manager
        
        if self._token_manager is None:
            self._token_manager = get_token_manager()
        
        success = await self._token_manager.refresh_token()
        if success:
            self.bearer_token = self._token_manager.bearer_token
            self._update_headers()
        return success
    
    def _build_cdn_image_url(self, image_path: str, width: int = 300, height: int = 300) -> str:
        """
        Build a proper SXM CDN image URL with base64-encoded config
        
        SXM uses URLs like:
        https://imgsrv-sxm-prod-device.streaming.siriusxm.com/{base64_config}
        
        Where base64_config is JSON: {"key":"path/to/image.jpeg","edits":[...]}
        """
        config = {
            "key": image_path,
            "edits": [
                {"format": {"type": "jpeg"}},
                {"resize": {"height": height, "width": width}}
            ]
        }
        config_json = json.dumps(config, separators=(',', ':'))
        config_b64 = base64.b64encode(config_json.encode()).decode()
        return f"{self.CDN_BASE}{config_b64}"
    
    async def get_stream_url(self, channel_id: str, channel_type: str = "channel-linear") -> Optional[Dict]:
        """
        Get HLS stream URL for a channel using m3u8XM approach
        
        Args:
            channel_id: Channel UUID
            channel_type: Type of channel (channel-linear, aod-episode, etc)
            
        Returns:
            Dict with stream info including URLs
        """
        await self._ensure_valid_token()
        
        url = f'{self.BASE_URL}/playback/play/v1/tuneSource'
        
        payload = {
            'id': channel_id,
            'type': channel_type,
            'hlsVersion': 'V3',
            'manifestVariant': 'WEB' if channel_type == 'channel-linear' else 'FULL',
            'mtcVersion': 'V2'
        }

        if channel_type == "channel-xtra":
            payload["trackResumeSupported"] = False
        
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(url, headers=self.headers, json=payload, timeout=15)
                    
                    # Auto-retry on 401/403 (like m3u8XM approach)
                    if response.status_code in (401, 403) and attempt < self.MAX_RETRIES:
                        print(f"⚠️ Stream URL got {response.status_code}, refreshing token (attempt {attempt + 1})")
                        if await self._refresh_and_retry():
                            continue
                        return None
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        # Extract stream URL from response
                        streams = data.get('streams', [])
                        if streams:
                            primary_url = streams[0].get('urls', [{}])[0].get('url')
                            if primary_url:
                                return {
                                    'stream_url': primary_url,
                                    'raw_data': data
                                }
                        
                        # Fallback to other fields
                        hls_url = data.get('hlsUrl') or data.get('primaryStreamUrl')
                        if hls_url:
                            return {'stream_url': hls_url, 'raw_data': data}
                        
                        print(f"No stream URL in response: {list(data.keys())}")
                        return None
                    else:
                        print(f"Stream URL API error: {response.status_code}")
                        print(f"Response: {response.text[:300]}")
                        return None
                        
            except Exception as e:
                print(f"Error getting stream URL: {e}")
                if attempt < self.MAX_RETRIES:
                    print(f"Retrying after error (attempt {attempt + 1})")
                    await self._refresh_and_retry()
                    continue
                return None
        
        return None
    
    async def get_schedule(self, channel_id: str, hours_back: int = 5) -> List[Dict]:
        """
        Fetch track schedule from liveUpdate API
        
        CRITICAL: Returns tracks in CHRONOLOGICAL order
        - LAST track in list = CURRENTLY PLAYING
        - Includes past tracks (5-hour DVR buffer)
        - Each track has EXACT UTC timestamp (millisecond precision)
        
        Args:
            channel_id: Channel ID
            hours_back: Hours back to fetch (1-5)
            
        Returns:
            List of tracks with exact timestamps
        """
        await self._ensure_valid_token()
        
        url = f'{self.BASE_URL}/playback/play/v1/liveUpdate'
        
        # Request from hours_back ago
        past_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        start_timestamp = past_time.isoformat().replace('+00:00', 'Z')
        
        payload = {
            'channelId': channel_id,
            'hlsVersion': 'V3',
            'manifestVariant': 'WEB',
            'mtcVersion': 'V2',
            'startTimestamp': start_timestamp
        }
        
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(url, headers=self.headers, json=payload, timeout=10)
                    
                    # Auto-retry on 401/403
                    if response.status_code in (401, 403) and attempt < self.MAX_RETRIES:
                        print(f"⚠️ Schedule API got {response.status_code}, refreshing token (attempt {attempt + 1})")
                        if await self._refresh_and_retry():
                            continue
                        return []
                    
                    if response.status_code == 200:
                        data = response.json()
                        items = data.get('items', [])
                        
                        tracks = []
                        now = datetime.now(timezone.utc)
                        
                        for item in items:
                            # Skip promos/interstitials
                            if item.get('isInterstitial', False):
                                continue
                            
                            track_timestamp = item.get('timestamp')
                            if track_timestamp:
                                try:
                                    track_time = datetime.fromisoformat(track_timestamp.replace('Z', '+00:00'))
                                    # Only include tracks in the past
                                    if track_time <= now:
                                        # Extract image URL from various possible locations
                                        image_url = None
                                        raw_image_path = None
                                        
                                        # Try images dict first (has tile structure with aspect ratios)
                                        images = item.get('images', {})
                                        if images and isinstance(images, dict):
                                            # Check for tile structure (tile -> aspect_1x1 -> preferredImage)
                                            tile = images.get('tile', {})
                                            if tile:
                                                for aspect in ['aspect_1x1', 'aspect_16x9']:
                                                    aspect_data = tile.get(aspect, {})
                                                    if aspect_data:
                                                        for img_type in ['preferredImage', 'defaultImage']:
                                                            img = aspect_data.get(img_type, {})
                                                            if img and img.get('url'):
                                                                raw_image_path = img['url']
                                                                break
                                                    if raw_image_path:
                                                        break
                                            
                                            # Fallback to flat structure
                                            if not raw_image_path:
                                                for key in ['default', 'large', 'medium', 'small']:
                                                    if key in images and images[key].get('url'):
                                                        raw_image_path = images[key]['url']
                                                        break
                                        
                                        # Try artistImages as fallback
                                        if not raw_image_path:
                                            artist_images = item.get('artistImages', {})
                                            if artist_images and isinstance(artist_images, dict):
                                                tile = artist_images.get('tile', {})
                                                if tile:
                                                    for aspect in ['aspect_1x1', 'aspect_16x9']:
                                                        aspect_data = tile.get(aspect, {})
                                                        if aspect_data:
                                                            for img_type in ['preferredImage', 'defaultImage']:
                                                                img = aspect_data.get(img_type, {})
                                                                if img and img.get('url'):
                                                                    raw_image_path = img['url']
                                                                    break
                                                        if raw_image_path:
                                                            break
                                        
                                        # Convert raw path to proper CDN URL with base64 config
                                        if raw_image_path:
                                            if raw_image_path.startswith('http'):
                                                image_url = raw_image_path
                                            else:
                                                image_url = self._build_cdn_image_url(raw_image_path)
                                        
                                        track = {
                                            'artist': item.get('artistName', 'Unknown'),
                                            'title': item.get('name', 'Unknown'),
                                            'album': item.get('albumName'),
                                            'timestamp_utc': track_timestamp,
                                            'duration_ms': item.get('duration', 0),
                                            'image_url': image_url
                                        }
                                        tracks.append(track)
                                except:
                                    continue
                        
                        return tracks
                    else:
                        print(f"Schedule API error: {response.status_code}")
                        return []
                        
            except Exception as e:
                print(f"API error: {e}")
                if attempt < self.MAX_RETRIES:
                    await self._refresh_and_retry()
                    continue
                return []
        
        return []
    
    def _normalize_public_text(self, value) -> str:
        """Clean text scraped from siriusxm.com/channels."""
        if value is None:
            return ""

        value = str(value)
        try:
            value = html_lib.unescape(value)
            # Repair common mojibake from UTF-8 decoded as Latin-1.
            if any(bad in value for bad in ("\u00c3", "\u00c2", "\u00e2")):
                try:
                    repaired = value.encode("latin-1").decode("utf-8")
                    if repaired:
                        value = repaired
                except Exception:
                    pass
        except Exception:
            pass

        return value.strip()

    def _json_object_around(self, text: str, pos: int) -> Optional[str]:
        """Return the JSON object surrounding pos, if it looks like a channel."""
        start = text.rfind('{', 0, pos)

        while start != -1:
            depth = 0
            in_string = False
            escape = False

            for i in range(start, len(text)):
                ch = text[i]
                if in_string:
                    if escape:
                        escape = False
                    elif ch == '\\':
                        escape = True
                    elif ch == '"':
                        in_string = False
                else:
                    if ch == '"':
                        in_string = True
                    elif ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            candidate = text[start:i + 1]
                            try:
                                obj = json.loads(candidate)
                                if (
                                    isinstance(obj, dict)
                                    and "streamingChannelNumber" in obj
                                    and "uuid" in obj
                                ):
                                    return candidate
                            except Exception:
                                break
                            break

            start = text.rfind('{', 0, start)

        return None

    async def fetch_public_channels(self) -> Dict[str, Dict]:
        """
        Fetch public SiriusXM channel metadata from siriusxm.com/channels.

        This mirrors the old m3u8XM strategy: authenticated browse data remains
        the playback source of truth, while the public page provides friendlier
        names and genres for the UI and M3U.
        """
        results = {}
        url = "https://www.siriusxm.com/channels"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers, timeout=20)

            if response.status_code != 200:
                print(f"Public channel page returned status {response.status_code}")
                return results

            raw_text = response.content.decode("utf-8", errors="replace")
            decoded_variants = [raw_text, html_lib.unescape(raw_text)]

            for candidate in list(decoded_variants):
                try:
                    decoded_variants.append(bytes(candidate, "utf-8").decode("unicode_escape"))
                except Exception:
                    pass

            seen_objects = set()
            for source in decoded_variants:
                for match in re.finditer(r'"streamingChannelNumber"\s*:\s*\d+', source):
                    obj_text = self._json_object_around(source, match.start())
                    if not obj_text or obj_text in seen_objects:
                        continue
                    seen_objects.add(obj_text)

                    try:
                        item = json.loads(obj_text)
                    except Exception:
                        continue

                    channel_number = item.get("streamingChannelNumber") or item.get("xmChannelNumber")
                    channel_uuid = item.get("uuid")
                    if not channel_number or not channel_uuid:
                        continue

                    title = self._normalize_public_text(item.get("displayName") or item.get("name"))
                    genre = self._normalize_public_text(item.get("genreTitle") or item.get("genre"))
                    is_xtra = bool(item.get("xtra_channel"))

                    logo_path = ""
                    web_image = item.get("web_2_0_image")
                    if isinstance(web_image, dict):
                        logo_path = web_image.get("url") or ""
                    logo_path = self._normalize_public_text(
                        logo_path or item.get("colorLogo") or item.get("greyscaleLogo") or ""
                    )

                    results[str(channel_number)] = {
                        "title": title,
                        "genre": genre,
                        "channel_type": "channel-xtra" if is_xtra else "channel-linear",
                        "id": str(channel_uuid),
                        "logo_path": logo_path,
                    }

            print(f"🌐 Loaded {len(results)} public channel metadata overrides")
            return results

        except Exception as e:
            print(f"Failed to fetch public channel metadata: {e}")
            return results

    def _format_public_logo_url(self, logo_path: str, width: int = 300, height: int = 300) -> str:
        """Return a CDN image URL for public channel logo paths."""
        if not logo_path:
            return ""
        logo_path = str(logo_path)
        if logo_path.startswith("http"):
            return logo_path
        return self._build_cdn_image_url(logo_path, width, height)

    def _resolve_channel_type(self, channel: Dict, public_override: Optional[Dict]) -> str:
        """Resolve channel type while keeping authenticated playback type first."""
        channel_uuid = str(channel.get("id") or "")
        if channel_uuid and channel_uuid in self.CHANNEL_TYPE_OVERRIDES:
            return self.CHANNEL_TYPE_OVERRIDES[channel_uuid]

        auth_type = channel.get("channel_type")
        if auth_type:
            return auth_type

        if public_override and public_override.get("channel_type"):
            return public_override["channel_type"]

        return "channel-linear"

    def _apply_public_channel_metadata(self, channels: List[Dict], public_map: Dict[str, Dict]) -> List[Dict]:
        """Merge public names/groups into authenticated channel rows."""
        if not public_map:
            return channels

        for channel in channels:
            number = channel.get("number")
            number_key = str(number) if number is not None else ""
            override = public_map.get(number_key, {})

            if override.get("title"):
                channel["name"] = override["title"]

            if override.get("genre"):
                channel["category"] = override["genre"]
                channel["genre"] = override["genre"]

            if number_key in self.CHANNEL_GROUP_OVERRIDES:
                group = self.CHANNEL_GROUP_OVERRIDES[number_key]
                channel["category"] = group
                channel["genre"] = group

            channel["channel_type"] = self._resolve_channel_type(channel, override)

            public_logo = self._format_public_logo_url(override.get("logo_path", "")) if override else ""
            if public_logo:
                channel["images"] = {
                    "thumbnail": public_logo,
                    "large": public_logo,
                }

        return channels

    async def fetch_all_channels(self) -> List[Dict]:
        """
        Fetch complete channel list from API
        Based on m3u8XM approach
        
        Returns all 700+ channels with full metadata
        """
        await self._ensure_valid_token()
        
        CDN_URL = "https://imgsrv-sxm-prod-device.streaming.siriusxm.com/{}"
        
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                channels = []
                
                async with httpx.AsyncClient() as client:
                    # Initial request to get first batch of channels
                    init_payload = {
                        "containerConfiguration": {
                            "3JoBfOCIwo6FmTpzM1S2H7": {
                                "filter": {
                                    "one": {
                                        "filterId": "all"
                                    }
                                },
                                "sets": {
                                    "5mqCLZ21qAwnufKT8puUiM": {
                                        "sort": {
                                            "sortId": "CHANNEL_NUMBER_ASC"
                                        }
                                    }
                                }
                            }
                        },
                        "pagination": {
                            "offset": {
                                "containerLimit": 3,
                                "setItemsLimit": 50
                            }
                        },
                        "deviceCapabilities": {
                            "supportsDownloads": False
                        }
                    }
                    
                    url = f'{self.BASE_URL}/browse/v1/pages/curated-grouping/403ab6a5-d3c9-4c2a-a722-a94a6a5fd056/view'
                    response = await client.post(url, headers=self.headers, json=init_payload, timeout=30)
                    
                    # Auto-retry on 401/403
                    if response.status_code in (401, 403) and attempt < self.MAX_RETRIES:
                        print(f"⚠️ Channels API got {response.status_code}, refreshing token (attempt {attempt + 1})")
                        if await self._refresh_and_retry():
                            break  # Break inner loop to retry outer loop
                        return []
                    
                    if response.status_code != 200:
                        print(f"Channels API error: {response.status_code}")
                        print(f"Response: {response.text[:500]}")
                        return []
                    
                    data = response.json()
                    
                    # Parse initial batch
                    try:
                        items = data["page"]["containers"][0]["sets"][0]["items"]
                        total_channels = data["page"]["containers"][0]["sets"][0]["pagination"]["offset"]["size"]
                        
                        for item in items:
                            ch = self._parse_channel_item(item, CDN_URL)
                            if ch:
                                channels.append(ch)
                        
                        print(f"📻 Loaded {len(channels)}/{total_channels} channels...")
                        
                        # Fetch remaining channels in batches of 50
                        for offset in range(50, total_channels, 50):
                            batch_payload = {
                                "filter": {
                                    "one": {
                                        "filterId": "all"
                                    }
                                },
                                "sets": {
                                    "5mqCLZ21qAwnufKT8puUiM": {
                                        "sort": {
                                            "sortId": "CHANNEL_NUMBER_ASC"
                                        },
                                        "pagination": {
                                            "offset": {
                                                "setItemsOffset": offset,
                                                "setItemsLimit": 50
                                            }
                                        }
                                    }
                                },
                                "pagination": {
                                    "offset": {
                                        "setItemsLimit": 50
                                    }
                                }
                            }
                            
                            batch_url = f'{self.BASE_URL}/browse/v1/pages/curated-grouping/403ab6a5-d3c9-4c2a-a722-a94a6a5fd056/containers/3JoBfOCIwo6FmTpzM1S2H7/view'
                            response = await client.post(batch_url, headers=self.headers, json=batch_payload, timeout=30)
                            
                            if response.status_code == 200:
                                batch_data = response.json()
                                batch_items = batch_data.get("container", {}).get("sets", [{}])[0].get("items", [])
                                
                                for item in batch_items:
                                    ch = self._parse_channel_item(item, CDN_URL)
                                    if ch:
                                        channels.append(ch)
                                
                                print(f"📻 Loaded {len(channels)}/{total_channels} channels...")
                            
                    except KeyError as e:
                        print(f"Error parsing channel data: {e}")
                    
                    public_map = await self.fetch_public_channels()
                    channels = self._apply_public_channel_metadata(channels, public_map)

                    print(f"✅ Loaded {len(channels)} channels total")
                    return channels
                
            except Exception as e:
                print(f"Error fetching channels: {e}")
                import traceback
                traceback.print_exc()
                if attempt < self.MAX_RETRIES:
                    await self._refresh_and_retry()
                    continue
                return []
        
        return []
    
    def _parse_channel_item(self, item: Dict, cdn_url: str) -> Optional[Dict]:
        """Parse a single channel item from API response"""
        import base64
        import json
        
        try:
            entity = item.get("entity", {})
            texts = entity.get("texts", {})
            images = entity.get("images", {})
            decorations = item.get("decorations", {})
            actions = item.get("actions", {})
            
            title = texts.get("title", {}).get("default", "Unknown")
            description = texts.get("description", {}).get("default", "")
            genre = decorations.get("genre", "")
            channel_id = entity.get("id", "")
            channel_number = (
                decorations.get("channelNumber")
                or decorations.get("streamingChannelNumber")
                or decorations.get("xmChannelNumber")
            )
            try:
                channel_number = int(channel_number) if channel_number not in (None, "") else None
            except (TypeError, ValueError):
                channel_number = None
            
            # Get channel type from actions
            channel_type = "channel-linear"
            if actions.get("play"):
                channel_type = actions["play"][0].get("entity", {}).get("type", "channel-linear")
            
            # Build logo URL
            logo_url = None
            try:
                tile_images = (
                    images.get("tile", {}).get("aspect_1x1", {}).get("preferred", {})
                    or images.get("tile", {}).get("aspect_1x1", {}).get("preferredImage", {})
                )
                if tile_images:
                    logo_key = tile_images.get("url", "")
                    logo_width = tile_images.get("width", 300)
                    logo_height = tile_images.get("height", 300)
                    
                    if logo_key:
                        json_logo = json.dumps({
                            "key": logo_key,
                            "edits": [
                                {"format": {"type": "jpeg"}},
                                {"resize": {"width": logo_width, "height": logo_height}}
                            ]
                        }, separators=(',', ':'))
                        b64_logo = base64.b64encode(json_logo.encode("ascii")).decode("utf-8")
                        logo_url = cdn_url.format(b64_logo)
            except:
                pass
            
            return {
                'id': channel_id,
                'name': title,
                'number': channel_number,
                'category': genre,
                'genre': genre,
                'description': description,
                'channel_type': channel_type,
                'images': {
                    'thumbnail': logo_url,
                    'large': logo_url
                }
            }
            
        except Exception as e:
            print(f"Error parsing channel: {e}")
            return None
    
    async def get_current_track(self, channel_id: str) -> Optional[Dict]:
        """Get currently playing track"""
        tracks = await self.get_schedule(channel_id, hours_back=1)
        return tracks[-1] if tracks else None
