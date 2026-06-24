import { createContext, useContext, useState, useRef, useEffect, useCallback } from 'react'
import Hls from 'hls.js'
import api, { streamsApi } from '../services/api'
import { useJukebox } from './JukeboxContext'

const PlayerContext = createContext(null)

function getChannelId(channel) {
  return channel?.channel_id || channel?.id || channel?.uuid
}

function isXtraChannel(channel) {
  const type = String(channel?.channel_type || channel?.channelType || channel?.type || '').toLowerCase()
  return type === 'channel-xtra' || type.includes('xtra')
}

function normalizeXtraMetadata(data) {
  if (!data || data.ok === false) return null

  return {
    artist: data.artist || 'Unknown',
    title: data.title || 'Unknown',
    album: data.album || '',
    timestamp_utc: data.timestamp_utc || data.startedAt || null,
    duration_ms: data.duration_ms ?? data.durationMs ?? 0,
    started_at_ms: data.started_at_ms ?? data.startedAtMs ?? null,
    image_url: data.image_url || data.imageUrl || null,
    is_xtra: true,
    channel_id: data.channelId,
    track_id: data.trackId,
    position_ms: data.positionMs,
    available_forward_skips: data.availableForwardSkips,
    available_backward_skips: data.availableBackwardSkips
  }
}

