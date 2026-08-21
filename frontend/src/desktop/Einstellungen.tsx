/**
 * Die Einstellungen der Desktop-App — dieselbe Formensprache wie die
 * Panel-Einstellungen (Karten, Schalter, Knöpfe aus Singra/UI), aber eigene
 * Inhalte: was dieser **Rechner** tut, nicht was das Panel tut.
 */
import { useEffect, useRef, useState } from 'react'
import { disable, enable, isEnabled } from '@tauri-apps/plugin-autostart'
import { useTranslation } from 'react-i18next'

import { Button, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { Gefahrenzone } from './Gefahrenzone'
import { WakewordEinrichtung } from './WakewordEinrichtung'
import {
  duckingSetzen,
  hotkeysSetzen,
  konfigLaden,
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

      <Hotkeys />

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

/**
 * Baut aus einem Tastendruck die Kombination im Format der Registrierung
 * („Ctrl+Shift+K"). `null` heißt: nur Modifier gedrückt — weiter warten.
 * Der eigentliche Prüfer sitzt in Rust (`hotkey_pruefen`); was er ablehnt,
 * kommt als Fehlermeldung zurück, und der alte Hotkey bleibt.
 */
function komboAusEreignis(ereignis: KeyboardEvent): string | null {
  const code = ereignis.code
  if (!code || /^(Control|Alt|Shift|Meta)/.test(code)) return null
  const teile: string[] = []
  if (ereignis.ctrlKey) teile.push('Ctrl')
  if (ereignis.altKey) teile.push('Alt')
  if (ereignis.shiftKey) teile.push('Shift')
  if (ereignis.metaKey) teile.push('Super')
  const taste = code.startsWith('Key')
    ? code.slice(3)
    : code.startsWith('Digit')
      ? code.slice(5)
      : code
  return [...teile, taste].join('+')
}

/** Die Vorgaben — dieselben Werte wie `konfig::Default` in Rust. */
const HOTKEY_VORGABEN = { fenster: 'Alt+Space', sprache: 'Alt+Shift+Space' } as const

type HotkeyArt = 'fenster' | 'sprache'

/**
 * Zwei Hotkeys, je einzeln abschaltbar: einer fürs Hauptfenster, einer für
 * die Sprachsitzung im Overlay. Geändert wird per Aufnahme (nächster
 * Tastendruck), registriert und gespeichert in Rust (`hotkeys_setzen`) —
 * eine belegte Kombination kommt als Fehler zurück, nichts stellt sich um.
 */
function Hotkeys() {
  const { t } = useTranslation()
  const [werte, setWerte] = useState<Record<HotkeyArt, string | null> | null>(null)
  const [aufnahme, setAufnahme] = useState<HotkeyArt | null>(null)
  // Die letzte Kombination je Seite: der Aktiv-Schalter stellt sie wieder
  // her — nicht die Werksvorgabe, die der Benutzer längst ersetzt hat.
  const zuletzt = useRef({ ...HOTKEY_VORGABEN } as Record<HotkeyArt, string>)

  useEffect(() => {
    void konfigLaden()
      .then((konfig) => {
        const geladen = { fenster: konfig.hotkey_fenster, sprache: konfig.hotkey_sprache }
        if (geladen.fenster) zuletzt.current.fenster = geladen.fenster
        if (geladen.sprache) zuletzt.current.sprache = geladen.sprache
        setWerte(geladen)
      })
      .catch(() => setWerte(null))
  }, [])

  async function anwenden(neu: Record<HotkeyArt, string | null>) {
    try {
      await hotkeysSetzen(neu.fenster, neu.sprache)
      if (neu.fenster) zuletzt.current.fenster = neu.fenster
      if (neu.sprache) zuletzt.current.sprache = neu.sprache
      setWerte(neu)
    } catch (fehler) {
      toast.error(String(fehler))
    }
  }

  useEffect(() => {
    if (!aufnahme || !werte) return
    const taste = (ereignis: KeyboardEvent) => {
      ereignis.preventDefault()
      ereignis.stopPropagation()
      if (ereignis.key === 'Escape') {
        setAufnahme(null)
        return
      }
      const kombi = komboAusEreignis(ereignis)
      if (!kombi) return
      setAufnahme(null)
      void anwenden({ ...werte, [aufnahme]: kombi })
    }
    window.addEventListener('keydown', taste, true)
    return () => window.removeEventListener('keydown', taste, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aufnahme, werte])

  const zeile = (art: HotkeyArt) => {
    const wert = werte?.[art] ?? null
    return (
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-on-surface">{t(`mss.einstellungen.hotkey.${art}`)}</p>
          <p className="text-xs text-on-surface-variant">
            {wert ? <kbd className="font-mono">{wert}</kbd> : t('mss.einstellungen.hotkey.aus')}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={!werte || wert === null}
            onClick={() => setAufnahme(aufnahme === art ? null : art)}
          >
            {aufnahme === art
              ? t('mss.einstellungen.hotkey.druecken')
              : t('mss.einstellungen.hotkey.aendern')}
          </Button>
          <Switch
            checked={wert !== null}
            disabled={!werte}
            onCheckedChange={(an) => {
              if (!werte) return
              setAufnahme(null)
              void anwenden({ ...werte, [art]: an ? zuletzt.current[art] : null })
            }}
            aria-label={t(`mss.einstellungen.hotkey.${art}`)}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 border-t border-outline-variant/40 pt-4">
      <div>
        <p className="text-sm text-on-surface">{t('mss.einstellungen.hotkey.titel')}</p>
        <p className="text-xs text-on-surface-variant">
          {t('mss.einstellungen.hotkey.hinweis')}
        </p>
      </div>
      {zeile('fenster')}
      {zeile('sprache')}
    </div>
  )
}
