import axios from 'axios'

const API_URL = import.meta.env.VITE_API_URL || ''

export const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json'
  }
})

// Auth API
export const authApi = {
  login: (username, password) => 
    api.post('/api/auth/login', { username, password }),
  
  status: () => 
    api.get('/api/auth/status'),
  
  refresh: () => 
    api.post('/api/auth/refresh'),
  
  logout: () => 
    api.post('/api/auth/logout')
}

// Config API
export const configApi = {
  get: () => 
    api.get('/api/config'),
  
  update: (config) => 
    api.post('/api/config', config),
  
  setupStatus: () => 
    api.get('/api/config/setup-status'),
  
  setup: (data) => 
    api.post('/api/config/setup', data)
}

// Channels API
export const channelsApi = {
  getAll: (params = {}) => 
    api.get('/api/channels', { params }),
  
  get: (channelId) => 
    api.get(`/api/channels/${channelId}`),
  
  getCategories: () => 
    api.get('/api/channels/categories'),
  
  refresh: () => 
    api.post('/api/channels/refresh')
}

// Streams API
export const streamsApi = {
  getSchedule: (channelId, hoursBack = 5, options = {}) => 
    api.get(`/api/streams/${channelId}/schedule`, { params: { hours_back: hoursBack, ...options } }),
  
  getNowPlaying: (channelId) => 
    api.get(`/api/streams/${channelId}/now-playing`),
  
  getStreamUrl: (channelId) => 
    api.get(`/api/streams/${channelId}/stream-url`),

  getXtraQueue: (channelId) =>
    api.get(`/api/streams/${channelId}/xtra/queue`),

  xtraNext: (channelId) =>
    api.post(`/api/streams/${channelId}/xtra/next`),

  xtraPrevious: (channelId) =>
    api.post(`/api/streams/${channelId}/xtra/previous`),

  xtraResume: (channelId) =>
    api.post(`/api/streams/${channelId}/xtra/resume`),
  
  // Get proxy stream URL for HLS playback (bypasses CORS)
  getProxyStreamUrl: (channelId) => 
    `${API_URL}/api/streams/${channelId}/proxy-stream`
}

// Downloads API
export const downloadsApi = {
  downloadTrack: (track) => 
    api.post('/api/downloads/track', track),

  downloadTrackToPlaylist: (track, playlist = {}) =>
    api.post('/api/downloads/track', { ...track, ...playlist }),
  
  downloadBulk: (channelId, tracks, playlist = {}) => 
    api.post('/api/downloads/bulk', { channel_id: channelId, tracks, ...playlist }),
  
  getHistory: (limit = 50, offset = 0) => 
    api.get('/api/downloads/history', { params: { limit, offset } }),
  
  getStatus: (downloadId) => 
    api.get(`/api/downloads/${downloadId}/status`)
}

// Library API (Jukebox)
export const libraryApi = {
  // Scanning
  scan: () => 
    api.post('/api/library/scan'),
  
  getStats: () => 
    api.get('/api/library/stats'),
  
  // Tracks
  getTracks: (params = {}) => 
    api.get('/api/library/tracks', { params }),
  
  getTrack: (trackId) => 
    api.get(`/api/library/tracks/${trackId}`),

  searchTrackMetadata: (trackId, params = {}) =>
    api.get(`/api/library/tracks/${trackId}/metadata/search`, { params }),

  applyTrackMetadata: (trackId, metadata) =>
    api.post(`/api/library/tracks/${trackId}/metadata/apply`, metadata),
  
  getStreamUrl: (trackId) => 
    `${API_URL}/api/library/tracks/${trackId}/stream`,
  
  getCoverUrl: (trackId) => 
    `${API_URL}/api/library/tracks/${trackId}/cover`,

  getDownloadsM3uUrl: () =>
    `${API_URL}/api/library/downloads.m3u`,

  getPlaylistM3uUrl: (playlistId) =>
    `${API_URL}/api/library/playlists/${playlistId}.m3u`,

  getPlaylistCoverUrl: (playlistId) =>
    `${API_URL}/api/library/playlists/${playlistId}/cover`,

  setPlaylistCoverUrl: (playlistId, coverImage) =>
    api.post(`/api/library/playlists/${playlistId}/cover-url`, { cover_image: coverImage }),

  uploadPlaylistCover: (playlistId, file) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post(`/api/library/playlists/${playlistId}/cover-upload`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
  },

  clearPlaylistCover: (playlistId) =>
    api.delete(`/api/library/playlists/${playlistId}/cover`),

  getExternalPlayUrl: (trackId) =>
    `${API_URL}/api/library/files/${trackId}/play`,

  getExternalMetadataUrl: (trackId) =>
    `${API_URL}/api/library/files/${trackId}/metadata`,

  captureCurrentToPlaylist: (playlistId, payload = {}) =>
    api.post(`/api/library/playlists/${playlistId}/capture-current`, payload),
  
  deleteTrack: (trackId, deleteFile = false) => 
    api.delete(`/api/library/tracks/${trackId}`, { params: { delete_file: deleteFile } }),

  bulkDeleteTracks: (trackIds, deleteFiles = true) =>
    api.post('/api/library/tracks/bulk-delete', { track_ids: trackIds, delete_files: deleteFiles }),
  
  // Artists & Albums
  getArtists: () => 
    api.get('/api/library/artists'),
  
  getAlbums: () => 
    api.get('/api/library/albums'),
  
  // Playlists
  getPlaylists: () => 
    api.get('/api/library/playlists'),
  
  getPlaylist: (playlistId) => 
    api.get(`/api/library/playlists/${playlistId}`),
  
  createPlaylist: (name, description = '') => 
    api.post('/api/library/playlists', { name, description }),
  
  updatePlaylist: (playlistId, name, description = '') => 
    api.put(`/api/library/playlists/${playlistId}`, { name, description }),
  
  deletePlaylist: (playlistId) => 
    api.delete(`/api/library/playlists/${playlistId}`),
  
  addToPlaylist: (playlistId, trackIds) => 
    api.post(`/api/library/playlists/${playlistId}/tracks`, { track_ids: trackIds }),
  
  removeFromPlaylist: (playlistId, trackId) => 
    api.delete(`/api/library/playlists/${playlistId}/tracks/${trackId}`),

  bulkRemoveFromPlaylist: (playlistId, trackIds) =>
    api.post(`/api/library/playlists/${playlistId}/tracks/bulk-remove`, { track_ids: trackIds }),
  
  reorderPlaylist: (playlistId, trackIds) => 
    api.put(`/api/library/playlists/${playlistId}/reorder`, trackIds)
}

export default api
