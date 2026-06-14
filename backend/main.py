"""
ArchiveXM Backend - FastAPI Application
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
import asyncio

from database import create_tables
from routers import auth, channels, streams, downloads, config, recording, library, settings, playlist

# Background task reference
_background_refresh_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    global _background_refresh_task
    
    # Startup
    create_tables()
    
    # Start background token refresh task
    from services.token_manager import get_token_manager, start_background_refresh
    
    # Initialize token manager and load existing token
    token_manager = get_token_manager()
    token_manager.load_from_db()
    
    # Start background refresh (checks every 10 minutes, refreshes 30 min before expiry)
    _background_refresh_task = asyncio.create_task(start_background_refresh(check_interval_minutes=10))
    
    print("🚀 ArchiveXM Backend Started")
    print("🔄 Background token refresh enabled")
    
    yield
    
    # Shutdown - cancel background task
    if _background_refresh_task:
        _background_refresh_task.cancel()
        try:
            await _background_refresh_task
        except asyncio.CancelledError:
            pass
    
    print("👋 ArchiveXM Backend Shutdown")


app = FastAPI(
    title="ArchiveXM API",
    description="SiriusXM streaming and archival backend",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(channels.router, prefix="/api/channels", tags=["Channels"])
app.include_router(streams.router, prefix="/api/streams", tags=["Streams"])
app.include_router(downloads.router, prefix="/api/downloads", tags=["Downloads"])
app.include_router(config.router, prefix="/api/config", tags=["Configuration"])
app.include_router(recording.router, prefix="/api/recording", tags=["Recording"])
app.include_router(library.router, prefix="/api/library", tags=["Library"])
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])
app.include_router(playlist.router)

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "ArchiveXM"}


@app.get("/api")
async def root():
    """API root"""
    return {
        "name": "ArchiveXM API",
        "version": "1.0.0",
        "endpoints": {
            "auth": "/api/auth",
            "channels": "/api/channels",
            "streams": "/api/streams",
            "downloads": "/api/downloads",
            "config": "/api/config"
        }
    }
