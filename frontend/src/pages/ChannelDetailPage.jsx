import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { 
  ArrowLeft, Download, Clock, Music, 
  CheckCircle, Loader2, RefreshCw, Radio, Play, Pause
} from 'lucide-react'
import { channelsApi, streamsApi, downloadsApi } from '../services/api'
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
  
  const { currentChannel, isPlaying, isLoading: playerLoading, playChannel, togglePlay } = usePlayer()

  useEffect(() => {
    loadChannelData()
  }, [channelId])

  // Poll for current track updates every 15 seconds
  useEffect(() => {
    if (!channelId || loading) return
    
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
    
    const interval = setInterval(pollCurrentTrack, 15000)
    return () => clearInterval(interval)
  }, [channelId, loading])

  const loadChannelData = async () => {
    setLoading(true)
    try {
      const [channelRes, scheduleRes] = await Promise.all([
        channelsApi.get(channelId),
        streamsApi.getSchedule(channelId, 5)
      ])
      
      setChannel(channelRes.data)
      setSchedule(scheduleRes.data)
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

  const currentTrack = schedule?.current_track
  const tracks = schedule?.tracks || []

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

      {/* Recording Panel */}
      <div className="card mb-6">
        <RecordingPanel channelId={channelId} channelName={channel.name} channel={channel} />
      </div>

      {/* DVR Buffer / Track History */}
      <div className="card">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-4">
          <div>
            <h2 className="text-xl font-bold text-white">Station History</h2>
            <p className="text-gray-400 text-sm">
              Last 5 hours • {tracks.length} tracks
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
                <button
                  onClick={downloadSelected}
                  disabled={downloading}
                  className="btn-primary text-sm py-1 px-3 flex items-center gap-2 ml-auto"
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

                {/* Download Button */}
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
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default ChannelDetailPage
