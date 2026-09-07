from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class AchievementResponse(BaseModel):
    id: str
    title: str
    description: str
    category: str
    points: int
    icon: str
    unlocked: bool = False
    unlocked_at: datetime | None = None
    global_unlocked_percentage: float = 0.0
    rarity_tier: str = "common"
    rarity_text: str = ""


class AchievementsOverviewResponse(BaseModel):
    achievements: list[AchievementResponse] = Field(default_factory=list)
    total_unlocked: int = 0
    total_available: int = 0
    prestige_score: int = 0


class UserStatsResponse(BaseModel):
    total_achievements: int = 0
    unlocked_achievements: int = 0
    total_points: int = 0
    earned_points: int = 0
    active_time_seconds: int = 0
    active_time_by_category: dict[str, int] = Field(default_factory=dict)
    # Frontend aliases
    achievements_unlocked: int = 0
    total_activity_seconds: int = 0
    categories: dict[str, int] = Field(default_factory=dict)


class PresenceInfo(BaseModel):
    status: str = "offline"
    device_type: str = "web"
    custom_status: str | None = None
    activity_label: str | None = None
    activity_detail: str | None = None
    updated_at: datetime | None = None


class FriendResponse(BaseModel):
    id: int
    user_id: int
    username: str
    avatar_url: str | None = None
    status: str
    is_requester: bool = False
    created_at: datetime
    presence: PresenceInfo | None = None


class FriendRequestCreate(BaseModel):
    username: str = ""
    target_username: str | None = None

    def __init__(self, **data):
        if "target_username" in data and not data.get("username"):
            data["username"] = data["target_username"]
        super().__init__(**data)


class PresenceUpdateRequest(BaseModel):
    status: str = Field("online", pattern="^(online|away|invisible)$")
    device_type: str = Field("web", pattern="^(web|desktop|mobile)$")
    custom_status: str | None = Field(None, max_length=128)
    activity_label: str | None = Field(None, max_length=128)
    activity_detail: str | None = Field(None, max_length=128)


class ActivityPingRequest(BaseModel):
    category: str = Field("general", max_length=32)
    seconds: int = Field(30, ge=1, le=300)


class PrivacyUpdateRequest(BaseModel):
    privacy: str = Field(..., pattern="^(private|friends|public)$")


class E2eeBlindEnvelopeCreate(BaseModel):
    blind_mailbox_id: str = Field(..., min_length=16, max_length=64)
    ciphertext_envelope: str = Field(..., min_length=10)


class E2eeBlindEnvelopeResponse(BaseModel):
    id: int
    blind_mailbox_id: str
    ciphertext_envelope: str
    created_at: datetime


class E2eePublicKeyUpdate(BaseModel):
    public_key: str = Field(..., min_length=10, max_length=8192)


class E2eePublicKeyResponse(BaseModel):
    user_id: int
    username: str
    public_key: str | None = None


class SocialProfileResponse(BaseModel):
    user_id: int
    username: str
    avatar_url: str | None = None
    privacy: str
    restricted: bool = False
    presence: PresenceInfo | None = None
    stats: UserStatsResponse | None = None
    achievements: list[AchievementResponse] | None = None
