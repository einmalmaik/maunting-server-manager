import { useEffect, useState } from 'react'
import { emit } from '@tauri-apps/api/event'
import { disable, enable, isEnabled } from '@tauri-apps/plugin-autostart'
import { useTranslation } from 'react-i18next'

import { Button, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { OVERLAY_ZUSTAND_TEST } from '../sprachKoordination'
import {
  konfigAendern,
  overlayTesten,
  setzeStatus,
  updatePruefen,
  type AgentStatus,
} from '../tauri'
import { ComputerUseSektion } from './ComputerUseSektion'
import { ArtefaktInstallationSektion } from './ArtefaktInstallationSektion'
import { BrowserInstallationSektion } from './BrowserInstallationSektion'
import { Systembereich } from './Systembereich'
import { Hotkeys } from './Hotkeys'

const STATUS_REIHE: AgentStatus[] = ['bereit', 'hoert', 'denkt', 'spricht']

export function DesktopIntegration({
  onKonfigAenderung,
  offline = false,
}: {
  onKonfigAenderung?: () => void
  offline?: boolean
}) {
  const { t } = useTranslation()
  const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)
  const [autostart, setAutostart] = useState<boolean | null>(null)
  const [status, setStatus] = useState<AgentStatus>('bereit')
  const [prueftUpdate, setPrueftUpdate] = useState(false)

  // Android hat keinen Autostart mehr: Terminerinnerungen plant die App bei
  // Android ein, und die zeigt sie auch, wenn die App nie gestartet wurde.
  useEffect(() => {
    if (isAndroid) return
    void isEnabled()
      .then(setAutostart)
      .catch(() => setAutostart(null))
  }, [isAndroid])

  async function autostartUmschalten(an: boolean) {
    try {
      if (an) {
        await enable()
      } else {
        await disable()
      }
      await konfigAendern({ autostart_aktiv: an })
      setAutostart(an)
      onKonfigAenderung?.()
    } catch {
      toast.error(t('mss.einstellungen.autostartFehler'))
    }
  }

  async function statusWechseln(neu: AgentStatus) {
    setStatus(neu)
    await setzeStatus(neu).catch(() => {})
    // Das Schaufenster-Ereignis kommt von hier und nur von hier — nicht aus
    // `setze_status` in Rust: den Befehl ruft auch die Zustandsverdrahtung
    // echter Sitzungen, und das Schaufenster folgte dann der fremden
    // Sitzung statt der geklickten Diagnose-Form.
    await emit(OVERLAY_ZUSTAND_TEST, neu).catch(() => {})
  }

  return (
    <section className="msm-card flex flex-col gap-4 p-5">
      <h2 className="text-sm font-medium text-on-surface">
        {isAndroid ? t('mss.einstellungen.tab.app') : t('mss.einstellungen.desktopIntegration')}
      </h2>

      {!isAndroid && (
        <>
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-on-surface">{t('mss.einstellungen.autostart')}</p>
            <Switch
              checked={autostart === true}
              disabled={autostart === null}
              onCheckedChange={(an) => void autostartUmschalten(an)}
              aria-label={t('mss.einstellungen.autostart')}
            />
          </div>

          <ArtefaktInstallationSektion onKonfigAenderung={onKonfigAenderung} />

          <Hotkeys />

          {/* Der Systembereich ist eine Einstellung des Panels. Offline gibt
              es nichts zu lesen, und ein Klick endete in einem Fehlertoast. */}
          {!offline && <Systembereich />}
        </>
      )}

      <ComputerUseSektion onKonfigAenderung={onKonfigAenderung} />

      <div className={isAndroid ? '' : 'border-t border-outline-variant/40 pt-4'}>
        <p className="mb-3 text-sm text-on-surface">{t('mss.einstellungen.diagnose')}</p>
        <div className="flex flex-wrap gap-2">
          {STATUS_REIHE.map((s) => (
            <button
              key={s}
              onClick={() => void statusWechseln(s)}
              className={`rounded-lg border px-3.5 py-2 text-sm transition-colors ${
                status === s
                  ? 'border-primary/40 bg-primary/10 text-primary'
                  : 'border-outline-variant/40 bg-surface-container-low/40 text-on-surface-variant hover:text-on-surface'
              }`}
            >
              {t(`mss.einstellungen.status.${s}`)}
            </button>
          ))}
        </div>
        <div className="mt-3">
          <Button variant="secondary" onClick={() => void overlayTesten().catch(() => {})}>
            {t('mss.einstellungen.overlayTesten')}
          </Button>
        </div>
      </div>

      <BrowserInstallationSektion />

      <div className="border-t border-outline-variant/40 pt-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-medium text-on-surface">{t('mss.einstellungen.systemUpdates')}</p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            disabled={prueftUpdate}
            onClick={() => {
              void (async () => {
                setPrueftUpdate(true)
                try {
                  const res = await updatePruefen()
                  if (res.verfuegbar) {
                    toast.success(t('mss.einstellungen.updateAvailableVersion', { version: res.neue_version }))
                  } else {
                    toast.success(t('mss.einstellungen.updateUpToDate'))
                  }
                } catch {
                  toast.error(t('mss.einstellungen.updateCheckError'))
                } finally {
                  setPrueftUpdate(false)
                }
              })()
            }}
          >
            {prueftUpdate ? t('mss.einstellungen.checkingUpdates') : t('mss.einstellungen.checkForUpdates')}
          </Button>
        </div>
      </div>
    </section>
  )
}
