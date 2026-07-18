import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Check, Clipboard, Loader2, Server, Terminal, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { SanitizedApiError } from '@/api/client'
import {
  approveNodeEnrollment,
  getNodeInstallCommand,
  getPendingNodeEnrollments,
  type PendingNodeEnrollment,
} from '@/services/nodeEnrollmentService'
import { toast } from '@/stores/toastStore'

interface NodeEnrollmentDialogProps {
  onClose: () => void
  onManualSetup: () => void
  onApproved: () => Promise<void>
}

const POLL_INTERVAL_MS = 4000

function safeErrorMessage(error: unknown, fallback: string): string {
  return error instanceof SanitizedApiError ? error.message : fallback
}

export function NodeEnrollmentDialog({
  onClose,
  onManualSetup,
  onApproved,
}: NodeEnrollmentDialogProps) {
  const { t, i18n } = useTranslation()
  const titleId = useId()
  const dialogRef = useRef<HTMLElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const onCloseRef = useRef(onClose)
  const [command, setCommand] = useState('')
  const [commandLoading, setCommandLoading] = useState(true)
  const [commandError, setCommandError] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingNodeEnrollment[]>([])
  const [pendingLoading, setPendingLoading] = useState(true)
  const [pendingError, setPendingError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [approvingId, setApprovingId] = useState<number | null>(null)

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null

    closeButtonRef.current?.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onCloseRef.current()
        return
      }

      if (event.key !== 'Tab') return

      const dialog = dialogRef.current
      if (!dialog) return

      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ))

      if (focusable.length === 0) {
        event.preventDefault()
        dialog.focus()
        return
      }

      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement

      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || !dialog.contains(active))) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [])

  const loadCommand = useCallback(async () => {
    setCommandLoading(true)
    setCommandError(null)
    try {
      const result = await getNodeInstallCommand()
      setCommand(result.command)
    } catch (error: unknown) {
      setCommandError(safeErrorMessage(error, t('nodes.enrollment.commandFailed')))
    } finally {
      setCommandLoading(false)
    }
  }, [t])

  const loadPending = useCallback(async (showLoading = false) => {
    if (showLoading) setPendingLoading(true)
    try {
      const enrollments = await getPendingNodeEnrollments()
      setPending(enrollments)
      setPendingError(null)
    } catch (error: unknown) {
      setPendingError(safeErrorMessage(error, t('nodes.enrollment.pendingFailed')))
    } finally {
      setPendingLoading(false)
    }
  }, [t])

  useEffect(() => {
    void loadCommand()

    void loadPending(true)
    const interval = window.setInterval(() => void loadPending(), POLL_INTERVAL_MS)
    return () => {
      window.clearInterval(interval)
    }
  }, [loadCommand, loadPending])

  const copyCommand = async () => {
    if (!command) return
    try {
      await navigator.clipboard.writeText(command)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error(t('nodes.enrollment.copyFailed'))
    }
  }

  const approve = async (enrollment: PendingNodeEnrollment) => {
    setApprovingId(enrollment.id)
    try {
      await approveNodeEnrollment(enrollment.id)
      setPending((items) => items.filter((item) => item.id !== enrollment.id))
      await onApproved()
      toast.success(t('nodes.enrollment.approved', { name: enrollment.name }))
    } catch (error: unknown) {
      toast.error(safeErrorMessage(error, t('nodes.enrollment.approveFailed')))
      await loadPending()
    } finally {
      setApprovingId(null)
    }
  }

  const formatExpiry = (value: string) =>
    new Intl.DateTimeFormat(i18n.language, {
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(value))

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/60 p-3 backdrop-blur-sm sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <section
        ref={dialogRef}
        className="msm-card my-auto w-full max-w-3xl overflow-hidden p-0 shadow-2xl"
        tabIndex={-1}
      >
        <header className="flex items-start justify-between gap-4 border-b border-outline-variant px-5 py-4 sm:px-6">
          <div>
            <h2 id={titleId} className="font-headline text-body-lg text-primary">
              {t('nodes.enrollment.title')}
            </h2>
            <p className="mt-1 max-w-2xl text-sm text-on-surface-variant">
              {t('nodes.enrollment.subtitle')}
            </p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="msm-btn-secondary shrink-0 p-2"
            aria-label={t('common.close')}
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="max-h-[calc(100vh-9rem)] space-y-6 overflow-y-auto px-5 py-5 sm:px-6">
          <ol className="grid gap-3 text-sm sm:grid-cols-3">
            {(['copy', 'run', 'confirm'] as const).map((step, index) => (
              <li key={step} className="flex gap-3 text-on-surface-variant">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-primary/40 bg-primary/10 font-mono text-xs text-primary">
                  {index + 1}
                </span>
                <span>{t(`nodes.enrollment.steps.${step}`)}</span>
              </li>
            ))}
          </ol>

          <section aria-labelledby={`${titleId}-command`}>
            <div className="mb-2 flex items-center gap-2">
              <Terminal className="h-4 w-4 text-primary" />
              <h3 id={`${titleId}-command`} className="font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                {t('nodes.enrollment.commandLabel')}
              </h3>
            </div>
            <div className="flex min-h-20 items-center gap-3 rounded-xl border border-outline-variant bg-surface-container-low p-3 sm:p-4">
              {commandLoading ? (
                <Loader2 className="mx-auto h-5 w-5 animate-spin text-primary" aria-label={t('common.loading')} />
              ) : commandError ? (
                <div className="flex w-full flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-sm text-status-error">{commandError}</p>
                  <button
                    type="button"
                    className="msm-btn-secondary shrink-0 px-3 py-2 text-sm"
                    onClick={() => void loadCommand()}
                  >
                    {t('nodes.enrollment.retryCommand')}
                  </button>
                </div>
              ) : (
                <>
                  <code className="min-w-0 flex-1 overflow-x-auto whitespace-pre-wrap break-all font-mono text-sm leading-6 text-on-surface">
                    {command}
                  </code>
                  <button
                    type="button"
                    className="msm-btn-secondary inline-flex shrink-0 items-center gap-2 px-3 py-2"
                    aria-label={copied ? t('nodes.enrollment.copied') : t('nodes.enrollment.copy')}
                    onClick={() => void copyCommand()}
                  >
                    {copied ? <Check className="h-4 w-4 text-status-success" /> : <Clipboard className="h-4 w-4" />}
                    <span className="hidden sm:inline">
                      {copied ? t('nodes.enrollment.copied') : t('nodes.enrollment.copy')}
                    </span>
                  </button>
                  <span className="sr-only" role="status" aria-live="polite" aria-atomic="true">
                    {copied ? t('nodes.enrollment.copied') : ''}
                  </span>
                </>
              )}
            </div>
          </section>

          <section aria-labelledby={`${titleId}-pending`}>
            <div className="mb-2 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <Server className="h-4 w-4 text-primary" />
                <h3 id={`${titleId}-pending`} className="font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                  {t('nodes.enrollment.pendingTitle')}
                </h3>
              </div>
              {!pendingLoading && !pendingError && (
                <span className="text-xs text-on-surface-variant">{t('nodes.enrollment.live')}</span>
              )}
            </div>

            <div className="divide-y divide-outline-variant overflow-hidden rounded-xl border border-outline-variant">
              {pendingLoading ? (
                <div className="flex min-h-24 items-center justify-center">
                  <Loader2 className="h-5 w-5 animate-spin text-primary" aria-label={t('common.loading')} />
                </div>
              ) : pendingError ? (
                <div className="p-4 text-sm text-status-error">{pendingError}</div>
              ) : pending.length === 0 ? (
                <div className="p-5 text-center">
                  <p className="text-sm font-medium text-on-surface">{t('nodes.enrollment.pendingEmpty')}</p>
                  <p className="mt-1 text-xs text-on-surface-variant">{t('nodes.enrollment.pendingEmptyHint')}</p>
                </div>
              ) : (
                pending.map((enrollment) => (
                  <div key={enrollment.id} className="flex flex-col gap-4 bg-surface-container-low p-4 sm:flex-row sm:items-center">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <strong className="text-sm text-on-surface">{enrollment.name}</strong>
                        <span className="rounded-md border border-primary/30 bg-primary/10 px-2 py-0.5 font-mono text-sm font-semibold tracking-widest text-primary">
                          {enrollment.display_code}
                        </span>
                      </div>
                      <p className="mt-1 break-all font-mono text-xs text-on-surface-variant">{enrollment.host}</p>
                      <p className="mt-1 text-xs text-on-surface-variant">
                        {t('nodes.enrollment.expiresAt', { time: formatExpiry(enrollment.expires_at) })}
                      </p>
                    </div>
                    <button
                      type="button"
                      className="msm-btn-primary inline-flex items-center justify-center gap-2 px-4 py-2 sm:shrink-0"
                      disabled={approvingId !== null}
                      onClick={() => void approve(enrollment)}
                    >
                      {approvingId === enrollment.id && <Loader2 className="h-4 w-4 animate-spin" />}
                      {t('nodes.enrollment.approve')}
                    </button>
                  </div>
                ))
              )}
            </div>
          </section>
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-outline-variant px-5 py-3 sm:px-6">
          <p className="hidden text-xs text-on-surface-variant sm:block">{t('nodes.enrollment.manualHint')}</p>
          <button type="button" className="msm-btn-secondary ml-auto px-3 py-2 text-sm" onClick={onManualSetup}>
            {t('nodes.enrollment.manual')}
          </button>
        </footer>
      </section>
    </div>
  )
}
