import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { 
  ArrowLeft, Settings, User, Key, Plus, Trash2, Edit2, Check, X, 
  RefreshCw, Shield, Activity, AlertCircle, CheckCircle, Globe2, Wifi, Save
} from 'lucide-react'
import { api } from '../services/api'
import { usePlayer } from '../context/PlayerContext'

function SettingsPage() {
  const { currentChannel } = usePlayer()
  const [credentials, setCredentials] = useState([])
  const [loading, setLoading] = useState(true)
  const [stats, setStats] = useState(null)
  const [config, setConfig] = useState(null)
  const [configSaving, setConfigSaving] = useState(false)
  const [configMessage, setConfigMessage] = useState('')
  const [channelCustomOffset, setChannelCustomOffset] = useState('')
  
  // Add credential modal
  const [showAddModal, setShowAddModal] = useState(false)
  const [addForm, setAddForm] = useState({ name: '', username: '', password: '', max_streams: 3 })
  const [addLoading, setAddLoading] = useState(false)
  const [addError, setAddError] = useState('')
  
  // Edit credential
  const [editingId, setEditingId] = useState(null)
  const [editForm, setEditForm] = useState({})
  
  // Testing credential
  const [testingId, setTestingId] = useState(null)
  const [testResult, setTestResult] = useState(null)

  useEffect(() => {
    loadCredentials()
  }, [])

  const getCurrentChannelId = () => currentChannel?.channel_id || currentChannel?.id || currentChannel?.uuid

  useEffect(() => {
    if (!config) return
    const channelId = getCurrentChannelId()
    if (!channelId) {
      setChannelCustomOffset('')
      return
    }

    const existingOffset = config.live_metadata_channel_offsets?.[channelId]
    setChannelCustomOffset(
      existingOffset !== undefined && existingOffset !== null
        ? String(existingOffset)
        : String(config.live_metadata_offset_seconds ?? 38)
    )
  }, [currentChannel, config?.live_metadata_channel_offsets, config?.live_metadata_offset_seconds])

  const loadCredentials = async () => {
    try {
      const [credsRes, statsRes, configRes] = await Promise.all([
        api.get('/api/settings/credentials'),
        api.get('/api/settings/stream-stats'),
        api.get('/api/config')
      ])
      setCredentials(credsRes.data.credentials || [])
      setStats(statsRes.data)
      setConfig(configRes.data)
    } catch (error) {
      console.error('Error loading credentials:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleSaveConfig = async () => {
    if (!config) return
    setConfigSaving(true)
    setConfigMessage('')

    try {
      const res = await api.post('/api/config', {
        download_path: config.download_path,
        audio_quality: config.audio_quality,
        playlist_url_mode: config.playlist_url_mode,
        playlist_local_base_url: config.playlist_local_base_url || '',
        playlist_public_base_url: config.playlist_public_base_url || '',
        playlist_url_style: config.playlist_url_style || 'listen',
        playlist_auto_generate: config.playlist_auto_generate,
        download_tail_pad_seconds: Number(config.download_tail_pad_seconds ?? 2),
        live_metadata_offset_seconds: Number(config.live_metadata_offset_seconds ?? 0),
        live_metadata_hide_short_cuts: !!config.live_metadata_hide_short_cuts,
        live_metadata_short_cut_max_seconds: Number(config.live_metadata_short_cut_max_seconds ?? 45),
        live_metadata_channel_offsets: config.live_metadata_channel_offsets || {}
      })
      setConfigMessage(res.data?.playlist?.status === 'error' ? `Saved, but playlist generation failed: ${res.data.playlist.message}` : 'Saved and playlist regenerated')
      const refreshed = await api.get('/api/config')
      setConfig(refreshed.data)
    } catch (error) {
      setConfigMessage(error.response?.data?.detail || 'Failed to save playlist settings')
    } finally {
      setConfigSaving(false)
    }
  }

  const handleSaveCurrentChannelOffset = async () => {
    const channelId = getCurrentChannelId()
    if (!channelId || !config) return

    const customValue = Number(channelCustomOffset === '' ? config.live_metadata_offset_seconds ?? 38 : channelCustomOffset)
    const offsets = { ...(config.live_metadata_channel_offsets || {}) }
    offsets[channelId] = customValue
    setConfig({ ...config, live_metadata_channel_offsets: offsets })

    setConfigSaving(true)
    setConfigMessage('')
    try {
      await api.post('/api/config', {
        live_metadata_channel_offsets: offsets
      })
      const refreshed = await api.get('/api/config')
      setConfig(refreshed.data)
      setChannelCustomOffset(String(refreshed.data?.live_metadata_channel_offsets?.[channelId] ?? customValue))
      setConfigMessage(`Saved ${customValue}s custom live metadata offset for ${currentChannel?.name || 'current channel'}`)
    } catch (error) {
      setConfigMessage(error.response?.data?.detail || 'Failed to save channel offset')
    } finally {
      setConfigSaving(false)
    }
  }

  const handleResetCurrentChannelOffset = async () => {
    const channelId = getCurrentChannelId()
    if (!channelId || !config) return

    const offsets = { ...(config.live_metadata_channel_offsets || {}) }
    delete offsets[channelId]
    setConfig({ ...config, live_metadata_channel_offsets: offsets })

    setConfigSaving(true)
    setConfigMessage('')
    try {
      await api.post('/api/config', { live_metadata_channel_offsets: offsets })
      const refreshed = await api.get('/api/config')
      setConfig(refreshed.data)
      setChannelCustomOffset(String(refreshed.data?.live_metadata_offset_seconds ?? 38))
      setConfigMessage(`Reset ${currentChannel?.name || 'current channel'} to the global offset`)
    } catch (error) {
      setConfigMessage(error.response?.data?.detail || 'Failed to reset channel offset')
    } finally {
      setConfigSaving(false)
    }
  }

  const handleClearLiveMetadataOffsets = async () => {
    if (!config || !confirm('Clear all custom per-channel live metadata offsets?')) return

    setConfigSaving(true)
    setConfigMessage('')
    try {
      await api.post('/api/config', { live_metadata_channel_offsets: {} })
      const refreshed = await api.get('/api/config')
      setConfig(refreshed.data)
      setChannelCustomOffset(currentChannel ? String(refreshed.data?.live_metadata_offset_seconds ?? 38) : '')
      setConfigMessage('Cleared all custom live metadata offsets')
    } catch (error) {
      setConfigMessage(error.response?.data?.detail || 'Failed to clear custom offsets')
    } finally {
      setConfigSaving(false)
    }
  }

  const handleAddCredential = async (e) => {
    e.preventDefault()
    setAddLoading(true)
    setAddError('')
    
    try {
      await api.post('/api/settings/credentials', addForm)
      setShowAddModal(false)
      setAddForm({ name: '', username: '', password: '', max_streams: 3 })
      await loadCredentials()
    } catch (error) {
      setAddError(error.response?.data?.detail || 'Failed to add credential')
    } finally {
      setAddLoading(false)
    }
  }

  const handleUpdateCredential = async (id) => {
    try {
      await api.put(`/api/settings/credentials/${id}`, editForm)
      setEditingId(null)
      await loadCredentials()
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to update credential')
    }
  }

  const handleDeleteCredential = async (id, name) => {
    if (!confirm(`Are you sure you want to delete "${name}"?`)) return
    
    try {
      await api.delete(`/api/settings/credentials/${id}`)
      await loadCredentials()
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to delete credential')
    }
  }

  const handleTestCredential = async (id) => {
    setTestingId(id)
    setTestResult(null)
    
    try {
      const res = await api.post(`/api/settings/credentials/${id}/test`)
      setTestResult({ id, success: res.data.success, message: res.data.message })
      await loadCredentials()
    } catch (error) {
      setTestResult({ id, success: false, message: error.response?.data?.detail || 'Test failed' })
    } finally {
      setTestingId(null)
    }
  }

  const startEdit = (cred) => {
    setEditingId(cred.id)
    setEditForm({
      name: cred.name,
      max_streams: cred.max_streams,
      is_active: cred.is_active
    })
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-12 h-12 border-4 border-sxm-accent border-t-transparent rounded-full animate-spin"></div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4 mb-6">
        <Link
          to="/"
          className="text-gray-400 hover:text-white transition-colors"
        >
          <ArrowLeft size={24} />
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Settings className="w-6 h-6" />
            Settings
          </h1>
          <p className="text-gray-400 text-sm">Manage credentials and application settings</p>
        </div>
      </div>

      {/* Stream Stats Overview */}
      {stats && (
        <div className="card mb-6">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <Activity className="w-5 h-5 text-sxm-accent" />
            Stream Capacity
          </h2>
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-sxm-darker rounded-lg p-4 text-center">
              <div className="text-2xl font-bold text-white">{stats.total_active_streams}</div>
              <div className="text-gray-400 text-sm">Active Streams</div>
            </div>
            <div className="bg-sxm-darker rounded-lg p-4 text-center">
              <div className="text-2xl font-bold text-white">{stats.total_capacity}</div>
              <div className="text-gray-400 text-sm">Total Capacity</div>
            </div>
            <div className="bg-sxm-darker rounded-lg p-4 text-center">
              <div className="text-2xl font-bold text-sxm-success">{stats.available_capacity}</div>
              <div className="text-gray-400 text-sm">Available</div>
            </div>
          </div>
        </div>
      )}

      {/* Playlist Settings */}
      {config && (
        <div className="card mb-6">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <Globe2 className="w-5 h-5 text-sxm-accent" />
            Playlist Output
          </h2>
          <p className="text-gray-400 text-sm mb-4">
            Choose the URL shape written into generated M3U playlists. Public mode is for reverse proxy access away from home.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <button
              type="button"
              onClick={() => setConfig({ ...config, playlist_url_mode: 'local' })}
              className={`p-4 rounded-lg border text-left transition-colors ${config.playlist_url_mode === 'local' ? 'border-sxm-accent bg-sxm-accent/10' : 'border-sxm-border hover:border-gray-600'}`}
            >
              <Wifi className="w-5 h-5 text-sxm-accent mb-2" />
              <div className="text-white font-medium">Local / LAN</div>
              <div className="text-gray-500 text-sm">Use your local ArchiveXM address</div>
            </button>
            <button
              type="button"
              onClick={() => setConfig({ ...config, playlist_url_mode: 'public' })}
              className={`p-4 rounded-lg border text-left transition-colors ${config.playlist_url_mode === 'public' ? 'border-sxm-accent bg-sxm-accent/10' : 'border-sxm-border hover:border-gray-600'}`}
            >
              <Globe2 className="w-5 h-5 text-sxm-accent mb-2" />
              <div className="text-white font-medium">Public / Reverse Proxy</div>
              <div className="text-gray-500 text-sm">Use your public HTTPS domain</div>
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-gray-400 text-sm mb-1">Local base URL</label>
              <input
                type="text"
                value={config.playlist_local_base_url || ''}
                onChange={(e) => setConfig({ ...config, playlist_local_base_url: e.target.value })}
                className="input w-full"
                placeholder="http://10.0.0.44:8742"
              />
            </div>
            <div>
              <label className="block text-gray-400 text-sm mb-1">Public proxy base URL</label>
              <input
                type="text"
                value={config.playlist_public_base_url || ''}
                onChange={(e) => setConfig({ ...config, playlist_public_base_url: e.target.value })}
                className="input w-full"
                placeholder="https://yourdomain.com"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
            <div>
              <label className="block text-gray-400 text-sm mb-1">Playlist URL style</label>
              <select
                value={config.playlist_url_style || 'listen'}
                onChange={(e) => setConfig({ ...config, playlist_url_style: e.target.value })}
                className="input w-full"
              >
                <option value="listen">/listen/&lt;uuid&gt; - M3You compatible</option>
                <option value="api">/api/streams/&lt;uuid&gt;/proxy-stream</option>
              </select>
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 cursor-pointer pb-3">
                <input
                  type="checkbox"
                  checked={!!config.playlist_auto_generate}
                  onChange={(e) => setConfig({ ...config, playlist_auto_generate: e.target.checked })}
                  className="w-4 h-4 rounded"
                />
                <span className="text-gray-300 text-sm">Auto-generate playlist on config changes</span>
              </label>
            </div>
          </div>

          <div className="flex items-center justify-between mt-5">
            <div className="text-sm text-gray-400">
              Playlist endpoint: <span className="text-gray-300">/api/playlist.m3u</span>
              {configMessage && <div className="text-sxm-success mt-1">{configMessage}</div>}
            </div>
            <button
              onClick={handleSaveConfig}
              disabled={configSaving}
              className="btn-primary flex items-center gap-2"
            >
              {configSaving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Save Playlist Settings
            </button>
          </div>
        </div>
      )}


      {/* Download Settings */}
      {config && (
        <div className="card mb-6">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <Save className="w-5 h-5 text-sxm-accent" />
            Downloads
          </h2>
          <p className="text-gray-400 text-sm mb-4">
            Add a small tail pad after the raw SiriusXM metadata boundary. This keeps song endings from sounding clipped while still preventing long DJ/talk bleed.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-gray-400 text-sm mb-1">Download tail pad seconds</label>
              <input
                type="number"
                min="0"
                max="5"
                step="0.5"
                value={config.download_tail_pad_seconds ?? 2}
                onChange={(e) => setConfig({ ...config, download_tail_pad_seconds: e.target.value })}
                className="input w-full"
              />
              <p className="text-gray-500 text-xs mt-1">Recommended: 1.5-2.0. Use 0 to disable. Maximum: 5 seconds.</p>
            </div>
          </div>

          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={handleSaveConfig}
              disabled={configSaving}
              className="btn-primary flex items-center gap-2 disabled:opacity-50"
            >
              {configSaving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Save Download Settings
            </button>
            {configMessage && (
              <span className={`text-sm ${configMessage.includes('Failed') ? 'text-red-400' : 'text-green-400'}`}>
                {configMessage}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Live Metadata Settings */}
      {config && (
        <div className="card mb-6">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <Activity className="w-5 h-5 text-sxm-accent" />
            Live Metadata
          </h2>
          <p className="text-gray-400 text-sm mb-4">
            Adjust when live metadata changes and whether short DJ/channel plug cuts should be shown in the player. Station History remains song-focused and hides interstitial cuts.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-gray-400 text-sm mb-1">Global metadata offset seconds</label>
              <input
                type="number"
                min="-120"
                max="120"
                step="1"
                value={config.live_metadata_offset_seconds ?? 38}
                onChange={(e) => setConfig({ ...config, live_metadata_offset_seconds: e.target.value })}
                className="input w-full"
              />
              <p className="text-gray-500 text-xs mt-1">Positive delays metadata. Negative shows it earlier. Example: use 38 if metadata changes 38 seconds too early.</p>
            </div>

            <div>
              <label className="block text-gray-400 text-sm mb-1">Short cut max seconds</label>
              <input
                type="number"
                min="1"
                max="300"
                step="1"
                value={config.live_metadata_short_cut_max_seconds ?? 45}
                onChange={(e) => setConfig({ ...config, live_metadata_short_cut_max_seconds: e.target.value })}
                className="input w-full"
              />
              <p className="text-gray-500 text-xs mt-1">Used only when hiding short DJ/channel plug cuts is enabled.</p>
            </div>
          </div>

          <div className="mt-4">
            <label className="flex items-center gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={!!config.live_metadata_hide_short_cuts}
                onChange={(e) => setConfig({ ...config, live_metadata_hide_short_cuts: e.target.checked })}
                className="w-4 h-4 rounded"
              />
              <span className="text-gray-300 text-sm">Hide short DJ/channel plug cuts in live metadata</span>
            </label>
            <p className="text-gray-500 text-xs mt-1 ml-7">Off by default. When off, ArchiveXM can show DJ/plug metadata if SiriusXM provides it.</p>
          </div>

          <div className="mt-5 p-4 rounded-lg bg-sxm-darker border border-sxm-border">
            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3">
              <div>
                <div className="text-white font-medium">Current channel custom offset</div>
                {currentChannel ? (
                  <div className="space-y-2">
                    <div className="text-gray-400 text-sm">
                      {currentChannel.name} {getCurrentChannelId() && config.live_metadata_channel_offsets?.[getCurrentChannelId()] !== undefined
                        ? `uses ${config.live_metadata_channel_offsets[getCurrentChannelId()]}s custom offset`
                        : `uses the global ${config.live_metadata_offset_seconds ?? 38}s offset`}
                    </div>
                    <div className="flex items-center gap-2">
                      <label className="text-gray-400 text-xs">Custom offset seconds</label>
                      <input
                        type="number"
                        min="-120"
                        max="120"
                        step="1"
                        value={channelCustomOffset}
                        onChange={(e) => setChannelCustomOffset(e.target.value)}
                        className="input w-28"
                      />
                    </div>
                  </div>
                ) : (
                  <div className="text-gray-500 text-sm">Start a live channel to save a custom offset for it.</div>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={handleSaveCurrentChannelOffset}
                  disabled={configSaving || !currentChannel}
                  className="btn-secondary text-sm disabled:opacity-50"
                >
                  Save Custom Offset
                </button>
                <button
                  type="button"
                  onClick={handleResetCurrentChannelOffset}
                  disabled={configSaving || !currentChannel}
                  className="btn-secondary text-sm disabled:opacity-50"
                >
                  Use Global Offset
                </button>
                <button
                  type="button"
                  onClick={handleClearLiveMetadataOffsets}
                  disabled={configSaving}
                  className="btn-secondary text-sm disabled:opacity-50"
                >
                  Clear Custom Offsets
                </button>
              </div>
            </div>
          </div>

          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={handleSaveConfig}
              disabled={configSaving}
              className="btn-primary flex items-center gap-2 disabled:opacity-50"
            >
              {configSaving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Save Live Metadata Settings
            </button>
            {configMessage && (
              <span className={`text-sm ${configMessage.includes('Failed') ? 'text-red-400' : 'text-green-400'}`}>
                {configMessage}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Credentials Management */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <Key className="w-5 h-5 text-sxm-accent" />
            SiriusXM Accounts
          </h2>
          <button
            onClick={() => setShowAddModal(true)}
            className="btn-primary flex items-center gap-2 text-sm"
          >
            <Plus className="w-4 h-4" />
            Add Account
          </button>
        </div>

        <p className="text-gray-400 text-sm mb-4">
          Add multiple accounts to increase concurrent stream capacity. Each account supports up to 3 simultaneous streams.
        </p>

        {credentials.length === 0 ? (
          <div className="text-center py-8 text-gray-400">
            <User className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>No accounts configured</p>
            <p className="text-sm mt-1">Add your SiriusXM credentials to get started</p>
          </div>
        ) : (
          <div className="space-y-3">
            {credentials.map((cred) => (
              <div
                key={cred.id}
                className={`bg-sxm-darker rounded-lg p-4 border ${
                  cred.is_active ? 'border-gray-700' : 'border-gray-800 opacity-60'
                }`}
              >
                {editingId === cred.id ? (
                  // Edit mode
                  <div className="space-y-3">
                    <div className="flex items-center gap-3">
                      <input
                        type="text"
                        value={editForm.name}
                        onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                        className="input flex-1"
                        placeholder="Account name"
                      />
                      <div className="flex items-center gap-2">
                        <span className="text-gray-400 text-sm">Max streams:</span>
                        <input
                          type="number"
                          min="1"
                          max="5"
                          value={editForm.max_streams}
                          onChange={(e) => setEditForm({ ...editForm, max_streams: parseInt(e.target.value) })}
                          className="input w-16 text-center"
                        />
                      </div>
                    </div>
                    <div className="flex items-center justify-between">
                      <label className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={editForm.is_active}
                          onChange={(e) => setEditForm({ ...editForm, is_active: e.target.checked })}
                          className="w-4 h-4 rounded"
                        />
                        <span className="text-gray-300 text-sm">Active</span>
                      </label>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setEditingId(null)}
                          className="p-2 text-gray-400 hover:text-white hover:bg-gray-700 rounded"
                        >
                          <X className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleUpdateCredential(cred.id)}
                          className="p-2 text-sxm-success hover:bg-gray-700 rounded"
                        >
                          <Check className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  </div>
                ) : (
                  // View mode
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-4">
                      <div className={`w-10 h-10 rounded-full flex items-center justify-center ${
                        cred.has_valid_session ? 'bg-sxm-success/20 text-sxm-success' : 'bg-gray-700 text-gray-400'
                      }`}>
                        <User className="w-5 h-5" />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-white font-medium">{cred.name}</span>
                          {!cred.is_active && (
                            <span className="text-xs bg-gray-700 text-gray-400 px-2 py-0.5 rounded">Disabled</span>
                          )}
                        </div>
                        <div className="text-gray-400 text-sm">{cred.username}</div>
                      </div>
                    </div>
                    
                    <div className="flex items-center gap-4">
                      {/* Stream usage */}
                      <div className="text-right">
                        <div className="text-white">
                          {cred.active_streams} / {cred.max_streams}
                        </div>
                        <div className="text-gray-500 text-xs">streams</div>
                      </div>
                      
                      {/* Status & Expiration */}
                      <div className="text-right hidden sm:block">
                        <div className={`flex items-center gap-1 text-sm ${
                          cred.has_valid_session ? 'text-sxm-success' : 'text-sxm-warning'
                        }`}>
                          {cred.has_valid_session ? (
                            <>
                              <CheckCircle className="w-4 h-4" />
                              <span>Valid</span>
                            </>
                          ) : (
                            <>
                              <AlertCircle className="w-4 h-4" />
                              <span>Needs auth</span>
                            </>
                          )}
                        </div>
                        {cred.session_expires_in && cred.has_valid_session && (
                          <div className="text-gray-500 text-xs">
                            Expires in {cred.session_expires_in}
                          </div>
                        )}
                      </div>
                      
                      {/* Mobile status icon only */}
                      <div className={`sm:hidden ${
                        cred.has_valid_session ? 'text-sxm-success' : 'text-sxm-warning'
                      }`}>
                        {cred.has_valid_session ? (
                          <CheckCircle className="w-4 h-4" />
                        ) : (
                          <AlertCircle className="w-4 h-4" />
                        )}
                      </div>
                      
                      {/* Test result */}
                      {testResult?.id === cred.id && (
                        <span className={`text-sm ${testResult.success ? 'text-sxm-success' : 'text-sxm-error'}`}>
                          {testResult.success ? '✓' : '✗'}
                        </span>
                      )}
                      
                      {/* Actions */}
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => handleTestCredential(cred.id)}
                          disabled={testingId === cred.id}
                          className="p-2 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors"
                          title="Test credential"
                        >
                          <RefreshCw className={`w-4 h-4 ${testingId === cred.id ? 'animate-spin' : ''}`} />
                        </button>
                        <button
                          onClick={() => startEdit(cred)}
                          className="p-2 text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors"
                          title="Edit"
                        >
                          <Edit2 className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleDeleteCredential(cred.id, cred.name)}
                          className="p-2 text-gray-400 hover:text-red-400 hover:bg-gray-700 rounded transition-colors"
                          title="Delete"
                          disabled={credentials.length <= 1}
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Add Credential Modal */}
      {showAddModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-sxm-card border border-sxm-border rounded-xl p-6 w-full max-w-md">
            <h3 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
              <Shield className="w-5 h-5 text-sxm-accent" />
              Add SiriusXM Account
            </h3>
            
            <form onSubmit={handleAddCredential} className="space-y-4">
              <div>
                <label className="block text-gray-400 text-sm mb-1">Account Name</label>
                <input
                  type="text"
                  value={addForm.name}
                  onChange={(e) => setAddForm({ ...addForm, name: e.target.value })}
                  className="input w-full"
                  placeholder="e.g., Primary, Wife's Account"
                  required
                />
              </div>
              
              <div>
                <label className="block text-gray-400 text-sm mb-1">SiriusXM Username/Email</label>
                <input
                  type="text"
                  value={addForm.username}
                  onChange={(e) => setAddForm({ ...addForm, username: e.target.value })}
                  className="input w-full"
                  placeholder="email@example.com"
                  required
                />
              </div>
              
              <div>
                <label className="block text-gray-400 text-sm mb-1">Password</label>
                <input
                  type="password"
                  value={addForm.password}
                  onChange={(e) => setAddForm({ ...addForm, password: e.target.value })}
                  className="input w-full"
                  required
                />
              </div>
              
              <div>
                <label className="block text-gray-400 text-sm mb-1">Max Concurrent Streams</label>
                <input
                  type="number"
                  min="1"
                  max="5"
                  value={addForm.max_streams}
                  onChange={(e) => setAddForm({ ...addForm, max_streams: parseInt(e.target.value) })}
                  className="input w-full"
                />
                <p className="text-gray-500 text-xs mt-1">SiriusXM typically allows 3 concurrent streams per account</p>
              </div>
              
              {addError && (
                <div className="text-sxm-error text-sm bg-sxm-error/10 p-3 rounded-lg">
                  {addError}
                </div>
              )}
              
              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => { setShowAddModal(false); setAddError('') }}
                  className="btn-secondary flex-1"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={addLoading}
                  className="btn-primary flex-1 flex items-center justify-center gap-2"
                >
                  {addLoading ? (
                    <>
                      <RefreshCw className="w-4 h-4 animate-spin" />
                      Verifying...
                    </>
                  ) : (
                    'Add Account'
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

export default SettingsPage
