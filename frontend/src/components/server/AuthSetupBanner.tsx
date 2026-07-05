import { useTranslation } from 'react-i18next'
import { ShieldAlert, X } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'

interface Props {
  serverId: number
}

/**
 * Banner der angezeigt wird, waehrend ein Server-Container auf einen interaktiven
 * Auth-Flow wartet (z.B. Hytale OAuth-Refresh-Token abgelaufen). Die eigentliche
 * Auth-URL erscheint automatisch im Konsolen-Tab als klickbarer Link (URL_RE).
 *
 * Generisch: kein Wissen ueber das konkrete Spiel. Wird durch das
 * Server-Flag ``auth_required=True`` ein- und ausgeblendet.
 */
export function AuthSetupBanner({ serverId }: Props) {
  const { t } = useTranslation()

  const cancel = async () => {
    try {
      const result = await api<{ message: string; container_stopped: boolean }>(
        `/servers/${serverId}/auth-setup/cancel`,
        { method: 'POST', body: JSON.stringify({}) }
      )
      toast.success(t('server.authSetup.cancelled', { defaultValue: 'Auth-Setup abgebrochen' }))
      void result
    } catch (err) {
      const message =
        err && typeof err === 'object' && 'message' in err
          ? String((err as { message?: unknown }).message ?? '')
          : ''
      toast.error(
        t('server.authSetup.cancelFailed', {
          defaultValue: 'Auth-Setup konnte nicht abgebrochen werden',
        }) + (message ? `: ${message}` : '')
      )
    }
  }

  return (
    <div
      role="alert"
      data-testid="auth-setup-banner"
      className="rounded-md border border-status-warning bg-status-warning/10 px-4 py-3 flex items-start gap-3"
    >
      <ShieldAlert className="w-5 h-5 text-status-warning shrink-0 mt-0.5" aria-hidden="true" />
      <div className="flex-1 min-w-0">
        <p className="font-medium text-status-warning">
          {t('server.authSetup.required', { defaultValue: 'Setup-Autorisierung erforderlich' })}
        </p>
        <p className="text-sm text-secondary mt-1">
          {t('server.authSetup.instructions', {
            defaultValue:
              'Im Konsolen-Tab erscheint eine URL — bitte im Browser öffnen und Anmeldung abschließen. Der Server startet danach automatisch.',
          })}
        </p>
      </div>
      <button
        type="button"
        onClick={cancel}
        aria-label={t('server.authSetup.cancel', { defaultValue: 'Abbrechen' })}
        className="text-secondary hover:text-on-surface shrink-0"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  )
}