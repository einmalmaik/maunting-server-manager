import React, { useState, useEffect } from 'react'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Button,
  Input,
  Badge,
} from '@/Singra/UI'
import {
  Users,
  MessageSquare,
  UserPlus,
  Check,
  X,
  Trophy,
  ChevronDown,
  ChevronUp,
  Radio,
  UserMinus,
} from 'lucide-react'
import { StatusDot, StatusSwitcher, type PresenceStatus } from './StatusIndicator'
import { DeviceBadge } from './DeviceBadge'
import { E2EEChatModal } from './E2EEChatModal'
import { AchievementsModal } from './AchievementsModal'
import {
  type FriendItem,
  getFriends,
  getFriendRequests,
  sendFriendRequest,
  acceptFriendRequest,
  declineFriendRequest,
  removeFriend,
  updatePresence,
} from '@/api/social'
import { detectDeviceType } from '@/hooks/usePresenceAndActivity'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'

interface FriendsListDockProps {
  collapsedDefault?: boolean
  className?: string
}

export function FriendsListDock({ collapsedDefault = false, className = '' }: FriendsListDockProps) {
  const { user } = useAuthStore()
  const [friends, setFriends] = useState<FriendItem[]>([])
  const [incomingRequests, setIncomingRequests] = useState<FriendItem[]>([])
  const [collapsed, setCollapsed] = useState(collapsedDefault)
  const [loading, setLoading] = useState(false)
  const [addUsername, setAddUsername] = useState('')
  const [activeTab, setActiveTab] = useState<'friends' | 'requests' | 'add'>('friends')
  const [myStatus, setMyStatus] = useState<PresenceStatus>('online')

  // Chat & Achievements Modals
  const [chatFriend, setChatFriend] = useState<FriendItem | null>(null)
  const [isChatOpen, setIsChatOpen] = useState(false)
  const [isAchievementsOpen, setIsAchievementsOpen] = useState(false)

  const loadFriends = async () => {
    try {
      const [friendsData, reqsData] = await Promise.all([
        getFriends(),
        getFriendRequests().catch(() => ({ incoming: [], outgoing: [] })),
      ])
      setFriends(friendsData)
      setIncomingRequests(reqsData.incoming)
    } catch {
      // Offline / network fallback
    }
  }

  useEffect(() => {
    loadFriends()
    const interval = setInterval(loadFriends, 10000)
    return () => clearInterval(interval)
  }, [])

  const handleStatusChange = async (newStatus: PresenceStatus) => {
    setMyStatus(newStatus)
    try {
      await updatePresence({
        status: newStatus,
        device_type: detectDeviceType(),
      })
    } catch {
      // Non-blocking
    }
  }

  const handleSendRequest = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!addUsername.trim()) return
    setLoading(true)
    try {
      const res = await sendFriendRequest(addUsername.trim())
      toast.success(res.message || 'Freundschaftsanfrage gesendet')
      setAddUsername('')
      setActiveTab('friends')
      await loadFriends()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler beim Senden'
      toast.error(msg)
    } finally {
      setLoading(false)
    }
  }

  const handleAccept = async (reqId: number) => {
    try {
      await acceptFriendRequest(reqId)
      toast.success('Freundschaftsanfrage angenommen')
      await loadFriends()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler'
      toast.error(msg)
    }
  }

  const handleDecline = async (reqId: number) => {
    try {
      await declineFriendRequest(reqId)
      toast.success('Freundschaftsanfrage abgelehnt')
      await loadFriends()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler'
      toast.error(msg)
    }
  }

  const handleRemove = async (friendId: number) => {
    try {
      await removeFriend(friendId)
      toast.success('Freund entfernt')
      await loadFriends()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Fehler'
      toast.error(msg)
    }
  }

  const acceptedFriends = friends.filter((f) => f.status === 'accepted')

  return (
    <>
      <Card className={`friends-list-dock border border-outline-variant/30 bg-surface-container-low/95 backdrop-blur-md shadow-xl transition-all duration-200 overflow-hidden ${className}`}>
        {/* Header */}
        <CardHeader className="p-3 border-b border-outline-variant/20 bg-surface-container flex flex-row items-center justify-between">
          <div className="flex items-center gap-2">
            <Users className="w-4 h-4 text-primary" />
            <CardTitle className="font-headline text-body-sm font-bold text-primary">
              Freunde & Social Hub
            </CardTitle>
            {incomingRequests.length > 0 && (
              <Badge variant="warning" className="text-[10px] px-1.5 py-0">
                {incomingRequests.length}
              </Badge>
            )}
          </div>

          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsAchievementsOpen(true)}
              className="h-7 w-7 p-0 text-amber-400 hover:text-amber-300"
              aria-label="Errungenschaften & Prestige"
            >
              <Trophy className="w-3.5 h-3.5" />
            </Button>
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
          <CardContent className="p-3 space-y-3">
            {/* Own Status Switcher */}
            <div className="flex items-center justify-between gap-2 p-2 rounded-lg bg-surface-container-high/40 border border-outline-variant/20">
              <div className="flex items-center gap-2">
                <StatusDot status={myStatus} size="sm" />
                <span className="text-xs font-medium text-primary">Mein Status</span>
              </div>
              <StatusSwitcher currentStatus={myStatus} onChange={handleStatusChange} className="w-32" />
            </div>

            {/* Sub-tabs */}
            <div className="flex items-center gap-1 border-b border-outline-variant/20 pb-2">
              <Button
                variant={activeTab === 'friends' ? 'primary' : 'ghost'}
                size="sm"
                onClick={() => setActiveTab('friends')}
                className="text-xs h-6 px-2.5"
              >
                Freunde ({acceptedFriends.length})
              </Button>
              <Button
                variant={activeTab === 'requests' ? 'primary' : 'ghost'}
                size="sm"
                onClick={() => setActiveTab('requests')}
                className="text-xs h-6 px-2.5 relative"
              >
                Anfragen
                {incomingRequests.length > 0 && (
                  <span className="ml-1 px-1 rounded-full bg-amber-500 text-[10px] text-white font-bold">
                    {incomingRequests.length}
                  </span>
                )}
              </Button>
              <Button
                variant={activeTab === 'add' ? 'primary' : 'ghost'}
                size="icon"
                onClick={() => setActiveTab('add')}
                className="text-xs h-6 w-6 p-0 ml-auto"
                aria-label="Freund hinzufügen"
              >
                <UserPlus className="w-3 h-3" />
              </Button>
            </div>

            {/* Tab: Friends List */}
            {activeTab === 'friends' && (
              <div className="space-y-1 max-h-60 overflow-y-auto pr-1">
                {acceptedFriends.length === 0 ? (
                  <p className="py-6 text-center text-xs text-on-surface-variant/70">
                    Noch keine Freunde in der Liste.
                  </p>
                ) : (
                  acceptedFriends.map((f) => (
                    <div
                      key={f.id}
                      className="flex items-center justify-between p-2 rounded-lg hover:bg-surface-container-high/60 transition-colors group"
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        <div className="relative shrink-0">
                          <div className="w-8 h-8 rounded-full bg-primary/15 flex items-center justify-center text-xs font-bold text-primary">
                            {f.username.slice(0, 2).toUpperCase()}
                          </div>
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
                          {f.presence?.activity_label && (
                            <p className="text-[10px] text-on-surface-variant/80 truncate flex items-center gap-1">
                              <Radio className="w-2.5 h-2.5 text-emerald-400 animate-pulse" />
                              <span>{f.presence.activity_label}</span>
                            </p>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-1 shrink-0 opacity-80 group-hover:opacity-100 transition-opacity">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => {
                            setChatFriend(f)
                            setIsChatOpen(true)
                          }}
                          className="h-7 w-7 p-0 text-primary hover:bg-primary/15"
                          aria-label="E2EE Direktnachricht"
                        >
                          <MessageSquare className="w-3.5 h-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => void handleRemove(f.user_id ?? f.id)}
                          className="h-7 w-7 p-0 text-on-surface-variant hover:text-rose-400 hover:bg-rose-500/10"
                          aria-label="Freund entfernen"
                        >
                          <UserMinus className="w-3 h-3" />
                        </Button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}

            {/* Tab: Requests */}
            {activeTab === 'requests' && (
              <div className="space-y-2 max-h-60 overflow-y-auto">
                {incomingRequests.length === 0 ? (
                  <p className="py-6 text-center text-xs text-on-surface-variant/70">
                    Keine ausstehenden Freundschaftsanfragen.
                  </p>
                ) : (
                  incomingRequests.map((req) => (
                    <div
                      key={req.id}
                      className="flex items-center justify-between p-2 rounded-lg bg-surface-container-high/40 border border-outline-variant/20"
                    >
                      <span className="text-xs font-medium text-primary truncate">
                        {req.username}
                      </span>
                      <div className="flex items-center gap-1">
                        <Button
                          variant="primary"
                          size="icon"
                          onClick={() => void handleAccept(req.id)}
                          className="h-6 w-6 p-0"
                          aria-label="Annehmen"
                        >
                          <Check className="w-3 h-3" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => void handleDecline(req.id)}
                          className="h-6 w-6 p-0 text-rose-400 hover:bg-rose-500/10"
                          aria-label="Ablehnen"
                        >
                          <X className="w-3 h-3" />
                        </Button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}

            {/* Tab: Add Friend */}
            {activeTab === 'add' && (
              <form onSubmit={handleSendRequest} className="space-y-2">
                <Input
                  value={addUsername}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setAddUsername(e.target.value)}
                  placeholder="Benutzername eingeben …"
                  className="text-xs"
                  disabled={loading}
                />
                <Button
                  type="submit"
                  size="sm"
                  disabled={!addUsername.trim() || loading}
                  className="w-full text-xs h-7 gap-1"
                >
                  <UserPlus className="w-3 h-3" />
                  <span>Anfrage senden</span>
                </Button>
              </form>
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

      {/* Achievements Modal */}
      <AchievementsModal
        open={isAchievementsOpen}
        onOpenChange={setIsAchievementsOpen}
      />
    </>
  )
}
