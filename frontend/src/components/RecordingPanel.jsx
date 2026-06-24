import { useState } from 'react'
import { Circle, Square, Clock, Music } from 'lucide-react'
import { useRecording } from '../context/RecordingContext'

function RecordingPanel({ channelId, channelName, channel }) {
  const { 
    isRecording, 
    recordingData, 
    startRecording: contextStartRecording, 
    stopRecording: contextStopRecording,
    forceStopRecording 
  } = useRecording()
  const [loading, setLoading] = useState(false)

  const handleStartRecording = async () => {
    setLoading(true)
    try {
      const result = await contextStartRecording(channelId, channel)
      if (!result.success) {
        alert(result.error || 'Failed to start recording')
      }
    } finally {
      setLoading(false)
    }
  }

  const handleStopRecording = async (waitForTrack = true) => {
    setLoading(true)
    try {
      const result = waitForTrack 
        ? await contextStopRecording(true)
        : await forceStopRecording()
      
      if (result.success && result.data?.tracks_recorded > 0) {
        alert(`Recording complete! ${result.data.tracks_recorded} tracks saved.`)
      } else if (!result.success) {
        alert(result.error || 'Failed to stop recording')
      }
    } finally {
      setLoading(false)
    }
  }

  const formatDuration = (seconds) => {
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const isRecordingThisChannel = isRecording && recordingData?.channelId === channelId

  return (
    <div className="bg-sxm-darker rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
        <Circle className="w-4 h-4" />
        Live Recording
      </h3>

      {isRecordingThisChannel ? (
        <div>
          {/* Recording indicator */}
          <div className="flex items-center gap-3 mb-4">
            <div className="w-3 h-3 bg-red-500 rounded-full animate-pulse" />
            <span className="text-white font-medium">Recording...</span>
            <span className="text-gray-400 text-sm">
              {formatDuration(recordingData?.elapsedSeconds || 0)}
            </span>
          </div>

          {/* Stats */}
          <div className="flex items-center gap-4 text-sm text-gray-400 mb-4">
            <span className="flex items-center gap-1">
              <Music className="w-4 h-4" />
              {recordingData?.tracksRecorded || 0} tracks
            </span>
          </div>

          {/* Stop button */}
          <div className="flex gap-2">
            <button
              onClick={() => handleStopRecording(true)}
              disabled={loading}
              className="flex-1 bg-sxm-error hover:bg-red-600 text-white py-2 px-4 rounded-lg flex items-center justify-center gap-2 transition-colors disabled:opacity-50"
            >
              <Square className="w-4 h-4 fill-current" />
              {loading ? 'Stopping...' : 'Stop (Wait for Track)'}
            </button>
            <button
              onClick={() => handleStopRecording(false)}
              disabled={loading}
              className="bg-sxm-card hover:bg-sxm-border text-white py-2 px-4 rounded-lg transition-colors disabled:opacity-50"
              title="Stop immediately without waiting"
            >
              Stop Now
            </button>
          </div>
        </div>
      ) : isRecording ? (
        <div className="text-gray-400 text-sm">
          <p>Recording in progress on another channel.</p>
          <p className="text-xs mt-1">Stop that recording first to record here.</p>
        </div>
      ) : (
        <div>
          <p className="text-gray-400 text-sm mb-3">
            Record live audio with automatic track splitting and metadata.
          </p>
          <button
            onClick={handleStartRecording}
            disabled={loading}
            className="w-full bg-sxm-error hover:bg-red-600 text-white py-2 px-4 rounded-lg flex items-center justify-center gap-2 transition-colors disabled:opacity-50"
          >
            <Circle className="w-4 h-4 fill-current" />
            {loading ? 'Starting...' : 'Start Recording'}
          </button>
        </div>
      )}
    </div>
  )
}

export default RecordingPanel
