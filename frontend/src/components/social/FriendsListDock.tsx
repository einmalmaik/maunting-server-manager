import React, { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Button,
  Input,
  Badge,
  Avatar,
} from '@/Singra/UI'
import {
  Users,
  MessageSquare,
  UserPlus,
  X,
  ChevronDown,
  ChevronUp,
  UserMinus,
  Search,
  ExternalLink,
  UsersRound,
} from 'lucide-react'
import { StatusDot } from './StatusIndicator'
import { DeviceBadge } from './DeviceBadge'
import { E2EEChatModal } from './E2EEChatModal'
import {
  type FriendItem,
  getFriends,
  getFriendRequests,
  removeFriend,
} from '@/api/social'
import { teamsApi, type TeamMember } from '@/api/teams'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'

interface FriendsListDockProps {
  collapsedDefault?: boolean
  className?: string
  onClose?: () => void
}

export function FriendsListDock({
  collapsedDefault = false,
  className = '',
  onClose,
}: FriendsListDockProps) {
  const { user } = useAuthStore()
  const navigate = useNavigate()
  const [friends, setFriends] = useState<FriendItem[]>([])
  const [incomingRequests, setIncomingRequests] = useState<FriendItem[]>([])
  const [teamMembers, setTeamMembers] = useState<Array<{ member: TeamMember; teamName: string }>>([])
  const [collapsed, setCollapsed] = useState(collapsedDefault)
  const [searchQuery, setSearchQuery] = useState('')

  // 2 Primary Tabs: 'friends' vs 'chats'
  const [primaryTab, setPrimaryTab] = useState<'friends' | 'chats'>('friends')

  // Chat Modal
  const [chatFriend, setChatFriend] = useState<FriendItem | null>(null)
  const [isChatOpen, setIsChatOpen] = useState(false)

  const currentUserId = user?.id || 0

  const loadData = async () => {
    try {
      const [friendsData, reqsData, teamsData] = await Promise.all([
        getFriends().catch(() => []),
        getFriendRequests().catch(() => ({ incoming: [], outgoing: [] })),
        teamsApi.list().catch(() => []),
      ])
      setFriends(friendsData)
      setIncomingRequests(reqsData.incoming)

      const membersList: Array<{ member: TeamMember; teamName: string }> = []
      for (const t of teamsData) {
        try {
          const detail = await teamsApi.get(t.id)
          if (detail && detail.members) {
            for (const m of detail.members) {
              if (m.user_id !== currentUserId) {
                membersList.push({ member: m, teamName: t.name })
              }
            }
          }
        } catch {
          // Non-blocking
        }
      }
      setTeamMembers(membersList)
    } catch {
      // Offline fallback
    }
  }

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 10000)
    return () => clearInterval(interval)
  }, [currentUserId])

  const handleRemove = async (friendId: number) => {
    try {
      await removeFriend(friendId)
      toast.success('Freund entfernt')
      await loadData()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler'
      toast.error(msg)
    }
  }

  const acceptedFriends = useMemo(() => {
    return friends
      .filter((f) => f.status === 'accepted')
      .sort((a, b) => {
        const statusOrder: Record<string, number> = { online: 0, away: 1, invisible: 2 }
        const aStatus = a.presence?.status || 'invisible'
        const bStatus = b.presence?.status || 'invisible'
        const diff = (statusOrder[aStatus] ?? 3) - (statusOrder[bStatus] ?? 3)
        if (diff !== 0) return diff
        return a.username.localeCompare(b.username)
      })
  }, [friends])

  const filteredFriends = useMemo(() => {
    const q = searchQuery.toLowerCase().trim()
    return acceptedFriends.filter((f) => !q || f.username.toLowerCase().includes(q))
  }, [acceptedFriends, searchQuery])

  // Open direct chat
  const handleStartChatWithFriend = (f: FriendItem) => {
    setChatFriend(f)
    setIsChatOpen(true)
  }

  const handleStartChatWithTeamMember = (member: TeamMember) => {
    // Treat team member as friend item for E2EEChatModal
    const fauxFriend: FriendItem = {
      id: member.user_id,
      user_id: member.user_id,
      username: member.username,
      avatar_url: member.avatar_url,
      status: 'accepted',
      is_requester: false,
      created_at: member.joined_at,
      presence: null,
    }
    setChatFriend(fauxFriend)
    setIsChatOpen(true)
  }

  return (
    <>
      <Card
        className={`friends-list-dock border border-outline-variant/30 bg-surface-container-low/95 backdrop-blur-md shadow-2xl transition-all duration-200 overflow-hidden ${className}`}
      >
        {/* Header */}
        <CardHeader className="p-3 border-b border-outline-variant/20 bg-surface-container flex flex-row items-center justify-between">
          <div className="flex items-center gap-2">
            <Users className="w-4 h-4 text-primary" />
            <CardTitle className="font-headline text-body-sm font-bold text-primary">
              Kontakte & Chat
            </CardTitle>
            {incomingRequests.length > 0 && (
              <Badge variant="warning" className="text-[10px] px-1.5 py-0">
                {incomingRequests.length}
              </Badge>
            )}
          </div>

          <div className="flex items-center gap-1">
            {onClose && (
              <Button
                variant="ghost"
                size="icon"
                onClick={onClose}
                className="h-7 w-7 p-0 text-on-surface-variant hover:text-primary"
                aria-label="Schließen"
              >
                <X className="w-4 h-4" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setCollapsed(!collapsed)}
              className="h-7 w-7 p-0 text-on-surface-variant hover:text-primary"
              aria-label={collapsed ? 'Ausklappen' : 'Einklappen'}
            >
              {collapsed ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
            </Button>
          </div>
        </CardHeader>

        {!collapsed && (
          <CardContent className="p-3 space-y-2.5">
            {/* 2 Primary Tabs: Freunde vs Chats */}
            <div className="grid grid-cols-2 gap-1 bg-surface-container-high/60 p-1 rounded-xl border border-outline-variant/20">
              <button
                type="button"
                onClick={() => setPrimaryTab('friends')}
                className={`py-1 text-xs font-semibold rounded-lg flex items-center justify-center gap-1.5 transition-all ${
                  primaryTab === 'friends'
                    ? 'bg-primary text-on-primary shadow-sm'
                    : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest'
                }`}
              >
                <Users className="w-3.5 h-3.5" />
                <span>Freunde</span>
                {incomingRequests.length > 0 && (
                  <span className="ml-1 px-1 rounded-full bg-amber-500 text-[10px] text-white font-bold">
                    {incomingRequests.length}
                  </span>
                )}
              </button>

              <button
                type="button"
                onClick={() => setPrimaryTab('chats')}
                className={`py-1 text-xs font-semibold rounded-lg flex items-center justify-center gap-1.5 transition-all ${
                  primaryTab === 'chats'
                    ? 'bg-primary text-on-primary shadow-sm'
                    : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest'
                }`}
              >
                <MessageSquare className="w-3.5 h-3.5" />
                <span>Chats</span>
              </button>
            </div>

            {/* TAB: FREUNDE */}
            {primaryTab === 'friends' && (
              <div className="space-y-2">
                {/* Search Bar */}
                <div className="relative">
                  <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-variant/60" />
                  <Input
                    value={searchQuery}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) => setSearchQuery(e.target.value)}
                    placeholder="Freunde suchen …"
                    className="text-xs pl-8 h-7"
                  />
                </div>

                {/* Incoming Requests Hint (if any) */}
                {incomingRequests.length > 0 && (
                  <button
                    type="button"
                    onClick={() => navigate('/profile?tab=social')}
                    className="w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-[11px] text-amber-300 hover:bg-amber-500/15 transition-colors"
                  >
                    <span className="flex items-center gap-1.5 font-medium">
                      <UserPlus className="w-3.5 h-3.5" />
                      <span>{incomingRequests.length} offene Anfrage{incomingRequests.length > 1 ? 'n' : ''}</span>
                    </span>
                    <span className="text-[10px] underline">Im Profil</span>
                  </button>
                )}

                {/* Friends List directly below search */}
                <div className="space-y-1 max-h-64 overflow-y-auto pr-1">
                  {filteredFriends.length === 0 ? (
                    <div className="py-8 text-center space-y-1">
                      <p className="text-xs text-on-surface-variant/70">
                        {searchQuery ? 'Keine Treffer für die Suche.' : 'Noch keine Freunde in der Liste.'}
                      </p>
                      {!searchQuery && (
                        <button
                          type="button"
                          onClick={() => navigate('/profile?tab=social')}
                          className="text-[11px] text-primary hover:underline inline-flex items-center gap-1"
                        >
                          <UserPlus className="w-3 h-3" />
                          <span>Freunde im Profil hinzufügen</span>
                        </button>
                      )}
                    </div>
                  ) : (
                    filteredFriends.map((f) => (
                      <div
                        key={f.id}
                        className="flex items-center justify-between p-2 rounded-lg hover:bg-surface-container-high/60 transition-colors group"
                      >
                        <div className="flex items-center gap-2.5 min-w-0">
                          <div className="relative shrink-0">
                            <Avatar src={f.avatar_url} name={f.username} size="sm" />
                            <StatusDot
                              status={f.presence?.status || 'invisible'}
                              size="sm"
                              className="absolute bottom-0 right-0"
                            />
                          </div>
                          <div className="min-w-0">
                            <div className="flex items-center gap-1.5">
                              <span className="text-xs font-semibold text-primary truncate">
                                {f.username}
                              </span>
                              <DeviceBadge deviceType={f.presence?.device_type} />
                            </div>
                            <p className="text-[10px] text-on-surface-variant/80 truncate">
                              {f.presence?.activity_label || (
                                f.presence?.status === 'online'
                                  ? 'Online'
                                  : f.presence?.status === 'away'
                                  ? 'Abwesend'
                                  : 'Offline'
                              )}
                            </p>
                          </div>
                        </div>

                        <div className="flex items-center gap-1 shrink-0 opacity-80 group-hover:opacity-100 transition-opacity">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleStartChatWithFriend(f)}
                            className="h-7 w-7 p-0 text-primary hover:bg-primary/15"
                            title="Nachricht senden"
                            aria-label="Nachricht senden"
                          >
                            <MessageSquare className="w-3.5 h-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => void handleRemove(f.user_id ?? f.id)}
                            className="h-7 w-7 p-0 text-on-surface-variant hover:text-rose-400 hover:bg-rose-500/10"
                            title="Freund entfernen"
                            aria-label="Freund entfernen"
                          >
                            <UserMinus className="w-3 h-3" />
                          </Button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}

            {/* TAB: CHATS */}
            {primaryTab === 'chats' && (
              <div className="space-y-2">
                {/* Full Chat Room Launcher */}
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => navigate('/chat')}
                  className="w-full text-xs h-7.5 gap-1.5 justify-center"
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  <span>Großen Chatraum öffnen</span>
                </Button>

                {/* Recent Contacts & Team Members */}
                <div className="space-y-1 max-h-60 overflow-y-auto pr-1">
                  <div className="text-[10px] font-bold text-on-surface-variant/70 uppercase tracking-wider px-1 pt-1">
                    Direkte Kontakte
                  </div>

                  {acceptedFriends.map((f) => (
                    <div
                      key={`chat-friend-${f.id}`}
                      onClick={() => handleStartChatWithFriend(f)}
                      className="flex items-center justify-between p-2 rounded-lg hover:bg-surface-container-high/60 transition-colors cursor-pointer group"
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        <div className="relative shrink-0">
                          <Avatar src={f.avatar_url} name={f.username} size="sm" />
                          <StatusDot
                            status={f.presence?.status || 'invisible'}
                            size="sm"
                            className="absolute bottom-0 right-0"
                          />
                        </div>
                        <div className="min-w-0">
                          <span className="text-xs font-semibold text-primary truncate block">
                            {f.username}
                          </span>
                          <span className="text-[10px] text-on-surface-variant/70 flex items-center gap-1">
                            <DeviceBadge deviceType={f.presence?.device_type} />
                            <span>Freund</span>
                          </span>
                        </div>
                      </div>

                      <MessageSquare className="w-3.5 h-3.5 text-primary opacity-60 group-hover:opacity-100" />
                    </div>
                  ))}

                  {/* Team Members without prior friendship */}
                  {teamMembers.length > 0 && (
                    <>
                      <div className="text-[10px] font-bold text-on-surface-variant/70 uppercase tracking-wider px-1 pt-2">
                        Teammitglieder
                      </div>
                      {teamMembers.map(({ member, teamName }) => (
                        <div
                          key={`chat-team-${member.user_id}`}
                          onClick={() => handleStartChatWithTeamMember(member)}
                          className="flex items-center justify-between p-2 rounded-lg hover:bg-surface-container-high/60 transition-colors cursor-pointer group"
                        >
                          <div className="flex items-center gap-2.5 min-w-0">
                            <Avatar src={member.avatar_url} name={member.username} size="sm" />
                            <div className="min-w-0">
                              <span className="text-xs font-semibold text-primary truncate block">
                                {member.username}
                              </span>
                              <span className="text-[10px] text-tertiary flex items-center gap-1">
                                <UsersRound className="w-2.5 h-2.5" />
                                <span>{teamName}</span>
                              </span>
                            </div>
                          </div>

                          <MessageSquare className="w-3.5 h-3.5 text-primary opacity-60 group-hover:opacity-100" />
                        </div>
                      ))}
                    </>
                  )}
                </div>
              </div>
            )}
          </CardContent>
        )}
      </Card>

      {/* E2EE Chat Modal */}
      {user && (
        <E2EEChatModal
          open={isChatOpen}
          onOpenChange={setIsChatOpen}
          currentUserId={user.id}
          friend={chatFriend}
        />
      )}
    </>
  )
}
