/**
 * Die Einstellungen der Desktop-App — dieselbe Formensprache wie die
 * Panel-Einstellungen (Karten, Schalter, Knöpfe aus Singra/UI), aber eigene
 * Inhalte: was dieser **Rechner** tut, nicht was das Panel tut.
 */
import { useEffect, useState } from 'react'
import { disable, enable, isEnabled } from '@tauri-apps/plugin-autostart'
import { useTranslation } from 'react-i18next'

import { Button, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { Gefahrenzone } from './Gefahrenzone'
import { WakewordEinrichtung } from './WakewordEinrichtung'
import {
  duckingSetzen,
  overlaySichtbar,
  setzeStatus,
  type AgentStatus,
} from './tauri'

const STATUS_REIHE: AgentStatus[] = ['bereit', 'hoert', 'denkt', 'spricht']

export function Einstellungen() {
  const { t } = useTranslation()

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <DesktopIntegration />
      <WakewordEinrichtung />
      <Gefahrenzone />
      <p className="text-center text-xs text-on-surface-variant/60">
        {t('mss.einstellungen.fussnote')}
      </p>
    </div>
  )
}

function DesktopIntegration() {
  const { t } = useTranslation()
  const [autostart, setAutostart] = useState<boolean | null>(null)
  const [status, setStatus] = useState<AgentStatus>('bereit')
  const [overlayAn, setOverlayAn] = useState(false)
  const [duckt, setDuckt] = useState(false)

  useEffect(() => {
    void isEnabled()
      .then(setAutostart)
      .catch(() => setAutostart(null))
  }, [])

  async function autostartUmschalten(an: boolean) {
    try {
      if (an) {
        await enable()
      } else {
        await disable()
      }
      setAutostart(an)
    } catch {
      toast.error(t('mss.einstellungen.autostartFehler'))
    }
  }

  async function statusWechseln(neu: AgentStatus) {
    setStatus(neu)
    await setzeStatus(neu).catch(() => {})
  }

  async function overlayUmschalten() {
    const neu = !overlayAn
    setOverlayAn(neu)
    await overlaySichtbar(neu).catch(() => {})
  }

  async function duckingTesten() {
    setDuckt(true)
    try {
      await duckingSetzen(true)
      await new Promise((fertig) => setTimeout(fertig, 3000))
      await duckingSetzen(false)
    } finally {
      setDuckt(false)
    }
  }

  return (
    <section className="msm-card flex flex-col gap-4 p-5">
      <h2 className="text-sm font-medium text-on-surface">
        {t('mss.einstellungen.desktopIntegration')}
      </h2>

      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-on-surface">{t('mss.einstellungen.autostart')}</p>
          <p className="text-xs text-on-surface-variant">
            {t('mss.einstellungen.autostartHinweis')}
          </p>
        </div>
        <Switch
          checked={autostart === true}
          disabled={autostart === null}
          onCheckedChange={(an) => void autostartUmschalten(an)}
          aria-label={t('mss.einstellungen.autostart')}
        />
      </div>

      <div className="border-t border-outline-variant/40 pt-4">
        <p className="text-sm text-on-surface">{t('mss.einstellungen.diagnose')}</p>
        <p className="mb-3 text-xs text-on-surface-variant">
          {t('mss.einstellungen.diagnoseHinweis')}
        </p>
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
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => void overlayUmschalten()}>
            {overlayAn
              ? t('mss.einstellungen.overlayAus')
              : t('mss.einstellungen.overlayAn')}
          </Button>
          <Button variant="secondary" onClick={() => void duckingTesten()} disabled={duckt}>
            {duckt
              ? t('mss.einstellungen.duckingLaeuft')
              : t('mss.einstellungen.duckingTesten')}
          </Button>
        </div>
      </div>
    </section>
  )
}
