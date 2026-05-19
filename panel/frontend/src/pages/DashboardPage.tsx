import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Play, Square, RotateCcw, Download, ShieldCheck, Package, HardDrive,
  AlertTriangle, Server, Activity, Database, Globe, Cpu, Clock,
  Loader2, ChevronRight, Terminal, Plus,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { dashboardApi, actionsApi, serversApi } from '@/lib/api'
import type { DashboardData, ServersData } from '@/lib/types'
import { formatDateTime, cn, getErrorMessage } from '@/lib/utils'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import TaskConsole from '@/components/TaskConsole'
import { useUiLanguage } from '@/lib/ui-language'

// ── Helpers ───────────────────────────────────────────────────────────────────

function stateBadge(state: string) {
  const s = state.toLowerCase()
  if (s === 'active' || s === 'running' || s === 'connected') return 'success'
  if (s === 'inactive' || s === 'stopped') return 'secondary'
  return 'warning'
}

function auditBadge(status: string) {
  const s = status.toLowerCase()
  if (s === 'success') return 'success'
  if (s === 'failed') return 'destructive'
  return 'secondary'
}

// ── Action Button ─────────────────────────────────────────────────────────────

interface ActionBtnProps {
  name: string
  label: string
  icon: React.ElementType
  variant?: 'default' | 'destructive' | 'secondary' | 'outline'
  onAction: (name: string) => void
  isPending: boolean
  pendingName: string | null
}

function ActionBtn({ name, label, icon: Icon, variant = 'secondary', onAction, isPending, pendingName }: ActionBtnProps) {
  const loading = isPending && pendingName === name
  return (
    <Button
      variant={variant}
      size="sm"
      className="justify-start gap-2 w-full"
      disabled={isPending}
      onClick={() => onAction(name)}
    >
      {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Icon className="h-3.5 w-3.5" />}
      {label}
    </Button>
  )
}

// ── Wipe Dialog ───────────────────────────────────────────────────────────────

