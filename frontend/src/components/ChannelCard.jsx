import { Link } from 'react-router-dom'
import { Radio, Circle } from 'lucide-react'
import { useRecording } from '../context/RecordingContext'

function ChannelCard({ channel }) {
  const { isRecording, recordingData } = useRecording()
  const isThisChannelRecording = isRecording && recordingData?.channelId === channel.channel_id
  
  return (
    <Link
      to={`/channel/${channel.channel_id}`}
      className={`card hover:border-sxm-accent transition-colors group ${isThisChannelRecording ? 'border-red-500/50 ring-1 ring-red-500/30' : ''}`}
    >
      {/* Channel Image */}
      <div className="aspect-square mb-3 rounded-lg overflow-hidden bg-sxm-darker relative">
        {/* Recording Indicator Badge */}
        {isThisChannelRecording && (
          <div className="absolute top-2 right-2 z-10 flex items-center gap-1 px-2 py-1 bg-red-600/90 rounded-full">
            <Circle className="w-2 h-2 text-white fill-white animate-pulse" />
            <span className="text-xs text-white font-medium">REC</span>
          </div>
        )}
        {channel.image_url ? (
          <img
            src={channel.image_url}
            alt={channel.name}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
            onError={(e) => {
              e.target.style.display = 'none'
              e.target.nextSibling.style.display = 'flex'
            }}
            loading="lazy"
          />
        ) : null}
        <div 
          className={`w-full h-full flex items-center justify-center ${channel.image_url ? 'hidden' : ''}`}
        >
          <Radio className="w-8 h-8 text-gray-600" />
        </div>
      </div>

      {/* Channel Info */}
      <div>
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-medium text-white text-sm line-clamp-2 group-hover:text-sxm-accent transition-colors">
            {channel.name}
          </h3>
          {channel.number && (
            <span className="text-xs text-gray-500 shrink-0">
              Ch {channel.number}
            </span>
          )}
        </div>
        {channel.genre && (
          <p className="text-xs text-gray-500 mt-1 line-clamp-1">
            {channel.genre}
          </p>
        )}
      </div>
    </Link>
  )
}

export default ChannelCard
