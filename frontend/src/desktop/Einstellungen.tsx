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
import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { emit } from '@tauri-apps/api/event'
import { disable, enable, isEnabled } from '@tauri-apps/plugin-autostart'
import { AlertTriangle, Mic, MonitorCog, Volume2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  aktuelleVerarbeitung,
  ausgabeGeraetId,
  eingabeGeraetId,
  registriereAudioGeraete,
  registriereAudioVerarbeitung,
  type AudioVerarbeitung,
} from '@/components/ai/voice/audioGeraete'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { Button, ProgressBar, Slider, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { Gefahrenzone } from './Gefahrenzone'
import { OVERLAY_ZUSTAND_TEST } from './sprachKoordination'
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

/**
 * Wie lange nach der letzten Verarbeitungsänderung gewartet wird, bevor sie
 * in konfig.json landet — der Verstärkungsregler feuert je Tick. Registriert
 * (und damit hörbar) ist jede Änderung sofort, nur das Schreiben wartet.
 */
const VERARBEITUNG_SPEICHERN_MS = 400

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
    // Das Schaufenster-Ereignis kommt von hier und nur von hier — nicht aus
    // `setze_status` in Rust: den Befehl ruft auch die Zustandsverdrahtung
    // echter Sitzungen, und die Blase im Schaufenster folgte dann der
    // fremden Sitzung statt der geklickten Diagnose-Farbe.
    await emit(OVERLAY_ZUSTAND_TEST, neu).catch(() => {})
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
          {/* Das Schaufenster: zeigt das Overlay ohne Mikrofon und ohne
              Sitzung — die Diagnose-Knöpfe oben färben dann die Blase.
              Zweiter Druck (oder X/ESC am Fenster) schließt wieder. */}
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
  // Bündelt das Speichern der Verarbeitung. Beim Unmount bewusst NICHT
  // geräumt: die Timeout-Schließung ist in sich geschlossen (frisches Laden,
  // Speichern, kein React-State) — räumen hieße, die letzte Änderung des
  // Benutzers wegzuwerfen.
  const speicherTimer = useRef<number | null>(null)

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

  /**
   * Ein Verarbeitungsfeld stellen: sofort registrieren (wirkt live in Sitzung
   * und Testhören), gebündelt speichern — der Verstärkungsregler feuert je
   * Tick, und jeder Tick wäre sonst ein Dateischreiben. Alles lokal —
   * Chromiums eigene Kette, kein Ton verlässt dafür den Rechner. Das
   * Wake-Word ist nicht betroffen (eigene Rust-Kette), darum kein Neustart.
   *
   * Gespeichert wird über einen frischen Konfigurationsstand, in den nur die
   * vier eigenen Felder gemischt werden: der React-State stammt vom Mount,
   * und der Wake-Word-Neustart beim Gerätewechsel schreibt `wakeword_aktiv`
   * parallel in dieselbe Datei.
   */
  function verarbeitungSetzen(
    feld: 'audio_echo' | 'audio_rauschen' | 'audio_autogain' | 'audio_verstaerkung',
    wert: boolean | number,
  ) {
    if (!konfig) return
    const neu: AppKonfig = { ...konfig, [feld]: wert }
    setKonfig(neu)
    registriereAudioVerarbeitung({
      echo: neu.audio_echo,
      rauschen: neu.audio_rauschen,
      autogain: neu.audio_autogain,
      verstaerkung: neu.audio_verstaerkung,
    })
    if (speicherTimer.current !== null) window.clearTimeout(speicherTimer.current)
    speicherTimer.current = window.setTimeout(() => {
      speicherTimer.current = null
      void (async () => {
        try {
          const aktuell = await konfigLaden()
          await konfigSpeichern({
            ...aktuell,
            audio_echo: neu.audio_echo,
            audio_rauschen: neu.audio_rauschen,
            audio_autogain: neu.audio_autogain,
            audio_verstaerkung: neu.audio_verstaerkung,
          })
        } catch (fehler) {
          toast.error(String(fehler))
        }
      })()
    }, VERARBEITUNG_SPEICHERN_MS)
  }

  const verarbeitungsZeile = (
    feld: 'audio_echo' | 'audio_rauschen' | 'audio_autogain',
  ) => {
    const kurz = feld.slice('audio_'.length)
    return (
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-on-surface">{t(`mss.audio.${kurz}`)}</p>
          <p className="text-xs text-on-surface-variant">{t(`mss.audio.${kurz}Hinweis`)}</p>
        </div>
        <Switch
          checked={konfig?.[feld] ?? true}
          disabled={konfig === null}
          onCheckedChange={(an) => void verarbeitungSetzen(feld, an)}
          aria-label={t(`mss.audio.${kurz}`)}
        />
      </div>
    )
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

      <div className="flex flex-col gap-3 border-t border-outline-variant/40 pt-4">
        <div>
          <p className="text-sm text-on-surface">{t('mss.audio.verarbeitung')}</p>
          <p className="text-xs text-on-surface-variant">{t('mss.audio.verarbeitungHinweis')}</p>
        </div>
        {verarbeitungsZeile('audio_echo')}
        {verarbeitungsZeile('audio_rauschen')}
        {verarbeitungsZeile('audio_autogain')}
        <Slider
          value={Math.round((konfig?.audio_verstaerkung ?? 1) * 100)}
          min={25}
          max={400}
          step={5}
          disabled={konfig === null}
          onValueChange={(prozent) => void verarbeitungSetzen('audio_verstaerkung', prozent / 100)}
          label={t('mss.audio.verstaerkung')}
          hint={`${Math.round((konfig?.audio_verstaerkung ?? 1) * 100)} %`}
        />
        <p className="-mt-2 text-xs text-on-surface-variant">
          {t('mss.audio.verstaerkungHinweis')}
        </p>
      </div>

      <Testhoeren
        verarbeitung={{
          echo: konfig?.audio_echo ?? true,
          rauschen: konfig?.audio_rauschen ?? true,
          autogain: konfig?.audio_autogain ?? true,
          verstaerkung: konfig?.audio_verstaerkung ?? 1,
        }}
      />

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
 * Testhören wie in Discord: das eigene Mikrofon auf den gewählten Lautsprecher
 * legen und dabei den Pegel sehen — mit genau der Verarbeitung, die auch die
 * Sprachsitzung nähme. Einzige Abweichung: die Echounterdrückung ist im Test
 * aus, weil sie sonst die eigene Wiedergabe als „Echo" erkennt und wegfiltert —
 * man hörte sich leiser werden, je länger man spricht. Alles bleibt im
 * Chromium-Prozess, nichts davon geht ins Netz.
 */
function Testhoeren({ verarbeitung }: { verarbeitung: AudioVerarbeitung }) {
  const { t } = useTranslation()
  const [laeuft, setLaeuft] = useState(false)
  const [pegel, setPegel] = useState(0)
  const [fehler, setFehler] = useState<string | null>(null)
  const aufraeumen = useRef<(() => void) | null>(null)
  const gainKnoten = useRef<GainNode | null>(null)
  // Ob die Komponente noch lebt: `starten` hat zwei awaits, bevor es sein
  // Aufräumen hinterlegt. Wer in diesem Fenster den Reiter wechselt, träfe
  // ein Unmount-Cleanup auf `null` — und das Mikrofon bliebe offen, ohne
  // dass irgendetwas es noch schließen könnte.
  const verlassen = useRef(false)
  // Laufende Nummer des jüngsten Starts. Zwei schnelle Klicks (oder Klick +
  // Schalter-Neustart) liefen sonst nebeneinander durch getUserMedia, und der
  // langsamere überschriebe das Aufräumen des schnelleren — dessen Mikrofon
  // bliebe offen und wäre durch nichts mehr stoppbar.
  const startNummer = useRef(0)

  const stoppen = useCallback(() => {
    startNummer.current += 1
    aufraeumen.current?.()
    aufraeumen.current = null
    gainKnoten.current = null
    setLaeuft(false)
    setPegel(0)
  }, [])

  const starten = useCallback(async () => {
    const nummer = startNummer.current + 1
    startNummer.current = nummer
    aufraeumen.current?.()
    aufraeumen.current = null
    setFehler(null)
    try {
      const geraet = await eingabeGeraetId().catch(() => null)
      const strom = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: verarbeitung.rauschen,
          autoGainControl: verarbeitung.autogain,
          ...(geraet ? { deviceId: { ideal: geraet } } : {}),
        },
      })
      if (verlassen.current || nummer !== startNummer.current) {
        strom.getTracks().forEach((spur) => spur.stop())
        return
      }
      const kontext = new AudioContext()
      // Dieselbe Gerätewahl wie die Stimme der KI (`audioWiedergabe`):
      // `setSinkId` gibt es erst seit Chromium 110; wo es fehlt, bleibt der
      // Systemstandard — Ton geht vor Gerätetreue.
      void ausgabeGeraetId()
        .then((sink) => {
          const mitSink = kontext as AudioContext & {
            setSinkId?: (id: string) => Promise<void>
          }
          if (sink && mitSink.setSinkId) return mitSink.setSinkId(sink)
        })
        .catch(() => undefined)
      const quelle = kontext.createMediaStreamSource(strom)
      const gain = kontext.createGain()
      gain.gain.value = aktuelleVerarbeitung().verstaerkung
      gainKnoten.current = gain
      // Der Analyser hängt als Abgriff hinter der Verstärkung: der Balken
      // zeigt, was am Lautsprecher ankommt, nicht das rohe Mikrofon.
      const analyser = kontext.createAnalyser()
      quelle.connect(gain)
      gain.connect(analyser)
      gain.connect(kontext.destination)
      const puffer = new Float32Array(analyser.fftSize)
      const takt = window.setInterval(() => {
        analyser.getFloatTimeDomainData(puffer)
        let summe = 0
        for (let i = 0; i < puffer.length; i += 1) summe += puffer[i] * puffer[i]
        // Dieselbe Skalierung wie der Sitzungspegel (`audioAufnahme`): RMS ×4
        // holt gesprochene Sprache in einen sichtbaren Bereich.
        setPegel(Math.min(1, Math.sqrt(summe / puffer.length) * 4))
      }, 100)
      aufraeumen.current = () => {
        window.clearInterval(takt)
        quelle.disconnect()
        gain.disconnect()
        analyser.disconnect()
        strom.getTracks().forEach((spur) => spur.stop())
        void kontext.close().catch(() => undefined)
      }
      setLaeuft(true)
    } catch {
      setFehler(t('mss.audio.testhoerenFehler'))
      setLaeuft(false)
    }
  }, [verarbeitung.rauschen, verarbeitung.autogain, t])

  // Die Verstärkung wirkt live in den laufenden Test — der Regler daneben
  // soll hörbar sein, ohne neu zu starten.
  useEffect(() => {
    if (gainKnoten.current) gainKnoten.current.gain.value = aktuelleVerarbeitung().verstaerkung
  }, [verarbeitung.verstaerkung])

  // Die Schalter dagegen sind getUserMedia-Constraints: ein laufender Test
  // startet neu, damit man hört, was man umgelegt hat.
  useEffect(() => {
    if (aufraeumen.current) void starten()
  }, [starten])

  // Beim Verlassen des Reiters geht das Mikrofon zu — ein Testton, der ohne
  // sichtbaren Ursprung weiterläuft, wäre genau das falsche Gefühl für eine
  // App, die mithören kann. `verlassen` fängt den Fall, dass `starten` noch
  // in seinen awaits steckt und sein Aufräumen erst danach hinterlegte.
  useEffect(() => () => {
    verlassen.current = true
    aufraeumen.current?.()
  }, [])

  return (
    <div className="flex flex-col gap-3 border-t border-outline-variant/40 pt-4">
      <div>
        <p className="text-sm text-on-surface">{t('mss.audio.testhoeren')}</p>
        <p className="text-xs text-on-surface-variant">{t('mss.audio.testhoerenHinweis')}</p>
      </div>
      <div className="flex items-center gap-3">
        <Button
          variant="secondary"
          onClick={() => (laeuft ? stoppen() : void starten())}
        >
          {laeuft ? t('mss.audio.testhoerenStopp') : t('mss.audio.testhoerenStart')}
        </Button>
        <ProgressBar
          value={laeuft ? Math.round(pegel * 100) : null}
          ariaLabel={t('mss.audio.testhoerenPegel')}
          className="flex-1"
        />
      </div>
      {fehler && <p className="msm-alert-warning">{fehler}</p>}
    </div>
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
