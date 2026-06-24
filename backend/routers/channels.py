"""
Channels Router - Browse and manage SiriusXM channels
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from typing import List, Optional
from datetime import datetime

from database import get_db, Channel, Session as AuthSession
from services.sxm_api import SiriusXMAPI

router = APIRouter()


class ChannelResponse(BaseModel):
    channel_id: str
    channel_type: str | None = "channel-linear"
    name: str
    number: int | None
    category: str | None
    genre: str | None
    description: str | None
    image_url: str | None
    large_image_url: str | None

    class Config:
        from_attributes = True


class ChannelListResponse(BaseModel):
    channels: List[ChannelResponse]
    total: int
    last_updated: str | None


def extract_channel_type(ch_data: dict) -> str:
    """
    Return the SiriusXM playback entity type for a channel.

    Expected values include:
      - channel-linear
      - channel-xtra

    This is intentionally defensive because different SiriusXM API parsing
    paths may expose the type under different keys.
    """
    direct_type = (
        ch_data.get("channel_type")
        or ch_data.get("channelType")
        or ch_data.get("type")
        or ch_data.get("entity_type")
        or ch_data.get("entityType")
    )

    if direct_type:
        return str(direct_type)

    # Some raw browse objects expose the playback type at:
    # actions.play[0].entity.type
    try:
        actions = ch_data.get("actions") or {}
        play_actions = actions.get("play") or []
        if play_actions:
            entity = play_actions[0].get("entity") or {}
            entity_type = entity.get("type")
            if entity_type:
                return str(entity_type)
    except Exception:
        pass

    # Boolean fallbacks, if sxm_api.py ever exposes these.
    if ch_data.get("is_xtra") or ch_data.get("xtra") or ch_data.get("xtra_channel"):
        return "channel-xtra"

    return "channel-linear"


@router.get("", response_model=ChannelListResponse)
async def get_channels(
    category: Optional[str] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search by name"),
    db: DBSession = Depends(get_db)
):
    """
    Get all channels, optionally filtered
    """
    query = db.query(Channel)

    if category:
        query = query.filter(Channel.category == category)

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (Channel.name.ilike(search_term)) |
            (Channel.description.ilike(search_term)) |
            (Channel.genre.ilike(search_term))
        )

    channels = query.order_by(Channel.number).all()

    # Get last update time
    last_channel = db.query(Channel).order_by(Channel.updated_at.desc()).first()
    last_updated = last_channel.updated_at.isoformat() if last_channel else None

    return ChannelListResponse(
        channels=[ChannelResponse.model_validate(ch) for ch in channels],
        total=len(channels),
        last_updated=last_updated
    )


@router.get("/categories")
async def get_categories(db: DBSession = Depends(get_db)):
    """
    Get all channel categories
    """
    categories = db.query(Channel.category).distinct().all()
    return {"categories": [c[0] for c in categories if c[0]]}


@router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel(channel_id: str, db: DBSession = Depends(get_db)):
    """
    Get a specific channel by ID
    """
    channel = db.query(Channel).filter(Channel.channel_id == channel_id).first()

    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    return ChannelResponse.model_validate(channel)


@router.post("/refresh")
async def refresh_channels(db: DBSession = Depends(get_db)):
    """
    Refresh channel list from SiriusXM API
    """
    # Get bearer token
    session = db.query(AuthSession).filter(AuthSession.is_valid == True).first()

    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        api = SiriusXMAPI(session.bearer_token)
        channels_data = await api.fetch_all_channels()

        if not channels_data:
            raise HTTPException(status_code=500, detail="Failed to fetch channels")

        # Update database
        updated_count = 0
        type_counts = {}

        for ch_data in channels_data:
            channel_type = extract_channel_type(ch_data)
            type_counts[channel_type] = type_counts.get(channel_type, 0) + 1

            existing = db.query(Channel).filter(
                Channel.channel_id == ch_data["id"]
            ).first()

            if existing:
                existing.channel_type = channel_type
                existing.name = ch_data.get("name", existing.name)
                existing.number = ch_data.get("number", existing.number)
                existing.category = ch_data.get("category", existing.category)
                existing.genre = ch_data.get("genre", existing.genre)
                existing.description = ch_data.get("description", existing.description)
                existing.image_url = ch_data.get("images", {}).get("thumbnail")
                existing.large_image_url = ch_data.get("images", {}).get("large")
                existing.updated_at = datetime.utcnow()
            else:
                channel = Channel(
                    channel_id=ch_data["id"],
                    channel_type=channel_type,
                    name=ch_data.get("name", "Unknown"),
                    number=ch_data.get("number") or 0,
                    category=ch_data.get("category"),
                    genre=ch_data.get("genre"),
                    description=ch_data.get("description"),
                    image_url=ch_data.get("images", {}).get("thumbnail"),
                    large_image_url=ch_data.get("images", {}).get("large")
                )
                db.add(channel)

            updated_count += 1

        db.commit()

        return {
            "success": True,
            "message": f"Refreshed {updated_count} channels",
            "total": updated_count,
            "channel_type_counts": type_counts
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error refreshing channels: {str(e)}")
