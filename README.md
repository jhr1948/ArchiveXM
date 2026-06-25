# ArchiveXM

<p align="center">
  <img src="logo.png" alt="ArchiveXM Logo" width="200"/>
</p>

**ArchiveXM** is a modern web-based SiriusXM streaming and archival application. Browse 700+ channels, listen live, explore the 5-hour DVR buffer, download tracks with full metadata, and enjoy your collection with the built-in Jukebox player.

This fork adds improved music metadata matching, canonical album prioritization, typo-tolerant lookup, short artist/acronym handling, and Jukebox/library workflow refinements.

<p align="center">
  <img src="screenshots/channels_page_active_recording.png" alt="ArchiveXM Channels Page" width="100%"/>
</p>

## What’s Different in This Fork

This fork builds on the original ArchiveXM project with usability and metadata improvements focused on downloaded music, jukebox playback, and library cleanup.

### Added / Improved

* **Improved Music Metadata Matching** - Metadata lookup now prioritizes canonical studio albums over singles, compilations, and live releases when possible.
* **Canonical Album Fallbacks** - Songs can resolve to their original album even when the album name is different from the track name.
* **Short Artist / Acronym Matching Fixes** - Metadata search handles short artist names and acronyms more reliably, such as `EMF` versus punctuation variants like `E.M.F.`.
* **Typo-Tolerant Metadata Lookup** - Common title spelling issues are handled more gracefully during metadata search, helping tracks still resolve when local filenames or SiriusXM metadata contain minor spelling differences.
* **Better Metadata Result Ranking** - Search results are ranked more intentionally to prefer studio albums, then singles, then compilations, while avoiding live albums unless the searched track appears to be a live version.
* **Jukebox and Library UI Improvements** - Additional frontend refinements improve the Jukebox and music library workflow.
* **M3U Playlist Generation** - This fork can generate M3U playlist output for use with external IPTV/player apps.
* **Reverse Proxy URL Support for M3U Links** - M3U stream URLs can be generated using a configured public/reverse-proxy base URL instead of only local Docker/internal addresses, making playlists usable outside the host network.
* * 📺 **M3U Playlist Generation** - Generate playlist output for external IPTV/player apps, with reverse-proxy-friendly stream URLs

### Metadata Ranking Notes

The metadata lookup tries to avoid applying compilation or live-album metadata when a clean studio-album match is available. Live albums are intentionally deprioritized unless the track title itself indicates a live version, such as `(Live)`, `Live at...`, or `Live from...`.

The preferred metadata result order is:

1. Studio Album
2. Single
3. Album / Compilation
4. Live Album, only when the searched track appears to be a live version

This makes downloaded tracks more likely to appear in the Jukebox under the original studio album rather than a later compilation, DJ mix, or live release.

## Features

* 🔐 **Secure Authentication** - Store SiriusXM credentials securely with auto-refresh
* 📻 **700+ Channels** - Browse all channels with artwork and descriptions
* 🎧 **Listen Live** - Stream any channel in real-time
* 📼 **DVR Buffer** - Access 5 hours of past content per channel
* ⬇️ **Download Tracks** - Download individual or bulk tracks with metadata
* 🎨 **Cover Art** - Automatic cover art embedding
* 🏷️ **Improved Metadata Tagging** - Full ID3 tags with smarter canonical album matching
* 💿 **Album Prioritization** - Prefer original studio albums over singles, compilations, and live releases when appropriate
* 🎬 **Live Recording** - Record live streams with auto-track splitting
* 🎵 **Jukebox Player** - Full-featured local music player with playlists and queue management
* 👥 **Multi-Account Support** - Add multiple SiriusXM accounts for increased stream capacity

## Quick Start

### Prerequisites

* Docker and Docker Compose
* SiriusXM subscription with streaming access

### Installation

1. Clone the repository:

```bash
git clone https://github.com/yourusername/ArchiveXM.git
cd ArchiveXM
```

