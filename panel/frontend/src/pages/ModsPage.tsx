import { useState, useRef, useCallback, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Package,
  Search,
  Plus,
  Trash2,
  RefreshCw,
  AlertTriangle,
  Loader2,
  Download,
  CheckCircle2,
  ExternalLink,
  ArrowUp,
  Clock,
  GripVertical,
  GitBranch,
} from 'lucide-react'
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import toast from 'react-hot-toast'
import { modsApi, actionsApi, ApiError, serversApi } from '@/lib/api'
import { getErrorMessage } from '@/lib/utils'
import type {
  ActionStatusResponse,
  ModAddResponse,
  ModAnalysisData,
  ModDryRunData,
  ModEntry,
  ModsData,
  ModUpdateStatus,
  ServersData,
  SteamMod,
  SteamModDetail,
  Task,
} from '@/lib/types'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import { useUiLanguage } from '@/lib/ui-language'

// ── Toggle switch ─────────────────────────────────────────────────────────────

function ToggleCheckbox({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  label: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent
        transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
        disabled:cursor-not-allowed disabled:opacity-50
        ${checked ? 'bg-accent' : 'bg-muted'}`}
    >
      <span
        className={`pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg ring-0 transition-transform
          ${checked ? 'translate-x-4' : 'translate-x-0'}`}
      />
    </button>
  )
}

// ── Installed mods tab ────────────────────────────────────────────────────────

const AUTOUPDATE_INTERVALS = [
  { value: '10', label: '10m' },
  { value: '30', label: '30m' },
  { value: '60', label: '1h' },
  { value: '120', label: '2h' },
  { value: '180', label: '3h' },
  { value: '240', label: '4h' },
  { value: '360', label: '6h' },
  { value: '480', label: '8h' },
  { value: '720', label: '12h' },
  { value: '1440', label: '24h' },
]

const WORKSHOP_RESULT_VISIBILITY_MS = 15_000

function formatAutoupdateLabel(intervalMinutes: string): string {
  const minutes = Number(intervalMinutes)
  if (!Number.isFinite(minutes) || minutes <= 0) return intervalMinutes
  if (minutes < 60) return `${minutes}m`
  return `${minutes / 60}h`
}

function getWorkshopProgress(task: Task | null, log: string[]): number {
  if (!task) return 0
  if (task.status === 'finished' || task.status === 'failed' || task.status === 'timeout') return 100
  if (task.status !== 'running' && task.status !== 'started') return 0

  const joined = log.join('\n').toLowerCase()
  if (
    joined.includes('copying mod keys') ||
    joined.includes('mod-keys werden') ||
    joined.includes('workshop.cfg wurde') ||
    joined.includes('updated workshop.cfg')
  ) {
    return 92
  }
  if (
    joined.includes('lowercase conversion complete') ||
    joined.includes('kleinbuchstaben-konvertierung abgeschlossen') ||
    joined.includes('converting mod files to lowercase') ||
    joined.includes('mod-dateien werden')
  ) {
    return 76
  }
  if (
    joined.includes('mods requiring update') ||
    joined.includes('mods die aktualisiert werden') ||
    joined.includes('downloading item') ||
    joined.includes('download item')
  ) {
    return 48
  }
  if (
    joined.includes('checking steam workshop for updates') ||
    joined.includes('steam workshop wird auf updates geprueft') ||
    joined.includes('all workshop mods are already up to date') ||
    joined.includes('alle workshop-mods sind bereits aktuell')
  ) {
    return 24
  }
  return 14
}

function getWorkshopStage(task: Task | null, log: string[], t: any): { title: string; detail: string } {
  if (!task) {
    return {
      title: t.waitingTaskTitle,
      detail: t.waitingTaskDescription,
    }
  }

  const lastLine = [...log].reverse().find((line) => line.trim().length > 0) ?? ''
  const joined = log.join('\n').toLowerCase()

  if (task.status === 'finished') {
    return {
      title: t.completedTitle,
      detail: lastLine || t.completedDescription,
    }
  }
  if (task.status === 'failed') {
    return {
      title: t.failedTitle,
      detail: lastLine || task.error || t.failedDescription,
    }
  }
  if (task.status === 'timeout') {
    return {
      title: t.failedTitle,
      detail: lastLine || task.error || t.failedDescription,
    }
  }
  if (
    joined.includes('copying mod keys') ||
    joined.includes('mod-keys werden') ||
    joined.includes('workshop.cfg wurde') ||
    joined.includes('updated workshop.cfg')
  ) {
    return {
      title: t.finishingTitle,
      detail: lastLine || t.finishingDescription,
    }
  }
  if (
    joined.includes('lowercase conversion complete') ||
    joined.includes('kleinbuchstaben-konvertierung abgeschlossen') ||
    joined.includes('converting mod files to lowercase') ||
    joined.includes('mod-dateien werden')
  ) {
    return {
      title: t.linuxPrepTitle,
      detail: lastLine || t.linuxPrepDescription,
    }
  }
  if (
    joined.includes('mods requiring update') ||
    joined.includes('mods die aktualisiert werden') ||
    joined.includes('downloading item') ||
    joined.includes('download item')
  ) {
    return {
      title: t.downloadingTitle,
      detail: lastLine || t.downloadingDescription,
    }
  }
  if (
    joined.includes('checking steam workshop for updates') ||
    joined.includes('steam workshop wird auf updates geprueft')
  ) {
    return {
      title: t.checkingTitle,
      detail: lastLine || t.checkingDescription,
    }
  }
  return {
    title: t.startingTitle,
    detail: lastLine || t.startingDescription,
  }
}

// ── Sortable mod row ─────────────────────────────────────────────────────────

function SortableModRow({
  mod,
  status,
  pendingToggles,
  isUpdating,
  onToggle,
  onRemove,
  onSelectiveUpdate,
}: {
  mod: ModEntry
  status?: ModUpdateStatus
  pendingToggles: Set<string>
  isUpdating: boolean
  onToggle: (mod_id: string, mod_type: 'client' | 'server', enabled: boolean) => void
  onRemove: (mod: ModEntry) => void
  onSelectiveUpdate: (ids: string[]) => void
}) {
  const { copy } = useUiLanguage()
  const t = copy.mods
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: mod.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <TableRow ref={setNodeRef} style={style}>
      <TableCell className="w-8 pr-0">
        <button
          {...attributes}
          {...listeners}
          className="cursor-grab active:cursor-grabbing text-muted-foreground/40 hover:text-muted-foreground p-1"
          aria-label={t.dragToReorder}
        >
          <GripVertical className="h-4 w-4" />
        </button>
      </TableCell>
      <TableCell className="font-mono text-sm">@{mod.name}</TableCell>
      <TableCell className="text-center">
        <a
          href={`https://steamcommunity.com/sharedfiles/filedetails/?id=${mod.id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-muted-foreground hover:text-foreground flex items-center justify-center gap-1"
        >
          {mod.id}
          <ExternalLink className="h-3 w-3" />
        </a>
      </TableCell>
      <TableCell className="text-center">
        {!status ? (
          <span className="text-xs text-muted-foreground">—</span>
        ) : status.update_available ? (
          <div className="flex items-center justify-center gap-1">
            <Badge variant="outline" className="border-amber-500/60 text-amber-500 text-[10px] px-1 py-0">{t.statusUpdate}</Badge>
            <Button
              type="button" variant="ghost" size="icon"
              className="h-5 w-5 text-amber-500 hover:bg-amber-500/10"
              disabled={isUpdating}
              onClick={() => onSelectiveUpdate([mod.id])}
            >
              <ArrowUp className="h-3 w-3" />
            </Button>
          </div>
        ) : (
          <span className="text-xs text-green-500 flex items-center justify-center gap-1">
            <CheckCircle2 className="h-3 w-3" />{t.upToDate}
          </span>
        )}
      </TableCell>
      <TableCell className="text-center">
        <div className="flex justify-center">
          <ToggleCheckbox
            checked={mod.client}
            disabled={pendingToggles.has(`${mod.id}:client`)}
            label={t.toggleClient(mod.name)}
            onChange={(enabled) => onToggle(mod.id, 'client', enabled)}
          />
        </div>
      </TableCell>
      <TableCell className="text-center">
        <div className="flex justify-center">
          <ToggleCheckbox
            checked={mod.server}
            disabled={pendingToggles.has(`${mod.id}:server`)}
            label={t.toggleServer(mod.name)}
            onChange={(enabled) => onToggle(mod.id, 'server', enabled)}
          />
        </div>
      </TableCell>
      <TableCell>
        <Button
          type="button" variant="ghost" size="icon"
          className="h-7 w-7 text-muted-foreground hover:text-destructive"
          onClick={() => onRemove(mod)}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </TableCell>
    </TableRow>
  )
}