function WipeDialog({ onConfirm, isPending }: { onConfirm: () => Promise<unknown>; isPending: boolean }) {
  const { copy } = useUiLanguage()
  const t = copy.dashboard
  const [text, setText] = useState('')
  const [open, setOpen] = useState(false)

  const handleOpenChange = (o: boolean) => {
    if (isPending) return  // block closing while in-flight
    setOpen(o)
    if (!o) setText('')
  }

  const handleConfirm = async () => {
    if (text !== 'WIPE') return
    try {
      await onConfirm()
      setOpen(false)
      setText('')
    } catch {
      // error toast fired by onError; keep dialog open so user sees it failed
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="destructive" size="sm" className="w-full justify-start gap-2" disabled={isPending}>
          <AlertTriangle className="h-3.5 w-3.5" />
          {t.wipeServerData}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.wipeTitle}</DialogTitle>
          <DialogDescription>
            {t.wipeDescription}{' '}
            <span className="font-mono font-bold text-destructive">WIPE</span>{' '}
            to confirm.
          </DialogDescription>
        </DialogHeader>
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="WIPE"
          className="font-mono"
        />
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)} disabled={isPending}>{t.cancel}</Button>
          <Button variant="destructive" onClick={handleConfirm} disabled={text !== 'WIPE' || isPending}>
            {isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : t.wipeConfirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Dashboard Skeleton ────────────────────────────────────────────────────────

function DashboardSkeleton() {
  return (
    <div className="space-y-6 animate-fade-in">
      <Skeleton className="h-36 w-full" />
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20" />)}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <Skeleton className="h-64 lg:col-span-3" />
        <Skeleton className="h-64 lg:col-span-2" />
      </div>
      <Skeleton className="h-48" />
    </div>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const { copy } = useUiLanguage()
  const t = copy.dashboard
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [pendingAction, setPendingAction] = useState<string | null>(null)

  const { data: serversData, isLoading: isServersLoading, isError: isServersError } = useQuery<ServersData>({
    queryKey: ['servers'],
    queryFn: serversApi.list,
  })

  const hasServers = (serversData?.servers?.length ?? 0) > 0
  const currentServerName = serversData?.current ?? null
  const hasCurrentServer = Boolean(currentServerName)
  const { data, isLoading, error } = useQuery<DashboardData>({
    queryKey: ['dashboard', currentServerName],
    queryFn: dashboardApi.get,
    refetchInterval: 30_000,
  })
  const actionLabels: Record<string, string> = {
    start: t.start,
    stop: t.stop,
    restart: t.restart,
    validate: t.validate,
    workshop: t.workshop,
    backup: t.backup,
    install: t.installServer,
    update: t.update,
    wipe: t.wipeServerData,
  }

  const actionMutation = useMutation({
    mutationFn: (name: string) => actionsApi.invoke(name),
    onMutate: (name) => setPendingAction(name),
    onSettled: () => setPendingAction(null),
    onSuccess: (result, name) => {
      const label = actionLabels[name] ?? name
      if (result.async) {
        toast.success(t.actionStarted(label))
        queryClient.invalidateQueries({ queryKey: ['action-status', currentServerName] })
      } else {
        toast.success(t.actionFinished(label))
      }
      queryClient.invalidateQueries({ queryKey: ['dashboard', currentServerName] })
    },
    onError: (err: unknown) => {
      toast.error(getErrorMessage(err))
    },
  })

  const handleAction = (name: string) => actionMutation.mutate(name)

  if (isLoading || isServersLoading) return <DashboardSkeleton />

  if (isServersError) return (
    <Alert variant="destructive" className="animate-fade-in">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>{t.loadServersFailed}</AlertTitle>
      <AlertDescription>{t.loadServersHelp}</AlertDescription>
    </Alert>
  )

  if (!hasServers) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[400px] space-y-4 animate-fade-in">
        <div className="p-6 rounded-full bg-muted/30 border border-dashed border-border">
          <Server className="h-12 w-12 text-muted-foreground/50" />
        </div>
        <div className="text-center space-y-1">
          <h2 className="text-xl font-semibold">{t.noServersTitle}</h2>
          <p className="text-muted-foreground">{t.noServersDescription}</p>
        </div>
        <Button onClick={() => navigate('/servers')} className="gap-2">
          <Plus className="h-4 w-4" />
          {t.goToServers}
        </Button>
      </div>
    )
  }

  if (error && !data) return (
    <Alert variant="destructive" className="animate-fade-in">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>{t.loadDashboardFailed}</AlertTitle>
      <AlertDescription>{error instanceof Error ? error.message : t.loadDashboardHelp}</AlertDescription>
    </Alert>
  )

  const {
    core_status: core,
    panel_status: panel,
    autorestart,
    workshop,
    backup_runs,
    audit_entries,
    bridge_error,
    task,
  } = data ?? {}

  const isOnline = core?.server_running ?? false
  const serverInstalled = core?.server_installed ?? false
  const isTaskRunning = task?.status === 'running'
  const hasScheduledWork =
    Boolean(autorestart && autorestart.mode !== 'off') ||
    Boolean(workshop && workshop.autoupdate_enabled)

  return (
    <div className="space-y-6 animate-fade-in">

      {/* ── Bridge error ─────────────────────────────────────── */}
      {bridge_error && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t.bridgeError}</AlertTitle>
          <AlertDescription className="font-mono text-xs whitespace-pre-wrap">{bridge_error}</AlertDescription>
        </Alert>
      )}

      {panel && !panel.cron_active && hasScheduledWork && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t.schedulerWarningTitle}</AlertTitle>
          <AlertDescription>{t.schedulerWarningDescription(panel.cron_service_name)}</AlertDescription>
        </Alert>
      )}

      {!hasCurrentServer && (
        <Alert>
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t.noServerTitle}</AlertTitle>
          <AlertDescription>
            {t.noServerDescription}
          </AlertDescription>
        </Alert>
      )}

      {/* ── Active Task ───────────────────────────────────────── */}
      {currentServerName && task && (
        <TaskConsole serverName={currentServerName} initialTask={task} />
      )}

      {/* ── Steam login warning ──────────────────────────────── */}
      {core && (!core.steamlogin_set || !core.steampassword_set) && (
        <Alert variant="destructive" className="border-red-500/50 bg-red-500/10">
          <AlertTriangle className="h-4 w-4 text-red-500" />
          <AlertTitle className="text-red-500 font-bold">{t.steamCredentialsTitle}</AlertTitle>
          <AlertDescription className="text-xs">
            [ Error ] {t.steamCredentialsHelp} <span className="font-mono font-bold underline decoration-red-500/30">{core.config_path}</span>.
          </AlertDescription>
        </Alert>
      )}

      {/* ── Hero: Server Status ──────────────────────────────── */}
      <Card className="border-border/60">
        <CardContent className="p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-4">
              <div
                className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-xl border ${
                  isOnline ? 'border-emerald-500/30 bg-emerald-500/10' : 'border-border bg-muted'
                }`}
                style={isOnline ? { boxShadow: '0 0 24px hsl(141 69% 58% / 0.2)' } : undefined}
              >
                <Server className={`h-7 w-7 ${isOnline ? 'text-emerald-400' : 'text-muted-foreground'}`} />
              </div>
              <div>
                <div className="flex flex-col">
                  <span className="text-[10px] text-muted-foreground uppercase font-bold tracking-widest">{t.activeServer}</span>
                  <span className="font-display text-lg font-bold truncate max-w-[200px]">
                    {currentServerName ?? t.noServerTitle}
                  </span>
                </div>
                <div className="flex items-center gap-2 mt-1">
                  <span className={isOnline ? 'status-dot-online' : 'status-dot-offline'} />
                  <span className="font-display text-xs font-bold uppercase tracking-wider text-foreground/60">
                    {isOnline ? t.online : t.offline}
                  </span>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant={serverInstalled ? 'success' : 'warning'} className="h-6 px-2">
                {serverInstalled ? t.installed : t.notInstalled}
              </Badge>
              <Button
                variant={serverInstalled ? 'outline' : 'default'}
                size="sm"
                className={cn("gap-1.5 h-9 px-4", !serverInstalled && "bg-primary hover:bg-primary/90 shadow-[0_0_20px_hsl(var(--primary)/0.3)] animate-pulse-subtle")}
                disabled={
                  !hasCurrentServer ||
                  !!pendingAction ||
                  isTaskRunning ||
                  (!!core && (!core.steamlogin_set || !core.steampassword_set))
                }
                onClick={() => handleAction(serverInstalled ? 'update' : 'install')}
              >
                {pendingAction === (serverInstalled ? 'update' : 'install')
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  : <Download className="h-3.5 w-3.5" />}
                {serverInstalled ? t.update : t.installServer}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* ── Infrastructure Stats ─────────────────────────────── */}
      {panel && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          {[
            { icon: Activity, label: t.service, value: panel.service_state ?? 'unknown' },
            { icon: Globe, label: panel.proxy_name ?? 'Caddy', value: panel.proxy_state ?? panel.nginx_state ?? 'unknown' },
            { icon: Database, label: t.database, value: panel.database_state ?? 'unknown' },
            { icon: Clock, label: 'Cron', value: panel.cron_state ?? 'unknown' },
            { icon: Cpu, label: 'URL', value: panel.url || '—', mono: true, plain: true },
          ].map(({ icon: Icon, label, value, mono, plain }) => (
            <Card key={label} className="border-border/40">
              <CardContent className="p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-xs text-muted-foreground uppercase tracking-wider">{label}</span>
                </div>
                <Badge variant={plain ? 'secondary' : stateBadge(value)} className={mono ? 'font-mono text-xs truncate max-w-full' : ''}>
                  {value}
                </Badge>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* ── Actions + Restart Schedule ───────────────────────── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">

        {/* Actions */}
        <Card className="border-border/60 lg:col-span-3">
          <CardHeader>
            <CardTitle>{t.serverActions}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {!serverInstalled && (
              <Alert>
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription className="text-xs">
                  {t.installFirst}
                </AlertDescription>
              </Alert>
            )}
            <div className={`grid grid-cols-2 gap-2 sm:grid-cols-3${!serverInstalled || !hasCurrentServer ? ' opacity-50 pointer-events-none' : ''}`}>
              <ActionBtn name="start"    label={t.start}    icon={Play}        onAction={handleAction} isPending={!!pendingAction || isTaskRunning || !serverInstalled || !hasCurrentServer} pendingName={pendingAction} />
              <ActionBtn name="stop"     label={t.stop}     icon={Square}      onAction={handleAction} isPending={!!pendingAction || isTaskRunning || !serverInstalled || !hasCurrentServer} pendingName={pendingAction} />
              <ActionBtn name="restart"  label={t.restart}  icon={RotateCcw}   onAction={handleAction} isPending={!!pendingAction || isTaskRunning || !serverInstalled || !hasCurrentServer} pendingName={pendingAction} />
              <ActionBtn name="validate" label={t.validate} icon={ShieldCheck} onAction={handleAction} isPending={!!pendingAction || isTaskRunning || !serverInstalled || !hasCurrentServer} pendingName={pendingAction} />
              <ActionBtn name="workshop" label={t.workshop} icon={Package}     onAction={handleAction} isPending={!!pendingAction || isTaskRunning || !serverInstalled || !hasCurrentServer} pendingName={pendingAction} />
              <ActionBtn name="backup"   label={t.backup}   icon={HardDrive}   onAction={handleAction} isPending={!!pendingAction || isTaskRunning || !serverInstalled || !hasCurrentServer} pendingName={pendingAction} />
            </div>
            <div className={`pt-2 border-t border-border/50${!serverInstalled || !hasCurrentServer ? ' opacity-50 pointer-events-none' : ''}`}>
              <p className="text-xs text-muted-foreground mb-2 font-display uppercase tracking-wider">{t.dangerZone}</p>
              <WipeDialog onConfirm={() => actionMutation.mutateAsync('wipe')} isPending={!!pendingAction || isTaskRunning || !serverInstalled || !hasCurrentServer} />
            </div>
          </CardContent>
        </Card>

        {/* Restart Schedule */}
        <Card className="border-border/60 lg:col-span-2">
          <CardHeader>
            <CardTitle>{t.restartSchedule}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {autorestart ? (
              <>
                {autorestart.mode !== 'off' && !autorestart.scheduler_ready && (
                  <Alert variant="destructive">
                    <AlertTriangle className="h-4 w-4" />
                    <AlertTitle>{t.schedulerWarningTitle}</AlertTitle>
                    <AlertDescription>
                      {autorestart.scheduler_error || t.schedulerWarningDescription(autorestart.cron_service_name)}
                    </AlertDescription>
                  </Alert>
                )}
                <div>
                  <span className="text-xs text-muted-foreground">{t.mode}</span>
                  <p className="font-mono text-sm text-foreground mt-0.5">{autorestart.mode_name}</p>
                </div>
                <div>
                  <span className="text-xs text-muted-foreground">{t.summary}</span>
                  <p className="text-sm text-foreground/80 mt-0.5">{autorestart.summary}</p>
                </div>
                {(autorestart.effective_times ?? autorestart.times)?.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {(autorestart.effective_times ?? autorestart.times).map((scheduledTime) => (
                      <Badge key={scheduledTime} variant="outline" className="font-mono text-xs">{scheduledTime}</Badge>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <p className="text-sm text-muted-foreground">{t.noScheduleData}</p>
            )}
            <Link
              to="/autorestart"
              className="flex items-center gap-1 text-xs text-accent hover:text-primary transition-colors mt-2"
            >
              {t.configureSchedule} <ChevronRight className="h-3 w-3" />
            </Link>
          </CardContent>
        </Card>
      </div>

      {/* ── Recent Backups + Workshop ────────────────────────── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">

        {/* Recent Backups */}
        <Card className="border-border/60 lg:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>{t.recentBackups}</CardTitle>
            <Link to="/backups" className="text-xs text-accent hover:text-primary flex items-center gap-1">
              {t.allBackups} <ChevronRight className="h-3 w-3" />
            </Link>
          </CardHeader>
          <CardContent className="p-0">
            {backup_runs && backup_runs.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t.timestamp}</TableHead>
                    <TableHead>{t.mission}</TableHead>
                    <TableHead>{t.profile}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {backup_runs.map((run, idx) => (
                    <TableRow key={`${run.timestamp}-${idx}`}>
                      <TableCell className="font-mono text-xs">{run.timestamp}</TableCell>
                      <TableCell><Badge variant={run.mission_present ? 'success' : 'warning'}>{run.mission_present ? t.present : t.missing}</Badge></TableCell>
                      <TableCell><Badge variant={run.profile_present ? 'success' : 'warning'}>{run.profile_present ? t.present : t.missing}</Badge></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <p className="p-5 text-sm text-muted-foreground">{t.noBackupRuns}</p>
            )}
          </CardContent>
        </Card>

        {/* Workshop */}
        <Card className="border-border/60">
          <CardHeader>
            <CardTitle>{t.workshopMods}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {workshop ? (
              <>
                <div>
                  <span className="text-xs text-muted-foreground">{t.config}</span>
                  <p className="font-mono text-xs text-foreground/70 mt-0.5 break-all">{workshop.workshop_cfg}</p>
                </div>
                <div>
                  <span className="text-xs text-muted-foreground">{t.configuredMods}</span>
                  <p className="font-display text-4xl font-bold text-primary mt-1">
                    {workshop.configured_mod_count}
                  </p>
                </div>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">{t.noWorkshopData}</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ── Audit Log ───────────────────────────────────────── */}
      <Card className="border-border/60">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Terminal className="h-4 w-4 text-muted-foreground" />
            <CardTitle>{t.auditLog}</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {audit_entries && audit_entries.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t.time}</TableHead>
                  <TableHead>{t.user}</TableHead>
                  <TableHead>{t.action}</TableHead>
                  <TableHead>{t.status}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {audit_entries.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell className="font-mono text-xs text-muted-foreground whitespace-nowrap">
                      {formatDateTime(entry.created_at)}
                    </TableCell>
                    <TableCell className="text-sm">{entry.actor_username}</TableCell>
                    <TableCell className="font-mono text-xs">{entry.action}</TableCell>
                    <TableCell><Badge variant={auditBadge(entry.status)}>{entry.status}</Badge></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="p-5 text-sm text-muted-foreground">{t.noAuditEntries}</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
