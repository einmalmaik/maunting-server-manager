/**
 * Settings-Tab „Sicherheit“: Rotation des Managed-Postgres-Cluster-Admins (msm_admin).
 * Nicht Panel-Login und nicht App-DB-User pro Server. Kein Passwort wird angezeigt.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyRound, ShieldAlert } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import {
  formatRotateSuccessSummary,
  mapRotateAdminResult,
} from '@/services/securityRotatePresentation'

export function SecurityTab() {
  const { t } = useTranslation()
  const canRotate = useHasPermission('system.secrets.rotate')
  const [busy, setBusy] = useState(false)
  const [lastSummary, setLastSummary] = useState<string | null>(null)

  const rotateClusterAdmin = async () => {
    if (!canRotate) {
      toast.error(t('security.noPermission', 'Keine Berechtigung zum Rotieren von Cluster-Secrets.'))
      return
    }
    const ok = await confirm({
      title: t('security.rotateConfirmTitle', 'Managed-Postgres-Admin rotieren?'),
      message: t(
        'security.rotateConfirmBody',
        'Erneuert das interne Cluster-Admin-Passwort (msm_admin) auf allen Nodes und im Panel. Das ist nicht dein Panel-Login und nicht das Passwort der Kunden-App-Datenbanken. Das neue Passwort wird dir nicht angezeigt.',
      ),
      confirmText: t('security.rotateConfirmBtn', 'Jetzt rotieren'),
      danger: true,
    })
    if (!ok) return

    setBusy(true)
    setLastSummary(null)
    try {
      const raw = await api<unknown>('/admin/managed-postgres/rotate-admin', {
        method: 'POST',
      })
      const result = mapRotateAdminResult(raw)
      if (!result.ok) {
        throw new Error(t('security.rotateFailed', 'Rotation wurde vom Server abgelehnt.'))
      }
      const summary = formatRotateSuccessSummary(result)
      setLastSummary(summary)
      toast.success(t('security.rotateSuccess', 'Cluster-Admin erfolgreich rotiert.'))
    } catch (err: unknown) {
      const message =
        err instanceof Error
          ? err.message
          : t('security.rotateFailed', 'Rotation fehlgeschlagen.')
      toast.error(message)
      setLastSummary(null)
    } finally {
      setBusy(false)
    }
  }

  if (!canRotate) {
    return (
      <div className="msm-card p-6 text-sm text-on-surface-variant">
        {t(
          'security.noPermissionDetail',
          'Für diesen Bereich brauchst du die Berechtigung system.secrets.rotate (oder Owner).',
        )}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="msm-card p-6">
        <div className="mb-3 flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-status-warning" />
          <h3 className="font-headline text-lg font-semibold text-on-surface">
            {t('security.clusterAdminTitle', 'Managed-Postgres Cluster-Admin')}
          </h3>
        </div>
        <p className="mb-4 max-w-2xl text-sm text-on-surface-variant">
          {t(
            'security.clusterAdminBody',
            'Das interne Passwort von msm_admin steuert Provisioning und Admin-DDL der Managed-Postgres-Instanz pro Node. Es ist verschlüsselt im Panel gespeichert. Rotation erneuert es auf den Nodes und im Panel — das neue Passwort erscheint nie in der UI.',
          )}
        </p>
        <ul className="mb-5 list-inside list-disc space-y-1 text-sm text-on-surface-variant">
          <li>{t('security.notPanelLogin', 'Nicht: Panel-Login-Passwort')}</li>
          <li>{t('security.notAppDbUser', 'Nicht: App-DB-User pro Gameserver (msm_s…_u…)')}</li>
          <li>{t('security.isMsmAdmin', 'Ja: Cluster-Rolle msm_admin (Managed Postgres)')}</li>
        </ul>
        <button
          type="button"
          className="msm-btn-destructive inline-flex items-center gap-2 px-4 py-2 text-sm"
          onClick={() => void rotateClusterAdmin()}
          disabled={busy}
        >
          <KeyRound className="h-4 w-4" />
          {busy
            ? t('security.rotating', 'Rotiere…')
            : t('security.rotateBtn', 'Cluster-Admin-Passwort rotieren')}
        </button>
        {lastSummary && (
          <p className="mt-4 rounded-lg border border-status-success/30 bg-status-success/10 p-3 text-sm text-on-surface">
            {lastSummary}
          </p>
        )}
      </div>
    </div>
  )
}
