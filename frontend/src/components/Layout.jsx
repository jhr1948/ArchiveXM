import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import { Radio, Settings as SettingsIcon, Download, RefreshCw, Disc3, Circle, Square, Music } from 'lucide-react'
import { useState } from 'react'
import { useJukebox } from '../context/JukeboxContext'
import { useRecording } from '../context/RecordingContext'

function Layout() {
  const location = useLocation()
  const navigate = useNavigate()
  const [refreshing, setRefreshing] = useState(false)
  const { currentTrack } = useJukebox()
  const { isRecording, recordingData, stopRecording } = useRecording()
  const [showStopMenu, setShowStopMenu] = useState(false)
  
  // Add top padding when Jukebox bar is visible at top
  const hasJukeboxBar = !!currentTrack
  
  // Format elapsed time
  const formatElapsed = (seconds) => {
    if (!seconds) return '0:00'
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }
  
  const handleStopRecording = async () => {
    setShowStopMenu(false)
    const result = await stopRecording(true) // Always wait for track
    if (!result.success) {
      alert(`Failed to stop recording: ${result.error}`)
    }
  }

  return (
    <div className={`min-h-screen bg-sxm-dark ${hasJukeboxBar ? 'pt-14' : ''}`}>
      {/* Header */}
      <header className="bg-sxm-darker border-b border-sxm-border sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 py-3">
          <div className="flex items-center justify-between">
            {/* Logo */}
            <Link to="/" className="flex items-center gap-2 sm:gap-3 hover:opacity-80 transition-opacity">
              <img src="/logo.png" alt="ArchiveXM" className="w-8 h-8 sm:w-10 sm:h-10 rounded-lg" />
              <span className="text-lg sm:text-xl font-bold text-white hidden xs:inline">ArchiveXM</span>
            </Link>

            {/* Navigation */}
            <nav className="flex items-center gap-1 sm:gap-2">
              {/* Recording Indicator with Current Track */}
              {isRecording && (
                <div className="relative flex items-center gap-3">
                  {/* Stopping indicator */}
                  {recordingData?.stopping && recordingData?.stoppingInSeconds != null && (
                    <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 bg-yellow-600/20 border border-yellow-500/50 rounded-lg">
                      <span className="text-yellow-400 text-sm">
                        Stopping in {Math.ceil(recordingData.stoppingInSeconds)}s...
                      </span>
                    </div>
                  )}
                  
                  {/* Current Track Info - hide when stopping */}
                  {recordingData?.currentTrack && !recordingData?.stopping && (
                    <div className="hidden sm:flex items-center gap-2 text-sm max-w-xs">
                      <Music className="w-4 h-4 text-red-400 flex-shrink-0" />
                      <span className="text-gray-300 truncate">
                        {recordingData.currentTrack.artist} - {recordingData.currentTrack.title}
                      </span>
                    </div>
                  )}
                  
                  <button
                    onClick={() => setShowStopMenu(!showStopMenu)}
                    className={`flex items-center gap-1 sm:gap-2 px-2 sm:px-3 py-1.5 sm:py-2 rounded-lg transition-colors ${
                      recordingData?.stopping 
                        ? 'bg-yellow-600/20 border border-yellow-500/50' 
                        : 'bg-red-600/20 border border-red-500/50 hover:bg-red-600/30'
                    }`}
                  >
                    <Circle className={`w-2.5 sm:w-3 h-2.5 sm:h-3 ${recordingData?.stopping ? 'text-yellow-500 fill-yellow-500' : 'text-red-500 fill-red-500 animate-pulse'}`} />
                    <span className={`text-xs sm:text-sm font-medium ${recordingData?.stopping ? 'text-yellow-400' : 'text-red-400'}`}>
                      {recordingData?.stopping ? 'STOP' : 'REC'}
                    </span>
                    <span className={`text-xs sm:text-sm hidden xs:inline ${recordingData?.stopping ? 'text-yellow-300' : 'text-red-300'}`}>{formatElapsed(recordingData?.elapsedSeconds)}</span>
                  </button>
                  
                  {/* Stop Menu Dropdown */}
                  {showStopMenu && (
                    <div className="absolute top-full right-0 mt-2 w-56 bg-gray-800 border border-gray-700 rounded-lg shadow-xl z-50">
                      {/* Show current track in dropdown on mobile */}
                      {recordingData?.currentTrack && (
                        <div className="sm:hidden px-4 py-2 border-b border-gray-700">
                          <div className="text-xs text-gray-500">Recording:</div>
                          <div className="text-sm text-gray-300 truncate">
                            {recordingData.currentTrack.artist} - {recordingData.currentTrack.title}
                          </div>
                        </div>
                      )}
                      <button
                        onClick={() => { setShowStopMenu(false); navigate(`/channel/${recordingData?.channelId}`) }}
                        className="w-full px-4 py-2 text-left text-sm text-gray-300 hover:bg-gray-700 rounded-t-lg"
                      >
                        View Channel
                      </button>
                      <button
                        onClick={handleStopRecording}
                        className="w-full px-4 py-2 text-left text-sm text-red-400 hover:bg-gray-700 rounded-b-lg flex items-center gap-2"
                      >
                        <Square className="w-3 h-3" />
                        Stop Recording
                      </button>
                    </div>
                  )}
                </div>
              )}
              
              <Link
                to="/"
                className={`flex items-center gap-1 sm:gap-2 px-2 sm:px-4 py-2 rounded-lg transition-colors ${
                  location.pathname === '/' 
                    ? 'bg-sxm-accent text-white' 
                    : 'text-gray-400 hover:text-white hover:bg-sxm-card'
                }`}
              >
                <Radio size={18} />
                <span className="hidden sm:inline">Channels</span>
              </Link>
              <Link
                to="/jukebox"
                className={`flex items-center gap-1 sm:gap-2 px-2 sm:px-4 py-2 rounded-lg transition-colors ${
                  location.pathname === '/jukebox' 
                    ? 'bg-sxm-accent text-white' 
                    : 'text-gray-400 hover:text-white hover:bg-sxm-card'
                }`}
              >
                <Disc3 size={18} />
                <span className="hidden sm:inline">Jukebox</span>
              </Link>
              <Link
                to="/settings"
                className={`flex items-center gap-1 sm:gap-2 px-2 sm:px-4 py-2 rounded-lg transition-colors ${
                  location.pathname === '/settings' 
                    ? 'bg-sxm-accent text-white' 
                    : 'text-gray-400 hover:text-white hover:bg-sxm-card'
                }`}
              >
                <SettingsIcon size={18} />
                <span className="hidden sm:inline">Settings</span>
              </Link>
            </nav>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-3 sm:px-4 py-4 sm:py-6">
        <Outlet />
      </main>
    </div>
  )
}

export default Layout
