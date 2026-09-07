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

export async function getProfile(userId: number): Promise<PublicProfileResponse> {
  return api<PublicProfileResponse>(`/social/profile/user/${userId}`)
}

export async function updatePrivacy(payload: {
  social_privacy?: 'private' | 'friends' | 'public'
  social_e2ee_public_key?: string
}): Promise<{ success: boolean }> {
  return api<{ success: boolean }>('/social/privacy', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
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

export async function recordActivityTime(category: string, seconds: number): Promise<{ success: boolean }> {
  return api<{ success: boolean }>('/social/activity/ping', {
    method: 'POST',
    body: JSON.stringify({ category, seconds }),
  })
}
