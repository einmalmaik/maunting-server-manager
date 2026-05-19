import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Server, Plus, Trash2, AlertTriangle, Loader2, ArrowRightLeft, Copy } from 'lucide-react'
import toast from 'react-hot-toast'
import { serversApi, ApiError } from '@/lib/api'
import type { ServersData } from '@/lib/types'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useUiLanguage } from '@/lib/ui-language'

const NAME_RE = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/
const MAX_SERVER_NAME_LENGTH = 64

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function getServerNameError(value: string, invalidFormatMessage: string, tooLongMessage: string): string | null {
  const normalized = value.trim()
  if (!normalized) return null
  if (normalized.length > MAX_SERVER_NAME_LENGTH) return tooLongMessage
  if (!NAME_RE.test(normalized)) return invalidFormatMessage
  return null
}

function CreateDialog({ onCreated }: { onCreated: () => void }) {
  const { copy } = useUiLanguage()
  const t = copy.serversPage
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')

  const nameError = getServerNameError(name, t.nameError, t.nameTooLong)

  const createMutation = useMutation({
    mutationFn: (serverName: string) => serversApi.create(serverName),
    onSuccess: (_data, serverName) => {
      toast.success(t.createSuccess(serverName))
      setOpen(false)
      setName('')
      onCreated()
    },
    onError: (err: unknown) => {
      toast.error(errMsg(err, t.createFailed))
    },
  })

  const handleOpen = (nextOpen: boolean) => {
    if (createMutation.isPending) return
    setOpen(nextOpen)
    if (!nextOpen) setName('')
  }

  const canSubmit =
    name.trim().length > 0 &&
    name.trim().length <= MAX_SERVER_NAME_LENGTH &&
    NAME_RE.test(name.trim()) &&
    !createMutation.isPending

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <Button size="sm" className="gap-2" onClick={() => setOpen(true)}>
        <Plus className="h-4 w-4" />
        {t.newServer}
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.createTitle}</DialogTitle>
          <DialogDescription>{t.createDescription}</DialogDescription>
        </DialogHeader>

        <div className="space-y-1">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value.toLowerCase())}
            placeholder={t.serverNamePlaceholder}
            disabled={createMutation.isPending}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && canSubmit) createMutation.mutate(name.trim())
            }}
            aria-label={t.serverNameAria}
          />
          {nameError && <p className="text-xs text-destructive">{nameError}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpen(false)}>
            {copy.files.cancel}
          </Button>
          <Button onClick={() => createMutation.mutate(name.trim())} disabled={!canSubmit}>
            {createMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : t.create}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function DeleteDialog({
  serverName,
  onDeleted,
}: {
  serverName: string
  onDeleted: () => void
}) {
  const { copy } = useUiLanguage()
  const t = copy.serversPage
  const [open, setOpen] = useState(false)
  const [confirm, setConfirm] = useState('')

  const deleteMutation = useMutation({
    mutationFn: (name: string) => serversApi.delete(name),
    onSuccess: (_data, name) => {
      toast.success(t.deleteSuccess(name))
      setOpen(false)
      setConfirm('')
      onDeleted()
    },
    onError: (err: unknown) => {
      toast.error(errMsg(err, t.deleteFailed))
    },
  })

  const handleOpen = (nextOpen: boolean) => {
    if (deleteMutation.isPending) return
    setOpen(nextOpen)
    if (!nextOpen) setConfirm('')
  }

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <Button
        variant="ghost"
        size="sm"
        className="text-destructive hover:text-destructive"
        onClick={() => setOpen(true)}
        aria-label={t.deleteServerAria(serverName)}
      >
        <Trash2 className="h-4 w-4" />
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.deleteTitle(serverName)}</DialogTitle>
          <DialogDescription>{t.deleteDescription(serverName)}</DialogDescription>
        </DialogHeader>

        <Input
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          placeholder={serverName}
          aria-label={t.confirmDeleteAria(serverName)}
          className="font-mono"
          disabled={deleteMutation.isPending}
        />

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpen(false)}>
            {copy.files.cancel}
          </Button>
          <Button
            variant="destructive"
            onClick={() => deleteMutation.mutate(serverName)}
            disabled={confirm !== serverName || deleteMutation.isPending}
          >
            {deleteMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              t.deletePermanently
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function CloneDialog({
  sourceName,
  onCloned,
}: {
  sourceName: string
  onCloned: () => void
}) {
  const { copy } = useUiLanguage()
  const t = copy.serversPage
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')

  const normalizedName = name.trim()
  const nameError = normalizedName.length === 0
    ? null
    : normalizedName.length > MAX_SERVER_NAME_LENGTH
      ? t.nameTooLong
      : !NAME_RE.test(normalizedName)
        ? t.nameError
        : normalizedName === sourceName
          ? t.cloneSameNameError
          : null

  const cloneMutation = useMutation({
    mutationFn: (targetName: string) => serversApi.clone(sourceName, targetName),
    onSuccess: (_data, targetName) => {
      toast.success(t.cloneSuccess(sourceName, targetName))
      setOpen(false)
      setName('')
      onCloned()
    },
    onError: (err: unknown) => {
      toast.error(errMsg(err, t.cloneFailed))
    },
  })

  const handleOpen = (nextOpen: boolean) => {
    if (cloneMutation.isPending) return
    setOpen(nextOpen)
    if (!nextOpen) setName('')
  }

  const canSubmit =
    normalizedName.length > 0 &&
    normalizedName.length <= MAX_SERVER_NAME_LENGTH &&
    NAME_RE.test(normalizedName) &&
    normalizedName !== sourceName &&
    !cloneMutation.isPending

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setOpen(true)}
        aria-label={t.cloneServerAria(sourceName)}
      >
        <Copy className="h-4 w-4" />
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.cloneTitle(sourceName)}</DialogTitle>
          <DialogDescription>{t.cloneDescription}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1">
            <p className="text-sm font-medium">{t.cloneSourceLabel}</p>
            <Input value={sourceName} readOnly aria-label={t.cloneSourceLabel} className="font-mono" />
          </div>

          <div className="space-y-1">
            <p className="text-sm font-medium">{t.cloneTargetLabel}</p>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value.toLowerCase())}
              placeholder={t.serverNamePlaceholder}
              disabled={cloneMutation.isPending}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && canSubmit) cloneMutation.mutate(normalizedName)
              }}
              aria-label={t.cloneTargetLabel}
            />
            {nameError && <p className="text-xs text-destructive">{nameError}</p>}
          </div>

          <Alert>
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>{t.cloneLiveNotice}</AlertDescription>
          </Alert>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpen(false)}>
            {copy.files.cancel}
          </Button>
          <Button onClick={() => cloneMutation.mutate(normalizedName)} disabled={!canSubmit}>
            {cloneMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : t.cloneAction}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function MigrationDialog({ onMigrated }: { onMigrated: () => void }) {
  const { copy } = useUiLanguage()
  const t = copy.serversPage
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('default')

  const nameError = getServerNameError(name, t.nameError, t.nameTooLong)

  const migrateMutation = useMutation({
    mutationFn: (serverName: string) => serversApi.migrate(serverName),
    onSuccess: (_data, serverName) => {
      toast.success(t.migrateSuccess(serverName))
      setOpen(false)
      setName('default')
      onMigrated()
    },
    onError: (err: unknown) => {
      toast.error(errMsg(err, t.migrateFailed))
    },
  })

  const handleOpen = (nextOpen: boolean) => {
    if (migrateMutation.isPending) return
    setOpen(nextOpen)
    if (!nextOpen) setName('default')
  }

  const canSubmit =
    name.trim().length > 0 &&
    name.trim().length <= MAX_SERVER_NAME_LENGTH &&
    NAME_RE.test(name.trim()) &&
    !migrateMutation.isPending

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <Button size="sm" variant="outline" className="gap-2" onClick={() => setOpen(true)}>
        <ArrowRightLeft className="h-4 w-4" />
        {t.migrate}
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.migrateTitle}</DialogTitle>
          <DialogDescription>{t.migrateDescription}</DialogDescription>
        </DialogHeader>

        <div className="space-y-1">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value.toLowerCase())}
            placeholder="default"
            disabled={migrateMutation.isPending}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && canSubmit) migrateMutation.mutate(name.trim())
            }}
            aria-label={t.targetServerName}
          />
          {nameError && <p className="text-xs text-destructive">{nameError}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpen(false)}>
            {copy.files.cancel}
          </Button>
          <Button onClick={() => migrateMutation.mutate(name.trim())} disabled={!canSubmit}>
            {migrateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : t.migrate}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default function ServersPage() {
  const { copy } = useUiLanguage()
  const t = copy.serversPage
  const queryClient = useQueryClient()

  const serversQuery = useQuery<ServersData>({
    queryKey: ['servers'],
    queryFn: serversApi.list,
  })

  const legacyQuery = useQuery<{ legacy: boolean }>({
    queryKey: ['servers-legacy'],
    queryFn: serversApi.legacyCheck,
  })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['servers'] })
    queryClient.invalidateQueries({ queryKey: ['servers-legacy'] })
  }

  const refreshAfterClone = () => {
    refresh()
    queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    queryClient.invalidateQueries({ queryKey: ['mods'] })
    queryClient.invalidateQueries({ queryKey: ['backups'] })
    queryClient.invalidateQueries({ queryKey: ['autorestart'] })
    queryClient.invalidateQueries({ queryKey: ['action-status'] })
    queryClient.invalidateQueries({ queryKey: ['files'] })
    queryClient.invalidateQueries({ queryKey: ['language'] })
  }

  useEffect(() => {
    if (legacyQuery.error) {
      console.warn('Failed to check legacy layout:', legacyQuery.error)
    }
  }, [legacyQuery.error])

  const servers = serversQuery.data?.servers ?? []
  const current = serversQuery.data?.current ?? ''
  const hasLegacy = legacyQuery.data?.legacy === true

  return (
    <div className="space-y-6 animate-fade-in">
      {serversQuery.error && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t.error}</AlertTitle>
          <AlertDescription>
            {serversQuery.error instanceof Error ? serversQuery.error.message : t.loadFailed}
          </AlertDescription>
        </Alert>
      )}

      {hasLegacy && (
        <Alert className="border-yellow-500/50 bg-yellow-500/10 text-yellow-700 dark:text-yellow-400">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t.legacyTitle}</AlertTitle>
          <AlertDescription className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <span>{t.legacyDescription}</span>
            <MigrationDialog onMigrated={refresh} />
          </AlertDescription>
        </Alert>
      )}

      <Card className="border-border/60">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Server className="h-4 w-4 text-muted-foreground" />
              <CardTitle>{t.serversTitle}</CardTitle>
            </div>
            <CreateDialog onCreated={refresh} />
          </div>
          <CardDescription>{t.serversDescription}</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {serversQuery.isLoading ? (
            <div className="p-5 space-y-2">
              {Array.from({ length: 2 }).map((_, i) => (
                <div key={i} className="h-10 rounded-md bg-muted/50 animate-pulse" />
              ))}
            </div>
          ) : servers.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t.name}</TableHead>
                  <TableHead>{t.status}</TableHead>
                  <TableHead className="w-28 text-right">{t.actions}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {servers.map((server) => (
                  <TableRow key={server.name}>
                    <TableCell className="font-mono font-medium">{server.name}</TableCell>
                    <TableCell>
                      {server.name === current ? (
                        <Badge variant="success">{t.active}</Badge>
                      ) : (
                        <Badge variant="secondary">{t.inactive}</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <CloneDialog sourceName={server.name} onCloned={refreshAfterClone} />
                        <DeleteDialog serverName={server.name} onDeleted={refresh} />
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="p-5 text-sm text-muted-foreground">{t.noServers}</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
