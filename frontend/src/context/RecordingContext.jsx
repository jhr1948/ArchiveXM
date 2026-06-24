import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../services/api'

const RecordingContext = createContext(null)

export function RecordingProvider({ children }) {
  const [isRecording, setIsRecording] = useState(false)
  const [recordingData, setRecordingData] = useState(null)
  const [channelInfo, setChannelInfo] = useState(null)
  const pollIntervalRef = useRef(null)

  // Check recording status from backend
  const checkStatus = useCallback(async () => {
    try {
      const response = await api.get('/api/recording/status')
      const data = response.data
      
      if (data.recording) {
        setIsRecording(true)
        setRecordingData({
          channelId: data.channel_id,
          startTime: data.start_time,
          elapsedSeconds: data.elapsed_seconds,
          tracksRecorded: data.tracks_recorded,
          currentTrack: data.current_track || null,
          stopping: data.stopping || false,
          stoppingInSeconds: data.stopping_in_seconds || null
        })
      } else {
        setIsRecording(false)
        setRecordingData(null)
        setChannelInfo(null)
      }
    } catch (error) {
      console.error('Error checking recording status:', error)
    }
  }, [])

  // Start polling when component mounts
  useEffect(() => {
    // Initial check
    checkStatus()
    
    // Poll every 5 seconds
    pollIntervalRef.current = setInterval(checkStatus, 5000)
    
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
      }
    }
  }, [checkStatus])

  // Start recording
  const startRecording = useCallback(async (channelId, channel) => {
    try {
      const response = await api.post('/api/recording/start', { channel_id: channelId })
      if (response.data.success) {
        setIsRecording(true)
        setRecordingData({
          channelId: channelId,
          startTime: response.data.start_time,
          elapsedSeconds: 0,
          tracksRecorded: 0
        })
        setChannelInfo(channel)
        return { success: true }
      }
      return { success: false, error: response.data.error }
    } catch (error) {
      return { success: false, error: error.response?.data?.detail || error.message }
    }
  }, [])

  // Stop recording
  const stopRecording = useCallback(async (waitForTrack = true) => {
    try {
      const response = await api.post(`/api/recording/stop?wait_for_track=${waitForTrack}`)
      setIsRecording(false)
      setRecordingData(null)
      setChannelInfo(null)
      return { success: true, data: response.data }
    } catch (error) {
      return { success: false, error: error.response?.data?.detail || error.message }
    }
  }, [])

  // Force stop recording
  const forceStopRecording = useCallback(async () => {
    try {
      const response = await api.post('/api/recording/force-stop')
      setIsRecording(false)
      setRecordingData(null)
      setChannelInfo(null)
      return { success: true, data: response.data }
    } catch (error) {
      return { success: false, error: error.response?.data?.detail || error.message }
    }
  }, [])

  // Set channel info (called from ChannelDetailPage when starting recording)
  const setRecordingChannel = useCallback((channel) => {
    setChannelInfo(channel)
  }, [])

  const value = {
    isRecording,
    recordingData,
    channelInfo,
    startRecording,
    stopRecording,
    forceStopRecording,
    setRecordingChannel,
    checkStatus
  }

  return (
    <RecordingContext.Provider value={value}>
      {children}
    </RecordingContext.Provider>
  )
}

export function useRecording() {
  const context = useContext(RecordingContext)
  if (!context) {
    throw new Error('useRecording must be used within a RecordingProvider')
  }
  return context
}
