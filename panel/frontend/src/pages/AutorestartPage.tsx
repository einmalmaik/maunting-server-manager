import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Clock, AlertTriangle, Loader2, Save, XCircle } from 'lucide-react'
import toast from 'react-hot-toast'
import { autorestartApi, ApiError, serversApi } from '@/lib/api'
import type { AutorestartData, ServersData } from '@/lib/types'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useUiLanguage } from '@/lib/ui-language'

// ── Constants ─────────────────────────────────────────────────────────────────

const VALID_INTERVALS = [1, 2, 3, 4, 6, 8, 12, 24]

// ── Helpers ───────────────────────────────────────────────────────────────────

function CurrentSchedule({ data }: { data: AutorestartData }) {
  const { copy } = useUiLanguage()
  const t = copy.autorestart
  const showSchedulerWarning = data.mode !== 'off' && !data.scheduler_ready
  const scheduledTimes = data.effective_times && data.effective_times.length > 0
    ? data.effective_times
    : data.times

  return (
    <Card className="border-border/60">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Clock className="h-4 w-4 text-muted-foreground" />
          <CardTitle>{t.currentSchedule}</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {showSchedulerWarning && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>{t.schedulerWarningTitle}</AlertTitle>
            <AlertDescription>
              {data.scheduler_error || t.schedulerWarningDescription}
            </AlertDescription>
          </Alert>
        )}

        <div className="grid grid-cols-2 gap-4">
          <div>
            <span className="text-xs text-muted-foreground uppercase tracking-wider">{t.mode}</span>
            <p className="font-mono text-sm text-foreground mt-1">{data.mode_name}</p>
          </div>
          <div>
            <span className="text-xs text-muted-foreground uppercase tracking-wider">{t.summary}</span>
            <p className="text-sm text-foreground/80 mt-1">{data.summary}</p>
          </div>
        </div>

        {data.interval_hours && data.mode === 'interval' && (
          <div>
            <span className="text-xs text-muted-foreground uppercase tracking-wider">{t.interval}</span>
            <p className="font-display text-3xl font-bold text-primary mt-1">
              {data.interval_hours}h
            </p>
          </div>
        )}

        {scheduledTimes?.length > 0 && (
          <div>
            <span className="text-xs text-muted-foreground uppercase tracking-wider mb-2 block">{t.scheduledTimes}</span>
            <div className="flex flex-wrap gap-1.5">
              {scheduledTimes.map((scheduledTime) => (
                <Badge key={scheduledTime} variant="outline" className="font-mono">{scheduledTime}</Badge>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function AutorestartPage() {
  const { copy } = useUiLanguage()
  const t = copy.autorestart
  const queryClient = useQueryClient()
  const [timesInput, setTimesInput] = useState('')
  const [intervalInput, setIntervalInput] = useState('')
  const isInitializedRef = useRef(false)
  const { data: serversData, isLoading: isServersLoading } = useQuery<ServersData>({
    queryKey: ['servers'],
    queryFn: serversApi.list,
  })
  const hasCurrentServer = Boolean(serversData?.current)
  const currentServer = serversData?.current ?? null

  const { data, isLoading, error } = useQuery<AutorestartData>({
    queryKey: ['autorestart', currentServer],
    queryFn: autorestartApi.get,
    enabled: hasCurrentServer,
  })

  // Pre-fill inputs from initial data load only — ignore background refetches
  // so user edits in progress are not overwritten.
  useEffect(() => {
    if (data && !isInitializedRef.current) {
      setTimesInput(data.times?.join(' ') ?? '')
      setIntervalInput(data.interval_hours?.toString() ?? '')
      isInitializedRef.current = true
    }
  }, [data])

  const mutation = useMutation({
    mutationFn: autorestartApi.update,
    onSuccess: (updated) => {
      toast.success(t.updateSuccess)
      isInitializedRef.current = false
      queryClient.setQueryData(['autorestart', currentServer], updated)
      queryClient.invalidateQueries({ queryKey: ['autorestart', currentServer] })
      queryClient.invalidateQueries({ queryKey: ['dashboard', currentServer] })
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : t.updateFailed)
    },
  })

  const save = (payload: Parameters<typeof autorestartApi.update>[0]) => mutation.mutate(payload)

  if (isServersLoading) {
    return <Skeleton className="h-40 w-full" />
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {!hasCurrentServer && (
        <Alert>
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t.noServerTitle}</AlertTitle>
          <AlertDescription>
            {t.noServerDescription}
          </AlertDescription>
        </Alert>
      )}

      {/* Error */}
      {error && hasCurrentServer && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>{t.errorTitle}</AlertTitle>
          <AlertDescription>{(error as Error).message}</AlertDescription>
        </Alert>
      )}

      {/* Current schedule */}
      {isLoading && hasCurrentServer ? (
        <Skeleton className="h-40 w-full" />
      ) : data && hasCurrentServer ? (
        <CurrentSchedule data={data} />
      ) : null}

      {/* Configuration tabs */}
      <Card className="border-border/60">
        <CardHeader>
          <CardTitle>{t.configureTitle}</CardTitle>
          <CardDescription>
            {t.configureDescription}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="times">
            <TabsList className="mb-4">
              <TabsTrigger value="times">{t.fixedTimes}</TabsTrigger>
              <TabsTrigger value="interval">{t.intervalTab}</TabsTrigger>
              <TabsTrigger value="off">{t.disable}</TabsTrigger>
            </TabsList>

            {/* Fixed Times */}
            <TabsContent value="times">
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="times-input">{t.restartTimesLabel}</Label>
                  <Input
                    id="times-input"
                    value={timesInput}
                    onChange={(e) => setTimesInput(e.target.value)}
                    placeholder="00:00 08:00 16:00"
                    className="font-mono"
                  />
                  <p className="text-xs text-muted-foreground">
                    {t.restartTimesHelp} <code className="font-mono">00:00 06:00 12:00 18:00</code> — {t.restartTimesHelpSuffix}
                  </p>
                </div>
                <Button
                  size="sm"
                  className="gap-2"
                  disabled={!hasCurrentServer || mutation.isPending || !timesInput.trim()}
                  onClick={() => save({ mode: 'times', times: timesInput.trim() })}
                >
                  {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                  {t.saveFixedTimes}
                </Button>
              </div>
            </TabsContent>

            {/* Interval */}
            <TabsContent value="interval">
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="interval-input">{t.everyNHours}</Label>
                  <Input
                    id="interval-input"
                    type="number"
                    min={1}
                    step={1}
                    value={intervalInput}
                    onChange={(e) => setIntervalInput(e.target.value)}
                    placeholder="8"
                    className="font-mono w-32"
                  />
                  <p className="text-xs text-muted-foreground">
                    {t.validValuesPrefix} 1, 2, 3, 4, 6, 8, 12, 24.
                    E.g. <code className="font-mono">8</code> → {t.validValuesSuffix}
                  </p>
                </div>
                <Button
                  size="sm"
                  className="gap-2"
                  disabled={!hasCurrentServer || mutation.isPending || !intervalInput.trim()}
                  onClick={() => {
                    const parsed = parseInt(intervalInput.trim(), 10)
                    if (!VALID_INTERVALS.includes(parsed)) {
                      toast.error(t.invalidInterval(VALID_INTERVALS.join(', ')))
                      return
                    }
                    save({ mode: 'interval', interval_hours: String(parsed) })
                  }}
                >
                  {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                  {t.saveInterval}
                </Button>
              </div>
            </TabsContent>

            {/* Disable */}
            <TabsContent value="off">
              <div className="space-y-4">
                <p className="text-sm text-muted-foreground">
                  {t.disableHelp}
                </p>
                <Button
                  variant="destructive"
                  size="sm"
                  className="gap-2"
                  disabled={!hasCurrentServer || mutation.isPending}
                  onClick={() => save({ mode: 'off' })}
                >
                  {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <XCircle className="h-4 w-4" />}
                  {t.disableButton}
                </Button>
              </div>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  )
}
