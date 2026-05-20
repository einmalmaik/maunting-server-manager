import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Server, Plus, Trash2, AlertTriangle, Loader2, ArrowRightLeft, Copy } from 'lucide-react'
import toast from 'react-hot-toast'
import { serversApi, gamesApi, ApiError } from '@/lib/api'
import type { ServersData, PterodactylCandidate, GameInfo } from '@/lib/types'
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
  const [gameId, setGameId] = useState('conan_exiles')

  const { data: gamesData } = useQuery({
    queryKey: ['games'],
    queryFn: () => gamesApi.list(),
    enabled: open,
  })
  const games = gamesData?.games ?? []

  const nameError = getServerNameError(name, t.nameError, t.nameTooLong)

  const createMutation = useMutation({
    mutationFn: (payload: { name: string; game_id: string }) => serversApi.create(payload.name, payload.game_id),
    onSuccess: (_data, payload) => {
      toast.success(t.createSuccess(payload.name))
      setOpen(false)
      setName('')
      setGameId('conan_exiles')
      onCreated()
    },
    onError: (err: unknown) => {
      toast.error(errMsg(err, t.createFailed))
    },
  })

  const handleOpen = (nextOpen: boolean) => {
    if (createMutation.isPending) return
    setOpen(nextOpen)
    if (!nextOpen) {
      setName('')
      setGameId('conan_exiles')
    }
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

        <div className="space-y-3">
          <div className="space-y-1">
            <label className="text-sm font-medium">Game</label>
            <select
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              value={gameId}
              onChange={(e) => setGameId(e.target.value)}
              disabled={createMutation.isPending}
            >
              {games.map((g: GameInfo) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
              {games.length === 0 && (
                <option value="conan_exiles">Conan Exiles</option>
              )}
            </select>
          </div>

          <div className="space-y-1">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value.toLowerCase())}
              placeholder={t.serverNamePlaceholder}
              disabled={createMutation.isPending}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && canSubmit) createMutation.mutate({ name: name.trim(), game_id: gameId })
              }}
              aria-label={t.serverNameAria}
            />
            {nameError && <p className="text-xs text-destructive">{nameError}</p>}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpen(false)}>
            {copy.files.cancel}
          </Button>
          <Button onClick={() => createMutation.mutate({ name: name.trim(), game_id: gameId })} disabled={!canSubmit}>
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

