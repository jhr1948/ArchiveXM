"""
Database configuration and models
"""
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/archivexm.db")

# Increase pool size to prevent exhaustion during recording
engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False},
    pool_size=10,
    max_overflow=20,
    pool_timeout=60,
    pool_pre_ping=True
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Config(Base):
    """Application configuration"""
    __tablename__ = "config"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, index=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Credentials(Base):
    """SiriusXM credentials (encrypted)"""
    __tablename__ = "credentials"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100))  # Friendly name like "Primary", "Wife's Account"
    username = Column(String(255))
    password_encrypted = Column(Text)
    is_active = Column(Boolean, default=True)  # Whether this credential is enabled
    max_streams = Column(Integer, default=3)  # Max concurrent streams for this credential
    priority = Column(Integer, default=0)  # Lower = higher priority for selection
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Session(Base):
    """Authentication session"""
    __tablename__ = "sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    credential_id = Column(Integer, index=True)  # Link to credential used
    bearer_token = Column(Text)
    cookies = Column(Text)  # JSON encoded
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_valid = Column(Boolean, default=True)


class ActiveStream(Base):
    """Track active streams per credential for load balancing"""
    __tablename__ = "active_streams"
    
    id = Column(Integer, primary_key=True, index=True)
    credential_id = Column(Integer, index=True)
    stream_type = Column(String(50))  # 'live', 'recording', 'download'
    channel_id = Column(String(100))
    started_at = Column(DateTime, default=datetime.utcnow)
    last_heartbeat = Column(DateTime, default=datetime.utcnow)  # For cleanup of stale entries


class Channel(Base):
    """SiriusXM channel"""
    __tablename__ = "channels"
    
    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(String(100), unique=True, index=True)
    channel_type = Column(String(50), default="channel-linear")
    name = Column(String(255))
    number = Column(Integer)
    category = Column(String(100))
    genre = Column(String(100))
    description = Column(Text)
    image_url = Column(Text)
    large_image_url = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Download(Base):
    """Download history"""
    __tablename__ = "downloads"
    
    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(String(100))
    channel_name = Column(String(255))
    artist = Column(String(255))
    title = Column(String(255))
    album = Column(String(255))
    duration_ms = Column(Integer)
    file_path = Column(Text)
    file_size = Column(Integer)
    timestamp_utc = Column(String(50))
    downloaded_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(50), default="completed")


class LocalTrack(Base):
    """Local audio file in library"""
    __tablename__ = "local_tracks"
    
    id = Column(Integer, primary_key=True, index=True)
    file_path = Column(Text, unique=True, index=True)
    filename = Column(String(500))
    artist = Column(String(255), index=True)
    title = Column(String(255), index=True)
    album = Column(String(255), index=True)
    genre = Column(String(100))
    duration_seconds = Column(Float)
    file_size = Column(Integer)
    bitrate = Column(Integer)
    sample_rate = Column(Integer)
    format = Column(String(20))  # m4a, mp3, flac, etc.
    cover_art_path = Column(Text)  # Extracted cover art path
    added_at = Column(DateTime, default=datetime.utcnow)
    last_played = Column(DateTime)
    play_count = Column(Integer, default=0)


class Playlist(Base):
    """User-created playlist"""
    __tablename__ = "playlists"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), index=True)
    description = Column(Text)
    cover_image = Column(Text)  # Optional custom cover
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    track_count = Column(Integer, default=0)


class PlaylistTrack(Base):
    """Track in a playlist (junction table)"""
    __tablename__ = "playlist_tracks"
    
    id = Column(Integer, primary_key=True, index=True)
    playlist_id = Column(Integer, index=True)
    track_id = Column(Integer, index=True)
    position = Column(Integer)  # Order in playlist
    added_at = Column(DateTime, default=datetime.utcnow)


def create_tables():
    """Create all database tables and run migrations"""
    Base.metadata.create_all(bind=engine)
    
    # Run migrations for existing databases
    run_migrations()


def run_migrations():
    """Add new columns to existing tables if they don't exist"""
    from sqlalchemy import inspect, text
    
    inspector = inspect(engine)
    
    with engine.connect() as conn:
        # Check and add new columns to channels table
        channel_columns = [col['name'] for col in inspector.get_columns('channels')]

        if 'channel_type' not in channel_columns:
            conn.execute(text("ALTER TABLE channels ADD COLUMN channel_type VARCHAR(50) DEFAULT 'channel-linear'"))

        # Check and add new columns to credentials table
        cred_columns = [col['name'] for col in inspector.get_columns('credentials')]
        
        if 'name' not in cred_columns:
            conn.execute(text("ALTER TABLE credentials ADD COLUMN name VARCHAR(100)"))
            conn.execute(text("UPDATE credentials SET name = 'Primary' WHERE name IS NULL"))
        if 'is_active' not in cred_columns:
            conn.execute(text("ALTER TABLE credentials ADD COLUMN is_active BOOLEAN DEFAULT 1"))
        if 'max_streams' not in cred_columns:
            conn.execute(text("ALTER TABLE credentials ADD COLUMN max_streams INTEGER DEFAULT 3"))
        if 'priority' not in cred_columns:
            conn.execute(text("ALTER TABLE credentials ADD COLUMN priority INTEGER DEFAULT 0"))
        
        # Check and add new columns to sessions table
        session_columns = [col['name'] for col in inspector.get_columns('sessions')]
        
        if 'credential_id' not in session_columns:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN credential_id INTEGER"))
            # Link existing sessions to first credential
            conn.execute(text("""
                UPDATE sessions SET credential_id = (SELECT id FROM credentials LIMIT 1)
                WHERE credential_id IS NULL
            """))
        
        # Create active_streams table if it doesn't exist
        if 'active_streams' not in inspector.get_table_names():
            conn.execute(text("""
                CREATE TABLE active_streams (
                    id INTEGER PRIMARY KEY,
                    credential_id INTEGER,
                    stream_type VARCHAR(50),
                    channel_id VARCHAR(100),
                    started_at DATETIME,
                    last_heartbeat DATETIME
                )
            """))
            conn.execute(text("CREATE INDEX ix_active_streams_credential_id ON active_streams(credential_id)"))
        
        conn.commit()
    
    print("✅ Database migrations completed")


def get_db():
    """Get database session (FastAPI dependency)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


from contextlib import contextmanager

@contextmanager
def get_db_session():
    """Get database session as context manager (for non-FastAPI use)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
