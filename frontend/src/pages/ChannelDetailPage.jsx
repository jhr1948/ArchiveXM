import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { 
  ArrowLeft, Download, Clock, Music, 
  CheckCircle, Loader2, RefreshCw, Radio, Play, Pause, ListPlus, X
} from 'lucide-react'
import { channelsApi, streamsApi, downloadsApi, libraryApi } from '../services/api'
import RecordingPanel from '../components/RecordingPanel'
import { usePlayer } from '../context/PlayerContext'

function ChannelDetailPage() {
  const { channelId } = useParams()
  const [channel, setChannel] = useState(null)
  const [schedule, setSchedule] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [selectedTracks, setSelectedTracks] = useState(new Set())
  const [downloading, setDownloading] = useState(false)
  const [downloadingTracks, setDownloadingTracks] = useState(new Set())
  const [playlists, setPlaylists] = useState([])
  const [playlistTarget, setPlaylistTarget] = useState(null)
  const [newPlaylistName, setNewPlaylistName] = useState('')
  const [playlistActionLoading, setPlaylistActionLoading] = useState(false)
  const [xtraQueue, setXtraQueue] = useState(null)
  const [xtraQueueLoading, setXtraQueueLoading] = useState(false)
  
  const { currentChannel, currentTrack: playerCurrentTrack, isPlaying, isLoading: playerLoading, playChannel, togglePlay } = usePlayer()

  const isXtraChannel = (item = channel) => {
    const type = String(item?.channel_type || item?.channelType || item?.type || '').toLowerCase()
    return type === 'channel-xtra' || type.includes('xtra')
  }

  useEffect(() => {
    loadChannelData()
  }, [channelId])

  useEffect(() => {
    if (!channelId || loading || !channel || !isXtraChannel(channel)) {
      setXtraQueue(null)
      return
    }

    let cancelled = false
    const loadXtraQueue = async () => {
      try {
        setXtraQueueLoading(true)
        const response = await streamsApi.getXtraQueue(channelId)
        if (!cancelled) {
          setXtraQueue(response.data || null)
        }
      } catch (e) {
        if (!cancelled) {
          console.error('Error loading XTRA queue:', e)
          setXtraQueue(null)
        }
      } finally {
        if (!cancelled) {
          setXtraQueueLoading(false)
        }
      }
    }

    loadXtraQueue()
    const interval = setInterval(loadXtraQueue, 5000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [channelId, loading, channel?.channel_type])

  // Poll for current track updates every 5 seconds so live metadata offsets feel responsive
  useEffect(() => {
    if (!channelId || loading || isXtraChannel(channel)) return
    
    const pollCurrentTrack = async () => {
      try {
        const response = await streamsApi.getSchedule(channelId, 1)
        if (response?.data?.current_track) {
          setSchedule(prev => ({
            ...prev,
            current_track: response.data.current_track
          }))
        }
      } catch (e) {
        console.error('Error polling current track:', e)
      }
    }
    
    const interval = setInterval(pollCurrentTrack, 5000)
    return () => clearInterval(interval)
  }, [channelId, loading, channel?.channel_type])

  const loadChannelData = async () => {
    setLoading(true)
    try {
      const [channelRes, scheduleRes, playlistsRes] = await Promise.all([
        channelsApi.get(channelId),
        streamsApi.getSchedule(channelId, 5),
        libraryApi.getPlaylists()
      ])
      
      setChannel(channelRes.data)
      setSchedule(scheduleRes.data)
      setPlaylists(playlistsRes.data || [])
    } catch (error) {
      console.error('Error loading channel:', error)
    } finally {
      setLoading(false)
    }
  }

  const refreshSchedule = async () => {
    setRefreshing(true)
    try {
      const response = await streamsApi.getSchedule(channelId, 5)
      setSchedule(response.data)
    } catch (error) {
      console.error('Error refreshing schedule:', error)
    } finally {
      setRefreshing(false)
    }
  }

  const toggleTrackSelection = (index) => {
    const newSelection = new Set(selectedTracks)
    if (newSelection.has(index)) {
      newSelection.delete(index)
    } else {
      newSelection.add(index)
    }
    setSelectedTracks(newSelection)
  }

  const selectAll = () => {
    if (schedule?.tracks) {
      setSelectedTracks(new Set(schedule.tracks.map((_, i) => i)))
    }
  }

  const selectNone = () => {
    setSelectedTracks(new Set())
  }

  const downloadSelected = async () => {
    if (selectedTracks.size === 0 || !schedule?.tracks) return

    setDownloading(true)
    try {
      const tracksToDownload = Array.from(selectedTracks).map(index => {
        const track = schedule.tracks[index]
        return {
          channel_id: channelId,
          artist: track.artist,
          title: track.title,
          album: track.album,
          timestamp_utc: track.timestamp_utc,
          duration_ms: track.duration_ms,
          image_url: track.image_url
        }
      })

      await downloadsApi.downloadBulk(channelId, tracksToDownload)
      setSelectedTracks(new Set())
      alert(`Started downloading ${tracksToDownload.length} tracks!`)
    } catch (error) {
      console.error('Download error:', error)
      alert('Download failed. Please try again.')
    } finally {
      setDownloading(false)
    }
  }

  const downloadSingle = async (track, index) => {
    // Add to downloading set for UI feedback
    setDownloadingTracks(prev => new Set([...prev, index]))
    
    try {
      await downloadsApi.downloadTrack({
        channel_id: channelId,
        artist: track.artist,
        title: track.title,
        album: track.album,
        timestamp_utc: track.timestamp_utc,
        duration_ms: track.duration_ms,
        image_url: track.image_url
      })
      // Keep in downloading state briefly to show feedback
      setTimeout(() => {
        setDownloadingTracks(prev => {
          const next = new Set(prev)
          next.delete(index)
          return next
        })
      }, 2000)
    } catch (error) {
      console.error('Download error:', error)
      setDownloadingTracks(prev => {
        const next = new Set(prev)
        next.delete(index)
        return next
      })
    }
  }

  const buildTrackPayload = (track) => ({
    channel_id: channelId,
    artist: track.artist,
    title: track.title,
    album: track.album,
    timestamp_utc: track.timestamp_utc,
    duration_ms: track.duration_ms,
    image_url: track.image_url
  })

  const openAddTrackToPlaylist = (track, index) => {
    setPlaylistTarget({ type: 'single', track, index })
    setNewPlaylistName('')
  }

  const openAddSelectedToPlaylist = () => {
    if (selectedTracks.size === 0) return
    setPlaylistTarget({ type: 'selected' })
    setNewPlaylistName('')
  }

  const closePlaylistModal = () => {
    if (playlistActionLoading) return
    setPlaylistTarget(null)
    setNewPlaylistName('')
  }

  const downloadToPlaylist = async (playlistPayload) => {
    if (!playlistTarget) return

    setPlaylistActionLoading(true)
    try {
      if (playlistTarget.type === 'selected') {
        const tracksToDownload = Array.from(selectedTracks).map(index => buildTrackPayload(schedule.tracks[index]))
        await downloadsApi.downloadBulk(channelId, tracksToDownload, playlistPayload)
        setSelectedTracks(new Set())
        alert(`Started downloading ${tracksToDownload.length} tracks and adding them to playlist.`)
      } else {
        const { track, index } = playlistTarget
        setDownloadingTracks(prev => new Set([...prev, index]))
        await downloadsApi.downloadTrackToPlaylist(buildTrackPayload(track), playlistPayload)
        setTimeout(() => {
          setDownloadingTracks(prev => {
            const next = new Set(prev)
            next.delete(index)
            return next
          })
        }, 2000)
      }

      const res = await libraryApi.getPlaylists()
      setPlaylists(res.data || [])

      setPlaylistTarget(null)
      setNewPlaylistName('')
    } catch (error) {
      console.error('Download to playlist error:', error)
      alert('Download + playlist failed. Please try again.')
    } finally {
      setPlaylistActionLoading(false)
    }
  }

  const downloadToExistingPlaylist = (playlist) => {
    downloadToPlaylist({ playlist_id: playlist.id })
  }

  const downloadToNewPlaylist = () => {
    const name = newPlaylistName.trim()
    if (!name) return
    downloadToPlaylist({ playlist_name: name })
  }

  // Check if this channel is currently playing
  const isThisChannelPlaying = currentChannel?.channel_id === channelId && isPlaying
  
  const handlePlayClick = () => {
    if (isThisChannelPlaying) {
      togglePlay()
    } else if (channel) {
      playChannel({
        channel_id: channelId,
        channel_type: channel.channel_type,
        name: channel.name,
        channel_number: channel.number,
        image: channel.large_image_url || channel.image_url
      })
    }
  }

  const formatTrackDuration = (ms) => {
    if (!ms) return '--:--'
    const seconds = Math.floor(Number(ms || 0) / 1000)
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const XtraQueueRow = ({ label, track, muted = false }) => {
    if (!track) return null
    return (
      <div className={`flex items-center gap-3 p-3 rounded-lg ${muted ? 'bg-sxm-darker/40' : 'bg-sxm-darker'}`}>
        <div className="w-12 h-12 rounded-lg bg-black/30 overflow-hidden flex items-center justify-center shrink-0">
          {(track.imageUrl || track.image_url) ? (
            <img src={track.imageUrl || track.image_url} alt="" className="w-full h-full object-cover" />
          ) : (
            <Music className="w-5 h-5 text-gray-600" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-xs uppercase tracking-wide text-sxm-accent font-semibold">{label}</span>
            {(track.durationMs || track.duration_ms) ? <span className="text-xs text-gray-500">{formatTrackDuration(track.durationMs || track.duration_ms)}</span> : null}
          </div>
          <p className="text-white font-medium truncate">{track.title || 'Unknown title'}</p>
          <p className="text-gray-400 text-sm truncate">{track.artist || 'Unknown artist'}{track.album ? ` • ${track.album}` : ''}</p>
        </div>
      </div>
    )
  }

  const formatDuration = (ms) => {
    if (!ms) return '--:--'
    const seconds = Math.floor(ms / 1000)
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-12 h-12 border-4 border-sxm-accent border-t-transparent rounded-full animate-spin"></div>
      </div>
    )
  }

  if (!channel) {
    return (
      <div className="text-center py-20">
        <p className="text-gray-400">Channel not found</p>
        <Link to="/" className="btn-primary mt-4 inline-block">
          Back to Channels
        </Link>
      </div>
    )
  }

  const playerChannelId = currentChannel?.channel_id || currentChannel?.id || currentChannel?.uuid
  const currentTrack = playerChannelId === channelId && playerCurrentTrack ? playerCurrentTrack : schedule?.current_track
  const tracks = schedule?.tracks || []
  const isXtraPage = isXtraChannel(channel)

  return (
    <div>
      {/* Back Button */}
      <Link
        to="/"
        className="inline-flex items-center gap-2 text-gray-400 hover:text-white mb-6 transition-colors"
      >
        <ArrowLeft size={20} />
        Back to Channels
      </Link>

      {/* Channel Header */}
      <div className="card mb-6">
        <div className="flex flex-col md:flex-row gap-6">
          {/* Channel Image */}
          <div className="w-32 h-32 rounded-xl overflow-hidden bg-sxm-darker shrink-0">
            {channel.image_url || channel.large_image_url ? (
              <img
                src={channel.large_image_url || channel.image_url}
                alt={channel.name}
                className="w-full h-full object-cover"
              />
            ) : (
              <div className="w-full h-full flex items-center justify-center">
                <Radio className="w-12 h-12 text-gray-600" />
              </div>
            )}
          </div>

          {/* Channel Info */}
          <div className="flex-1">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h1 className="text-2xl font-bold text-white">{channel.name}</h1>
                {channel.number && (
                  <span className="text-gray-400">Channel {channel.number}</span>
                )}
              </div>

            </div>

            {channel.description && (
              <p className="text-gray-400 text-sm mt-2 line-clamp-2">
                {channel.description}
              </p>
            )}

            {/* Now Playing */}
            {currentTrack && (
              <div className="mt-4 p-3 bg-sxm-darker rounded-lg">
                <p className="text-xs text-gray-500 mb-1">NOW PLAYING</p>
                <p className="text-white font-medium">
                  {currentTrack.artist} - {currentTrack.title}
                </p>
                {currentTrack.album && (
                  <p className="text-gray-400 text-sm">{currentTrack.album}</p>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Play Button */}
      <div className="mb-6">
        <button
          onClick={handlePlayClick}
          disabled={playerLoading}
          className="btn-primary flex items-center gap-3 px-6 py-3"
        >
          {playerLoading ? (
            <Loader2 className="w-5 h-5 animate-spin" />
          ) : isThisChannelPlaying ? (
            <Pause className="w-5 h-5" />
          ) : (
            <Play className="w-5 h-5" />
          )}
          {playerLoading ? 'Loading...' : isThisChannelPlaying ? 'Pause' : 'Play Live'}
        </button>
      </div>

      {/* XTRA Queue */}
      {isXtraPage && (
        <div className="card mb-6">
          <div className="flex items-center justify-between gap-4 mb-4">
            <div>
              <h2 className="text-xl font-bold text-white">XTRA Queue</h2>
              <p className="text-gray-400 text-sm">
                Now playing, coming up, and the one-track Back item when available.
              </p>
            </div>
            <button
              onClick={async () => {
                try {
                  setXtraQueueLoading(true)
                  const response = await streamsApi.getXtraQueue(channelId)
                  setXtraQueue(response.data || null)
                } catch (e) {
                  console.error('Error refreshing XTRA queue:', e)
                } finally {
                  setXtraQueueLoading(false)
                }
              }}
              disabled={xtraQueueLoading}
              className="btn-secondary flex items-center gap-2"
            >
              <RefreshCw className={`w-4 h-4 ${xtraQueueLoading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>

          {!xtraQueue?.hasActiveQueue && (
            <div className="p-4 rounded-lg bg-sxm-darker text-gray-400 text-sm">
              Start this XTRA channel to let ArchiveXM build the queue. Upcoming songs appear as ArchiveXM prefetches them.
            </div>
          )}

          {xtraQueue?.hasActiveQueue && (
            <div className="space-y-3">
              {xtraQueue.previous && <XtraQueueRow label="Previous" track={xtraQueue.previous} muted />}
              <XtraQueueRow label="Now" track={xtraQueue.current || currentTrack} />
              {xtraQueue.upcoming?.length > 0 ? (
                <div className="space-y-2">
                  {xtraQueue.upcoming.map((track, idx) => (
                    <XtraQueueRow key={`${track.trackId || track.title}-${idx}`} label={idx === 0 ? 'Coming Up' : `Up Next ${idx + 1}`} track={track} muted />
                  ))}
                </div>
              ) : (
                <div className="p-3 rounded-lg bg-sxm-darker/40 text-gray-500 text-sm">
                  No upcoming XTRA items prefetched yet. Keep playback running or use Next to build the queue.
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Recording Panel */}
      {!isXtraPage && (
        <div className="card mb-6">
          <RecordingPanel channelId={channelId} channelName={channel.name} channel={channel} />
        </div>
      )}

      {/* DVR Buffer / Track History */}
      <div className="card">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-4">
          <div>
            <h2 className="text-xl font-bold text-white">{isXtraPage ? 'XTRA History' : 'Station History'}</h2>
            <p className="text-gray-400 text-sm">
              {isXtraPage ? 'XTRA station history may be limited' : 'Last 5 hours'} • {tracks.length} tracks
            </p>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={refreshSchedule}
              disabled={refreshing}
              className="btn-secondary flex items-center gap-2"
            >
              <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </div>

        {/* Selection Actions */}
        {tracks.length > 0 && (
          <div className="flex flex-wrap items-center gap-3 mb-4 p-3 bg-sxm-darker rounded-lg">
            <button
              onClick={selectAll}
              className="text-sm text-sxm-accent hover:text-sxm-accent-hover"
            >
              Select All
            </button>
            <span className="text-gray-600">|</span>
            <button
              onClick={selectNone}
              className="text-sm text-gray-400 hover:text-white"
            >
              Select None
            </button>
            
            {selectedTracks.size > 0 && (
              <>
                <span className="text-gray-600">|</span>
                <span className="text-sm text-gray-400">
                  {selectedTracks.size} selected
                </span>
                <div className="ml-auto flex items-center gap-2">
                  <button
                    onClick={openAddSelectedToPlaylist}
                    disabled={downloading || playlistActionLoading}
                    className="btn-secondary text-sm py-1 px-3 flex items-center gap-2"
                  >
                    <ListPlus className="w-4 h-4" />
                    Download + Playlist
                  </button>
                  <button
                    onClick={downloadSelected}
                    disabled={downloading}
                    className="btn-primary text-sm py-1 px-3 flex items-center gap-2"
                  >
                    {downloading ? (
                      <>
                        <Loader2 className="w-4 h-4 animate-spin" />
                        Downloading...
                      </>
                    ) : (
                      <>
                        <Download className="w-4 h-4" />
                        Download Selected
                      </>
                    )}
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        {/* Track List */}
        {tracks.length === 0 ? (
          <div className="text-center py-12">
            <Music className="w-12 h-12 text-gray-600 mx-auto mb-4" />
            <p className="text-gray-400">No tracks in history</p>
            <button onClick={refreshSchedule} className="btn-primary mt-4">
              Load History
            </button>
          </div>
        ) : (
          <div className="space-y-1">
            {tracks.map((track, index) => (
              <div
                key={`${track.timestamp_utc}-${index}`}
                className={`flex items-center gap-3 p-3 rounded-lg transition-colors cursor-pointer ${
                  selectedTracks.has(index)
                    ? 'bg-sxm-accent/20 border border-sxm-accent'
                    : 'hover:bg-sxm-darker border border-transparent'
                }`}
                onClick={() => toggleTrackSelection(index)}
              >
                {/* Selection Checkbox */}
                <div className={`w-5 h-5 rounded border flex items-center justify-center shrink-0 ${
                  selectedTracks.has(index)
                    ? 'bg-sxm-accent border-sxm-accent'
                    : 'border-gray-600'
                }`}>
                  {selectedTracks.has(index) && (
                    <CheckCircle className="w-4 h-4 text-white" />
                  )}
                </div>

                {/* Track Image */}
                <div className="w-10 h-10 rounded bg-sxm-darker shrink-0 overflow-hidden">
                  {track.image_url ? (
                    <img
                      src={track.image_url}
                      alt=""
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center">
                      <Music className="w-5 h-5 text-gray-600" />
                    </div>
                  )}
                </div>

                {/* Track Info */}
                <div className="flex-1 min-w-0">
                  <p className="text-white font-medium truncate">
                    {track.title}
                  </p>
                  <p className="text-gray-400 text-sm truncate">
                    {track.artist}
                    {track.album && ` • ${track.album}`}
                  </p>
                </div>

                {/* Time */}
                <div className="text-right shrink-0">
                  <p className="text-gray-400 text-sm">
                    {track.time_ago || 'Now'}
                  </p>
                  <p className="text-gray-500 text-xs flex items-center gap-1 justify-end">
                    <Clock className="w-3 h-3" />
                    {formatDuration(track.duration_ms)}
                  </p>
                </div>

                {/* Track Actions */}
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      openAddTrackToPlaylist(track, index)
                    }}
                    disabled={downloadingTracks.has(index)}
                    className="p-2 rounded-lg transition-colors hover:bg-sxm-accent/20 text-gray-400 hover:text-sxm-accent disabled:opacity-50"
                    title="Download and add to playlist"
                  >
                    <ListPlus className="w-5 h-5" />
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      downloadSingle(track, index)
                    }}
                    disabled={downloadingTracks.has(index)}
                    className={`p-2 rounded-lg transition-colors ${
                      downloadingTracks.has(index)
                        ? 'bg-sxm-success/20 text-sxm-success'
                        : 'hover:bg-sxm-accent/20 text-gray-400 hover:text-sxm-accent'
                    }`}
                    title={downloadingTracks.has(index) ? 'Downloading...' : 'Download track'}
                  >
                    {downloadingTracks.has(index) ? (
                      <Loader2 className="w-5 h-5 animate-spin" />
                    ) : (
                      <Download className="w-5 h-5" />
                    )}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Download + Add to Playlist Modal */}
      {playlistTarget && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md shadow-2xl">
            <div className="flex items-start justify-between gap-4 mb-4">
              <div>
                <h3 className="text-xl font-bold text-white">Download + Add to Playlist</h3>
                <p className="text-gray-400 text-sm mt-1">
                  {playlistTarget.type === 'selected'
                    ? `${selectedTracks.size} selected track${selectedTracks.size === 1 ? '' : 's'}`
                    : `${playlistTarget.track?.artist || 'Unknown'} - ${playlistTarget.track?.title || 'Unknown'}`}
                </p>
              </div>
              <button
                onClick={closePlaylistModal}
                className="text-gray-400 hover:text-white"
                title="Close"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-2 max-h-60 overflow-y-auto mb-4">
              {playlists.length === 0 ? (
                <p className="text-gray-500 text-sm py-2">No playlists yet. Create one below.</p>
              ) : (
                playlists.map(playlist => (
                  <button
                    key={playlist.id}
                    onClick={() => downloadToExistingPlaylist(playlist)}
                    disabled={playlistActionLoading}
                    className="w-full flex items-center gap-3 px-4 py-3 bg-gray-800 hover:bg-gray-700 rounded-lg text-left disabled:opacity-50"
                  >
                    <ListPlus className="w-5 h-5 text-gray-500" />
                    <div className="min-w-0">
                      <div className="text-white truncate">{playlist.name}</div>
                      <div className="text-xs text-gray-500">{playlist.track_count || 0} tracks</div>
                    </div>
                  </button>
                ))
              )}
            </div>

            <div className="border-t border-gray-800 pt-4">
              <label className="block text-sm text-gray-400 mb-2">Create new playlist</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="Playlist name"
                  value={newPlaylistName}
                  onChange={(e) => setNewPlaylistName(e.target.value)}
                  className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-primary"
                />
                <button
                  onClick={downloadToNewPlaylist}
                  disabled={!newPlaylistName.trim() || playlistActionLoading}
                  className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/80 disabled:opacity-50"
                >
                  {playlistActionLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Create'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default ChannelDetailPage
