import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { HardDrive, RotateCcw, AlertTriangle, Loader2, Plus } from 'lucide-react'
import toast from 'react-hot-toast'
import { backupsApi, ApiError, serversApi } from '@/lib/api'
import type { BackupRun, ServersData } from '@/lib/types'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import { useUiLanguage } from '@/lib/ui-language'

// ── Restore Dialog ────────────────────────────────────────────────────────────

function RestoreDialog({
  runs,
  onConfirm,
  isPending,
}: {
  runs: BackupRun[]
  onConfirm: (timestamp: string) => Promise<unknown>
  isPending: boolean
}) {
  const { copy } = useUiLanguage()
  const t = copy.backups
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState('')
  const [confirm, setConfirm] = useState('')

  const handleOpen = (o: boolean) => {
    if (isPending) return   // block closing while in-flight
    setOpen(o)
    if (!o) { setConfirm(''); setSelected('') }
  }

  const handleConfirm = async () => {
    if (!selected || confirm !== 'RESTORE') return
    try {
      await onConfirm(selected)
      handleOpen(false)   // only close after success
    } catch {
      // error toast fired by onError; keep dialog open so user can retry
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="gap-2" disabled={runs.length === 0 || isPending}>
          <RotateCcw className="h-4 w-4" />
          {t.restoreTitle}…
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.restoreDialogTitle}</DialogTitle>
          <DialogDescription>
            {t.restoreDialogDescription}{' '}
            <span className="font-mono font-bold text-destructive">RESTORE</span>{' '}
            {t.restoreDialogSuffix}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            aria-label={t.restoreDialogSelect}
            disabled={isPending}
            className="w-full rounded-md border border-border bg-input px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="">{`— ${t.restoreDialogSelect} —`}</option>
            {runs.map((r) => (
              <option key={r.timestamp} value={r.timestamp}>{r.timestamp}</option>
            ))}
          </select>

            <Input
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder={t.restoreDialogPlaceholder}
            aria-label={t.restoreDialogPlaceholder}
            disabled={isPending}
            className="font-mono"
          />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpen(false)}>{t.cancel}</Button>
          <Button
            variant="destructive"
            onClick={handleConfirm}
            disabled={!selected || confirm !== 'RESTORE' || isPending}
          >
            {isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : t.restoreDialogAction}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function BackupsPage() {
  const { copy } = useUiLanguage()
  const t = copy.backups
  const queryClient = useQueryClient()
  const { data: serversData, isLoading: isServersLoading } = useQuery<ServersData>({
    queryKey: ['servers'],
    queryFn: serversApi.list,
  })
  const hasCurrentServer = Boolean(serversData?.current)
  const currentServer = serversData?.current ?? null

  const { data, isLoading, error } = useQuery({
    queryKey: ['backups', currentServer],
    queryFn: backupsApi.list,
    enabled: hasCurrentServer,
  })

  const createMutation = useMutation({
    mutationFn: backupsApi.create,
    onSuccess: () => {
      toast.success(t.createSuccess)
      queryClient.invalidateQueries({ queryKey: ['backups', currentServer] })
      queryClient.invalidateQueries({ queryKey: ['dashboard', currentServer] })
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : t.createFailed)
    },
  })

  const restoreMutation = useMutation({
    mutationFn: (timestamp: string) => backupsApi.restore(timestamp),
    onSuccess: () => {
      toast.success(t.restoreSuccess)
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : t.restoreFailed)
    },
  })

  const runs: BackupRun[] = data?.runs ?? []

  return (
    <div className="space-y-6 animate-fade-in">

      {/* ── Error ────────────────────────────────────────────── */}
      {!hasCurrentServer && !isServersLoading && (
        <Alert>
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t.noServerTitle}</AlertTitle>
          <AlertDescription>
            {t.noServerDescription}
          </AlertDescription>
        </Alert>
      )}

      {error && hasCurrentServer && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t.errorTitle}</AlertTitle>
          <AlertDescription>{error instanceof Error ? error.message : t.loadFailed}</AlertDescription>
        </Alert>
      )}

      {/* ── Action Cards ─────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">

        {/* Create */}
        <Card className="border-border/60">
          <CardHeader>
            <div className="flex items-center gap-2">
              <HardDrive className="h-4 w-4 text-muted-foreground" />
              <CardTitle>{t.createTitle}</CardTitle>
            </div>
            <CardDescription>
              {t.createDescription}{' '}
              under <code className="font-mono">$HOME/backup</code>.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              size="sm"
              className="gap-2"
              disabled={!hasCurrentServer || createMutation.isPending}
              onClick={() => createMutation.mutate()}
            >
              {createMutation.isPending
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Plus className="h-4 w-4" />}
              {t.createNow}
            </Button>
          </CardContent>
        </Card>

        {/* Restore */}
        <Card className="border-border/60">
          <CardHeader>
            <div className="flex items-center gap-2">
              <RotateCcw className="h-4 w-4 text-muted-foreground" />
              <CardTitle>{t.restoreTitle}</CardTitle>
            </div>
            <CardDescription>{t.restoreDescription}</CardDescription>
          </CardHeader>
          <CardContent>
            <RestoreDialog
              runs={runs}
              onConfirm={(ts) => restoreMutation.mutateAsync(ts)}
              isPending={!hasCurrentServer || restoreMutation.isPending}
            />
          </CardContent>
        </Card>
      </div>

      {/* ── Inventory ────────────────────────────────────────── */}
      <Card className="border-border/60">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>{t.inventoryTitle}</CardTitle>
            {!isLoading && !error && hasCurrentServer && (
              <span className="text-xs text-muted-foreground">{t.runsFound(runs.length)}</span>
            )}
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading && hasCurrentServer ? (
            <div className="p-5 space-y-2">
              {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-10" />)}
            </div>
          ) : runs.length > 0 && hasCurrentServer ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t.timestamp}</TableHead>
                  <TableHead>{t.missionFiles}</TableHead>
                  <TableHead>{t.profileFiles}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((run) => (
                  <TableRow key={run.timestamp}>
                    <TableCell className="font-mono text-sm">{run.timestamp}</TableCell>
                    <TableCell>
                      <Badge variant={run.mission_present ? 'success' : 'warning'}>
                        {run.mission_present ? copy.dashboard.present : copy.dashboard.missing}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={run.profile_present ? 'success' : 'warning'}>
                        {run.profile_present ? copy.dashboard.present : copy.dashboard.missing}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="p-5 text-sm text-muted-foreground">
              {hasCurrentServer
                ? t.noRuns
                : t.selectServerInventory}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
