import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Users, Plus, Trash2, Edit2, ShieldCheck, ShieldOff,
  KeyRound, Loader2, Check,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { usersApi, ApiError } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import type { UserProfile, PermissionEntry } from '@/lib/types'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { formatDateTime } from '@/lib/utils'
import { useUiLanguage } from '@/lib/ui-language'

function RoleBadge({ role }: { role: string }) {
  const { copy } = useUiLanguage()
  const t = copy.usersPage
  if (role === 'owner') return <Badge className="bg-yellow-500/20 text-yellow-400 border-yellow-500/40">{t.owner}</Badge>
  if (role === 'admin') return <Badge className="bg-blue-500/20 text-blue-400 border-blue-500/40">{t.admin}</Badge>
  return <Badge variant="secondary">{t.user}</Badge>
}

function groupPerms(perms: PermissionEntry[]) {
  const groups: Record<string, PermissionEntry[]> = {}
  for (const p of perms) {
    const group = p.key.split('.')[0]
    if (!groups[group]) groups[group] = []
    groups[group].push(p)
  }
  return groups
}

function PermissionPicker({
  allPerms,
  selected,
  onChange,
}: {
  allPerms: PermissionEntry[]
  selected: string[]
  onChange: (perms: string[]) => void
}) {
  const { copy } = useUiLanguage()
  const t = copy.usersPage
  const groups = groupPerms(allPerms)
  const groupLabels: Record<string, string> = {
    dashboard: t.dashboard,
    console: t.console,
    files: t.files,
    mods: t.mods,
    backups: t.backups,
    servers: t.servers,
    autorestart: t.autorestart,
    users: t.users,
  }

  const toggle = (key: string) => {
    if (selected.includes(key)) {
      onChange(selected.filter((k) => k !== key))
    } else {
      onChange([...selected, key])
    }
  }

  const toggleGroup = (keys: string[]) => {
    const allSelected = keys.every((k) => selected.includes(k))
    if (allSelected) {
      onChange(selected.filter((k) => !keys.includes(k)))
    } else {
      const newSet = new Set([...selected, ...keys])
      onChange(Array.from(newSet))
    }
  }

  return (
    <div className="space-y-3 max-h-64 overflow-y-auto pr-1">
      {Object.entries(groups).map(([group, entries]) => {
        const keys = entries.map((e) => e.key)
        const allOn = keys.every((k) => selected.includes(k))
        const someOn = keys.some((k) => selected.includes(k))
        return (
          <div key={group} className="space-y-1">
            <button
              type="button"
              className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => toggleGroup(keys)}
            >
              <span className={`h-3 w-3 rounded border ${allOn ? 'bg-accent border-accent' : someOn ? 'bg-accent/50 border-accent/50' : 'border-border'}`} />
              {groupLabels[group] ?? group}
            </button>
            <div className="grid grid-cols-1 gap-0.5 ml-5">
              {entries.map((entry) => (
                <label key={entry.key} className="flex items-center gap-2 cursor-pointer py-0.5">
                  <input
                    type="checkbox"
                    className="accent-accent h-3 w-3"
                    checked={selected.includes(entry.key)}
                    onChange={() => toggle(entry.key)}
                  />
                  <span className="text-xs text-foreground/80">{entry.label}</span>
                </label>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function CreateUserDialog({
  open,
  onClose,
  allPerms,
  currentRole,
}: {
  open: boolean
  onClose: () => void
  allPerms: PermissionEntry[]
  currentRole: string
}) {
  const { copy } = useUiLanguage()
  const t = copy.usersPage
  const queryClient = useQueryClient()
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<'admin' | 'user'>('user')
  const [perms, setPerms] = useState<string[]>([])
  const canManageCustomPermissions = currentRole === 'owner'

  const mutation = useMutation({
    mutationFn: () => usersApi.create({
      username: username.trim(),
      email: email.trim() || undefined,
      password,
      role,
      permissions: canManageCustomPermissions ? perms : undefined,
    }),
    onSuccess: () => {
      toast.success(t.userCreated)
      queryClient.invalidateQueries({ queryKey: ['users'] })
      setUsername('')
      setEmail('')
      setPassword('')
      setRole('user')
      setPerms([])
      onClose()
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : t.userCreateFailed)
    },
  })

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) { setUsername(''); setEmail(''); setPassword(''); setRole('user'); setPerms([]); onClose() } }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t.createUser}</DialogTitle>
          <DialogDescription>{t.createDescription}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="cu-username">{t.username}</Label>
              <Input id="cu-username" value={username} onChange={(e) => setUsername(e.target.value)} maxLength={64} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="cu-email">{t.emailOptional}</Label>
              <Input id="cu-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="cu-password">{t.password}</Label>
            <Input id="cu-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {currentRole === 'owner' && (
            <div className="space-y-1">
              <Label>{t.role}</Label>
              <div className="flex gap-2">
                {(['user', 'admin'] as const).map((r) => (
                  <button
                    key={r}
                    type="button"
                    onClick={() => setRole(r)}
                    className={`px-3 py-1 text-xs rounded border transition-colors ${role === r ? 'border-accent bg-accent/10 text-accent' : 'border-border text-muted-foreground hover:border-accent/40'}`}
                  >
                    {r === 'user' ? t.user : t.admin}
                  </button>
                ))}
              </div>
            </div>
          )}
          {canManageCustomPermissions && role === 'user' && (
            <div className="space-y-1">
              <Label>{t.permissions}</Label>
              <PermissionPicker allPerms={allPerms} selected={perms} onChange={setPerms} />
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t.cancel}</Button>
          <Button disabled={!username.trim() || password.length < 8 || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {t.createUser}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function EditUserDialog({
  user,
  allPerms,
  currentUserRole,
  onClose,
}: {
  user: UserProfile
  allPerms: PermissionEntry[]
  currentUserRole: string
  onClose: () => void
}) {
  const { copy } = useUiLanguage()
  const t = copy.usersPage
  const queryClient = useQueryClient()
  const [perms, setPerms] = useState<string[]>(user.permissions)
  const [role, setRole] = useState(user.role)
  const [isActive, setIsActive] = useState(user.is_active)
  const canManageCustomPermissions = currentUserRole === 'owner'

  const mutation = useMutation({
    mutationFn: () => usersApi.update(user.id, {
      role: canManageCustomPermissions ? role : undefined,
      permissions: canManageCustomPermissions && role === 'user' ? perms : undefined,
      is_active: isActive,
    }),
    onSuccess: () => {
      toast.success(t.userUpdated)
      queryClient.invalidateQueries({ queryKey: ['users'] })
      onClose()
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : t.userUpdateFailed)
    },
  })

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t.editUser(user.username)}</DialogTitle>
          <DialogDescription>{t.editDescription}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {currentUserRole === 'owner' && (
            <div className="space-y-1">
              <Label>{t.role}</Label>
              <div className="flex gap-2">
                {(['user', 'admin'] as const).map((r) => (
                  <button
                    key={r}
                    type="button"
                    onClick={() => setRole(r)}
                    className={`px-3 py-1 text-xs rounded border transition-colors ${role === r ? 'border-accent bg-accent/10 text-accent' : 'border-border text-muted-foreground hover:border-accent/40'}`}
                  >
                    {r === 'user' ? t.user : t.admin}
                  </button>
                ))}
              </div>
            </div>
          )}
          {canManageCustomPermissions && role === 'user' && (
            <div className="space-y-1">
              <Label>{t.permissions}</Label>
              <PermissionPicker allPerms={allPerms} selected={perms} onChange={setPerms} />
            </div>
          )}
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="cu-active"
              checked={isActive}
              onChange={(e) => setIsActive(e.target.checked)}
              className="accent-accent"
            />
            <Label htmlFor="cu-active">{t.accountActive}</Label>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t.cancel}</Button>
          <Button disabled={mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {t.saveChanges}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ResetPasswordDialog({ user, onClose }: { user: UserProfile; onClose: () => void }) {
  const { copy } = useUiLanguage()
  const t = copy.usersPage
  const [newPw, setNewPw] = useState('')
  const mutation = useMutation({
    mutationFn: () => usersApi.resetPassword(user.id, newPw),
    onSuccess: () => { toast.success(t.passwordReset); onClose() },
    onError: (err: unknown) => toast.error(err instanceof ApiError ? err.message : t.passwordResetFailed),
  })

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.resetPassword(user.username)}</DialogTitle>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="rp-pw">{t.newPassword}</Label>
          <Input id="rp-pw" type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t.cancel}</Button>
          <Button disabled={newPw.length < 8 || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {t.reset}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default function UsersPage() {
  const { copy } = useUiLanguage()
  const t = copy.usersPage
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const [createOpen, setCreateOpen] = useState(false)
  const [editUser, setEditUser] = useState<UserProfile | null>(null)
  const [resetPwUser, setResetPwUser] = useState<UserProfile | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<UserProfile | null>(null)

  const { data: usersData, isLoading: loadingUsers } = useQuery({
    queryKey: ['users'],
    queryFn: usersApi.list,
  })
  const { data: permsData } = useQuery({
    queryKey: ['user-permissions'],
    queryFn: usersApi.permissions,
    staleTime: Infinity,
  })

  const allPerms: PermissionEntry[] = permsData?.permissions ?? []
  const currentRole = user?.role ?? 'user'

  const deleteMutation = useMutation({
    mutationFn: (id: number) => usersApi.delete(id),
    onSuccess: () => {
      toast.success(t.userDeleted)
      queryClient.invalidateQueries({ queryKey: ['users'] })
      setDeleteTarget(null)
    },
    onError: (err: unknown) => toast.error(err instanceof ApiError ? err.message : t.userDeleteFailed),
  })

  const users = usersData?.users ?? []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Users className="h-6 w-6 text-accent" />
            {t.title}
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            {t.description}
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="mr-2 h-4 w-4" />
          {t.addUser}
        </Button>
      </div>

      <Card>
        <CardContent className="p-0">
          {loadingUsers ? (
            <div className="flex justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t.username}</TableHead>
                  <TableHead>{t.email}</TableHead>
                  <TableHead>{t.role}</TableHead>
                  <TableHead>{t.status}</TableHead>
                  <TableHead>2FA</TableHead>
                  <TableHead>{t.lastLogin}</TableHead>
                  <TableHead className="text-right">{t.actions}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users.map((u) => (
                  <TableRow key={u.id}>
                    <TableCell className="font-medium">{u.username}</TableCell>
                    <TableCell className="text-muted-foreground text-sm">{u.email ?? t.noValue}</TableCell>
                    <TableCell><RoleBadge role={u.role} /></TableCell>
                    <TableCell>
                      {u.is_active
                        ? <Badge variant="outline" className="text-emerald-500 border-emerald-500/40 gap-1"><Check className="h-3 w-3" />{t.active}</Badge>
                        : <Badge variant="outline" className="text-muted-foreground gap-1"><ShieldOff className="h-3 w-3" />{t.inactive}</Badge>
                      }
                    </TableCell>
                    <TableCell>
                      {u.totp_enabled
                        ? <ShieldCheck className="h-4 w-4 text-emerald-500" />
                        : <span className="text-muted-foreground text-xs">{t.noValue}</span>
                      }
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {u.last_login_at ? formatDateTime(u.last_login_at) : t.noValue}
                    </TableCell>
                    <TableCell className="text-right">
                      {u.role !== 'owner' && (
                        <div className="flex items-center justify-end gap-1">
                          <Button aria-label={t.editUserAria} variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => setEditUser(u)}>
                            <Edit2 className="h-3.5 w-3.5" />
                          </Button>
                          <Button aria-label={t.resetPasswordAria} variant="ghost" size="sm" className="h-7 w-7 p-0" onClick={() => setResetPwUser(u)}>
                            <KeyRound className="h-3.5 w-3.5" />
                          </Button>
                          {currentRole === 'owner' && (
                            <Button aria-label={t.deleteUserAria} variant="ghost" size="sm" className="h-7 w-7 p-0 text-destructive hover:text-destructive" onClick={() => setDeleteTarget(u)}>
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          )}
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <CreateUserDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        allPerms={allPerms}
        currentRole={currentRole}
      />
      {editUser && (
        <EditUserDialog
          user={editUser}
          allPerms={allPerms}
          currentUserRole={currentRole}
          onClose={() => setEditUser(null)}
        />
      )}
      {resetPwUser && (
        <ResetPasswordDialog user={resetPwUser} onClose={() => setResetPwUser(null)} />
      )}

      <Dialog open={!!deleteTarget} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.deleteUser}</DialogTitle>
            <DialogDescription>
              {deleteTarget ? t.deleteDescription(deleteTarget.username) : ''}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>{t.cancel}</Button>
            <Button
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
            >
              {deleteMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t.deleteUser}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
