import { useState, useEffect } from 'react'
import { CheckCircle, AlertCircle, X, Info } from 'lucide-react'

function Toast({ message, type = 'info', onClose, duration = 5000 }) {
  useEffect(() => {
    if (duration > 0) {
      const timer = setTimeout(onClose, duration)
      return () => clearTimeout(timer)
    }
  }, [duration, onClose])

  const icons = {
    success: <CheckCircle className="w-5 h-5 text-sxm-success" />,
    error: <AlertCircle className="w-5 h-5 text-sxm-error" />,
    info: <Info className="w-5 h-5 text-sxm-accent" />
  }

  const bgColors = {
    success: 'bg-sxm-success/10 border-sxm-success/20',
    error: 'bg-sxm-error/10 border-sxm-error/20',
    info: 'bg-sxm-accent/10 border-sxm-accent/20'
  }

  return (
    <div className={`fixed bottom-4 right-4 flex items-center gap-3 px-4 py-3 rounded-lg border ${bgColors[type]} backdrop-blur-sm shadow-lg z-50 animate-slide-up`}>
      {icons[type]}
      <p className="text-white text-sm">{message}</p>
      <button 
        onClick={onClose}
        className="text-gray-400 hover:text-white ml-2"
      >
        <X size={16} />
      </button>
    </div>
  )
}

export default Toast
