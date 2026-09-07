import { useState, useEffect } from 'react'
import {
  Dialog,
  DialogContent,
  Button,
  Badge,
  Avatar,
  Dropdown,
  type DropdownOption,
} from '@/Singra/UI'
import { Switch } from '@/components/ui/Switch'
import {
  Shield,
  Users,
  UserMinus,
  Settings2,
  Check,
  X,
} from 'lucide-react'
import {
  type ChatGroupItem,
  type ChatGroupMemberItem,
  getGroupMembers,
  updateGroupMemberRole,
  kickGroupMember,
  updateGroupPermissions,
} from '@/api/social'
import { toast } from '@/stores/toastStore'

interface GroupPermissionsModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  group: ChatGroupItem | null
  currentUserId: number
  onGroupUpdated?: (group: ChatGroupItem) => void
}

const ROLE_OPTIONS: DropdownOption[] = [
  { value: 'admin', label: 'Administrator' },
  { value: 'moderator', label: 'Moderator' },
  { value: 'member', label: 'Mitglied (@everyone)' },
]

export function GroupPermissionsModal({
  open,
  onOpenChange,
  group,
  currentUserId,
  onGroupUpdated,
}: GroupPermissionsModalProps) {
  const [activeTab, setActiveTab] = useState<'members' | 'permissions'>('members')
  const [members, setMembers] = useState<ChatGroupMemberItem[]>([])
  const [loading, setLoading] = useState(false)
  const [savingPermissions, setSavingPermissions] = useState(false)

  // Standard permissions (@everyone)
  const [canSendMessages, setCanSendMessages] = useState(true)
  const [canInviteMembers, setCanInviteMembers] = useState(true)
  const [canDeleteMessages, setCanDeleteMessages] = useState(false)
  const [canKickMembers, setCanKickMembers] = useState(false)

  const isOwner = group?.owner_user_id === currentUserId
  const currentUserRole = group?.role || (isOwner ? 'owner' : 'member')
  const canManage = isOwner || currentUserRole === 'admin'

  useEffect(() => {
    if (open && group) {
      void loadMembers()
      const defPerms = (group.default_permissions || 'send_messages,invite_members').split(',')
      setCanSendMessages(defPerms.includes('send_messages'))
      setCanInviteMembers(defPerms.includes('invite_members'))
      setCanDeleteMessages(defPerms.includes('delete_messages'))
      setCanKickMembers(defPerms.includes('kick_members'))
    }
  }, [open, group?.id, group?.default_permissions])

  const loadMembers = async () => {
    if (!group) return
    setLoading(true)
    try {
      const data = await getGroupMembers(group.id)
      setMembers(data)
    } catch {
      // Fallback zu den bereits in group vorhandenen Mitgliedern
      if (group.members) setMembers(group.members)
    } finally {
      setLoading(false)
    }
  }

  const handleRoleChange = async (member: ChatGroupMemberItem, newRole: string) => {
    if (!group) return
    try {
      const updated = await updateGroupMemberRole(
        group.id,
        member.user_id,
        newRole as 'admin' | 'moderator' | 'member'
      )
      setMembers((prev) =>
        prev.map((m) => (m.user_id === member.user_id ? { ...m, role: updated.role } : m))
      )
      toast.success(`Rolle von ${member.username} auf "${newRole}" aktualisiert.`)
    } catch (err: any) {
      toast.error(err?.message || 'Rolle konnte nicht geändert werden.')
    }
  }

  const handleKickMember = async (member: ChatGroupMemberItem) => {
    if (!group) return
    try {
      await kickGroupMember(group.id, member.user_id)
      setMembers((prev) => prev.filter((m) => m.user_id !== member.user_id))
      toast.success(`${member.username} wurde aus der Gruppe entfernt.`)
    } catch (err: any) {
      toast.error(err?.message || 'Mitglied konnte nicht entfernt werden.')
    }
  }

  const handleSaveDefaultPermissions = async () => {
    if (!group) return
    setSavingPermissions(true)
    const perms: string[] = []
    if (canSendMessages) perms.push('send_messages')
    if (canInviteMembers) perms.push('invite_members')
    if (canDeleteMessages) perms.push('delete_messages')
    if (canKickMembers) perms.push('kick_members')

    const permString = perms.join(',')
    try {
      const updated = await updateGroupPermissions(group.id, permString)
      toast.success('Standard-Gruppenrechte (@everyone) gespeichert.')
      if (onGroupUpdated) onGroupUpdated(updated)
    } catch (err: any) {
      toast.error(err?.message || 'Gruppenrechte konnten nicht gespeichert werden.')
    } finally {
      setSavingPermissions(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="max-w-xl p-0 overflow-hidden bg-surface border-outline-variant/30 flex flex-col max-h-[85vh]"
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-outline-variant/20 bg-surface-container/60 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
              <Shield className="w-4 h-4" />
            </div>
            <div>
              <h2 className="font-headline text-body-md font-bold text-primary">
                Gruppen-Rollen & Rechte
              </h2>
              <p className="text-[11px] text-on-surface-variant">
                {group?.name || 'Gruppe'} • {members.length} Mitglieder
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="p-1 rounded-md text-on-surface-variant hover:text-on-surface"
            aria-label="Schließen"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Tab Switcher */}
        <div className="flex border-b border-outline-variant/15 px-4 pt-2 bg-surface-container-low/40 gap-2">
          <button
            type="button"
            onClick={() => setActiveTab('members')}
            className={`pb-2.5 px-3 text-xs font-semibold flex items-center gap-1.5 border-b-2 transition-colors ${
              activeTab === 'members'
                ? 'border-primary text-primary'
                : 'border-transparent text-on-surface-variant hover:text-on-surface'
            }`}
          >
            <Users className="w-3.5 h-3.5" />
            <span>Mitglieder & Rollen ({members.length})</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('permissions')}
            className={`pb-2.5 px-3 text-xs font-semibold flex items-center gap-1.5 border-b-2 transition-colors ${
              activeTab === 'permissions'
                ? 'border-primary text-primary'
                : 'border-transparent text-on-surface-variant hover:text-on-surface'
            }`}
          >
            <Settings2 className="w-3.5 h-3.5" />
            <span>Standardrechte (@everyone)</span>
          </button>
        </div>

        {/* Tab Content */}
        <div className="p-4 flex-1 overflow-y-auto space-y-3">
          {activeTab === 'members' && (
            <div className="space-y-2">
              <p className="text-[11px] text-on-surface-variant/80">
                Verwalte Berechtigungsrollen für Gruppenmitglieder. Administratoren und Moderatoren besitzen erweiterte Befugnisse.
              </p>

              {loading ? (
                <div className="py-12 text-center text-xs text-on-surface-variant">
                  Mitglieder werden geladen …
                </div>
              ) : (
                <div className="divide-y divide-outline-variant/15 rounded-xl border border-outline-variant/20 bg-surface-container-lowest/70 overflow-hidden">
                  {members.map((member) => {
                    const isMemberOwner = member.role === 'owner' || member.user_id === group?.owner_user_id
                    const isSelf = member.user_id === currentUserId
                    const canEditThisMember =
                      canManage &&
                      !isMemberOwner &&
                      (isOwner || member.role !== 'admin')

                    return (
                      <div
                        key={`grp-mem-${member.user_id}`}
                        className="p-3 flex items-center justify-between gap-3 hover:bg-surface-container-high/30 transition-colors"
                      >
                        <div className="flex items-center gap-3 min-w-0">
                          <Avatar src={member.avatar_url} name={member.username} size="sm" />
                          <div className="min-w-0">
                            <div className="flex items-center gap-1.5">
                              <span className="text-xs font-semibold text-primary truncate">
                                {member.username}
                              </span>
                              {isSelf && (
                                <span className="text-[10px] text-on-surface-variant/70 font-normal">
                                  (Du)
                                </span>
                              )}
                            </div>
                            <div className="flex items-center gap-1.5 mt-0.5">
                              <Badge
                                variant={
                                  isMemberOwner
                                    ? 'warning'
                                    : member.role === 'admin'
                                    ? 'info'
                                    : member.role === 'moderator'
                                    ? 'default'
                                    : 'default'
                                }
                                className="text-[10px] py-0 px-1.5 font-medium"
                              >
                                {isMemberOwner
                                  ? '👑 Eigentümer'
                                  : member.role === 'admin'
                                  ? '🛡️ Administrator'
                                  : member.role === 'moderator'
                                  ? '⚔️ Moderator'
                                  : 'Mitglied (@everyone)'}
                              </Badge>
                            </div>
                          </div>
                        </div>

                        {/* Controls */}
                        <div className="flex items-center gap-2 shrink-0">
                          {canEditThisMember && (
                            <>
                              <div className="w-36">
                                <Dropdown
                                  value={member.role}
                                  onChange={(val) => void handleRoleChange(member, val)}
                                  options={ROLE_OPTIONS}
                                />
                              </div>
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                onClick={() => void handleKickMember(member)}
                                className="h-8 w-8 p-0 text-error hover:bg-error/10"
                                title="Aus Gruppe entfernen"
                                aria-label={`${member.username} aus Gruppe entfernen`}
                              >
                                <UserMinus className="w-4 h-4" />
                              </Button>
                            </>
                          )}
                          {!canEditThisMember && isMemberOwner && (
                            <span className="text-[11px] text-on-surface-variant/70 font-medium px-2">
                              Gruppenleiter
                            </span>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {activeTab === 'permissions' && (
            <div className="space-y-4">
              <div className="p-3 rounded-xl bg-surface-container/60 border border-outline-variant/25 text-xs text-on-surface-variant space-y-1">
                <span className="font-semibold text-primary block">
                  Rechte für die @everyone-Rolle
                </span>
                <p className="text-[11px]">
                  Diese Einstellungen gelten für alle regulären Gruppenmitglieder. Administratoren und Moderatoren behalten ihre erweiterten Befugnisse.
                </p>
              </div>

              <div className="space-y-3 rounded-xl border border-outline-variant/20 p-4 bg-surface-container-lowest/80">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <span className="text-xs font-semibold text-primary block">
                      Nachrichten senden
                    </span>
                    <span className="text-[11px] text-on-surface-variant">
                      Erlaubt regulären Mitgliedern das Schreiben von Text-, Foto- und Dateinachrichten.
                    </span>
                  </div>
                  <Switch
                    checked={canSendMessages}
                    onCheckedChange={setCanSendMessages}
                    disabled={!canManage}
                    aria-label="Nachrichten senden erlauben"
                  />
                </div>

                <div className="flex items-center justify-between gap-3 pt-2 border-t border-outline-variant/15">
                  <div>
                    <span className="text-xs font-semibold text-primary block">
                      Neue Mitglieder einladen
                    </span>
                    <span className="text-[11px] text-on-surface-variant">
                      Erlaubt das Teilen und Verwenden des öffentlichen Gruppen-Einladungslinks.
                    </span>
                  </div>
                  <Switch
                    checked={canInviteMembers}
                    onCheckedChange={setCanInviteMembers}
                    disabled={!canManage}
                    aria-label="Mitglieder einladen erlauben"
                  />
                </div>

                <div className="flex items-center justify-between gap-3 pt-2 border-t border-outline-variant/15">
                  <div>
                    <span className="text-xs font-semibold text-primary block">
                      Nachrichten löschen
                    </span>
                    <span className="text-[11px] text-on-surface-variant">
                      Erlaubt Mitgliedern das Entfernen von Nachrichten anderer Benutzer.
                    </span>
                  </div>
                  <Switch
                    checked={canDeleteMessages}
                    onCheckedChange={setCanDeleteMessages}
                    disabled={!canManage}
                    aria-label="Nachrichten löschen erlauben"
                  />
                </div>

                <div className="flex items-center justify-between gap-3 pt-2 border-t border-outline-variant/15">
                  <div>
                    <span className="text-xs font-semibold text-primary block">
                      Mitglieder entfernen (Kicken)
                    </span>
                    <span className="text-[11px] text-on-surface-variant">
                      Erlaubt Mitgliedern das Entfernen anderer regulärer Gruppenmitglieder.
                    </span>
                  </div>
                  <Switch
                    checked={canKickMembers}
                    onCheckedChange={setCanKickMembers}
                    disabled={!canManage}
                    aria-label="Mitglieder kicken erlauben"
                  />
                </div>
              </div>

              {canManage && (
                <div className="flex justify-end pt-2">
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    disabled={savingPermissions}
                    onClick={() => void handleSaveDefaultPermissions()}
                    className="gap-1.5"
                  >
                    <Check className="w-4 h-4" />
                    <span>{savingPermissions ? 'Wird gespeichert …' : 'Standardrechte speichern'}</span>
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