function PterodactylMigrateDialog({
  candidate,
  onMigrated,
}: {
  candidate: PterodactylCandidate
  onMigrated: () => void
}) {
  const { copy } = useUiLanguage()
  const t = copy.serversPage
  const [open, setOpen] = useState(false)
  const [targetName, setTargetName] = useState(candidate.volume_name.toLowerCase())

  const nameError = getServerNameError(targetName, t.nameError, t.nameTooLong)

  const migrateMutation = useMutation({
    mutationFn: () =>
      serversApi.migratePterodactyl({
        pterodactyl_path: candidate.pterodactyl_path,
        target_server_name: targetName.trim(),
        create_target: true,
      }),
    onSuccess: () => {
      toast.success(t.pteroMigrateSuccess(targetName.trim()))
      setOpen(false)
      onMigrated()
    },
    onError: (err: unknown) => {
      toast.error(errMsg(err, t.pteroMigrateFailed))
    },
  })

  const canSubmit =
    targetName.trim().length > 0 &&
    targetName.trim().length <= MAX_SERVER_NAME_LENGTH &&
    NAME_RE.test(targetName.trim()) &&
    !migrateMutation.isPending

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button size="sm" onClick={() => setOpen(true)}>
        {t.pteroActionMigrate}
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.pteroImportTitle}</DialogTitle>
          <DialogDescription>{t.pteroImportDesc}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1">
            <p className="text-sm font-medium">{t.pteroColVolume}</p>
            <Input value={candidate.pterodactyl_path} readOnly className="font-mono bg-muted" />
          </div>

          <div className="space-y-1">
            <p className="text-sm font-medium">{t.pteroTargetLabel}</p>
            <Input
              value={targetName}
              onChange={(e) => setTargetName(e.target.value.toLowerCase())}
              placeholder={candidate.volume_name.toLowerCase()}
              disabled={migrateMutation.isPending}
            />
            {nameError && <p className="text-xs text-destructive">{nameError}</p>}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            {copy.files.cancel}
          </Button>
          <Button onClick={() => migrateMutation.mutate()} disabled={!canSubmit}>
            {migrateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : t.pteroActionMigrate}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function PterodactylImport({ onMigrated }: { onMigrated: () => void }) {
  const { copy } = useUiLanguage()
  const t = copy.serversPage
  const [rootPath, setRootPath] = useState('/var/lib/pterodactyl/volumes')
  const [candidates, setCandidates] = useState<PterodactylCandidate[]>([])

  const scanMutation = useMutation({
    mutationFn: (path: string) => serversApi.listPterodactylCandidates(path),
    onSuccess: (data) => {
      setCandidates(data)
    },
    onError: (err: unknown) => {
      toast.error(errMsg(err, t.loadFailed))
    },
  })

  const handleScan = () => {
    scanMutation.mutate(rootPath)
  }

  useEffect(() => {
    handleScan()
  }, [])

  return (
    <div className="space-y-6">
      <Card className="border-border/60">
        <CardHeader>
          <CardTitle>{t.pteroImportTitle}</CardTitle>
          <CardDescription>{t.pteroImportDesc}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-col gap-4 sm:flex-row">
            <div className="flex-1 space-y-1">
              <label className="text-sm font-medium">{t.pteroScanPath}</label>
              <Input
                value={rootPath}
                onChange={(e) => setRootPath(e.target.value)}
                placeholder="/var/lib/pterodactyl/volumes"
                disabled={scanMutation.isPending}
              />
            </div>
            <div className="flex items-end">
              <Button onClick={handleScan} disabled={scanMutation.isPending} className="w-full sm:w-auto gap-2">
                {scanMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  t.pteroScanButton
                )}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="border-border/60">
        <CardHeader>
          <CardTitle>{t.pteroCandidatesTitle}</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {scanMutation.isPending ? (
            <div className="p-5 space-y-2">
              {Array.from({ length: 2 }).map((_, i) => (
                <div key={i} className="h-12 rounded-md bg-muted/50 animate-pulse" />
              ))}
            </div>
          ) : candidates.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t.pteroColVolume}</TableHead>
                  <TableHead>{t.pteroColServer}</TableHead>
                  <TableHead>{t.pteroColDb}</TableHead>
                  <TableHead>{t.pteroColMods}</TableHead>
                  <TableHead className="w-36 text-right">{t.pteroColAction}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {candidates.map((cand) => (
                  <TableRow key={cand.pterodactyl_path}>
                    <TableCell className="font-mono text-xs max-w-[200px] truncate" title={cand.pterodactyl_path}>
                      {cand.volume_name}
                      <span className="block text-[10px] text-muted-foreground truncate">
                        {cand.pterodactyl_path}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="font-medium block">{cand.server_name}</span>
                      <span className="text-xs text-muted-foreground">
                        Max: {cand.max_players} | PW: <span className="font-mono text-[10px] bg-muted px-1 rounded">{cand.admin_password}</span>
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="block text-xs font-semibold">
                        {(cand.db_size / (1024 * 1024)).toFixed(2)} MB
                      </span>
                      <span className="block text-[10px] text-muted-foreground">
                        {new Date(cand.db_modified * 1000).toLocaleString()}
                      </span>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{cand.mods_count}</Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <PterodactylMigrateDialog candidate={cand} onMigrated={onMigrated} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="p-5 text-sm text-muted-foreground">{t.pteroNoCandidates}</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

export default function ServersPage() {
  const { copy } = useUiLanguage()
  const t = copy.serversPage
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<'list' | 'pterodactyl'>('list')

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

      <div className="flex border-b border-border/60 gap-4 mb-4">
        <button
          onClick={() => setActiveTab('list')}
          className={`pb-2 px-1 font-medium transition-colors border-b-2 -mb-[2px] ${
            activeTab === 'list'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          {t.tabsList}
        </button>
        <button
          onClick={() => setActiveTab('pterodactyl')}
          className={`pb-2 px-1 font-medium transition-colors border-b-2 -mb-[2px] ${
            activeTab === 'pterodactyl'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          {t.tabsPterodactyl}
        </button>
      </div>

      {activeTab === 'list' ? (
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
                    <TableHead>Game</TableHead>
                    <TableHead>{t.status}</TableHead>
                    <TableHead className="w-28 text-right">{t.actions}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {servers.map((server) => (
                    <TableRow key={server.name}>
                      <TableCell className="font-mono font-medium">{server.name}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-[10px]">
                          {server.game_id === 'dayz' ? 'DayZ' : server.game_id === 'conan_exiles' ? 'Conan Exiles' : server.game_id || 'Conan Exiles'}
                        </Badge>
                      </TableCell>
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
      ) : (
        <PterodactylImport onMigrated={refresh} />
      )}
    </div>
  )
}
