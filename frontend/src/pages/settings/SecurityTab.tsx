/**
 * Settings-Tab „Sicherheit“:
 * 1) Konfigurierbare Rate-Limits (Login/Auth + globales API-Limit) — panel.settings.*
 * 2) Rotation des Managed-Postgres-Cluster-Admins (msm_admin) — system.secrets.rotate
 *
 * Rate-Limits und Rotation sind bewusst getrennt permission-gated, damit
 * Admins mit Settings-Rechten Limits anpassen können, ohne Secret-Rotation
 * freizuschalten (und umgekehrt).
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Gauge, KeyRound, Save, ShieldAlert } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Button } from '@/components/ui/Button'
import { NumberStepper } from '@/components/ui/NumberStepper'
import {
  formatRotateSuccessSummary,
  mapRotateAdminResult,
} from '@/services/securityRotatePresentation'
import {
  EMPTY_PANEL_SETTINGS,
  PanelSettings,
  RATE_LIMIT_AUTH_DEFAULT,
  RATE_LIMIT_AUTH_MAX,
  RATE_LIMIT_AUTH_MIN,
  RATE_LIMIT_GLOBAL_DEFAULT,
  RATE_LIMIT_GLOBAL_MAX,
  RATE_LIMIT_GLOBAL_MIN,
} from './types'

/** Client-seitige Range-Prüfung vor dem POST — Backend validiert erneut. */
export function validateRateLimitAuth(value: number): string | null {
  if (!Number.isFinite(value) || !Number.isInteger(value)) {
    return 'Login-Limit muss eine ganze Zahl sein.'
  }
  if (value < RATE_LIMIT_AUTH_MIN || value > RATE_LIMIT_AUTH_MAX) {
    return `Login-Limit muss zwischen ${RATE_LIMIT_AUTH_MIN} und ${RATE_LIMIT_AUTH_MAX} liegen.`
  }
  return null
}

/** Client-seitige Range-Prüfung für das globale API-Limit. */
export function validateRateLimitGlobal(value: number): string | null {
  if (!Number.isFinite(value) || !Number.isInteger(value)) {
    return 'API-Limit muss eine ganze Zahl sein.'
  }
  if (value < RATE_LIMIT_GLOBAL_MIN || value > RATE_LIMIT_GLOBAL_MAX) {
    return `API-Limit muss zwischen ${RATE_LIMIT_GLOBAL_MIN} und ${RATE_LIMIT_GLOBAL_MAX} liegen.`
  }
  return null
}

/**
 * Parst Eingabefeld-Text zu Integer; leere/ungültige Werte → null
 * (kein stilles Fallback auf Default beim Speichern).
 */
export function parseRateLimitInput(raw: string): number | null {
  const trimmed = raw.trim()
  if (trimmed === '') return null
  if (!/^-?\d+$/.test(trimmed)) return null
  const n = Number.parseInt(trimmed, 10)
  if (!Number.isSafeInteger(n)) return null
  return n
}

