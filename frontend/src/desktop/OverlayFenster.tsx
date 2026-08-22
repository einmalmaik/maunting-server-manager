/**
 * Das schwebende Overlay — die Sprachblase des Wake-Words.
 *
 * Dieselben Bausteine wie der Realtime-Modus im Web (`SprachAnsicht`), nur
 * kompakt gesetzt: der Canvas-Orb mit den vier Zustandsfarben, der
 * Zustandstext und die letzten Transkriptzeilen. Kein eigener Zeichner —
 * `Sprachblase` ist derselbe, den auch das Panel zeichnet.
 *
 * Das Fenster ist frameless und startet unsichtbar (tauri.conf.json). Es
 * lebt ereignisgetrieben: `OVERLAY_SPRACHE_START` (Wake-Word, Hotkey) zeigt
 * es und beginnt die Sitzung; ESC oder der Schliessen-Knopf beenden sie und
 * verstecken es wieder. Eine eigene Sitzung im Hauptfenster beendet die
 * hiesige (`beiFremdemSprachstart`) — nie zwei Mikrofone zugleich.
 */
import { useEffect, useRef, useState } from 'react'
import { listen } from '@tauri-apps/api/event'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Sprachblase } from '@/components/ai/voice/Sprachblase'
import {
  registriereAudioGeraete,
  registriereAudioVerarbeitung,
} from '@/components/ai/voice/audioGeraete'
import { useSprachsitzung, type Sprachzustand } from '@/components/ai/voice/useSprachsitzung'
import { istAngemeldet, stillAnmelden } from './transport'
import {
  OVERLAY_SCHAUFENSTER,
  OVERLAY_SPRACHE_ENDE,
  OVERLAY_SPRACHE_START,
  OVERLAY_ZUSTAND_TEST,
  beiFremdemSprachstart,
  sprachstartMelden,
  sprachzustandVerdrahten,
} from './sprachKoordination'
import { konfigLaden, overlaySichtbar } from './tauri'

/**
 * Nach so viel Stille geht das Overlay von selbst zu. Großzügig genug für
 * eine Denkpause mitten im Gespräch, kurz genug, dass ein Fehltrigger des
 * Wake-Words kein offenes Mikrofon hinterlässt.
 */
const STILLE_SCHLIESST_MS = 20_000

