import { Link } from 'react-router-dom'
import { 
  Music, Play, Pause, SkipBack, SkipForward, Volume2, VolumeX,
  Shuffle, Repeat, X, Disc3
} from 'lucide-react'
import { useJukebox } from '../context/JukeboxContext'
import { libraryApi } from '../services/api'

function JukeboxPlayerBar() {
  const {
    currentTrack,
    isPlaying,
    currentTime,
    duration,
    volume,
    isMuted,
    shuffle,
    repeat,
    togglePlay,
    playNext,
    playPrevious,
    seek,
    setVolume,
    setIsMuted,
    setShuffle,
    setRepeat,
    clearQueue
  } = useJukebox()

  const formatTime = (seconds) => {
    if (!seconds || isNaN(seconds)) return '0:00'
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const handleSeek = (e) => {
    seek(parseFloat(e.target.value))
  }

  // Don't show if nothing is playing/queued
  if (!currentTrack) return null

  return (
    <div className="fixed bottom-0 left-0 right-0 bg-gray-900 border-t border-gray-800 px-4 py-2 z-50">
      <div className="flex items-center gap-4 max-w-screen-2xl mx-auto">
        {/* Track Info */}
        <div className="flex items-center gap-3 w-64 min-w-0">
          <Link to="/jukebox" className="flex items-center gap-3 min-w-0 group">
            <div className="w-12 h-12 bg-gray-800 rounded flex items-center justify-center flex-shrink-0 overflow-hidden">
              {currentTrack.cover_art_path ? (
                <img 
                  src={libraryApi.getCoverUrl(currentTrack.id)} 
                  alt="" 
                  className="w-full h-full object-cover"
                />
              ) : (
                <Music className="w-5 h-5 text-gray-600" />
              )}
            </div>
            <div className="min-w-0">
              <p className="text-white text-sm font-medium truncate group-hover:text-primary transition-colors">
                {currentTrack.title || currentTrack.filename}
              </p>
              <p className="text-gray-400 text-xs truncate">{currentTrack.artist || 'Unknown'}</p>
            </div>
          </Link>
          <div className="flex items-center gap-1 ml-2">
            <Disc3 className="w-4 h-4 text-primary animate-spin" style={{ animationDuration: '3s' }} />
            <span className="text-xs text-primary">Jukebox</span>
          </div>
        </div>

        {/* Center Controls */}
        <div className="flex-1 flex flex-col items-center gap-1">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setShuffle(!shuffle)}
              className={`transition-colors ${shuffle ? 'text-primary' : 'text-gray-400 hover:text-white'}`}
            >
              <Shuffle className="w-4 h-4" />
            </button>
            <button
              onClick={playPrevious}
              className="text-gray-400 hover:text-white"
            >
              <SkipBack className="w-5 h-5" />
            </button>
            <button
              onClick={togglePlay}
              className="w-8 h-8 bg-white text-black rounded-full flex items-center justify-center hover:scale-105 transition-transform"
            >
              {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
            </button>
            <button
              onClick={playNext}
              className="text-gray-400 hover:text-white"
            >
              <SkipForward className="w-5 h-5" />
            </button>
            <button
              onClick={() => setRepeat(repeat === 'none' ? 'all' : repeat === 'all' ? 'one' : 'none')}
              className={`transition-colors relative ${repeat !== 'none' ? 'text-primary' : 'text-gray-400 hover:text-white'}`}
            >
              <Repeat className="w-4 h-4" />
              {repeat === 'one' && <span className="absolute -top-1 -right-1 text-[10px]">1</span>}
            </button>
          </div>
          
          {/* Progress Bar */}
          <div className="flex items-center gap-2 w-full max-w-md">
            <span className="text-[10px] text-gray-500 w-8 text-right">{formatTime(currentTime)}</span>
            <input
              type="range"
              min={0}
              max={duration || 100}
              value={currentTime}
              onChange={handleSeek}
              className="flex-1 h-1 bg-gray-700 rounded-full appearance-none cursor-pointer
                [&::-webkit-slider-thumb]:appearance-none
                [&::-webkit-slider-thumb]:w-2
                [&::-webkit-slider-thumb]:h-2
                [&::-webkit-slider-thumb]:rounded-full
                [&::-webkit-slider-thumb]:bg-white
                [&::-webkit-slider-thumb]:cursor-pointer"
            />
            <span className="text-[10px] text-gray-500 w-8">{formatTime(duration)}</span>
          </div>
        </div>

        {/* Right Controls */}
        <div className="flex items-center gap-3 w-48 justify-end">
          <button
            onClick={() => setIsMuted(!isMuted)}
            className="text-gray-400 hover:text-white"
          >
            {isMuted || volume === 0 ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
          </button>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={isMuted ? 0 : volume}
            onChange={(e) => setVolume(parseFloat(e.target.value))}
            className="w-20 h-1 bg-gray-700 rounded-full appearance-none cursor-pointer
              [&::-webkit-slider-thumb]:appearance-none
              [&::-webkit-slider-thumb]:w-2
              [&::-webkit-slider-thumb]:h-2
              [&::-webkit-slider-thumb]:rounded-full
              [&::-webkit-slider-thumb]:bg-white
              [&::-webkit-slider-thumb]:cursor-pointer"
          />
          <button
            onClick={clearQueue}
            className="text-gray-400 hover:text-red-400"
            title="Stop Jukebox"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  )
}

export default JukeboxPlayerBar
