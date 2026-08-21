/**
 * Die Einstellungen der Desktop-App — dieselbe Formensprache wie die
 * Panel-Einstellungen: eine Reiterleiste oben (`TabBar`, dieselbe Komponente
 * wie `/settings` und `/profile` im Panel), darunter Karten. Eigene Inhalte:
 * was dieser **Rechner** tut, nicht was das Panel tut.
 *
 * Vier Reiter: Desktop-Integration (Autostart, Hotkeys, Diagnose), Wake-Word
 * (Kalibrierung, Aktiv-Schalter), Audio (Geräteauswahl, Ducking) und die
 * Gefahrenzone. `?tab=wakeword` wählt einen Reiter vor — der Weg des
 * Neukalibrierungs-Hinweises nach einer Umbenennung.
 */
import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { disable, enable, isEnabled } from '@tauri-apps/plugin-autostart'
import { AlertTriangle, Mic, MonitorCog, Volume2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { registriereAudioGeraete } from '@/components/ai/voice/audioGeraete'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { Button, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { Gefahrenzone } from './Gefahrenzone'
import { WakewordEinrichtung } from './WakewordEinrichtung'
import {
  audioGeraete,
  duckingSetzen,
  hotkeysSetzen,
  konfigLaden,
  konfigSpeichern,
  overlayTesten,
  setzeStatus,
  wakewordLauschen,
  type AgentStatus,
  type AppKonfig,
  type AudioGeraete,
} from './tauri'

const STATUS_REIHE: AgentStatus[] = ['bereit', 'hoert', 'denkt', 'spricht']

type EinstellungsTab = 'desktop' | 'wakeword' | 'audio' | 'gefahr'

const TABS: TabDef<EinstellungsTab>[] = [
  { id: 'desktop', labelKey: 'mss.einstellungen.tab.desktop', icon: MonitorCog },
  { id: 'wakeword', labelKey: 'mss.einstellungen.tab.wakeword', icon: Mic },
  { id: 'audio', labelKey: 'mss.einstellungen.tab.audio', icon: Volume2 },
  { id: 'gefahr', labelKey: 'mss.einstellungen.tab.gefahr', icon: AlertTriangle, variant: 'danger' },
]

function tabAusSuche(suche: string): EinstellungsTab {
  const wunsch = new URLSearchParams(suche).get('tab')
  return TABS.some((tab) => tab.id === wunsch) ? (wunsch as EinstellungsTab) : 'desktop'
}

export function Einstellungen() {
  const { t } = useTranslation()
  const ort = useLocation()
  const [tab, setTab] = useState<EinstellungsTab>(() => tabAusSuche(ort.search))

  // Eine spätere Navigation mit `?tab=…` (Neukalibrierungs-Hinweis) soll auch
  // dann greifen, wenn die Einstellungen schon offen sind.
  useEffect(() => {
    setTab(tabAusSuche(ort.search))
  }, [ort.search])

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <TabBar
        tabs={TABS}
        active={tab}
        onChange={setTab}
        ariaLabel={t('mss.app.einstellungen')}
      />
      {tab === 'desktop' && <DesktopIntegration />}
      {tab === 'wakeword' && <WakewordEinrichtung />}
      {tab === 'audio' && <AudioEinstellungen />}
      {tab === 'gefahr' && <Gefahrenzone />}
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
        <div className="mt-3">
          {/* Exakt derselbe Weg wie Sprach-Hotkey und Wake-Word — der Knopf
              startet die echte Sitzung im Overlay bzw. beendet sie. Vorher
              zeigte er nur das leere, durchsichtige Fenster: „nichts
              passiert" war die korrekte Beschreibung. */}
          <Button variant="secondary" onClick={() => void overlayTesten().catch(() => {})}>
            {t('mss.einstellungen.overlayTesten')}
          </Button>
          <p className="mt-1 text-xs text-on-surface-variant">
            {t('mss.einstellungen.overlayTestenHinweis')}
          </p>
        </div>
      </div>
    </section>
  )
}

/**
 * Der Audio-Reiter: welches Mikrofon hört, welcher Lautsprecher spricht —
 * unabhängig vom Windows-Standard. Gespeichert wird nur der Gerätename
 * (konfig.json); „Windows-Standard" heißt: dem System folgen, auch wenn es
 * sich ändert. Ein gewähltes Gerät, das gerade fehlt, fällt still auf den
 * Standard zurück — ein abgezogenes USB-Mikrofon legt nichts lahm.
 */
function AudioEinstellungen() {
  const { t } = useTranslation()
  const [geraete, setGeraete] = useState<AudioGeraete | null>(null)
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  const [duckt, setDuckt] = useState(false)

  useEffect(() => {
    void audioGeraete()
      .then(setGeraete)
      .catch(() => setGeraete(null))
    void konfigLaden()
      .then(setKonfig)
      .catch(() => setKonfig(null))
  }, [])

  async function waehlen(feld: 'audio_eingabe' | 'audio_ausgabe', wert: string) {
    if (!konfig) return
    const neu: AppKonfig = { ...konfig, [feld]: wert === '' ? null : wert }
    setKonfig(neu)
    try {
      await konfigSpeichern(neu)
      // Sofort wirksam für Sitzungen in diesem Fenster; das Overlay lädt die
      // Wahl bei jedem Sitzungsstart frisch, Rust (Wake-Word) liest sie je
      // Aufnahme selbst.
      registriereAudioGeraete(neu.audio_eingabe, neu.audio_ausgabe)
      // Der Lausch-Thread hält sein Mikrofon offen, bis er endet — läuft er,
      // einmal durchstarten, damit das neue Gerät auch wirklich hört.
      if (feld === 'audio_eingabe' && neu.wakeword_aktiv) {
        await wakewordLauschen(false)
        await wakewordLauschen(true)
      }
    } catch (fehler) {
      toast.error(String(fehler))
    }
  }

  const auswahl = (
    feld: 'audio_eingabe' | 'audio_ausgabe',
    liste: string[],
    standard: string | null,
  ) => {
    const wert = konfig?.[feld] ?? ''
    return (
      <select
        value={wert}
        onChange={(e) => void waehlen(feld, e.target.value)}
        disabled={konfig === null}
        className="msm-input"
        aria-label={t(`mss.audio.${feld === 'audio_eingabe' ? 'eingabe' : 'ausgabe'}`)}
      >
        <option value="">
          {standard
            ? t('mss.audio.standardMit', { name: standard })
            : t('mss.audio.standard')}
        </option>
        {/* Ein gespeichertes Gerät, das gerade fehlt, bleibt wählbar sichtbar —
            sonst spränge die Anzeige stumm auf den Standard, obwohl die Wahl
            gespeichert bleibt. */}
        {wert !== '' && !liste.includes(wert) && (
          <option value={wert}>{t('mss.audio.fehlt', { name: wert })}</option>
        )}
        {liste.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
    )
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
      <h2 className="text-sm font-medium text-on-surface">{t('mss.audio.titel')}</h2>
      <p className="text-xs text-on-surface-variant">{t('mss.audio.hinweis')}</p>

      <div className="flex flex-col gap-1.5">
        <label className="text-sm text-on-surface">{t('mss.audio.eingabe')}</label>
        {auswahl('audio_eingabe', geraete?.eingaenge ?? [], geraete?.standard_eingang ?? null)}
        <p className="text-xs text-on-surface-variant">{t('mss.audio.eingabeHinweis')}</p>
      </div>

      <div className="flex flex-col gap-1.5">
        <label className="text-sm text-on-surface">{t('mss.audio.ausgabe')}</label>
        {auswahl('audio_ausgabe', geraete?.ausgaenge ?? [], geraete?.standard_ausgang ?? null)}
        <p className="text-xs text-on-surface-variant">{t('mss.audio.ausgabeHinweis')}</p>
      </div>

      <div className="border-t border-outline-variant/40 pt-4">
        <p className="text-sm text-on-surface">{t('mss.audio.ducking')}</p>
        <p className="mb-3 text-xs text-on-surface-variant">{t('mss.audio.duckingHinweis')}</p>
        <Button variant="secondary" onClick={() => void duckingTesten()} disabled={duckt}>
          {duckt
            ? t('mss.einstellungen.duckingLaeuft')
            : t('mss.einstellungen.duckingTesten')}
        </Button>
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