export function OverlayFenster() {
  const { t } = useTranslation()
  // Keine eigene Providerwahl: `provider_id` ist am Voice-WS optional, und
  // ohne sie nimmt das Backend die am Konto gespeicherte Wahl — dieselbe, die
  // das Panel beim Modellwechsel dorthin schreibt. Vorher las dieses Fenster
  // den localStorage, der hier leer ist, und lief still auf dem ersten
  // verfuegbaren Zugang statt auf dem gewaehlten.
  const { zustand, zeilen, fehler, pegel, starten, beenden } = useSprachsitzung(null)
  const [sichtbar, setSichtbar] = useState(false)
  // Schaufenster: das Fenster zeigt sich mit der Blase, aber ohne Mikrofon
  // und ohne Leitung — der Testknopf der Einstellungen. Die Diagnose-Knöpfe
  // färben die Blase über `testZustand`; eine echte Sitzung beendet den Modus.
  const [schaufenster, setSchaufenster] = useState(false)
  const [testZustand, setTestZustand] = useState<Sprachzustand>('bereit')
  const transkriptEnde = useRef<HTMLDivElement>(null)

  // Frameless und transparent: der Fensterhintergrund kommt vom Panel-
  // Stylesheet und muss hier weg, sonst schwebt ein dunkles Rechteck.
  useEffect(() => {
    document.documentElement.style.background = 'transparent'
    document.body.style.background = 'transparent'
  }, [])

  useEffect(() => {
    const abo = listen(OVERLAY_SPRACHE_START, () => {
      setSichtbar(true)
      // Ein echter Start löst ein offenes Schaufenster ab — ab jetzt zeigt
      // die Blase den Sitzungszustand, nicht mehr die Diagnose-Farbe.
      setSchaufenster(false)
      void (async () => {
        // Jedes Fenster hält sein eigenes Access-Token im Speicher; das
        // Overlay meldet sich erst an, wenn es wirklich sprechen soll.
        if (!istAngemeldet()) {
          await stillAnmelden()
        }
        // Gerätewahl und Verarbeitung werden im Hauptfenster geändert —
        // dieses Fenster erfährt davon nichts. Frisch laden, damit die
        // Sitzung den heutigen Stand nimmt, nicht den vom App-Start.
        await konfigLaden()
          .then((k) => {
            registriereAudioGeraete(k.audio_eingabe, k.audio_ausgabe)
            registriereAudioVerarbeitung({
              echo: k.audio_echo,
              rauschen: k.audio_rauschen,
              autogain: k.audio_autogain,
              verstaerkung: k.audio_verstaerkung,
            })
          })
          .catch(() => undefined)
        await sprachstartMelden('overlay')
        await starten()
      })()
    })
    return () => {
      void abo.then((weg) => weg())
    }
  }, [starten])

  // Das Schaufenster (Testknopf): zeigen ohne Sitzung. Kein Mikrofon, keine
  // Anmeldung, keine Leitung — nur die Blase und der Zustandstext.
  useEffect(() => {
    const abo = listen(OVERLAY_SCHAUFENSTER, () => {
      setSichtbar(true)
      setSchaufenster(true)
      setTestZustand('bereit')
    })
    return () => {
      void abo.then((weg) => weg())
    }
  }, [])

  // Die Diagnose-Knöpfe der Einstellungen färben die Blase im Schaufenster.
  // Außerhalb des Schaufensters (echte Sitzung, verstecktes Fenster) wird
  // das Ereignis ignoriert — die Sitzung hat ihren eigenen Zustand.
  useEffect(() => {
    const abo = listen<string>(OVERLAY_ZUSTAND_TEST, (ereignis) => {
      const wert = ereignis.payload
      if (wert === 'bereit' || wert === 'hoert' || wert === 'denkt' || wert === 'spricht') {
        setTestZustand(wert)
      }
    })
    return () => {
      void abo.then((weg) => weg())
    }
  }, [])

  // Beginnt im Hauptfenster eine Sitzung, endet die hiesige.
  useEffect(() => beiFremdemSprachstart('overlay', beenden), [beenden])
  // Tray-Farbe und Ducking folgen dem Zustand dieses Fensters.
  useEffect(() => sprachzustandVerdrahten(), [])

  useEffect(() => {
    transkriptEnde.current?.scrollIntoView?.({ block: 'end' })
  }, [zeilen])

  function schliessen() {
    beenden()
    setSichtbar(false)
    setSchaufenster(false)
    void overlaySichtbar(false)
  }

  useEffect(() => {
    const taste = (ereignis: KeyboardEvent) => {
      if (ereignis.key === 'Escape') schliessen()
    }
    window.addEventListener('keydown', taste)
    return () => window.removeEventListener('keydown', taste)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Der Sprach-Hotkey drückt auch „aus": Rust sieht nur, dass das Fenster
  // sichtbar ist, und bittet hierher — beendet wird wie über X und ESC.
  useEffect(() => {
    const abo = listen(OVERLAY_SPRACHE_ENDE, () => schliessen())
    return () => {
      void abo.then((weg) => weg())
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Stille schließt das Overlay von selbst. Ein Fehltrigger des Wake-Words
  // öffnete sonst eine Sitzung, die nie endet — das Mikrofon streamte
  // dauerhaft zur KI, und nach der Höchstdauer verband der Hook sogar neu.
  // „Still" heißt: das Backend meldet über die ganze Frist weder hoert noch
  // denkt noch spricht (Rede erkennt allein seine VAD, nicht dieses Fenster);
  // `aus` zählt mit, damit auch ein Verbindungsfehler das Fenster nicht
  // ewig stehen lässt. Jede echte Aktivität setzt die Frist zurück.
  useEffect(() => {
    // Im Schaufenster gibt es kein Mikrofon, das die Frist schützen müsste —
    // wer mit den Diagnose-Farben spielt, soll nicht nach 20 s im Dunkeln stehen.
    if (!sichtbar || schaufenster || (zustand !== 'bereit' && zustand !== 'aus')) return
    const frist = window.setTimeout(() => schliessen(), STILLE_SCHLIESST_MS)
    return () => window.clearTimeout(frist)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sichtbar, schaufenster, zustand])

  // Solange keine Sitzung angefordert wurde, zeigt das (ohnehin versteckte)
  // Fenster nichts — sonst blitzte beim App-Start ein leerer Orb auf.
  if (!sichtbar) {
    return null
  }

  const letzteZeilen = schaufenster ? [] : zeilen.slice(-3)
  // Im Schaufenster zeigt die Blase die Diagnose-Farbe; ein fester kleiner
  // Pegel lässt „hört zu" und „spricht" atmen, wie sie es im Ernstfall täten.
  const anzeigeZustand = schaufenster ? testZustand : zustand
  const anzeigePegel = schaufenster
    ? () => (testZustand === 'hoert' || testZustand === 'spricht' ? 0.35 : 0)
    : pegel

  return (
    <div
      data-tauri-drag-region
      className="flex h-screen flex-col items-center justify-start overflow-hidden rounded-2xl border border-outline-variant/50 bg-surface-container-lowest/90 px-4 pb-3 pt-1 backdrop-blur-xl"
    >
      <div className="flex w-full items-center justify-end" data-tauri-drag-region>
        <button
          onClick={schliessen}
          aria-label={t('mss.overlay.schliessen')}
          className="rounded-md p-1 text-on-surface-variant transition-colors hover:text-on-surface"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="-mt-2" data-tauri-drag-region>
        <Sprachblase zustand={anzeigeZustand} pegel={anzeigePegel} breite={420} hoehe={150} />
      </div>
      <p className="-mt-2 text-sm text-on-surface" aria-live="polite">
        {!schaufenster && fehler ? t(fehler) : t(`ai.voice.zustand.${anzeigeZustand}`)}
      </p>
      {letzteZeilen.length > 0 && (
        <div className="mt-1 max-h-16 w-full overflow-y-auto text-xs leading-5">
          {letzteZeilen.map((zeile, i) => (
            <p
              key={i}
              className={zeile.wer === 'ich' ? 'text-on-surface-variant/70' : 'text-on-surface'}
            >
              {zeile.wer === 'ich' ? t('mss.overlay.ich') : t('mss.overlay.ki')} {zeile.text}
            </p>
          ))}
          <div ref={transkriptEnde} />
        </div>
      )}
    </div>
  )
}
