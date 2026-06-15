import { Play, Pause, Volume2, VolumeX, X, Radio, Loader2, SkipForward } from 'lucide-react'
import { usePlayer } from '../context/PlayerContext'

function PlayerBar() {
  const {
    currentChannel,
    currentTrack,
    isPlaying,
    isLoading,
    isSkippingNext,
    isXtra,
    volume,
    isMuted,
    togglePlay,
    skipNextXtra,
    stop,
    setVolume,
    toggleMute
  } = usePlayer()

  // Don't render if no channel is selected
  if (!currentChannel) return null

  return (
    <div className="fixed bottom-0 left-0 right-0 bg-gray-900 border-t border-gray-700 z-50">
      <div className="max-w-screen-2xl mx-auto px-4 py-3">
        <div className="flex items-center justify-between gap-4">
          {/* Left: Track Info */}
          <div className="flex items-center gap-3 flex-1 min-w-0">
            {/* Album Art / Channel Logo */}
            <div className="w-14 h-14 rounded-lg overflow-hidden bg-gray-800 flex-shrink-0">
              {currentTrack?.image_url ? (
                <img 
                  src={currentTrack.image_url} 
                  alt={currentTrack.title}
                  className="w-full h-full object-cover"
                  onError={(e) => {
                    e.target.style.display = 'none'
                    e.target.nextSibling.style.display = 'flex'
                  }}
                />
              ) : null}
              <div 
                className={`w-full h-full flex items-center justify-center ${currentTrack?.image_url ? 'hidden' : ''}`}
                style={{ display: currentTrack?.image_url ? 'none' : 'flex' }}
              >
                {currentChannel.image ? (
                  <img 
                    src={currentChannel.image} 
                    alt={currentChannel.name}
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <Radio className="w-6 h-6 text-gray-500" />
                )}
              </div>
            </div>
            
            {/* Track Details */}
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-xs text-primary font-medium uppercase tracking-wide">
                  {currentChannel.name}
                </span>
                <span className="text-xs text-gray-500">•</span>
                <span className="text-xs text-gray-400">Ch {currentChannel.channel_number}</span>
              </div>
              {currentTrack ? (
                <>
                  <p className="text-white font-medium truncate">
                    {currentTrack.title}
                  </p>
                  <p className="text-gray-400 text-sm truncate">
                    {currentTrack.artist}
                  </p>
                </>
              ) : (
                <p className="text-gray-400 text-sm">Loading track info...</p>
              )}
            </div>
          </div>

          {/* Center: Playback Controls */}
          <div className="flex items-center gap-3">
            <button
              onClick={togglePlay}
              disabled={isLoading}
              className="w-12 h-12 rounded-full bg-white text-black flex items-center justify-center hover:scale-105 transition-transform disabled:opacity-50"
            >
              {isLoading ? (
                <Loader2 className="w-6 h-6 animate-spin" />
              ) : isPlaying ? (
                <Pause className="w-6 h-6" />
              ) : (
                <Play className="w-6 h-6 ml-1" />
              )}
            </button>

            {isXtra && (
              <button
                onClick={skipNextXtra}
                disabled={isLoading || isSkippingNext}
                className="w-10 h-10 rounded-full bg-gray-800 text-white flex items-center justify-center hover:bg-gray-700 transition-colors disabled:opacity-50"
                title="Next XTRA track"
              >
                {isSkippingNext ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <SkipForward className="w-5 h-5" />
                )}
              </button>
            )}
          </div>

          {/* Right: Volume & Close */}
          <div className="flex items-center gap-4 flex-1 justify-end">
            {/* Live/XTRA Indicator */}
            <div className={`flex items-center gap-2 px-3 py-1 rounded-full ${isXtra ? 'bg-primary/20' : 'bg-red-600/20'}`}>
              <span className={`w-2 h-2 rounded-full animate-pulse ${isXtra ? 'bg-primary' : 'bg-red-500'}`}></span>
              <span className={`text-xs font-medium ${isXtra ? 'text-primary' : 'text-red-400'}`}>
                {isXtra ? 'XTRA' : 'LIVE'}
              </span>
            </div>
            
            {/* Volume Controls */}
            <div className="flex items-center gap-2">
              <button
                onClick={toggleMute}
                className="text-gray-400 hover:text-white transition-colors"
              >
                {isMuted || volume === 0 ? (
                  <VolumeX className="w-5 h-5" />
                ) : (
                  <Volume2 className="w-5 h-5" />
                )}
              </button>
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={isMuted ? 0 : volume}
                onChange={(e) => setVolume(parseFloat(e.target.value))}
                className="w-24 h-1 bg-gray-700 rounded-full appearance-none cursor-pointer
                  [&::-webkit-slider-thumb]:appearance-none
                  [&::-webkit-slider-thumb]:w-3
                  [&::-webkit-slider-thumb]:h-3
                  [&::-webkit-slider-thumb]:rounded-full
                  [&::-webkit-slider-thumb]:bg-white
                  [&::-webkit-slider-thumb]:cursor-pointer"
              />
            </div>
            
            {/* Close Button */}
            <button
              onClick={stop}
              className="text-gray-400 hover:text-white transition-colors p-2"
              title="Stop playback"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default PlayerBar
