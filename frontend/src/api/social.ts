/**
 * Social, Friends, Achievements & E2EE API Client
 */

import { api } from './client'

export interface FriendItem {
  id: number
  user_id: number
  username: string
  avatar_url?: string | null
  status: 'pending' | 'accepted' | 'blocked'
  is_requester: boolean
  presence?: {
    status: 'online' | 'away' | 'invisible'
    device_type: 'web' | 'desktop' | 'mobile'
    custom_status?: string | null
    activity_label?: string | null
    activity_detail?: string | null
    last_seen_at?: string | null
    updated_at?: string | null
  } | null
  created_at: string
}

export interface PresenceItem {
  user_id: number
  username: string
  status: 'online' | 'away' | 'invisible'
  device_type: 'web' | 'desktop' | 'mobile'
  custom_status?: string | null
  activity_label?: string | null
  activity_detail?: string | null
  last_seen_at?: string | null
  updated_at?: string | null
}

export interface PresenceUpdatePayload {
  status?: 'online' | 'away' | 'invisible'
  device_type?: 'web' | 'desktop' | 'mobile'
  custom_status?: string | null
  activity_label?: string | null
  activity_detail?: string | null
}

export interface AchievementItem {
  id: string
  title: string
  description: string
  category: string
  points: number
  icon: string
  unlocked: boolean
  unlocked_at?: string | null
  global_unlocked_percentage?: number
  rarity_percent?: number
  rarity_tier?: string
  rarity_text?: string
  unlocked_count?: number
}

export interface AchievementsOverview {
  achievements: AchievementItem[]
  total_unlocked: number
  total_available: number
  prestige_score: number
}

export interface UserStatsResponse {
  user_id?: number
  username?: string
  total_achievements: number
  unlocked_achievements: number
  total_points: number
  earned_points: number
  active_time_seconds: number
  active_time_by_category: Record<string, number>
  // Aliases for component ergonomics
  achievements_unlocked: number
  total_activity_seconds: number
  categories: Record<string, number>
  rarity_summary?: Record<string, unknown>
}

export interface PublicProfileResponse {
  user_id: number
  username: string
  social_privacy: 'private' | 'friends' | 'public'
  is_friend?: boolean
  presence?: PresenceItem | null
  achievements?: AchievementItem[] | null
  stats?: UserStatsResponse | null
  restricted?: boolean
}

export interface BlindEnvelopeItem {
  id: number
  blind_mailbox_id: string
  ciphertext_envelope: string
  created_at: string
}

export async function getFriends(): Promise<FriendItem[]> {
  return api<FriendItem[]>('/social/friends')
}

export async function sendFriendRequest(targetUsername: string): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>('/social/friends/request', {
    method: 'POST',
    body: JSON.stringify({ username: targetUsername, target_username: targetUsername }),
  })
}

export async function acceptFriendRequest(requestId: number): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`/social/friends/${requestId}/accept`, {
    method: 'POST',
  })
}

export async function declineFriendRequest(requestId: number): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`/social/friends/${requestId}/decline`, {
    method: 'POST',
  })
}

export async function removeFriend(friendUserId: number): Promise<{ success: boolean }> {
  return api<{ success: boolean }>(`/social/friends/${friendUserId}`, {
    method: 'DELETE',
  })
}

export async function getBlockedUsers(): Promise<FriendItem[]> {
  return api<FriendItem[]>('/social/friends/blocked')
}

export async function blockUserApi(targetUserId: number): Promise<{ ok: boolean; message: string }> {
  return api<{ ok: boolean; message: string }>(`/social/friends/${targetUserId}/block`, {
    method: 'POST',
  })
}

export async function unblockUserApi(targetUserId: number): Promise<{ ok: boolean; message: string }> {
  return api<{ ok: boolean; message: string }>(`/social/friends/${targetUserId}/unblock`, {
    method: 'POST',
  })
}

export async function getPresence(): Promise<PresenceItem[]> {
  return api<PresenceItem[]>('/social/presence')
}

