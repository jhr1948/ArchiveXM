import { Link } from 'react-router-dom'
import { useEffect, useState } from 'react'
import {
  Play, Pause, Volume2, VolumeX, X, Radio, Loader2, Music,
  SkipBack, SkipForward, Shuffle, Repeat, Disc3, ListPlus, Trash2
} from 'lucide-react'
import { usePlayer } from '../context/PlayerContext'
import { useJukebox } from '../context/JukeboxContext'
import { libraryApi } from '../services/api'

function UnifiedPlayerBar() {
  // Live stream player
  const livePlayer = usePlayer()

  // Jukebox player
  const jukebox = useJukebox()
  const [playlists, setPlaylists] = useState([])
  const [trackToAdd, setTrackToAdd] = useState(null)
  const [newPlaylistName, setNewPlaylistName] = useState('')

  const loadPlaylists = async () => {
    try {
      const res = await libraryApi.getPlaylists()
      setPlaylists(res.data || [])
    } catch (error) {
      console.error('Error loading playlists:', error)
    }
  }

  useEffect(() => {
    loadPlaylists()
  }, [])

  const createPlaylist = async () => {
    if (!newPlaylistName.trim()) return

    try {
      const created = await libraryApi.createPlaylist(newPlaylistName)
      if (trackToAdd && created?.data?.id) {
        await libraryApi.addToPlaylist(created.data.id, [trackToAdd.id])
      }
      setNewPlaylistName('')
      setTrackToAdd(null)
      await loadPlaylists()
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
      await loadPlaylists()
    } catch (error) {
      console.error('Error adding track to playlist:', error)
    }
  }

  const deleteCurrentJukeboxTrack = async () => {
    const track = jukebox.currentTrack
    if (!track) return

    const trackName = track.title || track.filename || 'this song'
    const confirmed = window.confirm(
      `Delete "${trackName}" from the Jukebox?\n\nThis removes it from all playlists and deletes the local audio file.`
    )

    if (!confirmed) return

    try {
      await libraryApi.deleteTrack(track.id, true)
      if (jukebox.removeTrackFromPlayback) {
        jukebox.removeTrackFromPlayback(track.id)
      } else if (jukebox.clearQueue) {
        jukebox.clearQueue(false)
      }
      await loadPlaylists()
    } catch (error) {
      console.error('Error deleting track:', error)
      window.alert('Failed to delete the song. Check the backend logs for details.')
    }
  }

  const hasLiveStream = !!livePlayer.currentChannel
  const hasJukebox = !!jukebox.currentTrack
  const showJukeboxBar = hasJukebox && !hasLiveStream

  // Don't render if nothing is playing
  if (!hasLiveStream && !hasJukebox) return null

  const formatTime = (seconds) => {
    if (!seconds || isNaN(seconds)) return '0:00'
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const stopJukebox = () => {
    // clearQueue(false) forces the queue/current track to clear instead of preserving the current item.
    if (jukebox.clearQueue) {
      jukebox.clearQueue(false)
    }
  }

  return (
    <>
      {/* Jukebox Bar - global bar for pages outside the Jukebox view. Keep it at the bottom like the live player. */}
      {showJukeboxBar && (
        <div className="fixed bottom-0 left-0 right-0 z-50 bg-gray-900 border-t border-gray-700 px-6 py-4 min-h-[104px]">
          <div className="max-w-screen-2xl mx-auto flex items-center justify-between gap-6">
            {/* Left: Track Info */}
            <div className="flex items-center gap-4 flex-1 min-w-0">
              <Link to="/jukebox" className="flex items-center gap-4 min-w-0 group">
                <div className="w-20 h-20 bg-gray-800 rounded-xl flex items-center justify-center flex-shrink-0 overflow-hidden">
                  {jukebox.currentTrack.cover_art_path ? (
                    <img
                      src={libraryApi.getCoverUrl(jukebox.currentTrack.id)}
                      alt=""
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <Music className="w-8 h-8 text-gray-600" />
                  )}
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-3 mb-1">
                    <div className="flex items-center gap-2 px-3 py-1 bg-primary/20 rounded-full">
                      <Disc3
                        className={`w-3.5 h-3.5 text-primary ${jukebox.isPlaying ? 'animate-spin' : ''}`}
                        style={{ animationDuration: '3s' }}
                      />
                      <span className="text-xs text-primary font-semibold tracking-wide">Jukebox</span>
                    </div>
                  </div>
                  <p className="text-white text-xl font-semibold truncate leading-tight group-hover:text-primary transition-colors">
                    {jukebox.currentTrack.title || jukebox.currentTrack.filename}
                  </p>
                  <p className="text-gray-300 text-base truncate">
                    {jukebox.currentTrack.artist || 'Unknown'}
                  </p>
                </div>
              </Link>
            </div>

            {/* Center: Controls */}
            <div className="flex flex-col items-center gap-2 flex-[1.2] min-w-[360px]">
              <div className="flex items-center gap-4">
                <button
                  onClick={() => jukebox.setShuffle(!jukebox.shuffle)}
                  className={`transition-colors ${jukebox.shuffle ? 'text-primary' : 'text-gray-400 hover:text-white'}`}
                  title="Shuffle"
                >
                  <Shuffle className="w-6 h-6" />
                </button>
                <button onClick={jukebox.playPrevious} className="text-gray-400 hover:text-white" title="Previous">
                  <SkipBack className="w-6 h-6" />
                </button>
                <button
                  onClick={jukebox.togglePlay}
                  className="w-14 h-14 bg-white text-black rounded-full flex items-center justify-center hover:scale-105 transition-transform"
                  title={jukebox.isPlaying ? 'Pause' : 'Play'}
                >
                  {jukebox.isPlaying ? <Pause className="w-7 h-7" /> : <Play className="w-7 h-7 ml-1" />}
                </button>
                <button onClick={jukebox.playNext} className="text-gray-400 hover:text-white" title="Next">
                  <SkipForward className="w-6 h-6" />
                </button>
                <button
                  onClick={() => jukebox.setRepeat(jukebox.repeat === 'none' ? 'all' : jukebox.repeat === 'all' ? 'one' : 'none')}
                  className={`transition-colors relative ${jukebox.repeat !== 'none' ? 'text-primary' : 'text-gray-400 hover:text-white'}`}
                  title="Repeat"
                >
                  <Repeat className="w-6 h-6" />
                  {jukebox.repeat === 'one' && <span className="absolute -top-1 -right-2 text-xs font-bold">1</span>}
                </button>
              </div>

              <div className="flex items-center gap-3 w-full max-w-xl">
                <span className="text-xs text-gray-500 w-10 text-right">{formatTime(jukebox.currentTime)}</span>
                <input
                  type="range"
                  min={0}
                  max={jukebox.duration || 100}
                  value={jukebox.currentTime || 0}
                  onInput={(e) => jukebox.seek(parseFloat(e.target.value))}
                  onChange={(e) => jukebox.seek(parseFloat(e.target.value))}
                  className="flex-1 h-2 bg-gray-700 rounded-full appearance-none cursor-pointer
                    [&::-webkit-slider-thumb]:appearance-none
                    [&::-webkit-slider-thumb]:w-4
                    [&::-webkit-slider-thumb]:h-4
                    [&::-webkit-slider-thumb]:rounded-full
                    [&::-webkit-slider-thumb]:bg-white
                    [&::-webkit-slider-thumb]:cursor-pointer"
                />
                <span className="text-xs text-gray-500 w-10">{formatTime(jukebox.duration)}</span>
              </div>
            </div>

            {/* Right: Actions, Volume & Close */}
            <div className="flex items-center gap-4 flex-1 justify-end">
              <button
                onClick={() => setTrackToAdd(jukebox.currentTrack)}
                className="text-gray-400 hover:text-white transition-colors"
                title="Add to playlist"
              >
                <ListPlus className="w-6 h-6" />
              </button>
              <button
                onClick={deleteCurrentJukeboxTrack}
                className="text-gray-400 hover:text-red-400 transition-colors"
                title="Delete song from Jukebox"
              >
                <Trash2 className="w-6 h-6" />
              </button>
              <button
                onClick={() => jukebox.setIsMuted(!jukebox.isMuted)}
                className="text-gray-400 hover:text-white"
                title={jukebox.isMuted ? 'Unmute' : 'Mute'}
              >
                {jukebox.isMuted || jukebox.volume === 0 ? <VolumeX className="w-6 h-6" /> : <Volume2 className="w-6 h-6" />}
              </button>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={jukebox.isMuted ? 0 : jukebox.volume}
                onChange={(e) => jukebox.setVolume(parseFloat(e.target.value))}
                className="w-32 h-2 bg-gray-700 rounded-full appearance-none cursor-pointer
                  [&::-webkit-slider-thumb]:appearance-none
                  [&::-webkit-slider-thumb]:w-4
                  [&::-webkit-slider-thumb]:h-4
                  [&::-webkit-slider-thumb]:rounded-full
                  [&::-webkit-slider-thumb]:bg-white"
              />
              <button
                onClick={stopJukebox}
                className="text-gray-400 hover:text-red-400"
                title="Stop Jukebox"
              >
                <X className="w-6 h-6" />
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


      {/* Add current Jukebox track to playlist */}
      {trackToAdd && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[70]">
          <div className="bg-gray-900 rounded-xl p-6 w-96 max-w-[calc(100vw-2rem)] border border-gray-800">
            <h3 className="text-xl font-bold text-white mb-2">Add to playlist</h3>
            <p className="text-gray-400 text-sm mb-4 truncate">
              {trackToAdd.title || trackToAdd.filename}
            </p>

            <div className="space-y-2 max-h-48 overflow-y-auto mb-4">
              {playlists.length === 0 ? (
                <p className="text-gray-500 text-sm">No playlists yet. Create one below.</p>
              ) : playlists.map(playlist => (
                <button
                  key={playlist.id}
                  onClick={() => addTrackToPlaylist(playlist.id)}
                  className="w-full text-left px-3 py-2 bg-gray-800 hover:bg-gray-700 text-white rounded-lg transition-colors flex items-center justify-between"
                >
                  <span className="truncate">{playlist.name}</span>
                  <span className="text-xs text-gray-500 ml-3">{playlist.track_count || 0}</span>
                </button>
              ))}
            </div>

            <div className="border-t border-gray-800 pt-4">
              <label className="block text-sm text-gray-400 mb-2">Create new playlist</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="Playlist name"
                  value={newPlaylistName}
                  onChange={(e) => setNewPlaylistName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') createPlaylist() }}
                  className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white focus:outline-none focus:border-primary"
                />
                <button
                  onClick={createPlaylist}
                  disabled={!newPlaylistName.trim()}
                  className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/80 disabled:opacity-50"
                >
                  Add
                </button>
              </div>
            </div>

            <div className="flex justify-end mt-4">
              <button
                onClick={() => { setTrackToAdd(null); setNewPlaylistName('') }}
                className="px-4 py-2 text-gray-400 hover:text-white"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

export default UnifiedPlayerBar
