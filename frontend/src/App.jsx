import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { useState, useEffect } from 'react'
import SetupPage from './pages/SetupPage'
import ChannelsPage from './pages/ChannelsPage'
import ChannelDetailPage from './pages/ChannelDetailPage'
import JukeboxPage from './pages/JukeboxPage'
import SettingsPage from './pages/SettingsPage'
import Layout from './components/Layout'
import DownloadStatus from './components/DownloadStatus'
import UnifiedPlayerBar from './components/UnifiedPlayerBar'
import { PlayerProvider } from './context/PlayerContext'
import { JukeboxProvider } from './context/JukeboxContext'
import { RecordingProvider } from './context/RecordingContext'
import { api } from './services/api'

function App() {
  const [isConfigured, setIsConfigured] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    checkSetupStatus()
  }, [])

  const checkSetupStatus = async () => {
    try {
      const response = await api.get('/api/config/setup-status')
      setIsConfigured(!response.data.needs_setup)
    } catch (error) {
      console.error('Error checking setup status:', error)
      setIsConfigured(false)
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-sxm-dark flex items-center justify-center">
        <div className="text-center">
          <div className="w-16 h-16 border-4 border-sxm-accent border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
          <p className="text-gray-400">Loading ArchiveXM...</p>
        </div>
      </div>
    )
  }

  return (
    <JukeboxProvider>
      <PlayerProvider>
        <RecordingProvider>
          <AppContent isConfigured={isConfigured} setIsConfigured={setIsConfigured} />
        </RecordingProvider>
      </PlayerProvider>
    </JukeboxProvider>
  )
}

// Scroll to top on route change
function ScrollToTop() {
  const { pathname } = useLocation()
  
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [pathname])
  
  return null
}

function AppContent({ isConfigured, setIsConfigured }) {
  const location = useLocation()
  const isJukeboxPage = location.pathname === '/jukebox'

  return (
    <div className="pb-20"> {/* Padding for bottom player bar */}
      <ScrollToTop />
      <Routes>
        <Route 
          path="/setup" 
          element={
            isConfigured ? <Navigate to="/" replace /> : <SetupPage onComplete={() => setIsConfigured(true)} />
          } 
        />
        <Route 
          path="/" 
          element={
            isConfigured ? <Layout /> : <Navigate to="/setup" replace />
          }
        >
          <Route index element={<ChannelsPage />} />
          <Route path="channel/:channelId" element={<ChannelDetailPage />} />
        </Route>
        <Route 
          path="/jukebox" 
          element={
            isConfigured ? <JukeboxPage /> : <Navigate to="/setup" replace />
          } 
        />
        <Route 
          path="/settings" 
          element={
            isConfigured ? <Layout /> : <Navigate to="/setup" replace />
          }
        >
          <Route index element={<SettingsPage />} />
        </Route>
      </Routes>
      {isConfigured && <DownloadStatus />}
      {isConfigured && !isJukeboxPage && <UnifiedPlayerBar />}
    </div>
  )
}

export default App
