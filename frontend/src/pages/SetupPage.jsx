import { useState } from 'react'
import { Radio, Lock, User, FolderOpen, Loader2, CheckCircle, AlertCircle, Globe2, Wifi } from 'lucide-react'
import { configApi } from '../services/api'

function SetupPage({ onComplete }) {
  const [step, setStep] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const [credentials, setCredentials] = useState({
    username: '',
    password: ''
  })

  const [downloadPath, setDownloadPath] = useState('/downloads')
  const [playlistSettings, setPlaylistSettings] = useState({
    playlist_url_mode: 'local',
    playlist_local_base_url: '',
    playlist_public_base_url: '',
    playlist_url_style: 'listen',
    playlist_auto_generate: true
  })

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError('')

    try {
      const response = await configApi.setup({
        username: credentials.username,
        password: credentials.password,
        download_path: downloadPath,
        playlist_url_mode: playlistSettings.playlist_url_mode,
        playlist_local_base_url: playlistSettings.playlist_local_base_url || null,
        playlist_public_base_url: playlistSettings.playlist_public_base_url || null,
        playlist_url_style: playlistSettings.playlist_url_style,
        playlist_auto_generate: playlistSettings.playlist_auto_generate
      })

      if (response.data.success) {
        setStep(3)
        setTimeout(() => {
          onComplete()
        }, 2500)
      }
    } catch (err) {
      setError(err.response?.data?.detail || 'Setup failed. Please check your credentials.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-sxm-dark flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <img src="/logo.png" alt="ArchiveXM" className="w-24 h-24 mx-auto mb-4 rounded-2xl shadow-2xl" />
          <h1 className="text-3xl font-bold text-white mb-2">ArchiveXM</h1>
          <p className="text-gray-400">SiriusXM streaming & archival</p>
        </div>

        <div className="card">
          {step === 3 ? (
            <div className="text-center py-8">
              <CheckCircle className="w-16 h-16 text-sxm-success mx-auto mb-4" />
              <h2 className="text-xl font-bold text-white mb-2">Setup Complete!</h2>
              <p className="text-gray-400">Channels refreshed and playlist generated.</p>
              <p className="text-gray-500 text-sm mt-2">Redirecting to channels...</p>
            </div>
          ) : (
            <form onSubmit={handleSubmit}>
              <div className="flex items-center justify-center gap-2 mb-8">
                <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
                  step >= 1 ? 'bg-sxm-accent text-white' : 'bg-sxm-border text-gray-500'
                }`}>
                  1
                </div>
                <div className={`w-12 h-1 rounded ${step >= 2 ? 'bg-sxm-accent' : 'bg-sxm-border'}`}></div>
                <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium ${
                  step >= 2 ? 'bg-sxm-accent text-white' : 'bg-sxm-border text-gray-500'
                }`}>
                  2
                </div>
              </div>

              {step === 1 && (
                <>
                  <h2 className="text-xl font-bold text-white mb-6 text-center">
                    SiriusXM Credentials
                  </h2>

                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm text-gray-400 mb-2">Username / Email</label>
                      <div className="relative">
                        <User className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
                        <input
                          type="text"
                          className="input pl-10"
                          placeholder="your@email.com"
                          value={credentials.username}
                          onChange={(e) => setCredentials({ ...credentials, username: e.target.value })}
                          required
                        />
                      </div>
                    </div>

                    <div>
                      <label className="block text-sm text-gray-400 mb-2">Password</label>
                      <div className="relative">
                        <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
                        <input
                          type="password"
                          className="input pl-10"
                          placeholder="••••••••"
                          value={credentials.password}
                          onChange={(e) => setCredentials({ ...credentials, password: e.target.value })}
                          required
                        />
                      </div>
                    </div>
                  </div>

                  <button
                    type="button"
                    onClick={() => setStep(2)}
                    disabled={!credentials.username || !credentials.password}
                    className="btn-primary w-full mt-6 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Continue
                  </button>
                </>
              )}

              {step === 2 && (
                <>
                  <h2 className="text-xl font-bold text-white mb-6 text-center">
                    Storage & Playlist
                  </h2>

                  <div className="space-y-5">
                    <div>
                      <label className="block text-sm text-gray-400 mb-2">Save files to</label>
                      <div className="relative">
                        <FolderOpen className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
                        <input
                          type="text"
                          className="input pl-10"
                          placeholder="/downloads"
                          value={downloadPath}
                          onChange={(e) => setDownloadPath(e.target.value)}
                          required
                        />
                      </div>
                      <p className="text-xs text-gray-500 mt-2">
                        Inside Docker, use /downloads when mounted to your host.
                      </p>
                    </div>

                    <div className="border border-sxm-border rounded-lg p-4 bg-sxm-darker/50">
                      <label className="block text-sm text-gray-300 mb-3">Playlist URL Mode</label>
                      <div className="grid grid-cols-2 gap-3 mb-4">
                        <button
                          type="button"
                          onClick={() => setPlaylistSettings({ ...playlistSettings, playlist_url_mode: 'local' })}
                          className={`p-3 rounded-lg border text-left transition-colors ${playlistSettings.playlist_url_mode === 'local' ? 'border-sxm-accent bg-sxm-accent/10' : 'border-sxm-border hover:border-gray-600'}`}
                        >
                          <Wifi className="w-5 h-5 text-sxm-accent mb-2" />
                          <div className="text-white text-sm font-medium">Local / LAN</div>
                          <div className="text-gray-500 text-xs">Home network</div>
                        </button>
                        <button
                          type="button"
                          onClick={() => setPlaylistSettings({ ...playlistSettings, playlist_url_mode: 'public' })}
                          className={`p-3 rounded-lg border text-left transition-colors ${playlistSettings.playlist_url_mode === 'public' ? 'border-sxm-accent bg-sxm-accent/10' : 'border-sxm-border hover:border-gray-600'}`}
                        >
                          <Globe2 className="w-5 h-5 text-sxm-accent mb-2" />
                          <div className="text-white text-sm font-medium">Public Proxy</div>
                          <div className="text-gray-500 text-xs">Away from home</div>
                        </button>
                      </div>

                      {playlistSettings.playlist_url_mode === 'local' ? (
                        <div>
                          <label className="block text-sm text-gray-400 mb-2">Local playlist base URL</label>
                          <input
                            type="text"
                            className="input w-full"
                            placeholder="http://10.0.0.44:8742"
                            value={playlistSettings.playlist_local_base_url}
                            onChange={(e) => setPlaylistSettings({ ...playlistSettings, playlist_local_base_url: e.target.value })}
                          />
                          <p className="text-xs text-gray-500 mt-2">
                            Leave blank to use Docker/.env defaults.
                          </p>
                        </div>
                      ) : (
                        <div>
                          <label className="block text-sm text-gray-400 mb-2">Public proxy host</label>
                          <input
                            type="text"
                            className="input w-full"
                            placeholder="https://yourdomain.com"
                            value={playlistSettings.playlist_public_base_url}
                            onChange={(e) => setPlaylistSettings({ ...playlistSettings, playlist_public_base_url: e.target.value })}
                            required={playlistSettings.playlist_url_mode === 'public'}
                          />
                          <p className="text-xs text-gray-500 mt-2">
                            Include https:// or enter a host name and ArchiveXM will use HTTPS.
                          </p>
                        </div>
                      )}

                      <label className="flex items-center gap-2 mt-4 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={playlistSettings.playlist_auto_generate}
                          onChange={(e) => setPlaylistSettings({ ...playlistSettings, playlist_auto_generate: e.target.checked })}
                          className="w-4 h-4 rounded"
                        />
                        <span className="text-gray-300 text-sm">Refresh channels and generate M3U after setup</span>
                      </label>
                    </div>
                  </div>

                  {error && (
                    <div className="flex items-center gap-2 text-sxm-error bg-sxm-error/10 rounded-lg p-3 mt-4">
                      <AlertCircle size={18} />
                      <span className="text-sm">{error}</span>
                    </div>
                  )}

                  <div className="flex gap-3 mt-6">
                    <button
                      type="button"
                      onClick={() => setStep(1)}
                      className="btn-secondary flex-1"
                    >
                      Back
                    </button>
                    <button
                      type="submit"
                      disabled={loading || !downloadPath || (playlistSettings.playlist_url_mode === 'public' && !playlistSettings.playlist_public_base_url)}
                      className="btn-primary flex-1 flex items-center justify-center gap-2 disabled:opacity-50"
                    >
                      {loading ? (
                        <>
                          <Loader2 className="w-5 h-5 animate-spin" />
                          Setting up...
                        </>
                      ) : (
                        'Complete Setup'
                      )}
                    </button>
                  </div>
                </>
              )}
            </form>
          )}
        </div>

        <p className="text-center text-gray-600 text-sm mt-6">
          Your credentials are stored locally and encrypted
        </p>
      </div>
    </div>
  )
}

export default SetupPage
