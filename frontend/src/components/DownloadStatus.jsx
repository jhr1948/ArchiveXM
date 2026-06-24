import { useState, useEffect } from 'react'
import { Download, CheckCircle, XCircle, Loader2, X, Music, Trash2 } from 'lucide-react'
import { downloadsApi } from '../services/api'

function DownloadStatus() {
  const [downloads, setDownloads] = useState([])
  const [isOpen, setIsOpen] = useState(false)

  useEffect(() => {
    loadDownloads()
    const interval = setInterval(loadDownloads, 5000)
    return () => clearInterval(interval)
  }, [])

  const loadDownloads = async () => {
    try {
      const response = await downloadsApi.getHistory(10, 0)
      setDownloads(response.data.downloads || [])
    } catch (error) {
      console.error('Error loading downloads:', error)
    }
  }

  const cancelDownload = async (downloadId) => {
    try {
      await downloadsApi.cancel(downloadId)
      await loadDownloads()
    } catch (error) {
      console.error('Error cancelling download:', error)
      alert(error.response?.data?.detail || 'Could not cancel download')
    }
  }

  const clearHistory = async () => {
    if (activeDownloads.length > 0) {
      alert('Cancel active downloads before clearing history.')
      return
    }
    try {
      await downloadsApi.clearHistory()
      await loadDownloads()
    } catch (error) {
      console.error('Error clearing download history:', error)
      alert('Could not clear download history')
    }
  }

  const activeDownloads = downloads.filter(d => 
    d.status === 'pending' || d.status === 'downloading'
  )
  const recentDownloads = downloads.filter(d => 
    d.status === 'completed' || d.status?.startsWith('failed') || d.status?.startsWith('cancelled')
  ).slice(0, 5)

  const getStatusIcon = (status) => {
    if (status === 'completed') return <CheckCircle className="w-4 h-4 text-sxm-success" />
    if (status === 'downloading') return <Loader2 className="w-4 h-4 text-sxm-accent animate-spin" />
    if (status === 'pending') return <Loader2 className="w-4 h-4 text-gray-400 animate-spin" />
    if (status?.startsWith('failed')) return <XCircle className="w-4 h-4 text-sxm-error" />
    if (status?.startsWith('cancelled')) return <XCircle className="w-4 h-4 text-gray-500" />
    return <Download className="w-4 h-4 text-gray-400" />
  }

  const getStatusText = (status) => {
    if (status === 'completed') return 'Downloaded'
    if (status === 'downloading') return 'Downloading...'
    if (status === 'pending') return 'Queued'
    if (status?.startsWith('failed')) return 'Failed'
    if (status?.startsWith('cancelled')) return 'Cancelled'
    return status
  }

  const formatTime = (dateStr) => {
    const date = new Date(dateStr)
    const now = new Date()
    const diff = (now - date) / 1000

    if (diff < 60) return 'just now'
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
    return date.toLocaleDateString()
  }

  if (downloads.length === 0) return null

  return (
    <div className="fixed bottom-24 right-4 z-50">
      {/* Toggle Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`flex items-center gap-2 px-4 py-3 rounded-xl shadow-lg transition-all ${
          activeDownloads.length > 0
            ? 'bg-sxm-accent text-white'
            : 'bg-sxm-card border border-sxm-border text-gray-300 hover:text-white'
        }`}
      >
        {activeDownloads.length > 0 ? (
          <>
            <Loader2 className="w-5 h-5 animate-spin" />
            <span className="font-medium">{activeDownloads.length} downloading</span>
          </>
        ) : (
          <>
            <Download className="w-5 h-5" />
            <span className="font-medium">Downloads</span>
          </>
        )}
      </button>

      {/* Downloads Panel */}
      {isOpen && (
        <div className="absolute bottom-14 right-0 w-80 bg-sxm-card border border-sxm-border rounded-xl shadow-2xl overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-sxm-border">
            <h3 className="font-medium text-white">Downloads</h3>
            <div className="flex items-center gap-2">
              {recentDownloads.length > 0 && activeDownloads.length === 0 && (
                <button
                  onClick={clearHistory}
                  className="text-gray-400 hover:text-red-400"
                  title="Clear download history"
                >
                  <Trash2 size={16} />
                </button>
              )}
              <button
                onClick={() => setIsOpen(false)}
                className="text-gray-400 hover:text-white"
              >
                <X size={18} />
              </button>
            </div>
          </div>

          {/* Downloads List */}
          <div className="max-h-80 overflow-y-auto">
            {activeDownloads.length > 0 && (
              <div className="p-2">
                <p className="text-xs text-gray-500 px-2 mb-2">Active</p>
                {activeDownloads.map(download => (
                  <div
                    key={download.id}
                    className="group flex items-center gap-3 p-2 rounded-lg bg-sxm-accent/10"
                  >
                    <div className="w-8 h-8 rounded bg-sxm-darker flex items-center justify-center shrink-0">
                      <Music className="w-4 h-4 text-gray-500" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-white truncate">{download.title}</p>
                      <p className="text-xs text-gray-400 truncate">{download.artist}</p>
                    </div>
                    <button
                      onClick={() => cancelDownload(download.id)}
                      className="hidden group-hover:flex w-6 h-6 items-center justify-center rounded-full text-gray-400 hover:text-red-400 hover:bg-red-500/10"
                      title="Cancel download"
                    >
                      <X size={15} />
                    </button>
                    <div className="group-hover:hidden">
                      {getStatusIcon(download.status)}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {recentDownloads.length > 0 && (
              <div className="p-2">
                <p className="text-xs text-gray-500 px-2 mb-2">Recent</p>
                {recentDownloads.map(download => (
                  <div
                    key={download.id}
                    className="flex items-center gap-3 p-2 rounded-lg hover:bg-sxm-darker/50"
                  >
                    <div className="w-8 h-8 rounded bg-sxm-darker flex items-center justify-center shrink-0">
                      <Music className="w-4 h-4 text-gray-500" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-white truncate">{download.title}</p>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-gray-400 truncate">{download.artist}</span>
                        <span className="text-xs text-gray-600">•</span>
                        <span className="text-xs text-gray-500">{formatTime(download.downloaded_at)}</span>
                      </div>
                    </div>
                    {getStatusIcon(download.status)}
                  </div>
                ))}
              </div>
            )}

            {downloads.length === 0 && (
              <div className="p-8 text-center">
                <Download className="w-8 h-8 text-gray-600 mx-auto mb-2" />
                <p className="text-gray-400 text-sm">No downloads yet</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default DownloadStatus
