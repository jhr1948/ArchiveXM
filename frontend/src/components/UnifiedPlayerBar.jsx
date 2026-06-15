import { Link } from 'react-router-dom'
import { 
  Play, Pause, Volume2, VolumeX, X, Radio, Loader2, Music,
  SkipBack, SkipForward, Shuffle, Repeat, Disc3
} from 'lucide-react'
import { usePlayer } from '../context/PlayerContext'
import { useJukebox } from '../context/JukeboxContext'
import { libraryApi } from '../services/api'

function UnifiedPlayerBar() {
  // Live stream player
  const livePlayer = usePlayer()
  
  // Jukebox player
  const jukebox = useJukebox()

  const hasLiveStream = !!livePlayer.currentChannel
  const hasJukebox = !!jukebox.currentTrack

  // Don't render if nothing is playing
  if (!hasLiveStream && !hasJukebox) return null

  const formatTime = (seconds) => {
    if (!seconds || isNaN(seconds)) return '0:00'
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  return (
    <>
      {/* Jukebox Bar - shows at TOP when active (like live stream in JukeboxPage) */}
      {hasJukebox && (
        <div className="fixed top-0 left-0 right-0 z-50 bg-gray-900 border-b border-gray-700 px-4 py-2">
          <div className="max-w-screen-2xl mx-auto flex items-center justify-between gap-4">
            {/* Left: Track Info */}
            <div className="flex items-center gap-3 min-w-0 flex-1">
              <Link to="/jukebox" className="flex items-center gap-3 min-w-0 group">
                <div className="w-10 h-10 bg-gray-800 rounded flex items-center justify-center flex-shrink-0 overflow-hidden">
                  {jukebox.currentTrack.cover_art_path ? (
                    <img 
                      src={libraryApi.getCoverUrl(jukebox.currentTrack.id)} 
                      alt="" 
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <Music className="w-5 h-5 text-gray-600" />
                  )}
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <div className="flex items-center gap-1 px-2 py-0.5 bg-primary/20 rounded-full">
                      <Disc3 className={`w-2.5 h-2.5 text-primary ${jukebox.isPlaying ? 'animate-spin' : ''}`} style={{ animationDuration: '3s' }} />
                      <span className="text-[10px] text-primary font-medium">Jukebox</span>
                    </div>
                  </div>
                  <p className="text-white text-sm truncate group-hover:text-primary transition-colors">
                    {jukebox.currentTrack.title || jukebox.currentTrack.filename}
                  </p>
                </div>
              </Link>
            </div>

            {/* Center: Controls */}
            <div className="flex items-center gap-3">
              <button
                onClick={() => jukebox.setShuffle(!jukebox.shuffle)}
                className={`transition-colors ${jukebox.shuffle ? 'text-primary' : 'text-gray-400 hover:text-white'}`}
              >
                <Shuffle className="w-3.5 h-3.5" />
              </button>
              <button onClick={jukebox.playPrevious} className="text-gray-400 hover:text-white">
                <SkipBack className="w-4 h-4" />
              </button>
              <button
                onClick={jukebox.togglePlay}
                className="w-8 h-8 bg-white text-black rounded-full flex items-center justify-center hover:scale-105 transition-transform"
              >
                {jukebox.isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
              </button>
              <button onClick={jukebox.playNext} className="text-gray-400 hover:text-white">
                <SkipForward className="w-4 h-4" />
              </button>
              <button
                onClick={() => jukebox.setRepeat(jukebox.repeat === 'none' ? 'all' : jukebox.repeat === 'all' ? 'one' : 'none')}
                className={`transition-colors relative ${jukebox.repeat !== 'none' ? 'text-primary' : 'text-gray-400 hover:text-white'}`}
              >
                <Repeat className="w-3.5 h-3.5" />
                {jukebox.repeat === 'one' && <span className="absolute -top-1 -right-1 text-[8px]">1</span>}
              </button>
            </div>

            {/* Right: Volume & Close */}
            <div className="flex items-center gap-3 flex-1 justify-end">
              <button
                onClick={() => jukebox.setIsMuted(!jukebox.isMuted)}
                className="text-gray-400 hover:text-white"
              >
                {jukebox.isMuted || jukebox.volume === 0 ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
              </button>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={jukebox.isMuted ? 0 : jukebox.volume}
                onChange={(e) => jukebox.setVolume(parseFloat(e.target.value))}
                className="w-20 h-1 bg-gray-700 rounded-full appearance-none cursor-pointer
                  [&::-webkit-slider-thumb]:appearance-none
                  [&::-webkit-slider-thumb]:w-2
                  [&::-webkit-slider-thumb]:h-2
                  [&::-webkit-slider-thumb]:rounded-full
                  [&::-webkit-slider-thumb]:bg-white"
              />
              <button
                onClick={jukebox.clearQueue}
                className="text-gray-400 hover:text-red-400"
                title="Stop Jukebox"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      )}

{/* Live Stream Bar - shows at BOTTOM */}
{hasLiveStream && (
  <div className="fixed bottom-0 left-0 right-0 z-50 bg-gray-900 border-t border-gray-700 px-6 py-4 min-h-[104px]">
    <div className="max-w-screen-2xl mx-auto flex items-center justify-between gap-6">
      {/* Left: Channel Info */}
      <div className="flex items-center gap-4 flex-1 min-w-0">
        <div className="w-20 h-20 rounded-xl overflow-hidden bg-gray-800 flex-shrink-0">
          {livePlayer.currentTrack?.image_url ? (
            <img
              src={livePlayer.currentTrack.image_url}
              alt={livePlayer.currentTrack.title || livePlayer.currentChannel.name}
              className="w-full h-full object-cover"
            />
          ) : livePlayer.currentChannel.image ? (
            <img
              src={livePlayer.currentChannel.image}
              alt={livePlayer.currentChannel.name}
              className="w-full h-full object-cover"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              <Radio className="w-8 h-8 text-gray-500" />
            </div>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-3 mb-1">
            <div className={`flex items-center gap-2 px-3 py-1 rounded-full ${livePlayer.isXtra ? 'bg-primary/20' : 'bg-red-600/20'}`}>
              <span className={`w-2 h-2 rounded-full animate-pulse ${livePlayer.isXtra ? 'bg-primary' : 'bg-red-500'}`}></span>
              <span className={`text-xs font-semibold tracking-wide ${livePlayer.isXtra ? 'text-primary' : 'text-red-400'}`}>
                {livePlayer.isXtra ? 'XTRA' : 'LIVE'}
              </span>
            </div>

            <span className="text-base text-primary font-semibold truncate">
              {livePlayer.currentChannel.name}
            </span>
          </div>

          {livePlayer.currentTrack ? (
            <div className="min-w-0">
              <p className="text-white text-xl font-semibold truncate leading-tight">
                {livePlayer.currentTrack.title}
              </p>
              <p className="text-gray-300 text-base truncate">
                {livePlayer.currentTrack.artist || ''}
              </p>
            </div>
          ) : (
            <p className="text-gray-300 text-base truncate">
              Loading track info...
            </p>
          )}
        </div>
      </div>

      {/* Center: Controls */}
      <div className="flex items-center gap-4">
        <button
          onClick={livePlayer.togglePlay}
          disabled={livePlayer.isLoading}
          className="w-14 h-14 rounded-full bg-white text-black flex items-center justify-center hover:scale-105 transition-transform disabled:opacity-50"
          title={livePlayer.isPlaying ? 'Pause' : 'Play'}
        >
          {livePlayer.isLoading ? (
            <Loader2 className="w-7 h-7 animate-spin" />
          ) : livePlayer.isPlaying ? (
            <Pause className="w-7 h-7" />
          ) : (
            <Play className="w-7 h-7 ml-1" />
          )}
        </button>

        {livePlayer.isXtra && (
          <button
            onClick={livePlayer.skipNextXtra}
            disabled={livePlayer.isLoading || livePlayer.isSkippingNext}
            className="w-14 h-14 rounded-full bg-gray-800 text-white flex items-center justify-center hover:bg-gray-700 transition-colors disabled:opacity-50"
            title="Next XTRA track"
          >
            {livePlayer.isSkippingNext ? (
              <Loader2 className="w-7 h-7 animate-spin" />
            ) : (
              <SkipForward className="w-7 h-7" />
            )}
          </button>
        )}
      </div>

      {/* Right: Volume & Close */}
      <div className="flex items-center gap-4 flex-1 justify-end">
        <button
          onClick={livePlayer.toggleMute}
          className="text-gray-400 hover:text-white transition-colors"
          title={livePlayer.isMuted ? 'Unmute' : 'Mute'}
        >
          {livePlayer.isMuted || livePlayer.volume === 0 ? (
            <VolumeX className="w-6 h-6" />
          ) : (
            <Volume2 className="w-6 h-6" />
          )}
        </button>

        <input
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={livePlayer.isMuted ? 0 : livePlayer.volume}
          onChange={(e) => livePlayer.setVolume(parseFloat(e.target.value))}
          className="w-32 h-2 bg-gray-700 rounded-full appearance-none cursor-pointer
            [&::-webkit-slider-thumb]:appearance-none
            [&::-webkit-slider-thumb]:w-4
            [&::-webkit-slider-thumb]:h-4
            [&::-webkit-slider-thumb]:rounded-full
            [&::-webkit-slider-thumb]:bg-white"
        />

        <button
          onClick={livePlayer.stop}
          className="text-gray-400 hover:text-red-400 transition-colors"
          title="Stop live stream"
        >
          <X className="w-6 h-6" />
        </button>
      </div>
    </div>
  </div>
)}
    </>
  )
}

export default UnifiedPlayerBar
