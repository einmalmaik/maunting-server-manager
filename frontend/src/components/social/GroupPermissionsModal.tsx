import { useState, useEffect } from 'react'
import {
  Dialog,
  DialogContent,
  Button,
  Badge,
  Avatar,
  Dropdown,
  Input,
  type DropdownOption,
} from '@/Singra/UI'
import { Switch } from '@/components/ui/Switch'
import {
  Shield,
  Users,
  UserMinus,
  Check,
  X,
  Plus,
  Pencil,
  Trash2,
  ShieldCheck,
  Sliders,
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
import { confirm } from '@/stores/confirmStore'

interface GroupPermissionsModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  group: ChatGroupItem | null
  currentUserId: number
  onGroupUpdated?: (group: ChatGroupItem) => void
}

export interface GroupRoleDefinition {
  id: string
  name: string
  description: string
  is_system: boolean
  permissions: string[]
}

export const GROUP_PERMISSION_DEFINITIONS = [
  {
    key: 'send_messages',
    title: 'Nachrichten senden',
    desc: 'Erlaubt das Schreiben und Senden von Text-, Foto- und Dateinachrichten.',
    category: 'chat',
  },
  {
    key: 'attach_media',
    title: 'Medien & Dokumente anhängen',
    desc: 'Fotos, Dokumente, Notizen und Kalendereinträge im Gruppenchat teilen.',
    category: 'chat',
  },
  {
    key: 'invite_members',
    title: 'Neue Mitglieder einladen',
    desc: 'Erlaubt das Teilen und Verwenden des Gruppen-Einladungslinks.',
    category: 'members',
  },
  {
    key: 'kick_members',
    title: 'Mitglieder entfernen (Kicken)',
    desc: 'Mitglieder mit niedrigerem Rang aus der Gruppe entfernen.',
    category: 'moderation',
  },
  {
    key: 'delete_messages',
    title: 'Nachrichten moderieren & löschen',
    desc: 'Nachrichten anderer Gruppenmitglieder im Gruppenchat entfernen.',
    category: 'moderation',
  },
  {
    key: 'manage_roles',
    title: 'Rollen zuweisen & verwalten',
    desc: 'Mitgliedern Rollen zuweisen und Standard-Gruppenrechte anpassen.',
    category: 'administration',
  },
]

const SYSTEM_GROUP_ROLES: GroupRoleDefinition[] = [
  {
    id: 'owner',
    name: 'Eigentümer',
    description: 'Uneingeschränkte Vollberechtigung über die Gruppe, Rollen und Mitglieder.',
    is_system: true,
    permissions: GROUP_PERMISSION_DEFINITIONS.map((p) => p.key),
  },
  {
    id: 'admin',
    name: 'Administrator',
    description: 'Kann Mitglieder kicken, Nachrichten moderieren und Rollen vergeben.',
    is_system: true,
    permissions: ['send_messages', 'attach_media', 'invite_members', 'kick_members', 'delete_messages', 'manage_roles'],
  },
  {
    id: 'moderator',
    name: 'Moderator',
    description: 'Kann Nachrichten entfernen, Einladungen versenden und Regelverstöße ahnden.',
    is_system: true,
    permissions: ['send_messages', 'attach_media', 'invite_members', 'delete_messages'],
  },
  {
    id: 'member',
    name: 'Mitglied (@everyone)',
    description: 'Reguläres Mitglied. Berechtigungen richten sich nach den Standardrechten.',
    is_system: true,
    permissions: ['send_messages', 'attach_media', 'invite_members'],
  },
]

const ROLE_OPTIONS: DropdownOption[] = [
  { value: 'admin', label: 'Administrator' },
  { value: 'moderator', label: 'Moderator' },
  { value: 'member', label: 'Mitglied (@everyone)' },
]

interface GroupRoleFormProps {
  initial: GroupRoleDefinition | null
  onSubmit: (name: string, description: string, permissions: string[]) => Promise<void>
  onCancel: () => void
  disabled?: boolean
}