export function SecurityTab() {
  const { t } = useTranslation()
  const canReadSettings = useHasPermission('panel.settings.read')
  const canWriteSettings = useHasPermission('panel.settings.write')
  const canRotate = useHasPermission('system.secrets.rotate')

  const [authLimit, setAuthLimit] = useState(String(RATE_LIMIT_AUTH_DEFAULT))
  const [globalLimit, setGlobalLimit] = useState(String(RATE_LIMIT_GLOBAL_DEFAULT))
  const [loading, setLoading] = useState(canReadSettings)
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [lastSummary, setLastSummary] = useState<string | null>(null)

  useEffect(() => {
    if (!canReadSettings) {
      setLoading(false)
      return
    }
    let active = true
    api<PanelSettings>('/settings')
      .then((data) => {
        if (!active) return
        const merged = { ...EMPTY_PANEL_SETTINGS, ...(data && typeof data === 'object' ? data : {}) }
        const auth =
          typeof merged.rate_limit_auth === 'number' && Number.isFinite(merged.rate_limit_auth)
            ? merged.rate_limit_auth
            : RATE_LIMIT_AUTH_DEFAULT
        const global =
          typeof merged.rate_limit_global === 'number' && Number.isFinite(merged.rate_limit_global)
            ? merged.rate_limit_global
            : RATE_LIMIT_GLOBAL_DEFAULT
        setAuthLimit(String(auth))
        setGlobalLimit(String(global))
      })
      .catch((err: unknown) => {
        // Kein stiller Fehler: Nutzer sieht die API-Meldung
        const message = err instanceof Error ? err.message : t('settings.loadFailed', 'Laden fehlgeschlagen')
        toast.error(message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [canReadSettings, t])

  const handleSaveRateLimits = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canWriteSettings) {
      toast.error(t('security.rateLimitNoWrite', 'Keine Berechtigung zum Speichern von Rate-Limits.'))
      return
    }

    const authParsed = parseRateLimitInput(authLimit)
    const globalParsed = parseRateLimitInput(globalLimit)

    if (authParsed === null) {
      toast.error(t('security.rateLimitAuthInvalid', 'Login-Limit muss eine ganze Zahl sein.'))
      return
    }
    if (globalParsed === null) {
      toast.error(t('security.rateLimitGlobalInvalid', 'API-Limit muss eine ganze Zahl sein.'))
      return
    }

    const authErr = validateRateLimitAuth(authParsed)
    if (authErr) {
      toast.error(authErr)
      return
    }
    const globalErr = validateRateLimitGlobal(globalParsed)
    if (globalErr) {
      toast.error(globalErr)
      return
    }

    setSaving(true)
    try {
      // Nur die beiden Rate-Limit-Felder — andere Settings-Tabs speichern separat
      await api('/settings', {
        method: 'POST',
        body: JSON.stringify({
          rate_limit_auth: authParsed,
          rate_limit_global: globalParsed,
        }),
      })
      toast.success(t('settings.saved', { defaultValue: 'Einstellungen gespeichert' }))
    } catch (err: unknown) {
      // API-4xx (Range, RBAC) und Netzwerkfehler sichtbar machen — nie still schlucken
      const message =
        err instanceof Error
          ? err.message
          : t('security.rateLimitSaveFailed', 'Rate-Limits konnten nicht gespeichert werden.')
      toast.error(message)
    } finally {
      setSaving(false)
    }
  }

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

  // Weder Settings noch Rotation: klare Meldung statt leerer Tab
  if (!canReadSettings && !canRotate) {
    return (
      <div className="msm-card p-6 text-sm text-on-surface-variant">
        {t(
          'security.noPermissionDetail',
          'Für diesen Bereich brauchst du panel.settings.read oder system.secrets.rotate (oder Owner).',
        )}
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {canReadSettings && (
        // noValidate: JS-Validierung liefert Toasts; HTML5 min/max würde Submit sonst still blocken
        <form
          noValidate
          onSubmit={(e) => void handleSaveRateLimits(e)}
          className="space-y-4"
        >
          <fieldset disabled={!canWriteSettings} className="m-0 space-y-4 border-0 p-0">
            <div className="msm-card p-6">
              <div className="mb-3 flex items-center gap-2">
                <Gauge className="h-5 w-5 text-primary" />
                <h3 className="font-headline text-lg font-semibold text-on-surface">
                  {t('security.rateLimitsTitle', {
                    defaultValue: 'API- und Login-Rate-Limits',
                  })}
                </h3>
              </div>
              <p className="mb-6 max-w-2xl text-sm text-on-surface-variant">
                {t('security.rateLimitsBody', {
                  defaultValue:
                    'Begrenzt Anfragen pro Minute pro IP. Schützt Login und die Panel-API vor Brute-Force und Überlastung. Änderungen greifen ohne Neustart.',
                })}
              </p>

              {/*
                Zwei Spalten mit gleicher Label-Mindesthöhe, damit beide
                NumberStepper auf einer Linie sitzen (kein Versatz durch
                unterschiedlich lange Labels). Links Auth (strenger/niedriger),
                rechts Global (höher) — bewusst so, Login bleibt enger als API.
              */}
              <div className="grid max-w-2xl grid-cols-1 gap-6 md:grid-cols-2 md:items-start">
                <div className="flex flex-col gap-1.5">
                  <label
                    htmlFor="rate-limit-auth"
                    className="flex min-h-[2.75rem] items-end font-label-md text-label-md uppercase tracking-wider text-on-surface-variant"
                  >
                    {t('security.rateLimitAuthLabel', {
                      defaultValue: 'Login & Authentifizierung (pro Minute)',
                    })}
                  </label>
                  <NumberStepper
                    id="rate-limit-auth"
                    value={authLimit}
                    onValueChange={setAuthLimit}
                    min={RATE_LIMIT_AUTH_MIN}
                    max={RATE_LIMIT_AUTH_MAX}
                    step={1}
                    disabled={!canWriteSettings}
                    aria-describedby="rate-limit-auth-help"
                  />
                  <div id="rate-limit-auth-help" className="space-y-1">
                    <p className="font-body-md text-xs text-on-surface-variant">
                      {t('security.rateLimitAuthHelp', {
                        defaultValue:
                          'Maximal erlaubte Login- und Passwort-Versuche pro Minute pro IP. Erhöhe diesen Wert, wenn sich mehrere Personen über dieselbe Firmen-IP anmelden.',
                      })}
                    </p>
                    <p className="text-xs text-on-surface-variant">
                      {t('security.rateLimitAuthRange', {
                        defaultValue: `Bereich: ${RATE_LIMIT_AUTH_MIN}–${RATE_LIMIT_AUTH_MAX} (Standard: ${RATE_LIMIT_AUTH_DEFAULT})`,
                        min: RATE_LIMIT_AUTH_MIN,
                        max: RATE_LIMIT_AUTH_MAX,
                        default: RATE_LIMIT_AUTH_DEFAULT,
                      })}
                    </p>
                  </div>
                </div>

                <div className="flex flex-col gap-1.5">
                  <label
                    htmlFor="rate-limit-global"
                    className="flex min-h-[2.75rem] items-end font-label-md text-label-md uppercase tracking-wider text-on-surface-variant"
                  >
                    {t('security.rateLimitGlobalLabel', {
                      defaultValue: 'Globales API-Limit (pro Minute)',
                    })}
                  </label>
                  <NumberStepper
                    id="rate-limit-global"
                    value={globalLimit}
                    onValueChange={setGlobalLimit}
                    min={RATE_LIMIT_GLOBAL_MIN}
                    max={RATE_LIMIT_GLOBAL_MAX}
                    step={1}
                    disabled={!canWriteSettings}
                    aria-describedby="rate-limit-global-help"
                  />
                  <div id="rate-limit-global-help" className="space-y-1">
                    <p className="font-body-md text-xs text-on-surface-variant">
                      {t('security.rateLimitGlobalHelp', {
                        defaultValue:
                          'Maximal erlaubte API-Aufrufe pro Minute pro IP. Erhöhe diesen Wert, wenn du eigene Skripte oder externe Tools zur Steuerung nutzt.',
                      })}
                    </p>
                    <p className="text-xs text-on-surface-variant">
                      {t('security.rateLimitGlobalRange', {
                        defaultValue: `Bereich: ${RATE_LIMIT_GLOBAL_MIN}–${RATE_LIMIT_GLOBAL_MAX} (Standard: ${RATE_LIMIT_GLOBAL_DEFAULT})`,
                        min: RATE_LIMIT_GLOBAL_MIN,
                        max: RATE_LIMIT_GLOBAL_MAX,
                        default: RATE_LIMIT_GLOBAL_DEFAULT,
                      })}
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {canWriteSettings && (
              <div className="flex justify-end">
                <Button type="submit" disabled={saving} className="px-6">
                  {saving ? (
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-on-primary border-t-transparent" />
                  ) : (
                    <Save className="h-4 w-4" />
                  )}
                  {t('settings.save', { defaultValue: 'Speichern' })}
                </Button>
              </div>
            )}
          </fieldset>
        </form>
      )}

      {canRotate && (
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
      )}

      {!canRotate && canReadSettings && (
        <p className="text-xs text-on-surface-variant">
          {t(
            'security.rotateHiddenHint',
            'Cluster-Admin-Rotation ist nur mit system.secrets.rotate (oder Owner) sichtbar.',
          )}
        </p>
      )}
    </div>
  )
}