2. Start the application:

```bash
docker-compose up -d
```

3. Open your browser to `http://localhost:8743`

4. Enter your SiriusXM credentials and configure download location

### Configuration

Edit `.env` file or environment variables:

| Variable        | Default     | Description              |
| --------------- | ----------- | ------------------------ |
| `FRONTEND_PORT` | 8743        | Web UI port              |
| `BACKEND_PORT`  | 8742        | API port                 |
| `DOWNLOAD_PATH` | ./downloads | Local download directory |

### Reverse Proxy / M3U Playlist URLs

This fork adds M3U playlist generation support. When using ArchiveXM behind a reverse proxy, configure the public-facing base URL so generated M3U entries point to the correct externally reachable address.

This is useful when importing the generated M3U playlist into external players or IPTV clients that need to access ArchiveXM through a reverse proxy, custom domain, or HTTPS endpoint.

Example use cases:

* Accessing generated M3U playlists from another device on your network
* Using ArchiveXM behind Nginx Proxy Manager, Traefik, SWAG, Caddy, or another reverse proxy
* Generating playlist URLs that use a public hostname instead of a local container or LAN address
* Supporting external players that need fully qualified stream URLs

### Ports

* **8743** - Web interface
* **8742** - Backend API

## Architecture

```
ArchiveXM/
├── backend/           # FastAPI Python backend
│   ├── services/      # Auth, API, HLS, Download services
│   ├── models/        # Database models
│   └── routers/       # API endpoints
├── frontend/          # React + Vite + TailwindCSS
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   └── services/
├── data/              # SQLite database, config
├── downloads/         # Downloaded tracks
└── docker-compose.yml
```

## Tech Stack

**Backend:**

* Python 3.11+
* FastAPI
* SQLAlchemy
* httpx (async HTTP client)
* Mutagen (audio metadata)
* FFmpeg (audio processing)

**Frontend:**

* React 18
* Vite
* TailwindCSS
* HLS.js (live streaming)
* Lucide Icons
* React Router

## Screenshots

### Channel Browser

Browse 700+ SiriusXM channels with artwork, search, and category filtering.

<p align="center">
  <img src="screenshots/channels_page_active_recording.png" alt="Channel Browser" width="100%"/>
</p>

### Channel Detail & Live Recording

View station history, now playing info, and record live streams with automatic track splitting.

<p align="center">
  <img src="screenshots/station_history_channel_page.png" alt="Channel Detail with Recording" width="100%"/>
</p>

### DVR Buffer & Downloads

Access 5 hours of past content and download tracks with full metadata and cover art.

<p align="center">
  <img src="screenshots/channel_history_view.png" alt="DVR Buffer History" width="80%"/>
</p>

<p align="center">
  <img src="screenshots/active_download_display.png" alt="Active Downloads" width="80%"/>
</p>

### Jukebox Player

Full-featured music player with queue management, playlists, and playback controls.

<p align="center">
  <img src="screenshots/jukebox_playback_with_active_recording.png" alt="Jukebox Player" width="100%"/>
</p>

### Multi-Account Settings

Manage multiple SiriusXM accounts to increase concurrent stream capacity.

<p align="center">
  <img src="screenshots/settings.png" alt="Settings - Multi-Account" width="100%"/>
</p>

## Jukebox Player

The built-in Jukebox lets you enjoy your downloaded music collection:

* 🎵 **Library Browser** - View all tracks, artists, and albums
* 🔀 **Queue Management** - Build and manage playback queue
* 📋 **Playlists** - Create and manage custom playlists
* 🔁 **Playback Controls** - Shuffle, repeat, seek, volume
* 🔍 **Search** - Quick search across your library

Access the Jukebox from the navigation bar after downloading some tracks.

## License

MIT License - See LICENSE file for details.

## Acknowledgments

* SiriusXM for providing the streaming service
