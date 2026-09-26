import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Play, ShieldAlert } from 'lucide-react'
import { Badge, Button, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import type { StudioApi, StudioOperation, StudioOverview, StudioPlan } from './studioApi'

interface RunOptions {
  /** Überschrift der Vorschau, z. B. „Tabelle anlegen". */
  title: string
  /** Meldung nach Erfolg; Standard: allgemeine Erfolgsmeldung. */
  success?: string
}

interface StudioContextValue {
  serverId: number
  databaseId: number
  databaseName: string
  api: StudioApi
  overview: StudioOverview
  dedicated: boolean
  canWrite: boolean
  canAdmin: boolean
  /** Zeigt die SQL-Vorschau und führt nach Bestätigung aus. `true` = ausgeführt. */
  runOperation: (operation: StudioOperation, options: RunOptions) => Promise<boolean>
  /** Wird nach jeder Strukturänderung erhöht — Listen laden dann neu. */
  revision: number
  bump: () => void
}

const StudioContext = createContext<StudioContextValue | null>(null)

export function useStudio(): StudioContextValue {
  const value = useContext(StudioContext)
  if (!value) throw new Error('useStudio braucht einen StudioProvider')
  return value
}

interface PendingRun {
  id: number
  operation: StudioOperation
  options: RunOptions
  plan: StudioPlan | null
  error: string | null
  busy: boolean
  resolve: (done: boolean) => void
}

export function StudioProvider({
  serverId,
  databaseId,
  databaseName,
  api,
  overview,
  children,
}: {
  serverId: number
  databaseId: number
  databaseName: string
  api: StudioApi
  overview: StudioOverview
  children: ReactNode
}) {
  const { t } = useTranslation()
  const [pending, setPending] = useState<PendingRun | null>(null)
  const [revision, setRevision] = useState(0)
  const pendingRef = useRef<PendingRun | null>(null)
  const runId = useRef(0)
  pendingRef.current = pending

  const bump = useCallback(() => setRevision((value) => value + 1), [])

  const runOperation = useCallback(
    (operation: StudioOperation, options: RunOptions) =>
      new Promise<boolean>((resolve) => {
        const id = ++runId.current
        setPending({ id, operation, options, plan: null, error: null, busy: true, resolve })
        // Eine spätere Vorschau ersetzt eine frühere; deren Antwort kommt zu spät.
        api
          .preview(operation)
          .then((plan) => setPending((current) => (current?.id === id ? { ...current, plan, busy: false } : current)))
          .catch((err: Error) =>
            setPending((current) => (current?.id === id ? { ...current, error: err.message, busy: false } : current)),
          )
      }),
    [api],
  )

  const close = (done: boolean) => {
    const current = pendingRef.current
    if (!current || current.busy) return
    setPending(null)
    current.resolve(done)
  }

  const execute = async () => {
    const current = pendingRef.current
    if (!current?.plan) return
    setPending({ ...current, busy: true, error: null })
    try {
      await api.execute(current.operation)
      toast.success(current.options.success || t('postgresStudio.preview.done'))
      setPending(null)
      bump()
      current.resolve(true)
    } catch (err) {
      setPending({ ...current, busy: false, error: (err as Error).message })
    }
  }

  const value = useMemo<StudioContextValue>(
    () => ({
      serverId,
      databaseId,
      databaseName,
      api,
      overview,
      dedicated: overview.kind === 'dedicated',
      canWrite: overview.permissions.write,
      canAdmin: overview.permissions.admin,
      runOperation,
      revision,
      bump,
    }),
    [serverId, databaseId, databaseName, api, overview, runOperation, revision, bump],
  )

  return (
    <StudioContext.Provider value={value}>
      {children}
      <SqlPreviewDialog pending={pending} onCancel={() => close(false)} onExecute={() => void execute()} />
    </StudioContext.Provider>
  )
}

function SqlPreviewDialog({
  pending,
  onCancel,
  onExecute,
}: {
  pending: PendingRun | null
  onCancel: () => void
  onExecute: () => void
}) {
  const { t } = useTranslation()
  const plan = pending?.plan
  return (
    <Dialog open={Boolean(pending)} onOpenChange={(open) => !open && onCancel()}>
      <DialogContent className="max-w-3xl" data-testid="sql-preview">
        <DialogHeader>
          <DialogTitle>{pending?.options.title}</DialogTitle>
          <DialogDescription>{t('postgresStudio.preview.description')}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3 p-6 max-h-[60vh] overflow-y-auto">
          {plan && (
            <div className="flex flex-wrap gap-2">
              <Badge variant={plan.mode === 'tx' ? 'success' : 'warning'}>
                {plan.mode === 'tx' ? t('postgresStudio.preview.transactional') : t('postgresStudio.preview.autocommit')}
              </Badge>
              <Badge variant={plan.identity === 'admin' ? 'warning' : 'info'}>
                {plan.identity === 'admin' ? t('postgresStudio.preview.asAdmin') : t('postgresStudio.preview.asOwner')}
              </Badge>
              {plan.scope === 'instance' && <Badge variant="warning">{t('postgresStudio.preview.instanceWide')}</Badge>}
            </div>
          )}
          {plan?.destructive && (
            <p className="flex items-start gap-2 rounded-lg border border-status-destructive/40 bg-status-destructive/10 p-3 text-sm text-status-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              {t('postgresStudio.preview.destructive')}
            </p>
          )}
          {plan && plan.mode === 'autocommit' && (
            <p className="flex items-start gap-2 rounded-lg border border-status-warning/40 bg-status-warning/10 p-3 text-sm text-status-warning">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
              {t('postgresStudio.preview.autocommitHint')}
            </p>
          )}
          {pending?.busy && !plan && <p className="text-sm text-on-surface-variant">{t('postgresStudio.preview.compiling')}</p>}
          {plan && (
            <pre className="whitespace-pre-wrap break-words rounded-lg border border-outline-variant bg-surface-container-lowest p-3 font-mono text-xs text-on-surface">
              {plan.statements.map((statement) => `${statement};`).join('\n\n')}
            </pre>
          )}
          {pending?.error && (
            <p role="alert" className="whitespace-pre-wrap rounded-lg border border-status-destructive/40 bg-status-destructive/10 p-3 font-mono text-xs text-status-destructive">
              {pending.error}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="secondary" onClick={onCancel} disabled={pending?.busy && Boolean(plan)}>
            {t('common.cancel')}
          </Button>
          <Button
            variant={plan?.destructive ? 'destructive' : 'primary'}
            onClick={onExecute}
            disabled={!plan || pending?.busy}
            data-testid="sql-preview-execute"
          >
            <Play className="h-4 w-4" />
            {t('postgresStudio.preview.execute')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