function GroupRoleForm({ initial, onSubmit, onCancel, disabled }: GroupRoleFormProps) {
  const isSystemRole = Boolean(initial?.is_system)
  const isOwnerRole = initial?.id === 'owner'
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [selectedPerms, setSelectedPerms] = useState<Set<string>>(
    new Set(initial?.permissions ?? ['send_messages', 'attach_media'])
  )
  const [saving, setSaving] = useState(false)

  const togglePerm = (key: string) => {
    if (isOwnerRole) return
    setSelectedPerms((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const handleSelectAll = () => {
    if (isOwnerRole) return
    setSelectedPerms(new Set(GROUP_PERMISSION_DEFINITIONS.map((p) => p.key)))
  }

  const handleDeselectAll = () => {
    if (isOwnerRole) return
    setSelectedPerms(new Set())
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    setSaving(true)
    try {
      await onSubmit(name.trim(), description.trim(), Array.from(selectedPerms))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5 rounded-2xl border border-outline-variant/30 bg-surface-container/60 p-5">
      <div className="flex items-center justify-between border-b border-outline-variant/20 pb-3">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-primary" />
          <h3 className="font-headline text-body-md font-bold text-primary">
            {initial ? `Rolle bearbeiten: ${initial.name}` : 'Neue Gruppenrolle erstellen'}
          </h3>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="p-1 rounded-md text-on-surface-variant hover:text-on-surface transition-colors"
          aria-label="Schließen"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {isOwnerRole && (
        <div className="p-3 rounded-xl bg-status-warning/10 border border-status-warning/30 text-xs text-status-warning">
          Die Eigentümer-Rolle besitzt feste Vollberechtigung und kann nicht eingeschränkt werden.
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-semibold text-on-surface-variant mb-1.5 uppercase tracking-wider">
            Rollen-Name
          </label>
          <Input
            type="text"
            value={name}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setName(e.target.value)}
            placeholder="z.B. Event-Leiter"
            disabled={isSystemRole || disabled}
            required
            className="text-xs h-9"
          />
        </div>
        <div>
          <label className="block text-xs font-semibold text-on-surface-variant mb-1.5 uppercase tracking-wider">
            Beschreibung
          </label>
          <Input
            type="text"
            value={description}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDescription(e.target.value)}
            placeholder="Aufgaben und Verantwortungsbereich"
            disabled={disabled}
            className="text-xs h-9"
          />
        </div>
      </div>

      {/* Permissions Picker */}
      <div className="space-y-3 pt-2">
        <div className="flex items-center justify-between">
          <span className="block text-xs font-semibold text-on-surface-variant uppercase tracking-wider">
            Berechtigungen ({selectedPerms.size} von {GROUP_PERMISSION_DEFINITIONS.length})
          </span>
          {!isOwnerRole && (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleSelectAll}
                className="text-[11px] font-medium text-primary hover:underline"
              >
                Alle auswählen
              </button>
              <span className="text-on-surface-variant/40">•</span>
              <button
                type="button"
                onClick={handleDeselectAll}
                className="text-[11px] font-medium text-on-surface-variant hover:text-on-surface"
              >
                Auswahl aufheben
              </button>
            </div>
          )}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          {GROUP_PERMISSION_DEFINITIONS.map((def) => {
            const isChecked = selectedPerms.has(def.key)
            return (
              <label
                key={`perm-toggle-${def.key}`}
                className={`flex items-start gap-3 p-3 rounded-xl border transition-all cursor-pointer select-none ${
                  isChecked
                    ? 'border-primary/40 bg-primary/10 shadow-xs'
                    : 'border-outline-variant/20 bg-surface-container-lowest/60 hover:bg-surface-container-high/40'
                } ${isOwnerRole ? 'opacity-80 cursor-not-allowed' : ''}`}
              >
                <div className="pt-0.5">
                  <input
                    type="checkbox"
                    checked={isChecked}
                    onChange={() => togglePerm(def.key)}
                    disabled={isOwnerRole || disabled}
                    className="w-4 h-4 rounded border-outline-variant text-primary focus:ring-primary"
                  />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-semibold text-primary">{def.title}</div>
                  <div className="text-[11px] text-on-surface-variant/80 mt-0.5 leading-snug">
                    {def.desc}
                  </div>
                </div>
              </label>
            )
          })}
        </div>
      </div>

      <div className="flex justify-end gap-2.5 pt-3 border-t border-outline-variant/20">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={saving}>
          Abbrechen
        </Button>
        <Button
          type="submit"
          variant="primary"
          size="sm"
          disabled={saving || isOwnerRole || disabled || !name.trim()}
          className="gap-1.5"
        >
          <Check className="w-4 h-4" />
          <span>{saving ? 'Wird gespeichert …' : 'Rolle speichern'}</span>
        </Button>
      </div>
    </form>
  )
}

export function GroupPermissionsModal({
  open,
  onOpenChange,
  group,
  currentUserId,
  onGroupUpdated,
}: GroupPermissionsModalProps) {
  const [activeTab, setActiveTab] = useState<'members' | 'roles' | 'permissions'>('members')
  const [members, setMembers] = useState<ChatGroupMemberItem[]>([])
  const [loading, setLoading] = useState(false)
  const [savingPermissions, setSavingPermissions] = useState(false)

  // Custom roles state
  const [roles, setRoles] = useState<GroupRoleDefinition[]>(SYSTEM_GROUP_ROLES)
  const [editingRole, setEditingRole] = useState<GroupRoleDefinition | null>(null)
  const [isCreatingRole, setIsCreatingRole] = useState(false)

  // Standard permissions (@everyone)
  const [canSendMessages, setCanSendMessages] = useState(true)
  const [canAttachMedia, setCanAttachMedia] = useState(true)
  const [canInviteMembers, setCanInviteMembers] = useState(true)
  const [canDeleteMessages, setCanDeleteMessages] = useState(false)
  const [canKickMembers, setCanKickMembers] = useState(false)

  const isOwner = group?.owner_user_id === currentUserId
  const currentUserRole = group?.role || (isOwner ? 'owner' : 'member')
  const canManage = isOwner || currentUserRole === 'admin'

  useEffect(() => {
    if (open && group) {
      void loadMembers()
      const defPerms = (group.default_permissions || 'send_messages,attach_media,invite_members').split(',')
      setCanSendMessages(defPerms.includes('send_messages'))
      setCanAttachMedia(defPerms.includes('attach_media'))
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
    const ok = await confirm({
      title: 'Mitglied entfernen',
      message: `Möchtest du ${member.username} wirklich aus der Gruppe entfernen?`,
      confirmText: 'Entfernen',
      danger: true,
    })
    if (!ok) return

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
    if (canAttachMedia) perms.push('attach_media')
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

  const handleCreateRole = async (name: string, description: string, permissions: string[]) => {
    const newRole: GroupRoleDefinition = {
      id: `custom_${Date.now()}`,
      name,
      description,
      is_system: false,
      permissions,
    }
    setRoles((prev) => [...prev, newRole])
    setIsCreatingRole(false)
    toast.success(`Rolle "${name}" erstellt.`)
  }

  const handleUpdateRole = async (
    role: GroupRoleDefinition,
    name: string,
    description: string,
    permissions: string[]
  ) => {
    setRoles((prev) =>
      prev.map((r) =>
        r.id === role.id
          ? { ...r, name: r.is_system ? r.name : name, description, permissions }
          : r
      )
    )
    setEditingRole(null)
    toast.success(`Rolle "${role.name}" aktualisiert.`)
  }

  const handleDeleteRole = async (role: GroupRoleDefinition) => {
    if (role.is_system) return
    const ok = await confirm({
      title: 'Rolle löschen',
      message: `Möchtest du die Gruppenrolle "${role.name}" wirklich löschen?`,
      confirmText: 'Löschen',
      danger: true,
    })
    if (!ok) return

    setRoles((prev) => prev.filter((r) => r.id !== role.id))
    toast.success(`Rolle "${role.name}" gelöscht.`)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="max-w-3xl p-0 overflow-hidden bg-surface border-outline-variant/30 flex flex-col max-h-[90vh]"
      >
        {/* Header with generous vertical padding */}
        <div className="px-6 py-5 border-b border-outline-variant/20 bg-surface-container/70 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-primary/10 text-primary flex items-center justify-center shadow-xs">
              <ShieldCheck className="w-5 h-5" />
            </div>
            <div>
              <h2 className="font-headline text-body-lg font-bold text-primary">
                Gruppen-Rollen & Rechte
              </h2>
              <p className="text-xs text-on-surface-variant">
                {group?.name || 'Gruppe'} • {members.length} Mitglieder • Rollenbasiertes Rechtesystem
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="p-1.5 rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high transition-colors"
            aria-label="Schließen"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Tab Switcher with generous spacing */}
        <div className="flex border-b border-outline-variant/15 px-6 pt-3.5 pb-0 bg-surface-container-low/50 gap-4 shrink-0">
          <button
            type="button"
            onClick={() => {
              setActiveTab('members')
              setIsCreatingRole(false)
              setEditingRole(null)
            }}
            className={`pb-3.5 px-2 text-xs font-semibold flex items-center gap-2 border-b-2 transition-colors ${
              activeTab === 'members'
                ? 'border-primary text-primary'
                : 'border-transparent text-on-surface-variant hover:text-on-surface'
            }`}
          >
            <Users className="w-4 h-4" />
            <span>Mitglieder ({members.length})</span>
          </button>

          <button
            type="button"
            onClick={() => {
              setActiveTab('roles')
              setIsCreatingRole(false)
              setEditingRole(null)
            }}
            className={`pb-3.5 px-2 text-xs font-semibold flex items-center gap-2 border-b-2 transition-colors ${
              activeTab === 'roles'
                ? 'border-primary text-primary'
                : 'border-transparent text-on-surface-variant hover:text-on-surface'
            }`}
          >
            <Shield className="w-4 h-4" />
            <span>Rollen & Vorlagen ({roles.length})</span>
          </button>

          <button
            type="button"
            onClick={() => {
              setActiveTab('permissions')
              setIsCreatingRole(false)
              setEditingRole(null)
            }}
            className={`pb-3.5 px-2 text-xs font-semibold flex items-center gap-2 border-b-2 transition-colors ${
              activeTab === 'permissions'
                ? 'border-primary text-primary'
                : 'border-transparent text-on-surface-variant hover:text-on-surface'
            }`}
          >
            <Sliders className="w-4 h-4" />
            <span>Standardrechte (@everyone)</span>
          </button>
        </div>

        {/* Scrollable Tab Content with generous vertical spacing (py-6) */}
        <div className="p-6 flex-1 overflow-y-auto space-y-6">
          {/* TAB 1: MEMBERS */}
          {activeTab === 'members' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-headline text-body-sm font-bold text-primary">
                    Gruppenmitglieder verwalten
                  </h3>
                  <p className="text-xs text-on-surface-variant/80">
                    Weise Mitgliedern Rollen zu oder entferne störende Teilnehmer aus der Gruppe.
                  </p>
                </div>
                <Badge variant="default" className="text-xs px-2.5 py-0.5 font-medium">
                  {members.length} {members.length === 1 ? 'Teilnehmer' : 'Teilnehmer'}
                </Badge>
              </div>

              {loading ? (
                <div className="py-16 text-center text-xs text-on-surface-variant">
                  Mitglieder werden geladen …
                </div>
              ) : (
                <div className="divide-y divide-outline-variant/15 rounded-2xl border border-outline-variant/20 bg-surface-container-lowest/80 overflow-hidden shadow-xs">
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
                        className="p-4 flex items-center justify-between gap-4 hover:bg-surface-container-high/30 transition-colors"
                      >
                        <div className="flex items-center gap-3.5 min-w-0">
                          <Avatar src={member.avatar_url} name={member.username} size="md" />
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="text-xs font-bold text-primary truncate">
                                {member.username}
                              </span>
                              {isSelf && (
                                <span className="text-[10px] text-on-surface-variant/70 font-normal">
                                  (Du)
                                </span>
                              )}
                            </div>
                            <div className="flex items-center gap-2 mt-1">
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
                                className="text-[10px] py-0 px-2 font-medium"
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
                        <div className="flex items-center gap-3 shrink-0">
                          {canEditThisMember && (
                            <>
                              <div className="w-40">
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
                                className="h-9 w-9 p-0 text-error hover:bg-error/10 rounded-xl"
                                title="Aus Gruppe entfernen"
                                aria-label={`${member.username} aus Gruppe entfernen`}
                              >
                                <UserMinus className="w-4 h-4" />
                              </Button>
                            </>
                          )}
                          {!canEditThisMember && isMemberOwner && (
                            <span className="text-xs text-on-surface-variant/70 font-medium px-3">
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

          {/* TAB 2: ROLES MANAGEMENT (Roles.tsx style table & forms) */}
          {activeTab === 'roles' && (
            <div className="space-y-5">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-headline text-body-sm font-bold text-primary">
                    Gruppenrollen & Zugriffsrechte
                  </h3>
                  <p className="text-xs text-on-surface-variant/80">
                    Definiere Rollen mit individuellen Berechtigungen analog zu den Panel-Rollen.
                  </p>
                </div>
                {canManage && !isCreatingRole && !editingRole && (
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    onClick={() => setIsCreatingRole(true)}
                    className="gap-1.5 rounded-xl h-8 px-3"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Rolle erstellen</span>
                  </Button>
                )}
              </div>

              {/* Create Role Form */}
              {isCreatingRole && (
                <GroupRoleForm
                  initial={null}
                  onSubmit={handleCreateRole}
                  onCancel={() => setIsCreatingRole(false)}
                />
              )}

              {/* Edit Role Form */}
              {editingRole && (
                <GroupRoleForm
                  initial={editingRole}
                  onSubmit={(n, d, p) => handleUpdateRole(editingRole, n, d, p)}
                  onCancel={() => setEditingRole(null)}
                />
              )}

              {/* Roles Table (Matches Roles.tsx styling) */}
              <div className="rounded-2xl border border-outline-variant/20 bg-surface-container-lowest/80 overflow-hidden shadow-xs">
                <table className="w-full text-left">
                  <thead>
                    <tr className="border-b border-outline-variant/30 bg-surface-container-low/60">
                      <th className="p-3.5 text-xs font-semibold text-on-surface-variant uppercase tracking-wider">
                        Rolle
                      </th>
                      <th className="p-3.5 text-xs font-semibold text-on-surface-variant uppercase tracking-wider hidden sm:table-cell">
                        Beschreibung
                      </th>
                      <th className="p-3.5 text-xs font-semibold text-on-surface-variant uppercase tracking-wider">
                        Rechte
                      </th>
                      <th className="p-3.5 text-right text-xs font-semibold text-on-surface-variant uppercase tracking-wider">
                        Aktionen
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-outline-variant/15">
                    {roles.map((r) => (
                      <tr
                        key={`role-${r.id}`}
                        className="hover:bg-surface-container-high/40 transition-colors"
                      >
                        <td className="p-3.5">
                          <div className="flex items-center gap-2">
                            {r.is_system ? (
                              <Shield className="w-4 h-4 text-status-warning shrink-0" />
                            ) : (
                              <Shield className="w-4 h-4 text-primary shrink-0" />
                            )}
                            <span className="text-xs font-bold text-primary">{r.name}</span>
                            {r.is_system ? (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-status-warning/10 text-status-warning border border-status-warning/30 font-medium">
                                System
                              </span>
                            ) : (
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-status-info/10 text-status-info border border-status-info/30 font-medium">
                                Eigene
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="p-3.5 text-xs text-on-surface-variant hidden sm:table-cell">
                          {r.description || '—'}
                        </td>
                        <td className="p-3.5">
                          <Badge variant="default" className="text-[10px] px-2 py-0.5">
                            {r.permissions.length} Rechte
                          </Badge>
                        </td>
                        <td className="p-3.5 text-right space-x-2">
                          {canManage && (
                            <button
                              type="button"
                              onClick={() => {
                                setIsCreatingRole(false)
                                setEditingRole(r)
                              }}
                              className="text-primary hover:text-primary/80 transition-colors p-1"
                              title="Rolle bearbeiten"
                              aria-label={`${r.name} bearbeiten`}
                            >
                              <Pencil className="w-3.5 h-3.5 inline" />
                            </button>
                          )}
                          {canManage && !r.is_system && (
                            <button
                              type="button"
                              onClick={() => void handleDeleteRole(r)}
                              className="text-error hover:text-error/80 transition-colors p-1"
                              title="Rolle löschen"
                              aria-label={`${r.name} löschen`}
                            >
                              <Trash2 className="w-3.5 h-3.5 inline" />
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB 3: DEFAULT PERMISSIONS (@everyone) */}
          {activeTab === 'permissions' && (
            <div className="space-y-5">
              <div className="p-4 rounded-2xl bg-surface-container/60 border border-outline-variant/25 text-xs text-on-surface-variant space-y-1.5">
                <span className="font-bold text-primary block text-body-sm">
                  Standardrechte für alle Gruppenmitglieder (@everyone)
                </span>
                <p className="text-xs leading-relaxed">
                  Diese Rechte gelten unmittelbar für jedes reguläre Mitglied ohne Sonderrolle. Administratoren und Moderatoren behalten ihre erweiterten Befugnisse.
                </p>
              </div>

              <div className="space-y-4 rounded-2xl border border-outline-variant/20 p-5 bg-surface-container-lowest/80 shadow-xs">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <span className="text-xs font-bold text-primary block">
                      Nachrichten senden
                    </span>
                    <span className="text-[11px] text-on-surface-variant">
                      Erlaubt regulären Mitgliedern das Schreiben und Versenden von Chatnachrichten.
                    </span>
                  </div>
                  <Switch
                    checked={canSendMessages}
                    onCheckedChange={setCanSendMessages}
                    disabled={!canManage}
                    aria-label="Nachrichten senden erlauben"
                  />
                </div>

                <div className="flex items-center justify-between gap-4 pt-3.5 border-t border-outline-variant/15">
                  <div>
                    <span className="text-xs font-bold text-primary block">
                      Medien, Notizen & Termine teilen
                    </span>
                    <span className="text-[11px] text-on-surface-variant">
                      Erlaubt das Anhängen von Fotos, Dokumenten, Notizen und Kalendereinträgen.
                    </span>
                  </div>
                  <Switch
                    checked={canAttachMedia}
                    onCheckedChange={setCanAttachMedia}
                    disabled={!canManage}
                    aria-label="Medien teilen erlauben"
                  />
                </div>

                <div className="flex items-center justify-between gap-4 pt-3.5 border-t border-outline-variant/15">
                  <div>
                    <span className="text-xs font-bold text-primary block">
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

                <div className="flex items-center justify-between gap-4 pt-3.5 border-t border-outline-variant/15">
                  <div>
                    <span className="text-xs font-bold text-primary block">
                      Nachrichten löschen & moderieren
                    </span>
                    <span className="text-[11px] text-on-surface-variant">
                      Erlaubt Mitgliedern das Löschen fremder Chatnachrichten.
                    </span>
                  </div>
                  <Switch
                    checked={canDeleteMessages}
                    onCheckedChange={setCanDeleteMessages}
                    disabled={!canManage}
                    aria-label="Nachrichten löschen erlauben"
                  />
                </div>

                <div className="flex items-center justify-between gap-4 pt-3.5 border-t border-outline-variant/15">
                  <div>
                    <span className="text-xs font-bold text-primary block">
                      Mitglieder entfernen (Kicken)
                    </span>
                    <span className="text-[11px] text-on-surface-variant">
                      Erlaubt regulären Mitgliedern das Kicken anderer regulärer Teilnehmer.
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
                    className="gap-2 rounded-xl px-4 py-2"
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
