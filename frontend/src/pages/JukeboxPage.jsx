import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { 
  Music, Play, Pause, SkipBack, SkipForward, Volume2, VolumeX,
  Shuffle, Repeat, List, Plus, Search, RefreshCw, Disc3,
  MoreVertical, Trash2, ListPlus, X, ChevronLeft, ChevronRight,
  Clock, Library, User, Album, Loader2, Radio, Home, Circle, Square, Copy, Image, Upload, CheckSquare
} from 'lucide-react'
import { libraryApi } from '../services/api'
import { useJukebox } from '../context/JukeboxContext'
import { usePlayer } from '../context/PlayerContext'
import { useRecording } from '../context/RecordingContext'

function JukeboxPage() {
  const navigate = useNavigate()
  
  // Get global jukebox state
  const {
    queue,
    currentIndex,
    currentTrack,
    isPlaying,
    currentTime,
    duration,
    volume,
    isMuted,
    shuffle,
    repeat,
    playTrack,
    togglePlay,
    playNext,
    playPrevious,
    seek,
    addToQueue,
    removeFromQueue,
    removeTrackFromPlayback,
    clearQueue,
    playAll,
    shuffleAll,
    setVolume,
    setIsMuted,
    setShuffle,
    setRepeat,
    setQueue,
    setCurrentIndex,
    playQueue,
  } = useJukebox()

  // Get live stream player state
  const livePlayer = usePlayer()
  
  // Get recording state
  const { isRecording, recordingData, stopRecording } = useRecording()

  // Library state
  const [tracks, setTracks] = useState([])
  const [playlists, setPlaylists] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [activeView, setActiveView] = useState('tracks') // tracks, playlist
  const [selectedPlaylist, setSelectedPlaylist] = useState(null)
  const [showQueue, setShowQueue] = useState(false) // Hidden by default on mobile
  const [showSidebar, setShowSidebar] = useState(false) // Mobile sidebar toggle
  
  // Playlist modal
  const [showPlaylistModal, setShowPlaylistModal] = useState(false)
  const [newPlaylistName, setNewPlaylistName] = useState('')
  const [trackToAdd, setTrackToAdd] = useState(null)
  
  // Playlist editing
  const [editingPlaylist, setEditingPlaylist] = useState(null)
  const [editPlaylistName, setEditPlaylistName] = useState('')
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(null)

  // Playlist cover editing
  const [coverEditingPlaylist, setCoverEditingPlaylist] = useState(null)
  const [coverUrlInput, setCoverUrlInput] = useState('')
  const [coverUploadFile, setCoverUploadFile] = useState(null)
  const [coverRefreshKey, setCoverRefreshKey] = useState(0)
  
  // Context menu for track actions
  const [contextMenu, setContextMenu] = useState({ show: false, x: 0, y: 0, track: null, index: -1 })

  // Bulk selection
  const [selectedTrackIds, setSelectedTrackIds] = useState(new Set())

  // Metadata lookup modal
  const [metadataTarget, setMetadataTarget] = useState(null)
  const [metadataCandidates, setMetadataCandidates] = useState([])
  const [metadataLoading, setMetadataLoading] = useState(false)
  const [metadataApplying, setMetadataApplying] = useState(false)
  const [metadataError, setMetadataError] = useState('')

  useEffect(() => {
    loadLibrary()
  }, [])

  useEffect(() => {
    setSelectedTrackIds(new Set())
  }, [activeView, selectedPlaylist?.id])

  const loadLibrary = async () => {
    setLoading(true)
    try {
      const [tracksRes, playlistsRes, statsRes] = await Promise.all([
        libraryApi.getTracks({ limit: 500 }),
        libraryApi.getPlaylists(),
        libraryApi.getStats()
      ])
      setTracks(tracksRes.data || [])
      setPlaylists(playlistsRes.data || [])
      setStats(statsRes.data)
    } catch (error) {
      console.error('Error loading library:', error)
    } finally {
      setLoading(false)
    }
  }

  const scanLibrary = async () => {
    setScanning(true)
    try {
      await libraryApi.scan()
      // Reload tracks without affecting playback (don't set loading)
      const [tracksRes, playlistsRes, statsRes] = await Promise.all([
        libraryApi.getTracks({ limit: 500 }),
        libraryApi.getPlaylists(),
        libraryApi.getStats()
      ])
      setTracks(tracksRes.data || [])
      setPlaylists(playlistsRes.data || [])
      setStats(statsRes.data)
    } catch (error) {
      console.error('Error scanning library:', error)
    } finally {
      setScanning(false)
    }
  }

  // Helper functions
  const handleSeek = (e) => {
    seek(parseFloat(e.target.value))
  }

  const formatTime = (seconds) => {
    if (!seconds || isNaN(seconds)) return '0:00'
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const absoluteUrl = (url) => {
    if (!url) return ''
    if (url.startsWith('http://') || url.startsWith('https://')) return url
    return `${window.location.origin}${url}`
  }

  const copyToClipboard = async (text, label = 'link') => {
    try {
      await navigator.clipboard.writeText(text)
      window.alert(`Copied ${label} to clipboard`)
    } catch (error) {
      console.error('Error copying link:', error)
      window.prompt(`Copy this ${label}:`, text)
    }
  }

  const copyDownloadsM3u = () => {
    copyToClipboard(absoluteUrl(libraryApi.getDownloadsM3uUrl()), 'downloads M3U link')
  }

  const copyPlaylistM3u = (playlistId, playlistName = 'playlist') => {
    copyToClipboard(absoluteUrl(libraryApi.getPlaylistM3uUrl(playlistId)), `${playlistName} M3U link`)
  }

  const playlistCoverSrc = (playlist) => {
    if (!playlist?.id) return null
    return `${libraryApi.getPlaylistCoverUrl(playlist.id)}?v=${coverRefreshKey}`
  }

  const openCoverEditor = (playlist) => {
    setCoverEditingPlaylist(playlist)
    setCoverUrlInput(playlist?.cover_image?.startsWith('http') ? playlist.cover_image : '')
    setCoverUploadFile(null)
  }

  const refreshPlaylistCoverState = async (playlistId) => {
    setCoverRefreshKey(prev => prev + 1)
    const [playlistsRes, playlistRes] = await Promise.all([
      libraryApi.getPlaylists(),
      libraryApi.getPlaylist(playlistId)
    ])
    setPlaylists(playlistsRes.data || [])
    setSelectedPlaylist(playlistRes.data)
    setCoverEditingPlaylist(playlistRes.data)
  }

  const savePlaylistCoverUrl = async () => {
    if (!coverEditingPlaylist?.id) return
    try {
      await libraryApi.setPlaylistCoverUrl(coverEditingPlaylist.id, coverUrlInput.trim())
      await refreshPlaylistCoverState(coverEditingPlaylist.id)
    } catch (error) {
      console.error('Error saving playlist cover URL:', error)
      window.alert('Failed to save playlist cover URL.')
    }
  }

  const uploadPlaylistCover = async () => {
    if (!coverEditingPlaylist?.id || !coverUploadFile) return
    try {
      await libraryApi.uploadPlaylistCover(coverEditingPlaylist.id, coverUploadFile)
      setCoverUploadFile(null)
      await refreshPlaylistCoverState(coverEditingPlaylist.id)
    } catch (error) {
      console.error('Error uploading playlist cover:', error)
      window.alert('Failed to upload playlist cover image.')
    }
  }

  const clearPlaylistCover = async () => {
    if (!coverEditingPlaylist?.id) return
    try {
      await libraryApi.clearPlaylistCover(coverEditingPlaylist.id)
      setCoverUrlInput('')
      setCoverUploadFile(null)
      await refreshPlaylistCoverState(coverEditingPlaylist.id)
    } catch (error) {
      console.error('Error clearing playlist cover:', error)
      window.alert('Failed to clear playlist cover.')
    }
  }

  // Stop live stream before starting Jukebox playback so the Jukebox player
  // becomes the active bottom banner immediately. Pausing live audio is not enough
  // because currentChannel remains set and the UI still thinks live is active.
  const pauseLiveIfPlaying = () => {
    if (livePlayer.currentChannel || livePlayer.isPlaying) {
      livePlayer.stop()
    }
  }

  // Local playAll/shuffleAll that use filtered tracks
  const handlePlayAll = () => {
    const filtered = getFilteredTracks()
    if (filtered.length > 0) {
      pauseLiveIfPlaying()
      playAll(filtered)
    }
  }

  const handleShuffleAll = () => {
    const filtered = getFilteredTracks()
    if (filtered.length > 0) {
      pauseLiveIfPlaying()
      shuffleAll(filtered)
    }
  }

  // Wrapper to pause live stream before playing track
  const handlePlayTrack = (track, index, trackList) => {
    pauseLiveIfPlaying()
    playTrack(track, index, trackList)
  }

  // Playlist management
  const createPlaylist = async () => {
    if (!newPlaylistName.trim()) return
    
    try {
      const created = await libraryApi.createPlaylist(newPlaylistName)

      if (trackToAdd && created?.data?.id) {
        await libraryApi.addToPlaylist(created.data.id, [trackToAdd.id])
        setTrackToAdd(null)
      }

      setNewPlaylistName('')
      setShowPlaylistModal(false)
      const res = await libraryApi.getPlaylists()
      setPlaylists(res.data || [])
    } catch (error) {
      console.error('Error creating playlist:', error)
    }
  }

  const addTrackToPlaylist = async (playlistId) => {
    if (!trackToAdd) return
    
    try {
      await libraryApi.addToPlaylist(playlistId, [trackToAdd.id])
      setTrackToAdd(null)
      setNewPlaylistName('')
      // Refresh playlists
      const res = await libraryApi.getPlaylists()
      setPlaylists(res.data || [])
      if (selectedPlaylist?.id === playlistId) {
        const playlistRes = await libraryApi.getPlaylist(playlistId)
        setSelectedPlaylist(playlistRes.data)
      }
    } catch (error) {
      console.error('Error adding to playlist:', error)
    }
  }

  const loadPlaylist = async (playlist) => {
    try {
      const res = await libraryApi.getPlaylist(playlist.id)
      setSelectedPlaylist(res.data)
      setActiveView('playlist')
    } catch (error) {
      console.error('Error loading playlist:', error)
    }
  }

  const playPlaylist = (playlist, shuffleMode = false) => {
    if (playlist.tracks && playlist.tracks.length > 0) {
      pauseLiveIfPlaying()
      const playlistTracks = playlist.tracks.map(pt => pt.track)
      if (shuffleMode) {
        shuffleAll(playlistTracks)
      } else {
        playAll(playlistTracks)
      }
    }
  }

  // Playlist editing
  const updatePlaylist = async () => {
    if (!editingPlaylist || !editPlaylistName.trim()) return
    
    try {
      await libraryApi.updatePlaylist(editingPlaylist.id, editPlaylistName)
      setEditingPlaylist(null)
      setEditPlaylistName('')
      // Refresh playlists
      const res = await libraryApi.getPlaylists()
      setPlaylists(res.data || [])
      // Refresh selected playlist if it was the one edited
      if (selectedPlaylist?.id === editingPlaylist.id) {
        const playlistRes = await libraryApi.getPlaylist(editingPlaylist.id)
        setSelectedPlaylist(playlistRes.data)
      }
    } catch (error) {
      console.error('Error updating playlist:', error)
    }
  }

  const deletePlaylist = async (playlistId) => {
    try {
      await libraryApi.deletePlaylist(playlistId)
      setShowDeleteConfirm(null)
      // Refresh playlists
      const res = await libraryApi.getPlaylists()
      setPlaylists(res.data || [])
      // Go back to tracks view if deleted playlist was selected
      if (selectedPlaylist?.id === playlistId) {
        setSelectedPlaylist(null)
        setActiveView('tracks')
      }
    } catch (error) {
      console.error('Error deleting playlist:', error)
    }
  }

  const removeTrackFromPlaylist = async (trackId) => {
    if (!selectedPlaylist) return
    
    try {
      await libraryApi.removeFromPlaylist(selectedPlaylist.id, trackId)
      // Refresh playlist
      const res = await libraryApi.getPlaylist(selectedPlaylist.id)
      setSelectedPlaylist(res.data)
      // Refresh playlists list for track count
      const playlistsRes = await libraryApi.getPlaylists()
      setPlaylists(playlistsRes.data || [])
    } catch (error) {
      console.error('Error removing track from playlist:', error)
    }
  }

  const deleteTrackEverywhere = async (track) => {
    if (!track) return

    const trackName = track.title || track.filename || 'this song'
    const confirmed = window.confirm(
      `Delete "${trackName}" from the Jukebox?

This removes it from all playlists and deletes the local audio file.`
    )

    if (!confirmed) return

    try {
      await libraryApi.deleteTrack(track.id, true)

      // Remove from local track list immediately
      setTracks(prev => prev.filter(t => t.id !== track.id))

      // Remove from selected playlist immediately if it is visible
      if (selectedPlaylist?.tracks) {
        setSelectedPlaylist(prev => prev ? {
          ...prev,
          tracks: (prev.tracks || []).filter(pt => pt.track?.id !== track.id),
          track_count: Math.max(0, (prev.track_count || 0) - ((prev.tracks || []).some(pt => pt.track?.id === track.id) ? 1 : 0))
        } : prev)
      }

      // Stop playback if this is the currently-playing track, and remove it from queue.
      if (removeTrackFromPlayback) {
        removeTrackFromPlayback(track.id)
      } else {
        setQueue(prev => prev.filter(t => t.id !== track.id))
      }

      // Refresh playlists/stats because deleting a track can affect every playlist
      const [playlistsRes, statsRes] = await Promise.all([
        libraryApi.getPlaylists(),
        libraryApi.getStats()
      ])
      setPlaylists(playlistsRes.data || [])
      setStats(statsRes.data)

      if (selectedPlaylist?.id) {
        const playlistRes = await libraryApi.getPlaylist(selectedPlaylist.id)
        setSelectedPlaylist(playlistRes.data)
      }
    } catch (error) {
      console.error('Error deleting track:', error)
      window.alert('Failed to delete the song. Check the backend logs for details.')
    }
  }

  const refreshTrackAfterMetadataApply = async (trackId) => {
    try {
      const [tracksRes, playlistsRes, statsRes] = await Promise.all([
        libraryApi.getTracks({ limit: 500 }),
        libraryApi.getPlaylists(),
        libraryApi.getStats()
      ])
      setTracks(tracksRes.data || [])
      setPlaylists(playlistsRes.data || [])
      setStats(statsRes.data)

      if (selectedPlaylist?.id) {
        const playlistRes = await libraryApi.getPlaylist(selectedPlaylist.id)
        setSelectedPlaylist(playlistRes.data)
      }
    } catch (error) {
      console.error('Error refreshing after metadata apply:', error)
    }
  }

  const openMetadataLookup = async (track) => {
    if (!track) return
    setMetadataTarget(track)
    setMetadataCandidates([])
    setMetadataError('')
    setMetadataLoading(true)

    try {
      const response = await libraryApi.searchTrackMetadata(track.id)
      setMetadataCandidates(response.data?.candidates || [])
      if (!response.data?.candidates?.length) {
        setMetadataError('No metadata matches found. Try editing artist/title manually later or search again after correcting the track name.')
      }
    } catch (error) {
      console.error('Error searching metadata:', error)
      setMetadataError(error.response?.data?.detail || 'Metadata lookup failed')
    } finally {
      setMetadataLoading(false)
    }
  }

  const closeMetadataLookup = () => {
    if (metadataApplying) return
    setMetadataTarget(null)
    setMetadataCandidates([])
    setMetadataError('')
  }

  const applyMetadataCandidate = async (candidate) => {
    if (!metadataTarget || !candidate) return
    setMetadataApplying(true)
    setMetadataError('')

    try {
      await libraryApi.applyTrackMetadata(metadataTarget.id, candidate)
      await refreshTrackAfterMetadataApply(metadataTarget.id)
      closeMetadataLookup()
    } catch (error) {
      console.error('Error applying metadata:', error)
      setMetadataError(error.response?.data?.detail || 'Failed to apply metadata')
    } finally {
      setMetadataApplying(false)
    }
  }

  const selectedTrackCount = selectedTrackIds.size

  const currentVisibleTrackIds = () => {
    if (activeView === 'playlist' && selectedPlaylist?.tracks) {
      return selectedPlaylist.tracks.map(pt => pt.track?.id).filter(Boolean)
    }
    return getFilteredTracks().map(track => track.id).filter(Boolean)
  }

  const isTrackSelected = (trackId) => selectedTrackIds.has(trackId)

  const toggleTrackSelected = (trackId) => {
    setSelectedTrackIds(prev => {
      const next = new Set(prev)
      if (next.has(trackId)) {
        next.delete(trackId)
      } else {
        next.add(trackId)
      }
      return next
    })
  }

  const clearSelection = () => {
    setSelectedTrackIds(new Set())
  }

  const toggleSelectAllVisible = () => {
    const visibleIds = currentVisibleTrackIds()
    const allSelected = visibleIds.length > 0 && visibleIds.every(id => selectedTrackIds.has(id))
    setSelectedTrackIds(prev => {
      const next = new Set(prev)
      if (allSelected) {
        visibleIds.forEach(id => next.delete(id))
      } else {
        visibleIds.forEach(id => next.add(id))
      }
      return next
    })
  }

  const refreshAfterBulkChange = async (playlistId = selectedPlaylist?.id) => {
    const [tracksRes, playlistsRes, statsRes] = await Promise.all([
      libraryApi.getTracks({ limit: 500 }),
      libraryApi.getPlaylists(),
      libraryApi.getStats()
    ])
    setTracks(tracksRes.data || [])
    setPlaylists(playlistsRes.data || [])
    setStats(statsRes.data)
    if (playlistId) {
      try {
        const playlistRes = await libraryApi.getPlaylist(playlistId)
        setSelectedPlaylist(playlistRes.data)
      } catch (error) {
        // Playlist may have been removed elsewhere; ignore here.
      }
    }
  }

  const handleBulkDeleteSelected = async () => {
    const ids = Array.from(selectedTrackIds)
    if (ids.length === 0) return

    const confirmed = window.confirm(
      `Delete ${ids.length} selected song${ids.length === 1 ? '' : 's'} from the Jukebox?\n\nThis removes them from all playlists and deletes the local audio files.`
    )
    if (!confirmed) return

    try {
      await libraryApi.bulkDeleteTracks(ids, true)
      ids.forEach(id => {
        if (removeTrackFromPlayback) {
          removeTrackFromPlayback(id)
        }
      })
      if (!removeTrackFromPlayback) {
        setQueue(prev => prev.filter(track => !ids.includes(track.id)))
      }
      clearSelection()
      await refreshAfterBulkChange()
    } catch (error) {
      console.error('Error bulk deleting tracks:', error)
      window.alert('Failed to delete selected songs. Check the backend logs for details.')
    }
  }

  const handleBulkRemoveFromPlaylist = async () => {
    if (!selectedPlaylist) return
    const ids = Array.from(selectedTrackIds)
    if (ids.length === 0) return

    const confirmed = window.confirm(
      `Remove ${ids.length} selected song${ids.length === 1 ? '' : 's'} from "${selectedPlaylist.name}"?\n\nThe files will stay in the Jukebox.`
    )
    if (!confirmed) return

    try {
      await libraryApi.bulkRemoveFromPlaylist(selectedPlaylist.id, ids)
      clearSelection()
      await refreshAfterBulkChange(selectedPlaylist.id)
    } catch (error) {
      console.error('Error bulk removing tracks from playlist:', error)
      window.alert('Failed to remove selected songs from this playlist.')
    }
  }

  const getFilteredTracks = () => {
    if (!searchQuery) return tracks
    const query = searchQuery.toLowerCase()
    return tracks.filter(t => 
      t.title?.toLowerCase().includes(query) ||
      t.artist?.toLowerCase().includes(query) ||
      t.album?.toLowerCase().includes(query)
    )
  }

  const filteredTracks = getFilteredTracks()

  // Handle track click - show context menu
  const handleTrackClick = (e, track, index) => {
    e.preventDefault()
    const rect = e.currentTarget.getBoundingClientRect()
    setContextMenu({
      show: true,
      x: e.clientX,
      y: e.clientY,
      track,
      index
    })
  }

  // Close context menu when clicking elsewhere
  const closeContextMenu = () => {
    setContextMenu({ show: false, x: 0, y: 0, track: null, index: -1 })
  }

  // Play now from context menu
  const handlePlayNow = () => {
    if (contextMenu.track) {
      handlePlayTrack(contextMenu.track, contextMenu.index, filteredTracks)
    }
    closeContextMenu()
  }

  // Add to queue from context menu
  const handleAddToQueue = () => {
    if (contextMenu.track) {
      addToQueue(contextMenu.track)
    }
    closeContextMenu()
  }

  // Play next from context menu
  const handlePlayNext = () => {
    if (contextMenu.track) {
      const newQueue = [...queue]
      newQueue.splice(currentIndex + 1, 0, contextMenu.track)
      setQueue(newQueue)
    }
    closeContextMenu()
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    )
  }

  return (
    <div className="fixed inset-0 flex flex-col bg-gray-950 overflow-hidden overscroll-none" onClick={closeContextMenu}>
      {/* Live streams are handled by the global UnifiedPlayerBar at the bottom. */}

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Mobile Sidebar Overlay */}
        {showSidebar && (
          <div 
            className="fixed inset-0 bg-black/50 z-40 md:hidden"
            onClick={() => setShowSidebar(false)}
          />
        )}
        
        {/* Sidebar */}
        <div className={`fixed md:relative inset-y-0 left-0 z-50 w-64 bg-gray-900 border-r border-gray-800 flex flex-col transform transition-transform duration-200 md:translate-x-0 ${
          showSidebar ? 'translate-x-0' : '-translate-x-full'
        } md:flex`}>
          {/* Logo/Title with back link */}
          <div className="p-4 border-b border-gray-800 flex items-center justify-between">
            <Link to="/" className="flex items-center gap-2 hover:opacity-80 transition-opacity">
              <img src="/logo.png" alt="ArchiveXM" className="w-8 h-8 rounded" />
              <span className="text-lg font-bold text-white">ArchiveXM</span>
            </Link>
            <button 
              onClick={() => setShowSidebar(false)}
              className="md:hidden text-gray-400 hover:text-white"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
          
          {/* Back to Channels link */}
          <div className="p-2 border-b border-gray-800">
            <Link
              to="/"
              className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-gray-400 hover:bg-gray-800 hover:text-white transition-colors"
            >
              <Radio className="w-5 h-5" />
              <span>Back to Channels</span>
            </Link>
          </div>
          
          {/* Recording Indicator */}
          {isRecording && (
            <div className={`p-3 border-b border-gray-800 ${recordingData?.stopping ? 'bg-yellow-900/20' : 'bg-red-900/20'}`}>
              <div className="flex items-center gap-2 mb-2">
                <Circle className={`w-3 h-3 ${recordingData?.stopping ? 'text-yellow-500 fill-yellow-500' : 'text-red-500 fill-red-500 animate-pulse'}`} />
                <span className={`text-sm font-medium ${recordingData?.stopping ? 'text-yellow-400' : 'text-red-400'}`}>
                  {recordingData?.stopping ? 'Stopping...' : 'Recording'}
                </span>
              </div>
              {/* Stopping countdown */}
              {recordingData?.stopping && recordingData?.stoppingInSeconds != null && (
                <div className="text-sm text-yellow-400 mb-2">
                  Finishing in {Math.ceil(recordingData.stoppingInSeconds)}s
                </div>
              )}
              {/* Current track - show when not stopping */}
              {recordingData?.currentTrack && !recordingData?.stopping && (
                <div className="text-sm text-gray-300 truncate mb-2">
                  {recordingData.currentTrack.artist} - {recordingData.currentTrack.title}
                </div>
              )}
              <div className="flex gap-2">
                <button
                  onClick={() => navigate(`/channel/${recordingData?.channelId}`)}
                  className="flex-1 px-2 py-1.5 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded transition-colors"
                >
                  View Channel
                </button>
                {!recordingData?.stopping && (
                  <button
                    onClick={() => stopRecording(true)}
                    className="flex-1 px-2 py-1.5 text-xs bg-red-600 hover:bg-red-500 text-white rounded transition-colors flex items-center justify-center gap-1"
                  >
                    <Square className="w-3 h-3" />
                    Stop
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Navigation */}
          <nav className="p-2 space-y-1">
            <button
              onClick={() => { setActiveView('tracks'); setSearchQuery('') }}
              className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors ${
                activeView === 'tracks' ? 'bg-primary text-white' : 'text-gray-400 hover:bg-gray-800 hover:text-white'
              }`}
            >
              <Music className="w-5 h-5" />
              <span>All Tracks</span>
            </button>
          </nav>
          
          {/* Now Playing Mini */}
          {currentTrack && (
            <div className="p-3 border-t border-gray-800">
              <div className="text-xs text-gray-500 mb-2">NOW PLAYING</div>
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 bg-gray-800 rounded flex items-center justify-center flex-shrink-0 overflow-hidden">
                  {currentTrack.cover_art_path ? (
                    <img 
                      src={libraryApi.getCoverUrl(currentTrack.id)} 
                      alt="" 
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <Music className="w-6 h-6 text-gray-600" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-white text-sm font-medium truncate">{currentTrack.title || currentTrack.filename}</div>
                  <div className="text-gray-500 text-xs truncate">{currentTrack.artist || 'Unknown'}</div>
                </div>
              </div>
              {queue.length > 1 && (
                <div className="mt-2 text-xs text-gray-500">
                  {currentIndex + 1} of {queue.length} in queue
                </div>
              )}
            </div>
          )}

          {/* Playlists */}
          <div className="flex-1 overflow-y-auto p-2">
            <div className="flex items-center justify-between px-3 py-2">
              <span className="text-sm font-medium text-gray-400">Playlists</span>
              <button
                onClick={() => setShowPlaylistModal(true)}
                className="text-gray-400 hover:text-white"
              >
                <Plus className="w-4 h-4" />
              </button>
            </div>
            <div className="space-y-1">
              {playlists.map(playlist => (
                <button
                  key={playlist.id}
                  onClick={() => loadPlaylist(playlist)}
                  className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left transition-colors ${
                    selectedPlaylist?.id === playlist.id ? 'bg-gray-800 text-white' : 'text-gray-400 hover:bg-gray-800 hover:text-white'
                  }`}
                >
                  <List className="w-4 h-4" />
                  <span className="truncate">{playlist.name}</span>
                  <span className="text-xs text-gray-500 ml-auto">{playlist.track_count}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Stats */}
          {stats && (
            <div className="p-4 border-t border-gray-800 text-xs text-gray-500">
              <div>{stats.total_tracks} tracks</div>
              <div>{stats.unique_artists} artists</div>
            </div>
          )}
        </div>

        {/* Main Area */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Header */}
          <div className="p-3 sm:p-4 border-b border-gray-800">
            <div className="flex items-center gap-2 sm:gap-4">
              {/* Mobile menu button */}
              <button
                onClick={() => setShowSidebar(true)}
                className="md:hidden p-2 text-gray-400 hover:text-white hover:bg-gray-800 rounded-lg"
              >
                <List className="w-5 h-5" />
              </button>
              
              <div className="flex-1 relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 sm:w-5 h-4 sm:h-5 text-gray-500" />
                <input
                  type="text"
                  placeholder="Search..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-9 sm:pl-10 pr-3 sm:pr-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white text-sm placeholder-gray-500 focus:outline-none focus:border-primary"
                />
              </div>
              
              {/* Desktop buttons */}
              <button
                onClick={scanLibrary}
                disabled={scanning}
                className="hidden sm:flex items-center gap-2 px-3 sm:px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-lg transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`w-4 h-4 ${scanning ? 'animate-spin' : ''}`} />
                <span className="hidden md:inline">{scanning ? 'Scanning...' : 'Scan'}</span>
              </button>
              <button
                onClick={copyDownloadsM3u}
                className="hidden sm:flex items-center gap-2 px-3 sm:px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-lg transition-colors"
                title="Copy all downloads M3U link"
              >
                <Copy className="w-4 h-4" />
                <span className="hidden md:inline">Copy M3U</span>
              </button>
              <button
                onClick={handlePlayAll}
                className="hidden xs:flex items-center gap-1 sm:gap-2 px-2 sm:px-4 py-2 bg-primary hover:bg-primary/80 text-white rounded-lg transition-colors"
              >
                <Play className="w-4 h-4" />
                <span className="hidden sm:inline">Play All</span>
              </button>
              <button
                onClick={handleShuffleAll}
                className="hidden xs:flex items-center gap-1 sm:gap-2 px-2 sm:px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-lg transition-colors"
              >
                <Shuffle className="w-4 h-4" />
                <span className="hidden sm:inline">Shuffle</span>
              </button>
              
              {/* Mobile action buttons */}
              <div className="flex xs:hidden items-center gap-1">
                <button
                  onClick={handlePlayAll}
                  className="p-2 bg-primary hover:bg-primary/80 text-white rounded-lg"
                >
                  <Play className="w-4 h-4" />
                </button>
                <button
                  onClick={handleShuffleAll}
                  className="p-2 bg-gray-800 hover:bg-gray-700 text-white rounded-lg"
                >
                  <Shuffle className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>

          {/* Bulk Actions */}
          {selectedTrackCount > 0 && (
            <div className="px-3 sm:px-4 py-3 border-b border-gray-800 bg-primary/10 flex flex-wrap items-center gap-2">
              <div className="flex items-center gap-2 text-white font-medium mr-2">
                <CheckSquare className="w-5 h-5 text-primary" />
                <span>{selectedTrackCount} selected</span>
              </div>
              {activeView === 'playlist' && selectedPlaylist && (
                <button
                  onClick={handleBulkRemoveFromPlaylist}
                  className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-white rounded-lg text-sm transition-colors"
                >
                  Remove from playlist
                </button>
              )}
              <button
                onClick={handleBulkDeleteSelected}
                className="px-3 py-1.5 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm transition-colors"
              >
                Delete songs
              </button>
              <button
                onClick={clearSelection}
                className="px-3 py-1.5 text-gray-400 hover:text-white rounded-lg text-sm transition-colors"
              >
                Clear
              </button>
            </div>
          )}

          {/* Track List */}
          <div className="flex-1 overflow-y-auto">
            {activeView === 'tracks' && (
              <table className="w-full">
                <thead className="sticky top-0 bg-gray-900 text-left text-sm text-gray-400 border-b border-gray-800">
                  <tr>
                    <th className="px-2 sm:pl-4 sm:pr-2 py-2 sm:py-3 w-10">
                      <input
                        type="checkbox"
                        checked={currentVisibleTrackIds().length > 0 && currentVisibleTrackIds().every(id => selectedTrackIds.has(id))}
                        onChange={toggleSelectAllVisible}
                        className="w-4 h-4 rounded bg-gray-800 border-gray-600"
                        title="Select all visible"
                      />
                    </th>
                    <th className="px-2 sm:px-4 py-2 sm:py-3 w-10 sm:w-12">#</th>
                    <th className="px-2 sm:px-4 py-2 sm:py-3">Title</th>
                    <th className="hidden md:table-cell px-4 py-3">Artist</th>
                    <th className="hidden lg:table-cell px-4 py-3">Album</th>
                    <th className="hidden sm:table-cell px-4 py-3 w-16 sm:w-20">
                      <Clock className="w-4 h-4" />
                    </th>
                    <th className="px-2 sm:px-4 py-2 sm:py-3 w-12 sm:w-20"></th>
                  </tr>
                </thead>
                <tbody>
                  {filteredTracks.map((track, index) => (
                    <tr
                      key={track.id}
                      className={`hover:bg-gray-800/50 transition-colors cursor-pointer ${
                        currentTrack?.id === track.id ? 'bg-primary/20' : ''
                      }`}
                      onClick={(e) => handleTrackClick(e, track, index)}
                      onDoubleClick={() => handlePlayTrack(track, index, filteredTracks)}
                    >
                      <td className="px-2 sm:pl-4 sm:pr-2 py-2 sm:py-3">
                        <input
                          type="checkbox"
                          checked={isTrackSelected(track.id)}
                          onClick={(e) => e.stopPropagation()}
                          onChange={() => toggleTrackSelected(track.id)}
                          className="w-4 h-4 rounded bg-gray-800 border-gray-600"
                          title="Select song"
                        />
                      </td>
                      <td className="px-2 sm:px-4 py-2 sm:py-3 text-gray-500">
                        {currentTrack?.id === track.id && isPlaying ? (
                          <div className="flex items-center gap-0.5">
                            <span className="w-1 h-3 bg-primary rounded-full animate-pulse"></span>
                            <span className="w-1 h-4 bg-primary rounded-full animate-pulse delay-75"></span>
                            <span className="w-1 h-2 bg-primary rounded-full animate-pulse delay-150"></span>
                          </div>
                        ) : (
                          <button
                            onClick={(e) => { e.stopPropagation(); handlePlayTrack(track, index, filteredTracks) }}
                            className="w-6 h-6 flex items-center justify-center text-gray-500 hover:text-white hover:bg-gray-700 rounded transition-colors"
                          >
                            <Play className="w-3 h-3" />
                          </button>
                        )}
                      </td>
                      <td className="px-2 sm:px-4 py-2 sm:py-3">
                        <div className="flex items-center gap-2 sm:gap-3">
                          <div className="w-8 h-8 sm:w-10 sm:h-10 bg-gray-800 rounded flex items-center justify-center flex-shrink-0 overflow-hidden">
                            {track.cover_art_path ? (
                              <img 
                                src={libraryApi.getCoverUrl(track.id)} 
                                alt="" 
                                className="w-full h-full object-cover"
                                onError={(e) => { e.target.style.display = 'none'; e.target.nextSibling.style.display = 'flex' }}
                              />
                            ) : null}
                            <Music className={`w-4 sm:w-5 h-4 sm:h-5 text-gray-600 ${track.cover_art_path ? 'hidden' : ''}`} />
                          </div>
                          <div className="min-w-0 flex-1">
                            <span className="text-white text-sm sm:text-base font-medium truncate block">
                              {track.title || track.filename}
                            </span>
                            {/* Show artist on mobile under title */}
                            <span className="md:hidden text-gray-400 text-xs truncate block">
                              {track.artist || 'Unknown'}
                            </span>
                          </div>
                        </div>
                      </td>
                      <td className="hidden md:table-cell px-4 py-3 text-gray-400 truncate">{track.artist || 'Unknown'}</td>
                      <td className="hidden lg:table-cell px-4 py-3 text-gray-400 truncate">{track.album || '-'}</td>
                      <td className="hidden sm:table-cell px-4 py-3 text-gray-500 text-sm">{formatTime(track.duration_seconds)}</td>
                      <td className="px-2 sm:px-4 py-2 sm:py-3">
                        <div className="flex items-center gap-1 sm:gap-2">
                          <button
                            onClick={(e) => { e.stopPropagation(); addToQueue(track) }}
                            className="p-1 text-gray-500 hover:text-white"
                            title="Add to queue"
                          >
                            <Plus className="w-4 h-4" />
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); openMetadataLookup(track) }}
                            className="hidden sm:block p-1 text-gray-500 hover:text-primary"
                            title="Find missing metadata"
                          >
                            <Search className="w-4 h-4" />
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); setTrackToAdd(track) }}
                            className="hidden sm:block p-1 text-gray-500 hover:text-white"
                            title="Add to playlist"
                          >
                            <ListPlus className="w-4 h-4" />
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); deleteTrackEverywhere(track) }}
                            className="hidden sm:block p-1 text-gray-500 hover:text-red-400"
                            title="Delete song from Jukebox"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {activeView === 'playlist' && selectedPlaylist && (
              <div className="p-4">
                <div className="flex items-center gap-4 mb-6">
                  <button
                    onClick={() => openCoverEditor(selectedPlaylist)}
                    className="relative group w-32 h-32 bg-gradient-to-br from-primary to-purple-600 rounded-lg flex items-center justify-center overflow-hidden hover:ring-2 hover:ring-primary transition-all"
                    title="Edit playlist cover"
                  >
                    <img
                      src={playlistCoverSrc(selectedPlaylist)}
                      alt=""
                      className="w-full h-full object-cover"
                      onError={(e) => { e.currentTarget.style.display = 'none' }}
                    />
                    <div className="absolute inset-0 bg-black/0 group-hover:bg-black/50 flex items-center justify-center transition-colors">
                      <div className="opacity-0 group-hover:opacity-100 flex flex-col items-center gap-1 text-white text-xs font-medium">
                        <Image className="w-6 h-6" />
                        <span>Edit Cover</span>
                      </div>
                    </div>
                  </button>
                  <div className="flex-1">
                    <h2 className="text-2xl font-bold text-white">{selectedPlaylist.name}</h2>
                    <p className="text-gray-400">{selectedPlaylist.track_count} tracks</p>
                    <div className="mt-2 flex items-center gap-2">
                      <button
                        onClick={() => playPlaylist(selectedPlaylist)}
                        className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary/80 text-white rounded-full transition-colors"
                      >
                        <Play className="w-4 h-4" />
                        <span>Play</span>
                      </button>
                      <button
                        onClick={() => playPlaylist(selectedPlaylist, true)}
                        className="flex items-center gap-2 px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-full transition-colors"
                      >
                        <Shuffle className="w-4 h-4" />
                        <span>Shuffle</span>
                      </button>
                      <button
                        onClick={() => copyPlaylistM3u(selectedPlaylist.id, selectedPlaylist.name)}
                        className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-full transition-colors"
                        title="Copy playlist M3U link"
                      >
                        <Copy className="w-4 h-4" />
                        <span>M3U</span>
                      </button>
                      <button
                        onClick={() => {
                          setEditingPlaylist(selectedPlaylist)
                          setEditPlaylistName(selectedPlaylist.name)
                        }}
                        className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-full transition-colors"
                        title="Rename playlist"
                      >
                        <MoreVertical className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => setShowDeleteConfirm(selectedPlaylist.id)}
                        className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-red-600 text-white rounded-full transition-colors"
                        title="Delete playlist"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </div>
                
                {selectedPlaylist.tracks?.length === 0 ? (
                  <div className="text-center py-12 text-gray-500">
                    <List className="w-12 h-12 mx-auto mb-3 opacity-50" />
                    <p>This playlist is empty</p>
                    <p className="text-sm">Add tracks from All Tracks view</p>
                  </div>
                ) : (
                  <table className="w-full">
                    <thead className="text-left text-sm text-gray-400 border-b border-gray-800">
                      <tr>
                        <th className="px-4 py-3 w-10">
                          <input
                            type="checkbox"
                            checked={currentVisibleTrackIds().length > 0 && currentVisibleTrackIds().every(id => selectedTrackIds.has(id))}
                            onChange={toggleSelectAllVisible}
                            className="w-4 h-4 rounded bg-gray-800 border-gray-600"
                            title="Select all playlist tracks"
                          />
                        </th>
                        <th className="px-4 py-3 w-12">#</th>
                        <th className="px-4 py-3">Title</th>
                        <th className="px-4 py-3">Artist</th>
                        <th className="px-4 py-3 w-20">
                          <Clock className="w-4 h-4" />
                        </th>
                        <th className="px-4 py-3 w-24"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedPlaylist.tracks?.map((pt, index) => (
                        <tr
                          key={pt.track.id}
                          className="hover:bg-gray-800/50 transition-colors cursor-pointer group"
                          onDoubleClick={() => {
                            const playlistTracks = selectedPlaylist.tracks.map(p => p.track)
                            handlePlayTrack(pt.track, index, playlistTracks)
                          }}
                        >
                          <td className="px-4 py-3">
                            <input
                              type="checkbox"
                              checked={isTrackSelected(pt.track.id)}
                              onClick={(e) => e.stopPropagation()}
                              onChange={() => toggleTrackSelected(pt.track.id)}
                              className="w-4 h-4 rounded bg-gray-800 border-gray-600"
                              title="Select song"
                            />
                          </td>
                          <td className="px-4 py-3 text-gray-500">{index + 1}</td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-3">
                              <div className="w-10 h-10 bg-gray-800 rounded flex items-center justify-center flex-shrink-0 overflow-hidden">
                                {pt.track.cover_art_path ? (
                                  <img 
                                    src={libraryApi.getCoverUrl(pt.track.id)} 
                                    alt="" 
                                    className="w-full h-full object-cover"
                                  />
                                ) : (
                                  <Music className="w-5 h-5 text-gray-600" />
                                )}
                              </div>
                              <span className="text-white truncate">{pt.track.title || pt.track.filename}</span>
                            </div>
                          </td>
                          <td className="px-4 py-3 text-gray-400">{pt.track.artist || 'Unknown'}</td>
                          <td className="px-4 py-3 text-gray-500">{formatTime(pt.track.duration_seconds)}</td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-all">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation()
                                  removeTrackFromPlaylist(pt.track.id)
                                }}
                                className="text-gray-500 hover:text-white transition-colors"
                                title="Remove from this playlist"
                              >
                                <X className="w-4 h-4" />
                              </button>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation()
                                  deleteTrackEverywhere(pt.track)
                                }}
                                className="text-gray-500 hover:text-red-400 transition-colors"
                                title="Delete song from Jukebox"
                              >
                                <Trash2 className="w-4 h-4" />
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Queue Panel - Full overlay on mobile, sidebar on desktop */}
        {showQueue && (
          <>
            {/* Mobile overlay background */}
            <div 
              className="fixed inset-0 bg-black/50 z-40 sm:hidden"
              onClick={() => setShowQueue(false)}
            />
            <div className="fixed inset-x-0 bottom-0 top-20 z-50 sm:relative sm:inset-auto sm:w-80 bg-gray-900 border-l border-gray-800 flex flex-col rounded-t-xl sm:rounded-none">
            <div className="p-4 border-b border-gray-800">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-medium text-white">Queue</h3>
                <button
                  onClick={() => setShowQueue(false)}
                  className="text-gray-400 hover:text-white"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
              {queue.length > 0 && (
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => playQueue(0)}
                    disabled={queue.length === 0}
                    className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-primary hover:bg-primary/80 text-white rounded-lg text-sm transition-colors disabled:opacity-50"
                  >
                    <Play className="w-4 h-4" />
                    Play Queue
                  </button>
                  <button
                    onClick={() => clearQueue(isPlaying)}
                    className="px-3 py-2 text-gray-400 hover:text-white hover:bg-gray-800 rounded-lg text-sm transition-colors"
                    title={isPlaying ? "Clear except current" : "Clear all"}
                  >
                    Clear
                  </button>
                </div>
              )}
            </div>
            <div className="flex-1 overflow-y-auto">
              {queue.length === 0 ? (
                <div className="p-4 text-center text-gray-500">
                  <List className="w-8 h-8 mx-auto mb-2 opacity-50" />
                  <p className="text-sm">Queue is empty</p>
                  <p className="text-xs mt-1">Click + on tracks to add them</p>
                </div>
              ) : (
                queue.map((track, index) => (
                  <div
                    key={`${track.id}-${index}`}
                    onClick={() => playQueue(index)}
                    className={`flex items-center gap-3 p-3 hover:bg-gray-800 cursor-pointer group ${
                      index === currentIndex ? 'bg-primary/20' : ''
                    }`}
                  >
                    <span className="text-gray-500 text-sm w-6 group-hover:hidden">{index + 1}</span>
                    <Play className="w-4 h-4 text-white hidden group-hover:block" />
                    <div className="flex-1 min-w-0">
                      <p className={`text-sm truncate ${index === currentIndex ? 'text-primary font-medium' : 'text-white'}`}>
                        {track.title || track.filename}
                      </p>
                      <p className="text-gray-500 text-xs truncate">{track.artist || 'Unknown'}</p>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); removeFromQueue(index) }}
                      className="text-gray-500 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                ))
              )}
            </div>
            {queue.length > 0 && (
              <div className="p-3 border-t border-gray-800 text-xs text-gray-500">
                {queue.length} track{queue.length !== 1 ? 's' : ''} in queue
              </div>
            )}
            </div>
          </>
        )}
      </div>

      {/* Live Player Bar - shown inside Jukebox when a live stream is active */}
      {livePlayer.currentChannel && (
        <div className="bg-gray-900 border-t border-gray-700 px-6 py-4 min-h-[104px] flex-shrink-0">
          <div className="flex items-center justify-between gap-6">
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
                <>
                  <button
                    onClick={livePlayer.skipPreviousXtra}
                    disabled={livePlayer.isLoading || livePlayer.isSkippingPrevious || !livePlayer.hasXtraPrevious}
                    className="w-14 h-14 rounded-full bg-gray-800 text-white flex items-center justify-center hover:bg-gray-700 transition-colors disabled:opacity-40"
                    title={livePlayer.hasXtraPrevious ? 'Previous XTRA track' : 'No previous XTRA track available'}
                  >
                    {livePlayer.isSkippingPrevious ? (
                      <Loader2 className="w-7 h-7 animate-spin" />
                    ) : (
                      <SkipBack className="w-7 h-7" />
                    )}
                  </button>

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
                </>
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

      {/* Player Bar - only show Jukebox controls when a live stream is not active */}
      {!livePlayer.currentChannel && (
      <div className="bg-gray-900 border-t border-gray-800 px-6 py-4 min-h-[104px] flex-shrink-0">
        {/* Mobile layout */}
        <div className="sm:hidden">
          <div className="flex items-center gap-3 mb-3">
            {currentTrack ? (
              <>
                <div className="w-14 h-14 bg-gray-800 rounded-xl flex items-center justify-center flex-shrink-0 overflow-hidden">
                  {currentTrack.cover_art_path ? (
                    <img src={libraryApi.getCoverUrl(currentTrack.id)} alt="" className="w-full h-full object-cover" />
                  ) : (
                    <Music className="w-7 h-7 text-gray-600" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-white text-base font-semibold truncate">{currentTrack.title || currentTrack.filename}</p>
                  <p className="text-gray-300 text-sm truncate">{currentTrack.artist || 'Unknown'}</p>
                </div>
                <button
                  onClick={() => setTrackToAdd(currentTrack)}
                  className="p-2 text-gray-400 hover:text-white"
                  title="Add to playlist"
                >
                  <ListPlus className="w-5 h-5" />
                </button>
                <button
                  onClick={() => deleteTrackEverywhere(currentTrack)}
                  className="p-2 text-gray-400 hover:text-red-400"
                  title="Delete song from Jukebox"
                >
                  <Trash2 className="w-5 h-5" />
                </button>
              </>
            ) : (
              <p className="text-gray-500 text-sm">No track playing</p>
            )}
          </div>

          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs text-gray-500 w-10 text-right">{formatTime(currentTime)}</span>
            <input
              type="range"
              min={0}
              max={duration || 100}
              step="0.1"
              value={currentTime || 0}
              onInput={handleSeek}
              onChange={handleSeek}
              className="flex-1 h-2 bg-gray-700 rounded-full appearance-none cursor-pointer
                [&::-webkit-slider-thumb]:appearance-none
                [&::-webkit-slider-thumb]:w-4
                [&::-webkit-slider-thumb]:h-4
                [&::-webkit-slider-thumb]:rounded-full
                [&::-webkit-slider-thumb]:bg-white"
            />
            <span className="text-xs text-gray-500 w-10">{formatTime(duration)}</span>
          </div>

          <div className="flex items-center justify-center gap-3">
            <button onClick={() => setShuffle(!shuffle)} className={`p-2 ${shuffle ? 'text-primary' : 'text-gray-400 hover:text-white'}`} title="Shuffle">
              <Shuffle className="w-5 h-5" />
            </button>
            <button onClick={playPrevious} className="p-2 text-gray-400 hover:text-white" title="Previous">
              <SkipBack className="w-5 h-5" />
            </button>
            <button
              onClick={togglePlay}
              disabled={!currentTrack}
              className="w-12 h-12 bg-white text-black rounded-full flex items-center justify-center disabled:opacity-50"
              title={isPlaying ? 'Pause' : 'Play'}
            >
              {isPlaying ? <Pause className="w-6 h-6" /> : <Play className="w-6 h-6 ml-0.5" />}
            </button>
            <button onClick={playNext} className="p-2 text-gray-400 hover:text-white" title="Next">
              <SkipForward className="w-5 h-5" />
            </button>
            <button
              onClick={() => setShowQueue(!showQueue)}
              className={`p-2 ${showQueue ? 'text-primary' : 'text-gray-400 hover:text-white'}`}
              title="Queue"
            >
              <List className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Desktop layout */}
        <div className="hidden sm:flex items-center justify-between gap-6">
          {/* Track Info */}
          <div className="flex items-center gap-4 flex-1 min-w-0">
            {currentTrack ? (
              <>
                <div className="w-20 h-20 bg-gray-800 rounded-xl flex items-center justify-center flex-shrink-0 overflow-hidden">
                  {currentTrack.cover_art_path ? (
                    <img
                      src={libraryApi.getCoverUrl(currentTrack.id)}
                      alt=""
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <Music className="w-8 h-8 text-gray-600" />
                  )}
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <div className="flex items-center gap-2 px-3 py-1 bg-primary/20 rounded-full">
                      <Disc3 className={`w-3.5 h-3.5 text-primary ${isPlaying ? 'animate-spin' : ''}`} style={{ animationDuration: '3s' }} />
                      <span className="text-xs text-primary font-semibold tracking-wide">Jukebox</span>
                    </div>
                  </div>
                  <p className="text-white text-xl font-semibold truncate leading-tight">{currentTrack.title || currentTrack.filename}</p>
                  <p className="text-gray-300 text-base truncate">{currentTrack.artist || 'Unknown'}</p>
                </div>
              </>
            ) : (
              <p className="text-gray-500 text-lg">No track playing</p>
            )}
          </div>

          {/* Center Controls */}
          <div className="flex flex-col items-center gap-2 flex-[1.2] min-w-[360px]">
            <div className="flex items-center gap-4">
              <button
                onClick={() => setShuffle(!shuffle)}
                className={`transition-colors ${shuffle ? 'text-primary' : 'text-gray-400 hover:text-white'}`}
                title="Shuffle"
              >
                <Shuffle className="w-6 h-6" />
              </button>
              <button onClick={playPrevious} className="text-gray-400 hover:text-white" title="Previous">
                <SkipBack className="w-6 h-6" />
              </button>
              <button
                onClick={togglePlay}
                disabled={!currentTrack}
                className="w-14 h-14 bg-white text-black rounded-full flex items-center justify-center hover:scale-105 transition-transform disabled:opacity-50"
                title={isPlaying ? 'Pause' : 'Play'}
              >
                {isPlaying ? <Pause className="w-7 h-7" /> : <Play className="w-7 h-7 ml-1" />}
              </button>
              <button onClick={playNext} className="text-gray-400 hover:text-white" title="Next">
                <SkipForward className="w-6 h-6" />
              </button>
              <button
                onClick={() => setRepeat(repeat === 'none' ? 'all' : repeat === 'all' ? 'one' : 'none')}
                className={`transition-colors relative ${repeat !== 'none' ? 'text-primary' : 'text-gray-400 hover:text-white'}`}
                title="Repeat"
              >
                <Repeat className="w-6 h-6" />
                {repeat === 'one' && <span className="absolute -top-1 -right-2 text-xs font-bold">1</span>}
              </button>
            </div>

            <div className="flex items-center gap-3 w-full max-w-xl">
              <span className="text-xs text-gray-500 w-10 text-right">{formatTime(currentTime)}</span>
              <input
                type="range"
                min={0}
                max={duration || 100}
                step="0.1"
                value={currentTime || 0}
                onInput={handleSeek}
                onChange={handleSeek}
                className="flex-1 h-2 bg-gray-700 rounded-full appearance-none cursor-pointer
                  [&::-webkit-slider-thumb]:appearance-none
                  [&::-webkit-slider-thumb]:w-4
                  [&::-webkit-slider-thumb]:h-4
                  [&::-webkit-slider-thumb]:rounded-full
                  [&::-webkit-slider-thumb]:bg-white
                  [&::-webkit-slider-thumb]:cursor-pointer"
              />
              <span className="text-xs text-gray-500 w-10">{formatTime(duration)}</span>
            </div>
          </div>

          {/* Right Controls */}
          <div className="flex items-center gap-4 flex-1 justify-end">
            {currentTrack && (
              <>
                <button
                  onClick={() => setTrackToAdd(currentTrack)}
                  className="text-gray-400 hover:text-white transition-colors"
                  title="Add to playlist"
                >
                  <ListPlus className="w-6 h-6" />
                </button>
                <button
                  onClick={() => openMetadataLookup(currentTrack)}
                  className="text-gray-400 hover:text-primary transition-colors"
                  title="Find metadata"
                >
                  <Search className="w-6 h-6" />
                </button>
                <button
                  onClick={() => deleteTrackEverywhere(currentTrack)}
                  className="text-gray-400 hover:text-red-400 transition-colors"
                  title="Delete song from Jukebox"
                >
                  <Trash2 className="w-6 h-6" />
                </button>
              </>
            )}
            <button
              onClick={() => setShowQueue(!showQueue)}
              className={`transition-colors ${showQueue ? 'text-primary' : 'text-gray-400 hover:text-white'}`}
              title="Queue"
            >
              <List className="w-6 h-6" />
            </button>
            <button
              onClick={() => setIsMuted(!isMuted)}
              className="text-gray-400 hover:text-white"
              title={isMuted ? 'Unmute' : 'Mute'}
            >
              {isMuted || volume === 0 ? <VolumeX className="w-6 h-6" /> : <Volume2 className="w-6 h-6" />}
            </button>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={isMuted ? 0 : volume}
              onChange={(e) => setVolume(parseFloat(e.target.value))}
              className="w-32 h-2 bg-gray-700 rounded-full appearance-none cursor-pointer
                [&::-webkit-slider-thumb]:appearance-none
                [&::-webkit-slider-thumb]:w-4
                [&::-webkit-slider-thumb]:h-4
                [&::-webkit-slider-thumb]:rounded-full
                [&::-webkit-slider-thumb]:bg-white
                [&::-webkit-slider-thumb]:cursor-pointer"
            />
          </div>
        </div>
      </div>
      )}

      {/* Create Playlist Modal */}
      {showPlaylistModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-900 rounded-xl p-6 w-96">
            <h3 className="text-xl font-bold text-white mb-4">Create Playlist</h3>
            <input
              type="text"
              placeholder="Playlist name"
              value={newPlaylistName}
              onChange={(e) => setNewPlaylistName(e.target.value)}
              className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white mb-4 focus:outline-none focus:border-primary"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowPlaylistModal(false)}
                className="px-4 py-2 text-gray-400 hover:text-white"
              >
                Cancel
              </button>
              <button
                onClick={createPlaylist}
                className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/80"
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Add to Playlist Modal */}
      {trackToAdd && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-md shadow-2xl">
            <div className="flex items-start justify-between gap-4 mb-4">
              <div>
                <h3 className="text-xl font-bold text-white">Add to Playlist</h3>
                <p className="text-gray-400 text-sm mt-1 truncate">
                  {trackToAdd.artist || 'Unknown'} - {trackToAdd.title || trackToAdd.filename}
                </p>
              </div>
              <button
                onClick={() => { setTrackToAdd(null); setNewPlaylistName('') }}
                className="text-gray-400 hover:text-white"
                title="Close"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-2 max-h-64 overflow-y-auto mb-4">
              {playlists.length === 0 ? (
                <p className="text-gray-500 text-sm py-2">No playlists yet. Create one below.</p>
              ) : (
                playlists.map(playlist => (
                  <button
                    key={playlist.id}
                    onClick={() => addTrackToPlaylist(playlist.id)}
                    className="w-full flex items-center gap-3 px-4 py-3 bg-gray-800 hover:bg-gray-700 rounded-lg text-left"
                  >
                    <List className="w-5 h-5 text-gray-500" />
                    <div className="min-w-0">
                      <div className="text-white truncate">{playlist.name}</div>
                      <div className="text-xs text-gray-500">{playlist.track_count || 0} tracks</div>
                    </div>
                  </button>
                ))
              )}
            </div>

            <div className="border-t border-gray-800 pt-4">
              <label className="block text-sm text-gray-400 mb-2">Create new playlist</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="Playlist name"
                  value={newPlaylistName}
                  onChange={(e) => setNewPlaylistName(e.target.value)}
                  className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-primary"
                />
                <button
                  onClick={createPlaylist}
                  disabled={!newPlaylistName.trim()}
                  className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/80 disabled:opacity-50"
                >
                  Create
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Metadata Lookup Modal */}
      {metadataTarget && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-2xl shadow-2xl">
            <div className="flex items-start justify-between gap-4 mb-4">
              <div>
                <h3 className="text-xl font-bold text-white">Find Metadata</h3>
                <p className="text-gray-400 text-sm mt-1 truncate">
                  {metadataTarget.artist || 'Unknown'} - {metadataTarget.title || metadataTarget.filename}
                </p>
              </div>
              <button
                onClick={closeMetadataLookup}
                className="text-gray-400 hover:text-white"
                title="Close"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {metadataLoading ? (
              <div className="flex items-center justify-center gap-3 py-10 text-gray-300">
                <Loader2 className="w-6 h-6 animate-spin text-primary" />
                Searching MusicBrainz...
              </div>
            ) : (
              <>
                {metadataError && (
                  <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
                    {metadataError}
                  </div>
                )}

                <div className="space-y-3 max-h-96 overflow-y-auto">
                  {metadataCandidates.map((candidate, index) => (
                    <div
                      key={`${candidate.release_id || candidate.recording_id || index}-${index}`}
                      className="flex items-center gap-4 rounded-lg bg-gray-800/80 border border-gray-700 p-3"
                    >
                      <div className="w-16 h-16 rounded bg-gray-950 flex items-center justify-center overflow-hidden shrink-0">
                        {candidate.cover_url ? (
                          <img
                            src={candidate.cover_url}
                            alt=""
                            className="w-full h-full object-cover"
                            onError={(e) => { e.currentTarget.style.display = 'none' }}
                          />
                        ) : (
                          <Album className="w-7 h-7 text-gray-600" />
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-white font-medium truncate">{candidate.album || candidate.title || 'Unknown release'}</p>
                        <p className="text-gray-300 text-sm truncate">{candidate.artist || 'Unknown'} - {candidate.title || metadataTarget.title}</p>
                        <p className="text-gray-500 text-xs truncate">
                          {candidate.year || 'No year'} • {candidate.release_type || 'Release'} • Confidence {Math.round((candidate.confidence || 0) * 100)}%
                        </p>
                      </div>
                      <button
                        onClick={() => applyMetadataCandidate(candidate)}
                        disabled={metadataApplying}
                        className="px-4 py-2 bg-primary hover:bg-primary/80 text-white rounded-lg disabled:opacity-50 flex items-center gap-2"
                      >
                        {metadataApplying ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                        Apply
                      </button>
                    </div>
                  ))}

                  {!metadataCandidates.length && !metadataError && (
                    <div className="py-10 text-center text-gray-500">
                      <Search className="w-10 h-10 mx-auto mb-3 opacity-60" />
                      <p>No candidates found.</p>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Edit Playlist Modal */}
      {editingPlaylist && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-900 rounded-xl p-6 w-96">
            <h3 className="text-xl font-bold text-white mb-4">Edit Playlist</h3>
            <input
              type="text"
              placeholder="Playlist name"
              value={editPlaylistName}
              onChange={(e) => setEditPlaylistName(e.target.value)}
              className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white mb-4 focus:outline-none focus:border-primary"
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => { setEditingPlaylist(null); setEditPlaylistName('') }}
                className="px-4 py-2 text-gray-400 hover:text-white"
              >
                Cancel
              </button>
              <button
                onClick={updatePlaylist}
                className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/80"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Playlist Cover Modal */}
      {coverEditingPlaylist && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-full max-w-lg shadow-2xl">
            <div className="flex items-start justify-between gap-4 mb-5">
              <div>
                <h3 className="text-xl font-bold text-white">Edit Playlist Cover</h3>
                <p className="text-gray-400 text-sm mt-1 truncate">{coverEditingPlaylist.name}</p>
              </div>
              <button
                onClick={() => { setCoverEditingPlaylist(null); setCoverUrlInput(''); setCoverUploadFile(null) }}
                className="text-gray-400 hover:text-white"
                title="Close"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex items-center gap-4 mb-5">
              <div className="w-24 h-24 bg-gray-800 rounded-lg overflow-hidden flex items-center justify-center flex-shrink-0">
                <img
                  src={playlistCoverSrc(coverEditingPlaylist)}
                  alt=""
                  className="w-full h-full object-cover"
                  onError={(e) => { e.currentTarget.style.display = 'none' }}
                />
              </div>
              <div className="text-sm text-gray-400">
                Use a public image URL, upload a local image, or clear the custom cover to fall back to the first track album art.
              </div>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-sm text-gray-400 mb-2">Custom image URL</label>
                <div className="flex gap-2">
                  <input
                    type="url"
                    placeholder="https://example.com/cover.jpg"
                    value={coverUrlInput}
                    onChange={(e) => setCoverUrlInput(e.target.value)}
                    className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-primary"
                  />
                  <button
                    onClick={savePlaylistCoverUrl}
                    className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/80"
                  >
                    Save URL
                  </button>
                </div>
              </div>

              <div className="border-t border-gray-800 pt-4">
                <label className="block text-sm text-gray-400 mb-2">Upload local image</label>
                <div className="flex gap-2 items-center">
                  <input
                    type="file"
                    accept="image/*"
                    onChange={(e) => setCoverUploadFile(e.target.files?.[0] || null)}
                    className="flex-1 text-sm text-gray-300 file:mr-3 file:px-3 file:py-2 file:rounded-lg file:border-0 file:bg-gray-800 file:text-white hover:file:bg-gray-700"
                  />
                  <button
                    onClick={uploadPlaylistCover}
                    disabled={!coverUploadFile}
                    className="flex items-center gap-2 px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-lg disabled:opacity-50"
                  >
                    <Upload className="w-4 h-4" />
                    Upload
                  </button>
                </div>
              </div>
            </div>

            <div className="flex justify-between gap-2 mt-6 pt-4 border-t border-gray-800">
              <button
                onClick={clearPlaylistCover}
                className="px-4 py-2 text-gray-400 hover:text-red-400"
              >
                Clear Custom Cover
              </button>
              <button
                onClick={() => { setCoverEditingPlaylist(null); setCoverUrlInput(''); setCoverUploadFile(null) }}
                className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-lg"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Playlist Confirmation */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-900 rounded-xl p-6 w-96">
            <h3 className="text-xl font-bold text-white mb-2">Delete Playlist?</h3>
            <p className="text-gray-400 mb-6">This action cannot be undone. The tracks will remain in your library.</p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowDeleteConfirm(null)}
                className="px-4 py-2 text-gray-400 hover:text-white"
              >
                Cancel
              </button>
              <button
                onClick={() => deletePlaylist(showDeleteConfirm)}
                className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Context Menu */}
      {contextMenu.show && (
        <div 
          className="fixed bg-gray-800 rounded-lg shadow-xl border border-gray-700 py-2 z-50 min-w-48"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            onClick={handlePlayNow}
            className="w-full flex items-center gap-3 px-4 py-2 text-left text-white hover:bg-gray-700 transition-colors"
          >
            <Play className="w-4 h-4" />
            <span>Play Now</span>
          </button>
          <button
            onClick={handlePlayNext}
            className="w-full flex items-center gap-3 px-4 py-2 text-left text-white hover:bg-gray-700 transition-colors"
          >
            <SkipForward className="w-4 h-4" />
            <span>Play Next</span>
          </button>
          <button
            onClick={handleAddToQueue}
            className="w-full flex items-center gap-3 px-4 py-2 text-left text-white hover:bg-gray-700 transition-colors"
          >
            <Plus className="w-4 h-4" />
            <span>Add to Queue</span>
          </button>
          <div className="border-t border-gray-700 my-1"></div>
          <button
            onClick={() => { openMetadataLookup(contextMenu.track); closeContextMenu() }}
            className="w-full flex items-center gap-3 px-4 py-2 text-left text-white hover:bg-gray-700 transition-colors"
          >
            <Search className="w-4 h-4" />
            <span>Find Metadata</span>
          </button>
          <button
            onClick={() => { setTrackToAdd(contextMenu.track); closeContextMenu() }}
            className="w-full flex items-center gap-3 px-4 py-2 text-left text-white hover:bg-gray-700 transition-colors"
          >
            <ListPlus className="w-4 h-4" />
            <span>Add to Playlist</span>
          </button>
          <div className="border-t border-gray-700 my-1"></div>
          <button
            onClick={() => { deleteTrackEverywhere(contextMenu.track); closeContextMenu() }}
            className="w-full flex items-center gap-3 px-4 py-2 text-left text-red-400 hover:bg-gray-700 transition-colors"
          >
            <Trash2 className="w-4 h-4" />
            <span>Delete Song from Jukebox</span>
          </button>
        </div>
      )}
    </div>
  )
}

export default JukeboxPage
