function Spinner({ size = 'md', className = '' }) {
  const sizeClasses = {
    sm: 'w-4 h-4 border-2',
    md: 'w-8 h-8 border-3',
    lg: 'w-12 h-12 border-4'
  }

  return (
    <div 
      className={`${sizeClasses[size]} border-sxm-accent border-t-transparent rounded-full animate-spin ${className}`}
    />
  )
}

export default Spinner