// ── Installed mods tab ────────────────────────────────────────────────────────

function InstalledModsTab({
  mods,
  isLoading,
  isError,
  currentServer,
}: {
  mods: ModEntry[]
  isLoading: boolean
  isError: boolean
  currentServer: string | null
}) {
  const { copy } = useUiLanguage()
  const t = copy.mods
  const queryClient = useQueryClient()
  const [confirmRemove, setConfirmRemove] = useState<ModEntry | null>(null)
  const [pendingToggles, setPendingToggles] = useState<Set<string>>(new Set())
  const [orderedMods, setOrderedMods] = useState<ModEntry[]>(mods)
  const previousWorkshopTaskStatusRef = useRef<string | null>(null)
  const workshopResultTimeoutRef = useRef<number | null>(null)
  const [showCompletedWorkshopTask, setShowCompletedWorkshopTask] = useState(false)
  const [analysisData, setAnalysisData] = useState<ModAnalysisData | null>(null)
  const [dryRunData, setDryRunData] = useState<ModDryRunData | null>(null)

  // Keep orderedMods in sync when server data changes (but not during drag)
  useEffect(() => { setOrderedMods(mods) }, [mods])

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const reorderMutation = useMutation({
    mutationFn: (mod_ids: string[]) => modsApi.reorder(mod_ids),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mods', currentServer] })
      queryClient.invalidateQueries({ queryKey: ['dashboard', currentServer] })
    },
    onError: (err: unknown) => {
      toast.error(getErrorMessage(err))
      setOrderedMods(mods) // revert
    },
  })

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const oldIndex = orderedMods.findIndex((m) => m.id === active.id)
    const newIndex = orderedMods.findIndex((m) => m.id === over.id)
    const reordered = arrayMove(orderedMods, oldIndex, newIndex)
    setOrderedMods(reordered)
    reorderMutation.mutate(reordered.map((m) => m.id))
  }
  const [updateData, setUpdateData] = useState<ModUpdateStatus[] | null>(null)
  const [lastChecked, setLastChecked] = useState<Date | null>(null)
  const [autoupdateEnabled, setAutoupdateEnabled] = useState(false)
  const [autoupdateInterval, setAutoupdateInterval] = useState('10')

  // Build a map from mod ID → update status, filtered to installed mods only
  const installedIdSet = new Set(mods.map((m) => m.id))
  const visibleUpdateData = (updateData ?? []).filter((u) => installedIdSet.has(u.id))
  const updateStatusMap = new Map<string, ModUpdateStatus>(
    visibleUpdateData.map((u) => [u.id, u]),
  )
  const outdatedMods = visibleUpdateData.filter((u) => u.update_available)

  // ── Autoupdate query ──────────────────────────────────────────────────────
  const { data: autoupdateData } = useQuery({
    queryKey: ['mods-autoupdate', currentServer],
    queryFn: modsApi.getAutoupdate,
    staleTime: 60_000,
    enabled: currentServer !== null,
  })

  const { data: actionStatus } = useQuery<ActionStatusResponse>({
    queryKey: ['action-status', currentServer, 'workshop'],
    queryFn: () => actionsApi.status('workshop'),
    enabled: currentServer !== null,
    refetchInterval: (query) => {
      const status = query.state.data as ActionStatusResponse | undefined
      const task = status?.task
      return task?.action === 'workshop' && task.status === 'running' ? 1500 : 10_000
    },
  })

  const rawWorkshopTask = actionStatus?.task?.action === 'workshop' ? actionStatus.task : null
  const workshopTask = rawWorkshopTask && (
    rawWorkshopTask.status === 'running' ||
    rawWorkshopTask.status === 'failed' ||
    rawWorkshopTask.status === 'timeout' ||
    (rawWorkshopTask.status === 'finished' && showCompletedWorkshopTask)
  )
    ? rawWorkshopTask
    : null
  const workshopLog = workshopTask ? actionStatus?.log ?? [] : []
  const workshopProgress = getWorkshopProgress(workshopTask, workshopLog)
  const workshopStage = getWorkshopStage(workshopTask, workshopLog, t)

  useEffect(() => {
    if (autoupdateData) {
      setAutoupdateEnabled(autoupdateData.enabled)
      if (autoupdateData.interval_minutes) {
        setAutoupdateInterval(String(autoupdateData.interval_minutes))
      }
    }
  }, [autoupdateData])

  const setAutoupdateMutation = useMutation({
    mutationFn: (interval_minutes: number | null) => modsApi.setAutoupdate(interval_minutes),
    onSuccess: (data, interval_minutes) => {
      setAutoupdateEnabled(data.enabled)
      if (data.interval_minutes) {
        setAutoupdateInterval(String(data.interval_minutes))
      }
      queryClient.invalidateQueries({ queryKey: ['mods-autoupdate', currentServer] })
      toast.success(
        interval_minutes
          ? t.autoupdateEnabledSuccess(formatAutoupdateLabel(String(interval_minutes)))
          : t.autoupdateDisabledSuccess,
      )
    },
    onError: (err: unknown) => {
      toast.error(getErrorMessage(err))
    },
  })

  // ── Toggle ────────────────────────────────────────────────────────────────
  const toggleMutation = useMutation({
    mutationFn: ({
      mod_id,
      mod_type,
      enabled,
    }: {
      mod_id: string
      mod_type: 'client' | 'server'
      enabled: boolean
    }) => modsApi.toggle(mod_id, mod_type, enabled),
    onMutate: ({ mod_id, mod_type }) => {
      setPendingToggles((s) => new Set(s).add(`${mod_id}:${mod_type}`))
    },
    onSuccess: (_data, { mod_type, enabled, mod_id }) => {
      toast.success(t.toggleSuccess(mod_type, enabled))
      queryClient.invalidateQueries({ queryKey: ['mods', currentServer] })
      queryClient.invalidateQueries({ queryKey: ['dashboard', currentServer] })
      setPendingToggles((s) => {
        const next = new Set(s)
        next.delete(`${mod_id}:${mod_type}`)
        return next
      })
    },
    onError: (err: unknown, { mod_id, mod_type }) => {
      toast.error(getErrorMessage(err))
      setPendingToggles((s) => {
        const next = new Set(s)
        next.delete(`${mod_id}:${mod_type}`)
        return next
      })
    },
  })

  // ── Remove ────────────────────────────────────────────────────────────────
  const removeMutation = useMutation({
    mutationFn: (mod_id: string) => modsApi.remove(mod_id),
    onSuccess: () => {
      toast.success(t.removed)
      setConfirmRemove(null)
      queryClient.invalidateQueries({ queryKey: ['mods', currentServer] })
      queryClient.invalidateQueries({ queryKey: ['dashboard', currentServer] })
    },
    onError: (err: unknown) => {
      toast.error(getErrorMessage(err))
    },
  })

  // ── Check for updates ─────────────────────────────────────────────────────
  const checkUpdatesMutation = useMutation({
    mutationFn: () => modsApi.checkUpdates(),
    onSuccess: (data) => {
      setUpdateData(data.mods)
      setLastChecked(new Date())
    },
    onError: (err: unknown) => {
      toast.error(getErrorMessage(err))
    },
  })

  // ── Selective update ──────────────────────────────────────────────────────
  const selectiveUpdateMutation = useMutation({
    mutationFn: (mod_ids: string[]) => modsApi.updateSelective(mod_ids),
    onSuccess: () => {
      toast.success(t.updateTriggered)
      queryClient.invalidateQueries({ queryKey: ['action-status', currentServer] })
      queryClient.invalidateQueries({ queryKey: ['mods', currentServer] })
      queryClient.invalidateQueries({ queryKey: ['dashboard', currentServer] })
    },
    onError: (err: unknown) => {
      toast.error(getErrorMessage(err))
    },
  })

  // ── Full install / update ─────────────────────────────────────────────────
  const installMutation = useMutation({
    mutationFn: () => actionsApi.invoke('workshop'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['action-status', currentServer] })
      toast.success(t.workshopStarted)
      queryClient.invalidateQueries({ queryKey: ['mods', currentServer] })
      queryClient.invalidateQueries({ queryKey: ['dashboard', currentServer] })
    },
    onError: (err: unknown) => {
      toast.error(getErrorMessage(err))
    },
  })

  const analysisMutation = useMutation({
    mutationFn: () => modsApi.analysis(),
    onSuccess: (data) => {
      setAnalysisData(data)
    },
    onError: (err: unknown) => {
      toast.error(getErrorMessage(err))
    },
  })

  const dryRunMutation = useMutation({
    mutationFn: () => modsApi.dryRun(),
    onSuccess: (data) => {
      setDryRunData(data)
      toast.success(t.dryRunReady)
    },
    onError: (err: unknown) => {
      toast.error(getErrorMessage(err))
    },
  })

  // ── Helpers ───────────────────────────────────────────────────────────────
  useEffect(() => {
    const currentTask = actionStatus?.task
    const currentWorkshopStatus =
      currentTask?.action === 'workshop' ? currentTask.status : null
    const previousWorkshopStatus = previousWorkshopTaskStatusRef.current

    if (workshopResultTimeoutRef.current !== null && currentWorkshopStatus !== 'finished') {
      window.clearTimeout(workshopResultTimeoutRef.current)
      workshopResultTimeoutRef.current = null
    }

    if (currentWorkshopStatus === 'running' || currentWorkshopStatus === 'failed' || currentWorkshopStatus === 'timeout') {
      setShowCompletedWorkshopTask(true)
    } else if (currentWorkshopStatus === 'finished') {
      if (previousWorkshopStatus === 'running') {
        setShowCompletedWorkshopTask(true)
        workshopResultTimeoutRef.current = window.setTimeout(() => {
          setShowCompletedWorkshopTask(false)
          workshopResultTimeoutRef.current = null
        }, WORKSHOP_RESULT_VISIBILITY_MS)
      } else if (previousWorkshopStatus === null) {
        setShowCompletedWorkshopTask(false)
      }
    } else {
      setShowCompletedWorkshopTask(false)
    }

    if (
      previousWorkshopStatus === 'running' &&
      currentWorkshopStatus === 'finished' &&
      !checkUpdatesMutation.isPending
    ) {
      checkUpdatesMutation.mutate()
    }

    previousWorkshopTaskStatusRef.current = currentWorkshopStatus
  }, [actionStatus?.task, checkUpdatesMutation.isPending, checkUpdatesMutation.mutate])

  useEffect(() => {
    return () => {
      if (workshopResultTimeoutRef.current !== null) {
        window.clearTimeout(workshopResultTimeoutRef.current)
      }
    }
  }, [])

  const formatLastChecked = () => {
    if (!lastChecked) return null
    const secs = Math.round((Date.now() - lastChecked.getTime()) / 1000)
    if (secs < 60) return `${secs}s ago`
    return `${Math.round(secs / 60)}m ago`
  }

  const sourceLabels: Record<string, string> = {
    workshop_cfg: t.source_workshop_cfg,
    config_client: t.source_config_client,
    config_server: t.source_config_server,
    dependency: t.source_dependency,
  }

  const dryRunLabels: Record<string, string> = {
    install: t.dryRun_install,
    update: t.dryRun_update,
    relink: t.dryRun_relink,
    remove_symlink: t.dryRun_remove_symlink,
  }

  const dryRunReasons: Record<string, string> = {
    install: t.dryRunReason_install,
    update: t.dryRunReason_update,
    relink: t.dryRunReason_relink,
    remove_symlink: t.dryRunReason_remove_symlink,
  }

  const conflictLabels: Record<string, string> = {
    duplicate_id: t.conflict_duplicate_id,
    duplicate_name: t.conflict_duplicate_name,
    missing_install: t.conflict_missing_install,
    missing_symlink: t.conflict_missing_symlink,
    symlink_target_mismatch: t.conflict_symlink_target_mismatch,
  }

  if (isLoading) {
    return (
      <div className="space-y-2">
        {[...Array(3)].map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    )
  }

  const isUpdating =
    selectiveUpdateMutation.isPending || installMutation.isPending || workshopTask?.status === 'running'

  return (
    <div className="space-y-4">
      {/* Action bar */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {t.countLabel(mods.length)}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {lastChecked && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <Clock className="h-3 w-3" />
              {t.checkedAgo(formatLastChecked() ?? '')}
            </span>
          )}
          {outdatedMods.length > 0 && (
            <Button
              size="sm"
              variant="outline"
              className="border-amber-500/50 text-amber-500 hover:bg-amber-500/10"
              onClick={() =>
                selectiveUpdateMutation.mutate(outdatedMods.map((u) => u.id))
              }
              disabled={isUpdating}
            >
              {selectiveUpdateMutation.isPending ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <ArrowUp className="mr-1.5 h-3.5 w-3.5" />
              )}
              {t.updateOutdated(outdatedMods.length)}
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={() => checkUpdatesMutation.mutate()}
            disabled={checkUpdatesMutation.isPending || mods.length === 0}
          >
            {checkUpdatesMutation.isPending ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            )}
            {t.checkForUpdates}
          </Button>
          <Button
            size="sm"
            onClick={() => installMutation.mutate()}
            disabled={isUpdating}
          >
            {installMutation.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Download className="mr-2 h-4 w-4" />
            )}
            {t.installUpdate}
          </Button>
        </div>
      </div>

      {workshopTask && (
        <div className="rounded-xl border border-accent/30 bg-accent/5 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                {workshopTask.status === 'running' ? (
                  <Loader2 className="h-4 w-4 animate-spin text-accent" />
                ) : workshopTask.status === 'failed' || workshopTask.status === 'timeout' ? (
                  <AlertTriangle className="h-4 w-4 text-destructive" />
                ) : (
                  <CheckCircle2 className="h-4 w-4 text-green-500" />
                )}
                <p className="text-sm font-semibold">{workshopStage.title}</p>
              </div>
              <p className="text-sm text-muted-foreground">{workshopStage.detail}</p>
            </div>
            <Badge
              variant="outline"
              className={
                workshopTask.status === 'failed' || workshopTask.status === 'timeout'
                  ? 'border-destructive/40 text-destructive'
                  : workshopTask.status === 'finished'
                    ? 'border-green-500/40 text-green-600'
                    : 'border-accent/40 text-accent'
              }
            >
              {workshopTask.status === 'running'
                ? t.running
                : workshopTask.status === 'failed' || workshopTask.status === 'timeout'
                  ? t.failedStatus
                  : t.completed}
            </Badge>
          </div>
          <div className="mt-4 space-y-2">
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div
                className={
                  workshopTask.status === 'failed' || workshopTask.status === 'timeout'
                    ? 'h-full bg-destructive transition-all duration-500'
                    : workshopTask.status === 'finished'
                      ? 'h-full bg-green-500 transition-all duration-500'
                      : 'h-full bg-accent transition-all duration-500'
                }
                style={{ width: `${workshopProgress}%` }}
              />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>
                {workshopTask.status === 'running'
                  ? t.workshopRunningHelp
                  : workshopTask.status === 'failed' || workshopTask.status === 'timeout'
                    ? t.workshopFailedHelp
                    : t.workshopFinishedHelp}
              </span>
              <span>{workshopProgress}%</span>
            </div>
            {workshopLog.length > 0 && (
              <div className="max-h-32 overflow-y-auto rounded-md border border-border/60 bg-black/85 p-3 font-mono text-[11px] text-emerald-400">
                {workshopLog.slice(-8).map((line, index) => (
                  <div key={`${index}-${line}`} className="leading-relaxed">
                    {line}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {!isLoading && !isError && mods.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground gap-2">
          <Package className="h-8 w-8 opacity-40" />
          <p className="text-sm">{t.noModsConfigured}</p>
          <p className="text-xs opacity-70">{t.noModsConfiguredHelp}</p>
        </div>
      ) : (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <div className="rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  <TableHead>{t.tableName}</TableHead>
                  <TableHead className="w-[140px] text-center">{t.tableWorkshopId}</TableHead>
                  <TableHead className="w-[100px] text-center">{t.tableStatus}</TableHead>
                  <TableHead className="w-[80px] text-center">{t.tableClient}</TableHead>
                  <TableHead className="w-[80px] text-center">{t.tableServer}</TableHead>
                  <TableHead className="w-[60px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                <SortableContext items={orderedMods.map((m) => m.id)} strategy={verticalListSortingStrategy}>
                  {orderedMods.map((mod) => (
                    <SortableModRow
                      key={mod.id}
                      mod={mod}
                      status={updateStatusMap.get(mod.id)}
                      pendingToggles={pendingToggles}
                      isUpdating={isUpdating}
                      onToggle={(mid, type, enabled) => toggleMutation.mutate({ mod_id: mid, mod_type: type, enabled })}
                      onRemove={setConfirmRemove}
                      onSelectiveUpdate={(ids) => selectiveUpdateMutation.mutate(ids)}
                    />
                  ))}
                </SortableContext>
              </TableBody>
            </Table>
          </div>
        </DndContext>
      )}

      {/* Auto-update section */}
      <div className="rounded-md border border-border bg-[var(--el-1)] p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">{t.autoupdateTitle}</p>
            <p className="text-xs text-muted-foreground">
              {t.autoupdateDescription}
            </p>
          </div>
          <ToggleCheckbox
            checked={autoupdateEnabled}
            disabled={setAutoupdateMutation.isPending}
            label={t.autoupdateToggle}
            onChange={(v) => {
              if (setAutoupdateMutation.isPending) return
              const previousEnabled = autoupdateEnabled
              setAutoupdateEnabled(v)
              setAutoupdateMutation.mutate(v ? Number.parseInt(autoupdateInterval, 10) : null, {
                onError: () => setAutoupdateEnabled(previousEnabled),
              })
            }}
          />
        </div>

        {autoupdateData && (
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant={autoupdateData.enabled ? 'success' : 'secondary'}>
              {autoupdateData.display}
            </Badge>
            {autoupdateData.log_path && (
              <span className="font-mono break-all">{autoupdateData.log_path}</span>
            )}
          </div>
        )}

        {autoupdateData?.enabled && !autoupdateData.scheduler_ready && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>{t.schedulerWarningTitle}</AlertTitle>
            <AlertDescription>
              {autoupdateData.scheduler_error || t.schedulerWarningDescription(autoupdateData.cron_service_name)}
            </AlertDescription>
          </Alert>
        )}

      {autoupdateEnabled && (
          <div className="flex items-center gap-3">
            <label htmlFor="autoupdate-interval" className="text-sm text-muted-foreground shrink-0">
              {t.autoupdateEvery}
            </label>
            <select
              id="autoupdate-interval"
              value={autoupdateInterval}
              onChange={(e) => setAutoupdateInterval(e.target.value)}
              className="rounded-md border border-border bg-background px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            >
              {AUTOUPDATE_INTERVALS.map((interval) => (
                <option key={interval.value} value={interval.value}>
                  {interval.label}
                </option>
              ))}
            </select>
            <Button
              size="sm"
              variant="outline"
              disabled={setAutoupdateMutation.isPending}
              onClick={() => setAutoupdateMutation.mutate(Number.parseInt(autoupdateInterval, 10))}
            >
              {setAutoupdateMutation.isPending && (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              )}
              {t.autoupdateSave}
            </Button>
          </div>
        )}
      </div>

      <div className="rounded-md border border-border bg-[var(--el-1)] p-4 space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-sm font-medium">{t.analysisTitle}</p>
            <p className="text-xs text-muted-foreground">{t.analysisDescription}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={analysisMutation.isPending}
              onClick={() => analysisMutation.mutate()}
            >
              {analysisMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <GitBranch className="mr-2 h-4 w-4" />}
              {t.runAnalysis}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={dryRunMutation.isPending}
              onClick={() => dryRunMutation.mutate()}
            >
              {dryRunMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
              {t.runDryRun}
            </Button>
          </div>
        </div>

        {analysisData && analysisData.summary.conflicts > 0 && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>{t.analysisTitle}</AlertTitle>
            <AlertDescription>{t.analysisConflictSummary(analysisData.summary.conflicts)}</AlertDescription>
          </Alert>
        )}

        {analysisData?.steam_dependency_status === 'unverified' && (
          <Alert>
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>{t.analysisTitle}</AlertTitle>
            <AlertDescription>
              {analysisData.steam_dependency_error || t.analysisDependencyWarning}
            </AlertDescription>
          </Alert>
        )}

        {analysisData && (
          <div className="space-y-3">
            {analysisData.mods.map((mod) => (
              <div key={`analysis-${mod.id}-${mod.name}`} className="rounded-lg border border-border/70 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="text-sm font-medium">@{mod.name}</div>
                    <div className="text-xs text-muted-foreground">{mod.id}</div>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {mod.sources.map((source) => (
                      <Badge key={`${mod.id}-${source}`} variant="secondary">
                        {sourceLabels[source] ?? source}
                      </Badge>
                    ))}
                    {!mod.installed && <Badge variant="outline" className="border-destructive/50 text-destructive">{t.dryRun_install}</Badge>}
                  </div>
                </div>
                {mod.conflicts.length > 0 && (
                  <div className="mt-3 space-y-1">
                    {mod.conflicts.map((conflict) => (
                      <div key={`${mod.id}-${conflict.code}`} className="rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive">
                        {conflictLabels[conflict.code] ?? conflict.message}
                      </div>
                    ))}
                  </div>
                )}
                {(mod.dependencies.length > 0 || mod.required_by.length > 0) && (
                  <div className="mt-3 grid gap-2 md:grid-cols-2">
                    <div className="rounded-md border border-border/60 p-2 text-xs">
                      <div className="font-medium text-foreground/80">{t.dependenciesTitle}</div>
                      <div className="mt-1 text-muted-foreground">
                        {mod.dependencies.length > 0 ? mod.dependencies.join(', ') : '—'}
                      </div>
                    </div>
                    <div className="rounded-md border border-border/60 p-2 text-xs">
                      <div className="font-medium text-foreground/80">{t.requiredByTitle}</div>
                      <div className="mt-1 text-muted-foreground">
                        {mod.required_by.length > 0 ? mod.required_by.join(', ') : '—'}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ))}

            {(analysisData.stray_symlinks.length > 0 || analysisData.config_only_mods.length > 0) && (
              <div className="grid gap-3 md:grid-cols-2">
                <div className="rounded-lg border border-border/70 p-3">
                  <div className="text-sm font-medium">{t.straySymlinksTitle}</div>
                  <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                    {analysisData.stray_symlinks.length > 0 ? analysisData.stray_symlinks.map((entry) => (
                      <div key={`stray-${entry.name}`}>@{entry.name}</div>
                    )) : <div>—</div>}
                  </div>
                </div>
                <div className="rounded-lg border border-border/70 p-3">
                  <div className="text-sm font-medium">{t.configOnlyModsTitle}</div>
                  <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                    {analysisData.config_only_mods.length > 0 ? analysisData.config_only_mods.map((name) => (
                      <div key={`config-only-${name}`}>@{name}</div>
                    )) : <div>—</div>}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {dryRunData && (
          <div className="rounded-lg border border-border/70 p-3 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              {dryRunData.summary.has_changes ? (
                <Badge variant="outline" className="border-amber-500/60 text-amber-500">
                  {t.dryRunChangeCount(dryRunData.summary.total)}
                </Badge>
              ) : (
                <Badge variant="success">{t.dryRunNoChanges}</Badge>
              )}
            </div>
            {dryRunData.actions.length > 0 && (
              <div className="space-y-2">
                {dryRunData.actions.map((action, index) => (
                  <div key={`${action.type}-${action.id}-${index}`} className="rounded-md border border-border/60 px-3 py-2 text-xs">
                    <div className="font-medium">{dryRunLabels[action.type] ?? action.type}: @{action.name}</div>
                    <div className="mt-1 text-muted-foreground">{dryRunReasons[action.type] ?? action.reason}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Remove confirmation dialog */}
      <Dialog open={!!confirmRemove} onOpenChange={(o) => !o && setConfirmRemove(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.removeTitle}</DialogTitle>
            <DialogDescription>
              {t.removeDescriptionPrefix}{' '}
              <span className="font-mono text-foreground">@{confirmRemove?.name}</span> (
              {confirmRemove?.id}) {t.removeDescriptionSuffix}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmRemove(null)}>
              {t.cancel}
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={removeMutation.isPending}
              onClick={() => confirmRemove && removeMutation.mutate(confirmRemove.id)}
            >
              {removeMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t.remove}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// ── Workshop search tab ───────────────────────────────────────────────────────

function toFolderName(title: string): string {
  return sanitizeFolderName(title) || 'mod'
}

function sanitizeFolderName(value: string): string {
  return value
    .toLowerCase()
    .replace(/[\u0000-\u001f\u007f]/g, '')
    .replace(/[;"\\/]/g, '')
    .replace(/\s+/g, ' ')
    .replace(/^\.+/, '')
    .trim()
    .slice(0, 128)
}

interface DepsDialogState {
  mod: SteamModDetail
  modName: string
  deps: SteamModDetail[]
  depNames: string[]
}

interface UnverifiedDependencyState {
  modId: string
  modName: string
  title: string
  reason: string
}

function reportAddSuccess(
  response: ModAddResponse,
  t: any,
  successMessage = 'Mod added to workshop.cfg.',
) {
  if (!response.ok) return
  toast.success(successMessage)
  if (response.installed_dependencies.length > 0) {
    toast.success(
      t.autoInstalledDependencies(
        response.installed_dependencies.length,
        response.installed_dependencies.map((dep) => dep.name).join(', '),
      ),
    )
  }
  if (response.dep_warning) {
    toast.error(`${t.dependencyCheckPrefix}: ${response.dep_warning}`)
  }
}

function UnverifiedDependencyDialog({
  state,
  isPending,
  onClose,
  onChangeName,
  onConfirm,
}: {
  state: UnverifiedDependencyState | null
  isPending: boolean
  onClose: () => void
  onChangeName: (value: string) => void
  onConfirm: () => void
}) {
  const { copy } = useUiLanguage()
  const t = copy.mods
  return (
    <Dialog open={!!state} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.dependenciesVerificationFailed}</DialogTitle>
          <DialogDescription>
            {t.dependenciesVerificationFailed}{' '}
            <span className="font-medium text-foreground">{state?.title}</span>. Continue anyway?
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Alert>
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>{state?.reason}</AlertDescription>
          </Alert>
          <div className="space-y-1.5">
            <Label htmlFor="unverified-mod-name-input">{t.modFolderName}</Label>
            <Input
              id="unverified-mod-name-input"
              value={state?.modName ?? ''}
              onChange={(e) => onChangeName(sanitizeFolderName(e.target.value))}
              placeholder="e.g. cf"
              maxLength={128}
            />
            <p className="text-xs text-muted-foreground">
              {t.manualHelp}
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            {copy.files.cancel}
          </Button>
          <Button
            type="button"
            disabled={!state?.modName.trim() || isPending}
            onClick={onConfirm}
          >
            {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {t.add}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function WorkshopSearchTab({
  installedIds,
  currentServer,
}: {
  installedIds: Set<string>
  currentServer: string | null
}) {
  const { copy, language } = useUiLanguage()
  const t = copy.mods
  const queryClient = useQueryClient()
  const [searchInput, setSearchInput] = useState('')
  const [activeQuery, setActiveQuery] = useState('')
  const [addDialog, setAddDialog] = useState<SteamMod | null>(null)
  const [modNameInput, setModNameInput] = useState('')
  const [fetchingDepsMod, setFetchingDepsMod] = useState<string | null>(null)
  const [depsDialog, setDepsDialog] = useState<DepsDialogState | null>(null)
  const [unverifiedDialog, setUnverifiedDialog] = useState<UnverifiedDependencyState | null>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)

  const { data, isFetching, error } = useQuery({
    queryKey: ['steam-search', activeQuery],
    queryFn: () => modsApi.steamSearch(activeQuery),
    enabled: activeQuery.length > 0,
    staleTime: 60_000,
  })

  const steamMods: SteamMod[] =
    data?.response?.publishedfiledetails?.filter((m) => m.publishedfileid) ?? []

  const addMutation = useMutation({
    mutationFn: ({
      mod_id,
      mod_name,
      confirmUnverifiedDependencies = false,
    }: {
      mod_id: string
      mod_name: string
      confirmUnverifiedDependencies?: boolean
    }) => modsApi.add(mod_id, mod_name, { confirmUnverifiedDependencies }),
    onSuccess: (response) => {
      if (!response.ok) return
      queryClient.invalidateQueries({ queryKey: ['mods', currentServer] })
      queryClient.invalidateQueries({ queryKey: ['dashboard', currentServer] })
    },
    onError: (err: unknown) => {
      toast.error(getErrorMessage(err))
    },
  })

  // Add a single mod — used by the simple add dialog
  const closeSimpleAddDialog = useCallback(() => {
    setAddDialog(null)
    setModNameInput('')
  }, [])

  const handleSimpleAdd = useCallback(() => {
    if (!addDialog || !modNameInput.trim()) return
    addMutation.mutate(
      { mod_id: addDialog.publishedfileid, mod_name: modNameInput },
      {
        onSuccess: (response) => {
          if (response.confirm_required) {
            setUnverifiedDialog({
              modId: addDialog.publishedfileid,
              modName: modNameInput,
              title: addDialog.title,
              reason: response.message ?? t.dependenciesVerificationFailed,
            })
            closeSimpleAddDialog()
            return
          }
          reportAddSuccess(response, t)
          closeSimpleAddDialog()
        },
      },
    )
  }, [addDialog, modNameInput, addMutation, closeSimpleAddDialog])

  const handleConfirmUnverifiedAdd = useCallback(() => {
    if (!unverifiedDialog || !unverifiedDialog.modName.trim()) return
    addMutation.mutate(
      {
        mod_id: unverifiedDialog.modId,
        mod_name: unverifiedDialog.modName,
        confirmUnverifiedDependencies: true,
      },
      {
        onSuccess: (response) => {
          if (!response.ok) {
            toast.error(response.message ?? t.addFailed)
            return
          }
          reportAddSuccess(response, t)
          setUnverifiedDialog(null)
        },
      },
    )
  }, [addMutation, unverifiedDialog])

  // Add all mods in deps dialog (deps first, then main mod) sequentially
  const [depsAdding, setDepsAdding] = useState(false)
  const handleDepsAdd = useCallback(async () => {
    if (!depsDialog) return
    setDepsAdding(true)
    const toInstall = [
      ...depsDialog.deps
        .map((d, i) => ({ dep: d, originalIndex: i }))
        .filter(({ dep }) => !installedIds.has(dep.publishedfileid))
        .map(({ dep, originalIndex }) => ({
          mod_id: dep.publishedfileid,
          mod_name: depsDialog.depNames[originalIndex],
        })),
      { mod_id: depsDialog.mod.publishedfileid, mod_name: depsDialog.modName },
    ]
    let added = 0
    const extraDeps: string[] = []
    const warnings = new Set<string>()
    for (const item of toInstall) {
      try {
        const response = await modsApi.add(item.mod_id, item.mod_name, {
          confirmUnverifiedDependencies: true,
        })
        if (!response.ok) {
          warnings.add(response.message ?? `${t.addFailed} (${item.mod_id})`)
          continue
        }
        added++
        if (response.installed_dependencies?.length) {
          extraDeps.push(...response.installed_dependencies.map((d) => d.name))
        }
        if (response.dep_warning) {
          warnings.add(response.dep_warning)
        }
      } catch (err) {
        toast.error(`${t.addFailed} ${item.mod_id}: ${err instanceof ApiError ? err.message : t.unknownError}`)
      }
    }
    queryClient.invalidateQueries({ queryKey: ['mods', currentServer] })
    queryClient.invalidateQueries({ queryKey: ['dashboard', currentServer] })
    setDepsAdding(false)
    setDepsDialog(null)
    if (added > 0) toast.success(t.addedToWorkshopCount(added))
    if (extraDeps.length > 0) toast.success(t.autoInstalledDependencies(extraDeps.length, extraDeps.join(', ')))
    for (const warning of warnings) {
      toast.error(`${t.dependencyCheckPrefix}: ${warning}`)
    }
  }, [currentServer, depsDialog, installedIds, queryClient])

  const openAddDialog = useCallback(async (mod: SteamMod) => {
    setFetchingDepsMod(mod.publishedfileid)
    try {
      const result = await modsApi.steamWithDeps(mod.publishedfileid)
      const deps = (result.dependencies ?? []).filter(
        (d) => d.publishedfileid && d.result === 1,
      )
      if (deps.length === 0) {
        // No deps — use the simple dialog
        setModNameInput(toFolderName(mod.title))
        setAddDialog(mod)
      } else {
        setDepsDialog({
          mod: result.mod,
          modName: toFolderName(mod.title),
          deps,
          depNames: deps.map((d) => toFolderName(d.title || d.publishedfileid)),
        })
      }
    } catch (err) {
      setUnverifiedDialog({
        modId: mod.publishedfileid,
        modName: toFolderName(mod.title),
        title: mod.title,
        reason: `${t.couldNotVerifyDependencies}: ${getErrorMessage(err)}`,
      })
    } finally {
      setFetchingDepsMod(null)
    }
  }, [])

  const steamApiMissing =
    error instanceof ApiError && error.status === 503

  return (
    <div className="space-y-4">
      {/* Search bar */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            ref={searchInputRef}
            className="pl-8"
            placeholder={t.searchPlaceholder}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && searchInput.trim()) {
                setActiveQuery(searchInput.trim())
              }
            }}
          />
        </div>
        <Button
          type="button"
          disabled={!searchInput.trim() || isFetching}
          onClick={() => setActiveQuery(searchInput.trim())}
        >
          {isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          <span className="ml-2 hidden sm:inline">{t.searchButton}</span>
        </Button>
      </div>

      {/* Steam API key missing */}
      {steamApiMissing && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t.steamApiMissingTitle}</AlertTitle>
          <AlertDescription>
            {language === 'de' ? 'Trage ' : 'Add '}
            <code className="text-xs bg-muted px-1 py-0.5 rounded">STEAM_API_KEY=your_key</code> to{' '}
            <code className="text-xs bg-muted px-1 py-0.5 rounded">panel/.env</code>
            {language === 'de' ? ' ein und starte den Panel-Dienst neu. Einen Key bekommst du unter ' : ' and restart the panel service. Get a key at '}
            <a
              href="https://steamcommunity.com/dev/apikey"
              target="_blank"
              rel="noopener noreferrer"
              className="underline hover:no-underline"
            >
              steamcommunity.com/dev/apikey
            </a>
            .
          </AlertDescription>
        </Alert>
      )}

      {/* Other errors */}
      {error && !steamApiMissing && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t.searchFailedTitle}</AlertTitle>
          <AlertDescription>
            {error instanceof ApiError ? error.message : t.searchFailedDescription}
          </AlertDescription>
        </Alert>
      )}

      {/* Results */}
      {!activeQuery && !error && (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground gap-2">
          <Search className="h-8 w-8 opacity-40" />
          <p className="text-sm">{t.searchPrompt}</p>
        </div>
      )}

      {isFetching && (
        <div className="space-y-3">
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      )}

      {!isFetching && activeQuery && steamMods.length === 0 && !error && (
        <p className="text-sm text-muted-foreground text-center py-8">{t.noResults}</p>
      )}

      {!isFetching && steamMods.length > 0 && (
        <div className="space-y-2">
          {steamMods.map((mod) => {
            const alreadyAdded = installedIds.has(mod.publishedfileid)
            const loadingThis = fetchingDepsMod === mod.publishedfileid
            return (
              <div
                key={mod.publishedfileid}
                className="flex items-center gap-3 rounded-lg border border-border bg-[var(--el-1)] p-3"
              >
                {mod.preview_url && (
                  <img
                    src={mod.preview_url}
                    alt=""
                    className="h-14 w-14 shrink-0 rounded object-cover"
                    loading="lazy"
                  />
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{mod.title}</p>
                  <p className="text-xs text-muted-foreground line-clamp-1 mt-0.5">
                    {mod.short_description}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    ID: {mod.publishedfileid}
                    {mod.subscriptions != null && (
                      <span className="ml-2">{t.subscribers(mod.subscriptions)}</span>
                    )}
                  </p>
                </div>
                {alreadyAdded ? (
                  <Badge variant="secondary" className="shrink-0 gap-1">
                    <CheckCircle2 className="h-3 w-3" />
                    {t.added}
                  </Badge>
                ) : (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="shrink-0"
                    disabled={loadingThis || !!fetchingDepsMod}
                    onClick={() => openAddDialog(mod)}
                  >
                    {loadingThis
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <Plus className="mr-1 h-3.5 w-3.5" />}
                    {loadingThis ? t.loading : t.add}
                  </Button>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Simple add dialog (no deps) */}
      <Dialog open={!!addDialog} onOpenChange={(o) => !o && closeSimpleAddDialog()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.addModTitle}</DialogTitle>
            <DialogDescription>
              {t.addModDescription}{' '}
              <span className="font-medium text-foreground">{addDialog?.title}</span>. {t.addModDescriptionSuffix}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="mod-name-input">{t.modFolderName}</Label>
            <Input
              id="mod-name-input"
              value={modNameInput}
              onChange={(e) => setModNameInput(sanitizeFolderName(e.target.value))}
              placeholder="e.g. cf"
              maxLength={128}
            />
            <p className="text-xs text-muted-foreground">
              {t.storedAsHelp} <code className="bg-muted px-1 rounded">@{modNameInput || '...'}</code>{' '}
              {t.storedInConfigAndWorkshop}
            </p>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={closeSimpleAddDialog}>
              {copy.files.cancel}
            </Button>
            <Button
              type="button"
              disabled={!modNameInput.trim() || addMutation.isPending}
              onClick={handleSimpleAdd}
            >
              {addMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t.addToWorkshop}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Dependencies dialog */}
      <Dialog open={!!depsDialog} onOpenChange={(o) => !o && setDepsDialog(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t.dependenciesDetectedTitle}</DialogTitle>
            <DialogDescription>
              {depsDialog
                ? t.dependenciesDetectedDescription(depsDialog.mod.title, depsDialog.deps.length)
                : null}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 max-h-72 overflow-y-auto pr-1">
            {/* Dependencies */}
            {depsDialog?.deps.map((dep, i) => {
              const already = installedIds.has(dep.publishedfileid)
              return (
                <div key={dep.publishedfileid} className="flex items-start gap-2 rounded-md border border-border bg-[var(--el-1)] p-2.5">
                  {dep.preview_url && (
                    <img src={dep.preview_url} alt="" className="h-10 w-10 shrink-0 rounded object-cover" loading="lazy" />
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <p className="text-xs font-medium truncate">{dep.title || dep.publishedfileid}</p>
                      {already && (
                        <Badge variant="secondary" className="text-[10px] gap-0.5 h-4 px-1">
                          <CheckCircle2 className="h-2.5 w-2.5" />
                          {t.alreadyAdded}
                        </Badge>
                      )}
                    </div>
                    <p className="text-[10px] text-muted-foreground">ID: {dep.publishedfileid}</p>
                    {!already && (
                      <div className="mt-1">
                        <Input
                          value={depsDialog.depNames[i]}
                          onChange={(e) => {
                            const val = sanitizeFolderName(e.target.value)
                            setDepsDialog((prev) => {
                              if (!prev) return prev
                              const newNames = [...prev.depNames]
                              newNames[i] = val
                              return { ...prev, depNames: newNames }
                            })
                          }}
                          className="h-6 text-xs px-2"
                          maxLength={128}
                          placeholder={t.folderNamePlaceholder}
                        />
                      </div>
                    )}
                  </div>
                </div>
              )
            })}

            {/* Main mod */}
            {depsDialog && (
              <div className="flex items-start gap-2 rounded-md border border-accent/40 bg-accent/5 p-2.5">
                {depsDialog.mod.preview_url && (
                  <img src={depsDialog.mod.preview_url} alt="" className="h-10 w-10 shrink-0 rounded object-cover" loading="lazy" />
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold truncate text-foreground">{depsDialog.mod.title}</p>
                  <p className="text-[10px] text-muted-foreground">ID: {depsDialog.mod.publishedfileid} · {t.mainMod}</p>
                  <div className="mt-1">
                    <Input
                      value={depsDialog.modName}
                      onChange={(e) => {
                        const val = sanitizeFolderName(e.target.value)
                        setDepsDialog((prev) => prev ? { ...prev, modName: val } : prev)
                      }}
                      className="h-6 text-xs px-2"
                      maxLength={128}
                      placeholder={t.folderNamePlaceholder}
                    />
                  </div>
                </div>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setDepsDialog(null)}>
              {copy.files.cancel}
            </Button>
            <Button
              type="button"
              disabled={
                depsAdding ||
                !depsDialog?.modName.trim() ||
                depsDialog.deps.some((d, i) => !installedIds.has(d.publishedfileid) && !depsDialog.depNames[i].trim())
              }
              onClick={handleDepsAdd}
            >
              {depsAdding && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t.addMods(
                depsDialog
                  ? depsDialog.deps.filter((d) => !installedIds.has(d.publishedfileid)).length + 1
                  : 0,
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <UnverifiedDependencyDialog
        state={unverifiedDialog}
        isPending={addMutation.isPending}
        onClose={() => setUnverifiedDialog(null)}
        onChangeName={(value) => setUnverifiedDialog((prev) => prev ? { ...prev, modName: value } : prev)}
        onConfirm={handleConfirmUnverifiedAdd}
      />
    </div>
  )
}

// ── Manual add tab ────────────────────────────────────────────────────────────

function ManualAddTab({
  installedIds,
  currentServer,
}: {
  installedIds: Set<string>
  currentServer: string | null
}) {
  const { copy } = useUiLanguage()
  const t = copy.mods
  const queryClient = useQueryClient()
  const [idInput, setIdInput] = useState('')
  const [nameInput, setNameInput] = useState('')
  const [unverifiedDialog, setUnverifiedDialog] = useState<UnverifiedDependencyState | null>(null)

  const addMutation = useMutation({
    mutationFn: ({
      mod_id,
      mod_name,
      confirmUnverifiedDependencies = false,
    }: {
      mod_id: string
      mod_name: string
      confirmUnverifiedDependencies?: boolean
    }) => modsApi.add(mod_id, mod_name, { confirmUnverifiedDependencies }),
    onSuccess: (response, variables) => {
      if (response.confirm_required) {
        setUnverifiedDialog({
          modId: variables.mod_id,
          modName: variables.mod_name,
          title: variables.mod_id,
          reason: response.message ?? t.dependenciesVerificationFailed,
        })
        return
      }
      if (!response.ok) return
      reportAddSuccess(response, t)
      setIdInput('')
      setNameInput('')
      queryClient.invalidateQueries({ queryKey: ['mods', currentServer] })
      queryClient.invalidateQueries({ queryKey: ['dashboard', currentServer] })
    },
    onError: (err: unknown) => {
      toast.error(getErrorMessage(err))
    },
  })

  const handleConfirmUnverifiedAdd = useCallback(() => {
    if (!unverifiedDialog || !unverifiedDialog.modName.trim()) return
    addMutation.mutate(
      {
        mod_id: unverifiedDialog.modId,
        mod_name: unverifiedDialog.modName,
        confirmUnverifiedDependencies: true,
      },
      {
        onSuccess: (response) => {
          if (!response.ok) {
            toast.error(response.message ?? t.addFailed)
            return
          }
          setUnverifiedDialog(null)
        },
      },
    )
  }, [addMutation, unverifiedDialog])

  const trimmedId = idInput.trim()
  const alreadyAdded = trimmedId.length > 0 && installedIds.has(trimmedId)

  return (
    <div className="space-y-4 max-w-sm">
      <p className="text-sm text-muted-foreground">{t.manualHelp}</p>
      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="manual-id-input">{t.modIdOrUrl}</Label>
          <Input
            id="manual-id-input"
            placeholder={t.modIdPlaceholder}
            value={idInput}
            onChange={(e) => {
              const raw = e.target.value
              // Extract numeric ID from URL if pasted
              const match = raw.match(/[?&]id=(\d+)/) ?? raw.match(/^(\d+)$/)
              setIdInput(match ? match[1] : raw)
            }}
          />
          {alreadyAdded && (
            <p className="text-xs text-yellow-500">{t.alreadyInWorkshop}</p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="manual-name-input">{t.folderName}</Label>
          <Input
            id="manual-name-input"
            placeholder={t.folderNamePlaceholder}
            maxLength={128}
            value={nameInput}
            onChange={(e) => setNameInput(sanitizeFolderName(e.target.value))}
          />
          <p className="text-xs text-muted-foreground">
            {t.folderNameHelp}{' '}
            <code className="bg-muted px-1 rounded">@{nameInput || '...'}</code> in config.ini.
            {t.folderNameHelpSuffix}
          </p>
        </div>
        <Button
          type="button"
          disabled={
            !idInput.trim() ||
            !nameInput.trim() ||
            !/^\d+$/.test(idInput.trim()) ||
            alreadyAdded ||
            addMutation.isPending
          }
          onClick={() =>
            addMutation.mutate({ mod_id: idInput.trim(), mod_name: nameInput.trim() })
          }
        >
          {addMutation.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Plus className="mr-2 h-4 w-4" />
          )}
          {t.addToWorkshop}
        </Button>
      </div>

      <UnverifiedDependencyDialog
        state={unverifiedDialog}
        isPending={addMutation.isPending}
        onClose={() => setUnverifiedDialog(null)}
        onChangeName={(value) => setUnverifiedDialog((prev) => prev ? { ...prev, modName: value } : prev)}
        onConfirm={handleConfirmUnverifiedAdd}
      />
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ModsPage() {
  const { copy } = useUiLanguage()
  const t = copy.mods
  const { data: serversData, isLoading: isServersLoading } = useQuery<ServersData>({
    queryKey: ['servers'],
    queryFn: serversApi.list,
  })
  const hasCurrentServer = Boolean(serversData?.current)
  const currentServer = serversData?.current ?? null
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery<ModsData>({
    queryKey: ['mods', currentServer],
    queryFn: modsApi.list,
    refetchInterval: 30_000,
    enabled: hasCurrentServer,
  })

  const mods = data?.mods ?? []
  const installedIds = new Set(mods.map((m) => m.id))

  if (!hasCurrentServer && !isServersLoading) {
    return (
      <div className="space-y-6 animate-fade-in">
        <Alert>
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t.noServerTitle}</AlertTitle>
          <AlertDescription>{t.noServerDescription}</AlertDescription>
        </Alert>
      </div>
    )
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Package className="h-5 w-5 text-accent" />
          <h1 className="text-lg font-semibold">{t.title}</h1>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => refetch()}
          disabled={isFetching}
          aria-label={t.checkForUpdates}
        >
          <RefreshCw className={`h-4 w-4 ${isFetching ? 'animate-spin' : ''}`} />
        </Button>
      </div>

      {/* Fetch error */}
      {error && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t.loadFailedTitle}</AlertTitle>
          <AlertDescription>
            {error instanceof ApiError ? error.message : t.loadFailedDescription}
          </AlertDescription>
        </Alert>
      )}

      <Tabs defaultValue="installed">
        <TabsList>
          <TabsTrigger value="installed">
            {t.installedTab}
            {mods.length > 0 && (
              <span className="ml-1.5 rounded-full bg-accent/20 px-1.5 py-px text-xs font-medium text-accent">
                {mods.length}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="search">{t.workshopBrowserTab}</TabsTrigger>
          <TabsTrigger value="manual">{t.manualTab}</TabsTrigger>
        </TabsList>

        <TabsContent value="installed" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>{t.installedTitle}</CardTitle>
              <CardDescription>{t.installedDescription}</CardDescription>
            </CardHeader>
            <CardContent>
              <InstalledModsTab mods={mods} isLoading={isLoading} isError={isError} currentServer={currentServer} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="search" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>{t.workshopBrowserTitle}</CardTitle>
              <CardDescription>{t.workshopBrowserDescription}</CardDescription>
            </CardHeader>
            <CardContent>
              <WorkshopSearchTab installedIds={installedIds} currentServer={currentServer} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="manual" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>{t.manualTitle}</CardTitle>
              <CardDescription>{t.manualDescription}</CardDescription>
            </CardHeader>
            <CardContent>
              <ManualAddTab installedIds={installedIds} currentServer={currentServer} />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