export function PlayerProvider({ children }) {
  const audioRef = useRef(null)
  const hlsRef = useRef(null)
  const isChangingChannel = useRef(false)  // Prevent race conditions
  const currentChannelRef = useRef(null)
  const currentTrackRef = useRef(null)
  const isPlayingRef = useRef(false)
  const isLoadingRef = useRef(false)
  const isSkippingNextRef = useRef(false)
  const isSkippingPreviousRef = useRef(false)
  const lastXtraResumeAtRef = useRef(0)
  const lastXtraAudioProgressRef = useRef({ time: 0, seenAt: Date.now() })
  
  // Get Jukebox context to pause it when starting live stream
  const jukebox = useJukebox()
  
  const [currentChannel, setCurrentChannel] = useState(null)
  const [currentTrack, setCurrentTrack] = useState(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [isMuted, setIsMuted] = useState(false)
  const [volume, setVolume] = useState(0.8)
  const [error, setError] = useState(null)
  const [isSkippingNext, setIsSkippingNext] = useState(false)
  const [isSkippingPrevious, setIsSkippingPrevious] = useState(false)
  const [hasXtraPrevious, setHasXtraPrevious] = useState(false)

  useEffect(() => {
    currentChannelRef.current = currentChannel
    currentTrackRef.current = currentTrack
    isPlayingRef.current = isPlaying
    isLoadingRef.current = isLoading
    isSkippingNextRef.current = isSkippingNext
    isSkippingPreviousRef.current = isSkippingPrevious
  }, [currentChannel, currentTrack, isPlaying, isLoading, isSkippingNext, isSkippingPrevious])

  // Poll for current track info. Linear channels still use the normal schedule
  // endpoint; XTRA channels use the root metadata endpoint that tracks the
  // active XTRA queue by audio position.
  useEffect(() => {
    if (!currentChannel) return

    let cancelled = false
    const channelId = getChannelId(currentChannel)
    const isXtra = isXtraChannel(currentChannel)
    const pollMs = isXtra ? 3000 : 15000
    
    const fetchCurrentTrack = async () => {
      if (!channelId) return

      try {
        if (isXtra) {
          const positionMs = Math.max(0, Math.floor((audioRef.current?.currentTime || 0) * 1000))
          const response = await api.get(`/metadata/${channelId}`, {
            params: { positionMs }
          })
          const track = normalizeXtraMetadata(response?.data)
          if (!cancelled && track) {
            setCurrentTrack(track)
            setHasXtraPrevious(Number(track.available_backward_skips || track.availableBackwardSkips || 0) > 0)
          }
        } else {
          const response = await streamsApi.getSchedule(channelId, 1)
          if (!cancelled && response?.data?.current_track) {
            setCurrentTrack(response.data.current_track)
          }
        }
      } catch (e) {
        if (!cancelled) {
          console.error('Failed to fetch current track:', e)
        }
      }
    }
    
    fetchCurrentTrack()
    const interval = setInterval(fetchCurrentTrack, pollMs)
    
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [currentChannel, isPlaying])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy()
      }
    }
  }, [])

  // Update volume
  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.volume = isMuted ? 0 : volume
    }
  }, [volume, isMuted])

  // Register stop function with JukeboxContext so Jukebox playback fully takes over live streams.
  // Important: pausing only left currentChannel set, so the UI could keep showing the
  // live player banner while a Jukebox song was actually playing.
  useEffect(() => {
    if (jukebox?.registerPauseLiveStream) {
      const stopLiveForJukebox = () => {
        console.log('[Player] Stopping live stream for Jukebox')

        if (hlsRef.current) {
          hlsRef.current.destroy()
          hlsRef.current = null
        }

        if (audioRef.current) {
          audioRef.current.pause()
          audioRef.current.src = ''
          audioRef.current.load()
        }

        setIsPlaying(false)
        setIsLoading(false)
        setCurrentChannel(null)
        setCurrentTrack(null)
        setError(null)
        isChangingChannel.current = false
      }

      jukebox.registerPauseLiveStream(stopLiveForJukebox)
    }
  }, [jukebox])

  const playChannel = useCallback(async (channel, options = {}) => {
    if (!channel) return

    const { force = false, preserveTrack = false } = options
    const channelId = getChannelId(channel)
    if (!channelId) {
      setError('Missing channel id')
      return
    }
    
    // Prevent race conditions - if already changing, ignore unless forced by XTRA Next
    if (isChangingChannel.current && !force) {
      console.log('[Player] Already changing channel, ignoring request')
      return
    }
    
    console.log('[Player] Starting playback for:', channel.name)
    isChangingChannel.current = true
    
    // Pause Jukebox when starting live stream
    if (jukebox?.isPlaying) {
      console.log('[Player] Pausing Jukebox for live stream')
      jukebox.pause()
    }
    
    try {
      // First, fully stop any existing playback
      if (hlsRef.current) {
        console.log('[Player] Destroying existing HLS instance')
        hlsRef.current.destroy()
        hlsRef.current = null
      }
      
      if (audioRef.current) {
        audioRef.current.pause()
        audioRef.current.src = ''
        audioRef.current.load() // Reset audio element
      }
      
      // Clear previous state
      setIsPlaying(false)
      if (!preserveTrack) {
        setCurrentTrack(null)
      }
      setError(null)
      
      // Small delay to ensure cleanup is complete
      await new Promise(resolve => setTimeout(resolve, 100))
      
      // Now set loading and new channel
      setIsLoading(true)
      setCurrentChannel(channel)

      if (isXtraChannel(channel)) {
        const now = Date.now()
        lastXtraAudioProgressRef.current = { time: 0, seenAt: now }
        lastXtraResumeAtRef.current = now
      }
      
      let streamUrl = streamsApi.getProxyStreamUrl(channelId)

      // XTRA resume/next reloads can reuse the same logical proxy URL. Give
      // the browser/HLS.js a unique manifest URL on forced reloads so it must
      // request /proxy-stream again and consume the backend's prepared queued item.
      if (force && isXtraChannel(channel)) {
        const joiner = streamUrl.includes('?') ? '&' : '?'
        streamUrl = `${streamUrl}${joiner}_xtra_reload=${Date.now()}`
      }

      console.log('[Player] Stream URL:', streamUrl)
      
      // Make sure audio element exists
      if (!audioRef.current) {
        console.error('[Player] Audio element not available!')
        setError('Audio element not ready')
        setIsLoading(false)
        isChangingChannel.current = false
        return
      }
      
      if (Hls.isSupported()) {
        console.log('[Player] Using HLS.js')
        const hls = new Hls({
          enableWorker: true,
          lowLatencyMode: true,
          backBufferLength: 90
        })
        
        // Store reference immediately to track this instance
        hlsRef.current = hls
        
        hls.loadSource(streamUrl)
        hls.attachMedia(audioRef.current)
        
        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          // Check if this HLS instance is still current (not replaced)
          if (hlsRef.current !== hls) {
            console.log('[Player] HLS instance replaced, ignoring')
            hls.destroy()
            return
          }
          
          console.log('[Player] Manifest parsed, starting playback')
          setIsLoading(false)
          isChangingChannel.current = false
          
          audioRef.current.play()
            .then(() => {
              console.log('[Player] Playback started successfully')
              if (isXtraChannel(channel)) {
                lastXtraAudioProgressRef.current = {
                  time: audioRef.current?.currentTime || 0,
                  seenAt: Date.now()
                }
              }
              setIsPlaying(true)
            })
            .catch(err => {
              console.error('[Player] Playback failed:', err)
              setError('Failed to start playback: ' + err.message)
            })
        })
        
        hls.on(Hls.Events.ERROR, (event, data) => {
          // Check if this HLS instance is still current
          if (hlsRef.current !== hls) {
            return
          }
          
          console.error('HLS error:', data)
          if (data.fatal) {
            setIsLoading(false)
            isChangingChannel.current = false
            
            switch (data.type) {
              case Hls.ErrorTypes.NETWORK_ERROR:
                hls.startLoad()
                break
              case Hls.ErrorTypes.MEDIA_ERROR:
                hls.recoverMediaError()
                break
              default:
                setError('Stream error occurred')
                hls.destroy()
                break
            }
          }
        })
      } else if (audioRef.current.canPlayType('application/vnd.apple.mpegurl')) {
        audioRef.current.src = streamUrl
        audioRef.current.play()
          .then(() => {
            setIsLoading(false)
            if (isXtraChannel(channel)) {
              lastXtraAudioProgressRef.current = {
                time: audioRef.current?.currentTime || 0,
                seenAt: Date.now()
              }
            }
            setIsPlaying(true)
            isChangingChannel.current = false
          })
          .catch(err => {
            setIsLoading(false)
            setError('Failed to start playback')
            isChangingChannel.current = false
          })
      } else {
        setError('HLS not supported in this browser')
        setIsLoading(false)
        isChangingChannel.current = false
      }
    } catch (err) {
      console.error('[Player] Error in playChannel:', err)
      setError('Failed to start stream')
      setIsLoading(false)
      isChangingChannel.current = false
    }
  }, [jukebox])

  const skipNextXtra = useCallback(async () => {
    if (!currentChannel || !isXtraChannel(currentChannel) || isSkippingNext) return

    const channelId = getChannelId(currentChannel)
    if (!channelId) return

    try {
      setIsSkippingNext(true)
      setIsLoading(true)
      setError(null)

      const response = await api.get(`/xtra/${channelId}/next`)
      const metadata = response?.data?.metadata || response?.data
      const nextTrack = normalizeXtraMetadata(metadata)
      if (nextTrack) {
        setCurrentTrack(nextTrack)
      }
      setHasXtraPrevious(Number(response?.data?.availableBackwardSkips || 0) > 0)

      // The backend prepares the next XTRA item. Reload the HLS source so the
      // frontend starts the newly prepared item immediately.
      await playChannel(currentChannel, { force: true, preserveTrack: true })
    } catch (err) {
      console.error('[Player] XTRA next failed:', err)
      setError('Failed to skip XTRA track')
      setIsLoading(false)
    } finally {
      setIsSkippingNext(false)
    }
  }, [currentChannel, isSkippingNext, playChannel])

  const skipPreviousXtra = useCallback(async () => {
    if (!currentChannel || !isXtraChannel(currentChannel) || isSkippingPrevious) return

    const channelId = getChannelId(currentChannel)
    if (!channelId) return

    try {
      setIsSkippingPrevious(true)
      setIsLoading(true)
      setError(null)

      const response = await api.get(`/xtra/${channelId}/previous`)
      const metadata = response?.data?.metadata || response?.data
      const previousTrack = normalizeXtraMetadata(metadata)
      if (previousTrack) {
        setCurrentTrack(previousTrack)
      }
      setHasXtraPrevious(Number(response?.data?.availableBackwardSkips || 0) > 0)

      // The backend prepares the previous XTRA item. Reload the HLS source so
      // the frontend starts the prepared item immediately.
      await playChannel(currentChannel, { force: true, preserveTrack: true })
    } catch (err) {
      console.error('[Player] XTRA previous failed:', err)
      setHasXtraPrevious(false)
      setError('No previous XTRA track available')
      setIsLoading(false)
    } finally {
      setIsSkippingPrevious(false)
    }
  }, [currentChannel, isSkippingPrevious, playChannel])


  const togglePlay = useCallback(() => {
    if (!audioRef.current) return
    
    if (isPlaying) {
      audioRef.current.pause()
      setIsPlaying(false)
    } else {
      // Pause Jukebox when resuming live stream
      if (jukebox?.isPlaying) {
        console.log('[Player] Pausing Jukebox for live stream resume')
        jukebox.pause()
      }
      audioRef.current.play()
        .then(() => setIsPlaying(true))
        .catch(console.error)
    }
  }, [isPlaying, jukebox])

  const stop = useCallback(() => {
    if (hlsRef.current) {
      hlsRef.current.destroy()
      hlsRef.current = null
    }
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.src = ''
    }
    setIsPlaying(false)
    setCurrentChannel(null)
    setCurrentTrack(null)
    setHasXtraPrevious(false)
  }, [])

  const toggleMute = useCallback(() => {
    setIsMuted(prev => !prev)
  }, [])

  const currentChannelIsXtra = isXtraChannel(currentChannel)

  const resumeQueuedXtra = useCallback(async () => {
    const channel = currentChannelRef.current
    if (!channel || !isXtraChannel(channel)) {
      setIsPlaying(false)
      return
    }

    const channelId = getChannelId(channel)
    if (!channelId || isChangingChannel.current || isSkippingNextRef.current || isSkippingPreviousRef.current) {
      setIsPlaying(false)
      return
    }

    try {
      console.log('[Player] XTRA HLS ended; resuming queued item')
      setIsLoading(true)
      setError(null)

      const response = await api.get(`/xtra/${channelId}/resume`)
      const metadata = response?.data?.metadata || response?.data
      const resumeTrack = normalizeXtraMetadata(metadata)
      if (resumeTrack) {
        setCurrentTrack(resumeTrack)
      }
      setHasXtraPrevious(Number(response?.data?.availableBackwardSkips || 0) > 0)

      lastXtraResumeAtRef.current = Date.now()
      lastXtraAudioProgressRef.current = { time: 0, seenAt: Date.now() }

      await playChannel(channel, { force: true, preserveTrack: true })
    } catch (err) {
      console.error('[Player] XTRA resume failed:', err)
      setIsPlaying(false)
      setIsLoading(false)
    }
  }, [playChannel])

  // ArchiveXM's backend stitches XTRA items into the local HLS stream, but some
  // browsers do not reliably fire the ended event when hls.js runs out of buffered
  // XTRA media. Do not call manual Next automatically here because that can
  // double-advance the queue. Instead, only resume the already-prefetched queue
  // if playback is expected to be running and the audio time has stopped moving.
  useEffect(() => {
    if (!currentChannelIsXtra || !isPlaying) {
      lastXtraAudioProgressRef.current = {
        time: audioRef.current?.currentTime || 0,
        seenAt: Date.now()
      }
      return
    }

    const interval = setInterval(() => {
      const audio = audioRef.current
      if (!audio || !currentChannelRef.current || !isXtraChannel(currentChannelRef.current)) return
      if (!isPlayingRef.current || isLoadingRef.current || isChangingChannel.current) return
      if (isSkippingNextRef.current || isSkippingPreviousRef.current) return

      const now = Date.now()
      const currentTime = Number.isFinite(audio.currentTime) ? audio.currentTime : 0
      const last = lastXtraAudioProgressRef.current || { time: currentTime, seenAt: now }

      if (Math.abs(currentTime - last.time) > 0.75) {
        lastXtraAudioProgressRef.current = { time: currentTime, seenAt: now }
        return
      }

      const stalledMs = now - last.seenAt
      const sinceResumeMs = now - lastXtraResumeAtRef.current

      // Give a newly-loaded item time to start, and avoid repeated resume loops.
      // The important signal from the logs is repeated metadata polls with the
      // same position for many seconds after the second song reaches the end.
      if (currentTime > 20 && stalledMs >= 6000 && sinceResumeMs >= 45000) {
        console.log('[Player] XTRA playback time stalled; resuming queued item', {
          currentTime,
          stalledMs
        })
        lastXtraResumeAtRef.current = now
        lastXtraAudioProgressRef.current = { time: currentTime, seenAt: now }
        resumeQueuedXtra()
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [currentChannelIsXtra, isPlaying, resumeQueuedXtra])

  const handleAudioEnded = useCallback(() => {
    if (isXtraChannel(currentChannelRef.current)) {
      resumeQueuedXtra()
      return
    }

    setIsPlaying(false)
  }, [resumeQueuedXtra])

  const value = {
    currentChannel,
    currentTrack,
    isPlaying,
    isLoading,
    isSkippingNext,
    isSkippingPrevious,
    hasXtraPrevious,
    isXtra: currentChannelIsXtra,
    volume,
    isMuted,
    error,
    playChannel,
    togglePlay,
    skipNextXtra,
    skipPreviousXtra,
    stop,
    setVolume,
    toggleMute,
    setIsMuted
  }

  return (
    <PlayerContext.Provider value={value}>
      {/* Hidden audio element for HLS playback */}
      <audio ref={audioRef} onEnded={handleAudioEnded} style={{ display: 'none' }} />
      {children}
    </PlayerContext.Provider>
  )
}

export function usePlayer() {
  const context = useContext(PlayerContext)
  if (!context) {
    throw new Error('usePlayer must be used within a PlayerProvider')
  }
  return context
}