export async function updatePresence(payload: PresenceUpdatePayload): Promise<PresenceItem> {
  return api<PresenceItem>('/social/presence', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function getAchievements(): Promise<AchievementsOverview> {
  return api<AchievementsOverview>('/social/achievements')
}

export async function getStats(): Promise<UserStatsResponse> {
  return api<UserStatsResponse>('/social/stats')
}

export interface FriendRequestsResponse {
  incoming: FriendItem[]
  outgoing: FriendItem[]
}

export async function getFriendRequests(): Promise<FriendRequestsResponse> {
  return api<FriendRequestsResponse>('/social/friends/requests')
}

export async function getProfile(userId: number): Promise<PublicProfileResponse> {
  return api<PublicProfileResponse>(`/social/profile/user/${userId}`)
}

export async function getPublicProfiles(search?: string): Promise<PublicProfileResponse[]> {
  const q = search && search.trim() ? `?search=${encodeURIComponent(search.trim())}` : ''
  return api<PublicProfileResponse[]>(`/social/profiles/public${q}`)
}

export async function updatePrivacy(payload: {
  privacy?: 'private' | 'friends' | 'public'
  social_privacy?: 'private' | 'friends' | 'public'
}): Promise<{ success: boolean; social_privacy?: string }> {
  const privacyVal = payload.privacy ?? payload.social_privacy ?? 'friends'
  return api<{ success: boolean; social_privacy?: string }>('/social/privacy', {
    method: 'PATCH',
    body: JSON.stringify({ privacy: privacyVal }),
  })
}

export async function getE2eePublicKey(userId: number): Promise<{ user_id: number; username: string; public_key: string | null }> {
  return api<{ user_id: number; username: string; public_key: string | null }>(`/social/e2ee/public-key/${userId}`)
}

export async function setE2eePublicKey(publicKey: string): Promise<{ ok: boolean; message: string }> {
  return api<{ ok: boolean; message: string }>('/social/e2ee/public-key', {
    method: 'POST',
    body: JSON.stringify({ public_key: publicKey }),
  })
}

export interface ChatGroupMemberItem {
  user_id: number
  username: string
  avatar_url?: string | null
  role: string
  permissions?: string | null
  joined_at: string
}

export interface ChatGroupItem {
  id: number
  name: string
  description?: string | null
  avatar_url?: string | null
  invite_code: string
  owner_user_id: number
  member_count: number
  role: string
  default_permissions?: string | null
  created_at: string
  members: ChatGroupMemberItem[]
}

export interface ChatGroupInvitePublic {
  group_id: number
  name: string
  description?: string | null
  avatar_url?: string | null
  member_count: number
}

export async function relayE2eeEnvelope(payload: {
  blind_mailbox_id: string
  ciphertext_envelope: string
}): Promise<BlindEnvelopeItem> {
  return api<BlindEnvelopeItem>('/social/e2ee/relay', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function fetchE2eeEnvelopes(
  blindMailboxId: string,
  sinceId?: number
): Promise<BlindEnvelopeItem[]> {
  const query = sinceId ? `?since_id=${sinceId}` : ''
  return api<BlindEnvelopeItem[]>(`/social/e2ee/mailbox/${blindMailboxId}${query}`)
}

export async function sendTypingSignal(payload: {
  blind_mailbox_id: string
  status: 'typing' | 'recording' | 'idle'
}): Promise<{ ok: boolean }> {
  return api<{ ok: boolean }>('/social/e2ee/typing', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function getGroups(): Promise<ChatGroupItem[]> {
  return api<ChatGroupItem[]>('/social/groups')
}

export async function createGroup(payload: {
  name: string
  description?: string
  avatar_url?: string
}): Promise<ChatGroupItem> {
  return api<ChatGroupItem>('/social/groups', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function getGroupInviteInfo(inviteCode: string): Promise<ChatGroupInvitePublic> {
  return api<ChatGroupInvitePublic>(`/social/groups/invite/${inviteCode}`)
}

export async function joinGroupByInvite(inviteCode: string): Promise<ChatGroupItem> {
  return api<ChatGroupItem>(`/social/groups/join/${inviteCode}`, {
    method: 'POST',
  })
}

export async function leaveGroup(groupId: number): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`/social/groups/${groupId}/leave`, {
    method: 'POST',
  })
}

export async function deleteGroup(groupId: number): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`/social/groups/${groupId}`, {
    method: 'DELETE',
  })
}

export async function getGroupMembers(groupId: number): Promise<ChatGroupMemberItem[]> {
  return api<ChatGroupMemberItem[]>(`/social/groups/${groupId}/members`)
}

export async function updateGroupMemberRole(
  groupId: number,
  targetUserId: number,
  role: 'admin' | 'moderator' | 'member',
  permissions?: string
): Promise<ChatGroupMemberItem> {
  return api<ChatGroupMemberItem>(`/social/groups/${groupId}/members/${targetUserId}`, {
    method: 'PATCH',
    body: JSON.stringify({ role, permissions: permissions || null }),
  })
}

export async function kickGroupMember(
  groupId: number,
  targetUserId: number
): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`/social/groups/${groupId}/members/${targetUserId}`, {
    method: 'DELETE',
  })
}

export async function updateGroupPermissions(
  groupId: number,
  defaultPermissions: string
): Promise<ChatGroupItem> {
  return api<ChatGroupItem>(`/social/groups/${groupId}/permissions`, {
    method: 'PATCH',
    body: JSON.stringify({ default_permissions: defaultPermissions }),
  })
}

export interface ChatStoryItem {
  id: number
  user_id: number
  username: string
  avatar_url?: string | null
  content: string
  media_url?: string | null
  background: string
  created_at: string
  expires_at: string
  is_self: boolean
}

export async function getStories(): Promise<ChatStoryItem[]> {
  return api<ChatStoryItem[]>('/social/stories')
}

export async function createStory(payload: {
  content: string
  media_url?: string
  background?: string
}): Promise<ChatStoryItem> {
  return api<ChatStoryItem>('/social/stories', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function deleteStory(storyId: number): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`/social/stories/${storyId}`, {
    method: 'DELETE',
  })
}

export async function recordActivityTime(category: string, seconds: number): Promise<{ success: boolean }> {
  return api<{ success: boolean }>('/social/activity/ping', {
    method: 'POST',
    body: JSON.stringify({ category, seconds }),
  })
}

