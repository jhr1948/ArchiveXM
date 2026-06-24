import { useState, useRef, useEffect } from 'react'
import Hls from 'hls.js'
import { Play, Pause, Volume2, VolumeX, Radio } from 'lucide-react'

function AudioPlayer({ streamUrl, channelName, channelImage, onError }) {
  const audioRef = useRef(null)
  const hlsRef = useRef(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [isMuted, setIsMuted] = useState(false)
  const [volume, setVolume] = useState(0.8)
  const [error, setError] = useState(null)

  useEffect(() => {
    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy()
      }
    }
  }, [])

  const initializeHls = () => {
    if (!streamUrl || !audioRef.current) return

    setIsLoading(true)
    setError(null)

    // Destroy existing HLS instance
    if (hlsRef.current) {
      hlsRef.current.destroy()
    }

    if (Hls.isSupported()) {
      const hls = new Hls({
        enableWorker: true,
        lowLatencyMode: true,
        backBufferLength: 90
      })

      hls.loadSource(streamUrl)
      hls.attachMedia(audioRef.current)

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        setIsLoading(false)
        audioRef.current.play()
          .then(() => setIsPlaying(true))
          .catch(err => {
            console.error('Playback failed:', err)
            setError('Failed to start playback')
            onError?.('Failed to start playback')
          })
      })

      hls.on(Hls.Events.ERROR, (event, data) => {
        console.error('HLS error:', data)
        if (data.fatal) {
          setIsLoading(false)
          setError('Stream error occurred')
          onError?.('Stream error occurred')
          
          switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
              console.log('Network error, trying to recover...')
              hls.startLoad()
              break
            case Hls.ErrorTypes.MEDIA_ERROR:
              console.log('Media error, trying to recover...')
              hls.recoverMediaError()
              break
            default:
              hls.destroy()
              break
          }
        }
      })

      hlsRef.current = hls
    } else if (audioRef.current.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS support (Safari)
      audioRef.current.src = streamUrl
      audioRef.current.addEventListener('loadedmetadata', () => {
        setIsLoading(false)
        audioRef.current.play()
          .then(() => setIsPlaying(true))
          .catch(err => {
            setError('Failed to start playback')
            onError?.('Failed to start playback')
          })
      })
    } else {
      setError('HLS not supported in this browser')
      onError?.('HLS not supported')
    }
  }

  const togglePlay = () => {
    if (!audioRef.current) return

    if (isPlaying) {
      audioRef.current.pause()
      setIsPlaying(false)
      if (hlsRef.current) {
        hlsRef.current.destroy()
        hlsRef.current = null
      }
    } else {
      initializeHls()
    }
  }

  const toggleMute = () => {
    if (audioRef.current) {
      audioRef.current.muted = !isMuted
      setIsMuted(!isMuted)
    }
  }

  const handleVolumeChange = (e) => {
    const newVolume = parseFloat(e.target.value)
    setVolume(newVolume)
    if (audioRef.current) {
      audioRef.current.volume = newVolume
    }
  }

  return (
    <div className="bg-gradient-to-r from-sxm-accent/20 to-purple-600/20 rounded-xl p-4 border border-sxm-accent/30">
      <div className="flex items-center gap-4">
        {/* Channel Image */}
        <div className="w-16 h-16 rounded-lg overflow-hidden bg-sxm-darker shrink-0">
          {channelImage ? (
            <img src={channelImage} alt={channelName} className="w-full h-full object-cover" />
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              <Radio className="w-8 h-8 text-gray-600" />
            </div>
          )}
        </div>

        {/* Play Controls */}
        <div className="flex-1">
          <h3 className="text-white font-medium mb-1">
            {isPlaying ? 'Now Playing' : 'Live Stream'}
          </h3>
          <p className="text-gray-400 text-sm">{channelName}</p>
          
          {error && (
            <p className="text-sxm-error text-xs mt-1">{error}</p>
          )}
        </div>

        {/* Volume Control */}
        <div className="flex items-center gap-2">
          <button
            onClick={toggleMute}
            className="p-2 rounded-lg hover:bg-white/10 text-gray-400 hover:text-white transition-colors"
          >
            {isMuted ? <VolumeX size={20} /> : <Volume2 size={20} />}
          </button>
          <input
            type="range"
            min="0"
            max="1"
            step="0.1"
            value={volume}
            onChange={handleVolumeChange}
            className="w-20 h-1 bg-gray-600 rounded-lg appearance-none cursor-pointer"
          />
        </div>

        {/* Play Button */}
        <button
          onClick={togglePlay}
          disabled={isLoading}
          className={`w-14 h-14 rounded-full flex items-center justify-center transition-all ${
            isPlaying 
              ? 'bg-sxm-error hover:bg-red-600' 
              : 'bg-sxm-accent hover:bg-sxm-accent-hover'
          } ${isLoading ? 'opacity-50 cursor-wait' : ''}`}
        >
          {isLoading ? (
            <div className="w-6 h-6 border-2 border-white border-t-transparent rounded-full animate-spin" />
          ) : isPlaying ? (
            <Pause className="w-6 h-6 text-white fill-white" />
          ) : (
            <Play className="w-6 h-6 text-white fill-white ml-1" />
          )}
        </button>
      </div>

      <audio ref={audioRef} />
    </div>
  )
}

export default AudioPlayer
