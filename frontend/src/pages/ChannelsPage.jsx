import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Search, RefreshCw, Radio, Filter } from 'lucide-react'
import { channelsApi } from '../services/api'

function ChannelsPage() {
  const [channels, setChannels] = useState([])
  const [categories, setCategories] = useState([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [search, setSearch] = useState('')
  const [selectedCategory, setSelectedCategory] = useState('')

  useEffect(() => {
    loadChannels()
    loadCategories()
  }, [])

  const loadChannels = async () => {
    try {
      const response = await channelsApi.getAll()
      setChannels(response.data.channels || [])
    } catch (error) {
      console.error('Error loading channels:', error)
    } finally {
      setLoading(false)
    }
  }

  const loadCategories = async () => {
    try {
      const response = await channelsApi.getCategories()
      setCategories(response.data.categories || [])
    } catch (error) {
      console.error('Error loading categories:', error)
    }
  }

  const refreshChannels = async () => {
    setRefreshing(true)
    try {
      await channelsApi.refresh()
      await loadChannels()
      await loadCategories()
    } catch (error) {
      console.error('Error refreshing channels:', error)
    } finally {
      setRefreshing(false)
    }
  }

  const filteredChannels = channels.filter(channel => {
    const matchesSearch = !search || 
      channel.name?.toLowerCase().includes(search.toLowerCase()) ||
      channel.description?.toLowerCase().includes(search.toLowerCase()) ||
      channel.genre?.toLowerCase().includes(search.toLowerCase())
    
    const matchesCategory = !selectedCategory || channel.category === selectedCategory
    
    return matchesSearch && matchesCategory
  })

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-center">
          <div className="w-12 h-12 border-4 border-sxm-accent border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
          <p className="text-gray-400">Loading channels...</p>
        </div>
      </div>
    )
  }

  return (
    <div>
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Channels</h1>
          <p className="text-gray-400 text-sm">{channels.length} channels available</p>
        </div>

        <button
          onClick={refreshChannels}
          disabled={refreshing}
          className="btn-secondary flex items-center gap-2 self-start"
        >
          <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
          {refreshing ? 'Refreshing...' : 'Refresh Channels'}
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-col md:flex-row gap-4 mb-6">
        {/* Search */}
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
          <input
            type="text"
            className="input pl-10"
            placeholder="Search channels..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {/* Category Filter */}
        <div className="relative">
          <Filter className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
          <select
            className="input pl-10 pr-8 appearance-none cursor-pointer min-w-[200px]"
            value={selectedCategory}
            onChange={(e) => setSelectedCategory(e.target.value)}
          >
            <option value="">All Categories</option>
            {categories.map(cat => (
              <option key={cat} value={cat}>{cat}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Results count */}
      {(search || selectedCategory) && (
        <p className="text-gray-400 text-sm mb-4">
          Showing {filteredChannels.length} of {channels.length} channels
        </p>
      )}

      {/* Channels Grid */}
      {filteredChannels.length === 0 ? (
        <div className="text-center py-12">
          <Radio className="w-12 h-12 text-gray-600 mx-auto mb-4" />
          <p className="text-gray-400">No channels found</p>
          {channels.length === 0 && (
            <button onClick={refreshChannels} className="btn-primary mt-4">
              Load Channels
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
          {filteredChannels.map(channel => (
            <Link
              key={channel.channel_id}
              to={`/channel/${channel.channel_id}`}
              className="card hover:border-sxm-accent transition-colors group"
            >
              {/* Channel Image */}
              <div className="aspect-square mb-3 rounded-lg overflow-hidden bg-sxm-darker">
                {channel.image_url ? (
                  <img
                    src={channel.image_url}
                    alt={channel.name}
                    className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                    onError={(e) => {
                      e.target.style.display = 'none'
                      e.target.nextSibling.style.display = 'flex'
                    }}
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
          ))}
        </div>
      )}
    </div>
  )
}

export default ChannelsPage
