import { createContext, useContext, useState, useRef, useEffect, useCallback } from 'react'
import { libraryApi } from '../services/api'

const JukeboxContext = createContext(null)

export function JukeboxProvider({ children }) {
  const audioRef = useRef(null)
  const pauseLiveStreamRef = useRef(null) // Callback to pause live stream
  const isSeekingRef = useRef(false)
  
  // Register callback from PlayerContext to pause live stream
  const registerPauseLiveStream = useCallback((callback) => {
    pauseLiveStreamRef.current = callback
  }, [])

  // Helper to pause live stream before Jukebox playback
  const pauseLiveStream = useCallback(() => {
    if (pauseLiveStreamRef.current) {
      pauseLiveStreamRef.current()
    }
  }, [])
  
  // Queue and playback state
  const [queue, setQueue] = useState([])
  const [currentIndex, setCurrentIndex] = useState(-1)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(0.8)
  const [isMuted, setIsMuted] = useState(false)
  const [shuffle, setShuffle] = useState(false)
  const [repeat, setRepeat] = useState('none') // none, all, one

  const currentTrack = queue[currentIndex] || null

  const stopAudioElement = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.removeAttribute('src')
      audioRef.current.load()
    }
    setIsPlaying(false)
    setCurrentTime(0)
    setDuration(0)
    isSeekingRef.current = false
  }, [])

  // Update volume
  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.volume = isMuted ? 0 : volume
    }
  }, [volume, isMuted])

  // Audio event handlers
  const handleTimeUpdate = useCallback(() => {
    if (audioRef.current && !isSeekingRef.current) {
      setCurrentTime(audioRef.current.currentTime || 0)
    }
  }, [])

  const handleLoadedMetadata = useCallback(() => {
    if (audioRef.current) {
      const nextDuration = Number.isFinite(audioRef.current.duration) ? audioRef.current.duration : 0
      setDuration(nextDuration)
    }
  }, [])

  const handleSeeked = useCallback(() => {
    if (audioRef.current) {
      isSeekingRef.current = false
      setCurrentTime(audioRef.current.currentTime || 0)
    }
  }, [])

  const handleEnded = useCallback(() => {
    if (repeat === 'one') {
      if (audioRef.current) {
        audioRef.current.currentTime = 0
        audioRef.current.play()
      }
    } else {
      playNext()
    }
  }, [repeat])

  // Play a specific track
  const playTrack = useCallback((track, index = -1, trackList = null) => {
    // Pause live stream before starting Jukebox playback
    pauseLiveStream()
    
    if (trackList) {
      setQueue(trackList)
      setCurrentIndex(index >= 0 ? index : trackList.findIndex(t => t.id === track.id))
    } else if (index < 0) {
      // Add to queue and play
      const newQueue = [...queue, track]
      setQueue(newQueue)
      setCurrentIndex(newQueue.length - 1)
    } else {
      setCurrentIndex(index)
    }
    
    if (audioRef.current) {
      audioRef.current.src = libraryApi.getStreamUrl(track.id)
      audioRef.current.play()
        .then(() => setIsPlaying(true))
        .catch(console.error)
    }
  }, [queue, pauseLiveStream])

  const togglePlay = useCallback(() => {
    if (!audioRef.current || !currentTrack) return
    
    if (isPlaying) {
      audioRef.current.pause()
      setIsPlaying(false)
    } else {
      // Pause live stream before resuming Jukebox
      pauseLiveStream()
      audioRef.current.play()
        .then(() => setIsPlaying(true))
        .catch(console.error)
    }
  }, [isPlaying, currentTrack, pauseLiveStream])

  const pause = useCallback(() => {
    if (audioRef.current && isPlaying) {
      audioRef.current.pause()
      setIsPlaying(false)
    }
  }, [isPlaying])

  const resume = useCallback(() => {
    if (audioRef.current && currentTrack && !isPlaying) {
      // Pause live stream before resuming Jukebox
      pauseLiveStream()
      audioRef.current.play()
        .then(() => setIsPlaying(true))
        .catch(console.error)
    }
  }, [currentTrack, isPlaying, pauseLiveStream])

  const playNext = useCallback(() => {
    if (queue.length === 0) return
    
    let nextIndex
    if (shuffle) {
      nextIndex = Math.floor(Math.random() * queue.length)
    } else {
      nextIndex = currentIndex + 1
      if (nextIndex >= queue.length) {
        if (repeat === 'all') {
          nextIndex = 0
        } else {
          return
        }
      }
    }
    
    setCurrentIndex(nextIndex)
    if (audioRef.current) {
      audioRef.current.src = libraryApi.getStreamUrl(queue[nextIndex].id)
      audioRef.current.play()
        .then(() => setIsPlaying(true))
        .catch(console.error)
    }
  }, [queue, currentIndex, shuffle, repeat])

  const playPrevious = useCallback(() => {
    if (queue.length === 0) return
    
    // If more than 3 seconds in, restart current track
    if (currentTime > 3) {
      if (audioRef.current) {
        audioRef.current.currentTime = 0
      }
      return
    }
    
    let prevIndex = currentIndex - 1
    if (prevIndex < 0) {
      if (repeat === 'all') {
        prevIndex = queue.length - 1
      } else {
        return
      }
    }
    
    setCurrentIndex(prevIndex)
    if (audioRef.current) {
      audioRef.current.src = libraryApi.getStreamUrl(queue[prevIndex].id)
      audioRef.current.play()
        .then(() => setIsPlaying(true))
        .catch(console.error)
    }
  }, [queue, currentIndex, currentTime, repeat])

  const seek = useCallback((time) => {
    if (!audioRef.current) return

    const requested = Number(time)
    if (!Number.isFinite(requested)) return

    const maxDuration = Number.isFinite(audioRef.current.duration) && audioRef.current.duration > 0
      ? audioRef.current.duration
      : duration
    const target = Math.max(0, Math.min(requested, maxDuration || requested))

    isSeekingRef.current = true
    setCurrentTime(target)

    try {
      if (typeof audioRef.current.fastSeek === 'function') {
        audioRef.current.fastSeek(target)
      } else {
        audioRef.current.currentTime = target
      }
    } catch (error) {
      console.error('[Jukebox] Seek failed:', error)
      isSeekingRef.current = false
    }

    // Some browsers do not always fire seeked for short local files. Release
    // the time-update guard shortly after setting the target.
    window.setTimeout(() => {
      isSeekingRef.current = false
      if (audioRef.current) {
        setCurrentTime(audioRef.current.currentTime || target)
      }
    }, 350)
  }, [duration])

  const addToQueue = useCallback((track) => {
    setQueue(prev => [...prev, track])
  }, [])

  const removeFromQueue = useCallback((index) => {
    setQueue(prev => {
      const newQueue = [...prev]
      newQueue.splice(index, 1)
      return newQueue
    })
    if (index < currentIndex) {
      setCurrentIndex(prev => prev - 1)
    } else if (index === currentIndex) {
      // Current track removed, play next or stop
      if (queue.length > 1) {
        const nextIndex = index < queue.length - 1 ? index : index - 1
        if (audioRef.current && queue[nextIndex]) {
          audioRef.current.src = libraryApi.getStreamUrl(queue[nextIndex].id)
          audioRef.current.play()
        }
      } else {
        setCurrentIndex(-1)
        setIsPlaying(false)
      }
    }
  }, [currentIndex, queue])

  const clearQueue = useCallback((keepCurrent = false) => {
    if (keepCurrent && currentIndex >= 0 && queue[currentIndex]) {
      // Keep current track, remove everything else
      const current = queue[currentIndex]
      setQueue([current])
      setCurrentIndex(0)
    } else {
      // Full clear - stop playback
      stopAudioElement()
      setQueue([])
      setCurrentIndex(-1)
    }
  }, [currentIndex, queue, stopAudioElement])

  // Play the queue from the beginning (or from a specific index)
  const playQueue = useCallback((startIndex = 0) => {
    if (queue.length === 0) return
    pauseLiveStream()
    const idx = Math.min(startIndex, queue.length - 1)
    setCurrentIndex(idx)
    if (audioRef.current) {
      audioRef.current.src = libraryApi.getStreamUrl(queue[idx].id)
      audioRef.current.play()
        .then(() => setIsPlaying(true))
        .catch(console.error)
    }
  }, [queue, pauseLiveStream])

  const playAll = useCallback((tracks) => {
    if (tracks.length === 0) return
    // Pause live stream before starting Jukebox playback
    pauseLiveStream()
    setQueue(tracks)
    setCurrentIndex(0)
    if (audioRef.current) {
      audioRef.current.src = libraryApi.getStreamUrl(tracks[0].id)
      audioRef.current.play()
        .then(() => setIsPlaying(true))
        .catch(console.error)
    }
  }, [pauseLiveStream])

  const shuffleAll = useCallback((tracks) => {
    if (tracks.length === 0) return
    // Pause live stream before starting Jukebox playback
    pauseLiveStream()
    const shuffled = [...tracks].sort(() => Math.random() - 0.5)
    setQueue(shuffled)
    setCurrentIndex(0)
    setShuffle(true)
    if (audioRef.current) {
      audioRef.current.src = libraryApi.getStreamUrl(shuffled[0].id)
      audioRef.current.play()
        .then(() => setIsPlaying(true))
        .catch(console.error)
    }
  }, [pauseLiveStream])


  const removeTrackFromPlayback = useCallback((trackId) => {
    if (trackId == null) return

    setQueue(prev => {
      const removedIndex = prev.findIndex(t => t.id === trackId)
      if (removedIndex === -1) return prev

      const wasCurrent = removedIndex === currentIndex
      const newQueue = prev.filter(t => t.id !== trackId)

      if (wasCurrent) {
        stopAudioElement()
        setCurrentIndex(-1)
      } else if (removedIndex < currentIndex) {
        setCurrentIndex(i => Math.max(-1, i - 1))
      } else if (currentIndex >= newQueue.length) {
        setCurrentIndex(newQueue.length - 1)
      }

      return newQueue
    })
  }, [currentIndex, stopAudioElement])

  const value = {
    // State
    queue,
    currentIndex,
    currentTrack,
    isPlaying,
    currentTime,
    duration,
    volume,
    isMuted,
    shuffle,
    repeat,
    // Actions
    playTrack,
    togglePlay,
    pause,
    resume,
    playNext,
    playPrevious,
    seek,
    addToQueue,
    removeFromQueue,
    removeTrackFromPlayback,
    clearQueue,
    playQueue,
    playAll,
    shuffleAll,
    setVolume,
    setIsMuted,
    setShuffle,
    setRepeat,
    setQueue,
    setCurrentIndex,
    // Callback registration for cross-context communication
    registerPauseLiveStream,
  }

  return (
    <JukeboxContext.Provider value={value}>
      {/* Hidden audio element for Jukebox playback */}
      <audio 
        ref={audioRef} 
        style={{ display: 'none' }}
        onTimeUpdate={handleTimeUpdate}
        onLoadedMetadata={handleLoadedMetadata}
        onSeeked={handleSeeked}
        onEnded={handleEnded}
      />
      {children}
    </JukeboxContext.Provider>
  )
}

export function useJukebox() {
  const context = useContext(JukeboxContext)
  if (!context) {
    throw new Error('useJukebox must be used within a JukeboxProvider')
  }
  return context
}
