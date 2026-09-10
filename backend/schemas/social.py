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
    device_type: str | None = Field(None, pattern="^(web|desktop|mobile)$")
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
    recipient_id: int | None = Field(None, description="Optionale Empfänger-User-ID zur strikten Push-Filterung")
    client_uuid: str | None = Field(None, max_length=64, description="Client-UUID zur Idempotenz und Deduplizierung")


class E2eeBlindEnvelopeResponse(BaseModel):
    id: int
    blind_mailbox_id: str
    ciphertext_envelope: str
    client_uuid: str | None = None
    created_at: datetime


class E2eeTypingSignalCreate(BaseModel):
    blind_mailbox_id: str = Field(..., min_length=16, max_length=64)
    status: str = Field(..., pattern="^(typing|recording|idle)$")
    recipient_id: int | None = None


class DirectChatResponse(BaseModel):
    id: int
    other_user_id: int
    other_username: str
    other_avatar_url: str | None = None
    blind_mailbox_id: str
    is_friend: bool = False
    is_blocked: bool = False
    other_privacy: str = "friends"
    presence: PresenceInfo | None = None
    created_at: datetime
    updated_at: datetime


class CanMessageResponse(BaseModel):
    can_message: bool
    reason: str | None = None
    blind_mailbox_id: str | None = None


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
    is_friend: bool = False
    presence: PresenceInfo | None = None
    stats: UserStatsResponse | None = None
    achievements: list[AchievementResponse] | None = None


class ChatGroupCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)
    description: str | None = Field(None, max_length=256)
    avatar_url: str | None = None


class ChatGroupMemberResponse(BaseModel):
    user_id: int
    username: str
    avatar_url: str | None = None
    role: str
    permissions: str | None = None
    joined_at: datetime


class ChatGroupMemberUpdate(BaseModel):
    role: str = Field(..., pattern="^(admin|moderator|member)$")
    permissions: str | None = Field(None, max_length=256)


class ChatGroupPermissionsUpdate(BaseModel):
    default_permissions: str = Field(..., min_length=2, max_length=256)


class ChatGroupResponse(BaseModel):
    id: int
    name: str
    description: str | None = None
    avatar_url: str | None = None
    invite_code: str
    owner_user_id: int
    member_count: int
    role: str
    default_permissions: str | None = None
    created_at: datetime
    members: list[ChatGroupMemberResponse] = []


class ChatGroupInvitePublicResponse(BaseModel):
    group_id: int
    name: str
    description: str | None = None
    avatar_url: str | None = None
    member_count: int


class ChatStoryCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)
    media_url: str | None = None
    background: str = Field("gradient-1", max_length=64)


class ChatStoryResponse(BaseModel):
    id: int
    user_id: int
    username: str
    avatar_url: str | None = None
    content: str
    media_url: str | None = None
    background: str
    created_at: datetime
    expires_at: datetime
    is_self: bool = False

