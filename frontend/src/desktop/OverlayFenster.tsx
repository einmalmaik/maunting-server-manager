/**
 * Das schwebende Overlay — der Sprachschwarm des Wake-Words.
 *
 * Dieselben Bausteine wie der Realtime-Modus im Web (`SprachAnsicht`), nur
 * kompakt gesetzt: der Schwarm, darunter der Zustand, das laufende Werkzeug
 * und die letzten Zeilen des Gesprächs als Untertitel. Kein eigener Zeichner —
 * `Schwarm` ist derselbe, den auch das Panel zeichnet, und wird bei einer
 * Regionalanalyse auch hier zur Erde.
 *
 * **Kein Kasten.** Das Fenster ist durchsichtig, und der Schwarm schwebt frei
 * über dem Desktop — früher lag er auf einer halbdurchsichtigen schwarzen
 * Fläche. Klicks gehen durch das Fenster hindurch, nur das X nimmt sie an
 * (`durchklick.rs`). In der Android-App gilt dasselbe ohne abgedunkelten
 * Hintergrund: der Schwarm liegt über der Oberfläche, die darunter bedienbar
 * bleibt.
 *
 * Das Fenster ist frameless und startet unsichtbar (tauri.conf.json). Es
 * lebt ereignisgetrieben: `OVERLAY_SPRACHE_START` (Wake-Word, Hotkey) zeigt
 * es und beginnt die Sitzung; ESC oder der Schliessen-Knopf beenden sie und
 * verstecken es wieder. Eine eigene Sitzung im Hauptfenster beendet die
 * hiesige (`beiFremdemSprachstart`) — nie zwei Mikrofone zugleich.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { listen } from '@tauri-apps/api/event'
import { MapPin, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { voiceStateTones } from '@maunting/design-dna'

import { Schwarm, schwarmZustand } from '@/components/ai/voice/Schwarm'
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
import { konfigLaden, overlaySichtbar, overlayTrefferflaechen } from './tauri'

/**
 * Nach so viel Stille geht das Overlay von selbst zu. Großzügig genug für
 * eine Denkpause mitten im Gespräch, kurz genug, dass ein Fehltrigger des
 * Wake-Words kein offenes Mikrofon hinterlässt.
 */
const STILLE_SCHLIESST_MS = 20_000

/**
 * Schrift und Knopf stehen ohne Kasten über fremdem Untergrund — mal einem
 * weißen Browserfenster, mal einem dunklen Hintergrundbild. Ein enger dunkler
 * Saum hält sie auf beidem lesbar, wie Untertitel im Film.
 *
 * Der Saum reicht bis `SAUM` über den Buchstaben hinaus. Wo Text abgeschnitten
 * wird (`truncate`, die Untertitelbox), braucht er dort genau so viel
 * Innenabstand — sonst schneidet `overflow: hidden` den Schatten zu einem
 * grauen Rechteck ab. Die Schrift selbst ist deckend: durch halbdurchsichtige
 * Buchstaben schiene der Saum hindurch und machte sie grau.
 */
const SCHATTEN_SCHRIFT =
  '[text-shadow:0_0_1px_rgb(0_0_0/0.95),0_1px_2px_rgb(0_0_0/0.8),0_0_6px_rgb(0_0_0/0.45)]'
const SAUM = 'px-2 -mx-2 py-1.5 -my-1.5'
const SCHATTEN_FORM = '[filter:drop-shadow(0_0_1px_rgb(0_0_0/0.9))_drop-shadow(0_1px_3px_rgb(0_0_0/0.6))]'

interface OverlayFensterProps {
  inApp?: boolean
}

export function OverlayFenster({ inApp = false }: OverlayFensterProps) {
  const { t } = useTranslation()
  // Keine eigene Providerwahl: `provider_id` ist am Voice-WS optional, und
  // ohne sie nimmt das Backend die am Konto gespeicherte Wahl — dieselbe, die
  // das Panel beim Modellwechsel dorthin schreibt. Vorher las dieses Fenster
  // den localStorage, der hier leer ist, und lief still auf dem ersten
  // verfuegbaren Zugang statt auf dem gewaehlten.
  const {
    zustand,
    abgelaufen,
    zeilen,
    werkzeug,
    werkzeugLaeuft,
    werkzeugStarts,
    fehler,
    geoData,
    regionalContextActive,
    pegel,
    starten,
    beenden,
  } = useSprachsitzung(null)
  const [sichtbar, setSichtbar] = useState(false)
  // Schaufenster: das Fenster zeigt sich mit dem Schwarm, aber ohne Mikrofon
  // und ohne Leitung — der Testknopf der Einstellungen. Die Diagnose-Knöpfe
  // wählen die Form über `testZustand`; eine echte Sitzung beendet den Modus.
  const [schaufenster, setSchaufenster] = useState(false)
  const [testZustand, setTestZustand] = useState<Sprachzustand>('bereit')
  const untertitel = useRef<HTMLDivElement>(null)
  const knopf = useRef<HTMLButtonElement>(null)
  const [uebervoll, setUebervoll] = useState(false)

  // Frameless und transparent: der Fensterhintergrund kommt vom Panel-
  // Stylesheet und muss hier weg, sonst schwebt ein dunkles Rechteck (nur im Desktop-Fenstermodus).
  useEffect(() => {
    if (!inApp) {
      document.documentElement.style.background = 'transparent'
      document.body.style.background = 'transparent'
    }
  }, [inApp])

  useEffect(() => {
    const abo = listen(OVERLAY_SPRACHE_START, () => {
      setSichtbar(true)
      // Ein echter Start löst ein offenes Schaufenster ab — ab jetzt zeigt
      // der Schwarm den Sitzungszustand, nicht mehr die Diagnose-Form.
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
  // Anmeldung, keine Leitung — nur der Schwarm und der Zustandstext.
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

  // Die Diagnose-Knöpfe der Einstellungen wählen die Form im Schaufenster.
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
    // wer die Formen durchprobiert, soll nicht nach 20 s im Dunkeln stehen.
    if (!sichtbar || schaufenster || (zustand !== 'bereit' && zustand !== 'aus')) return
    const frist = window.setTimeout(() => schliessen(), STILLE_SCHLIESST_MS)
    return () => window.clearTimeout(frist)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sichtbar, schaufenster, zustand])

  // Laufen die Untertitel oben hinaus? Erst dann blendet die Maske die
  // älteste Zeile aus. `justify-end` schiebt das Zuviel nach oben, wo es kein
  // `scrollHeight` mehr sieht — deshalb der Vergleich mit dem Inhalt selbst,
  // gegen die Box ohne ihren Saum.
  useLayoutEffect(() => {
    const innen = untertitel.current
    const kasten = innen?.parentElement
    if (!innen || !kasten) return
    const stil = getComputedStyle(kasten)
    const platz = kasten.clientHeight - (parseFloat(stil.paddingTop) || 0) - (parseFloat(stil.paddingBottom) || 0)
    setUebervoll(innen.offsetHeight > platz + 1)
  })

  // Auf dem Desktop sieht man vom Fenster nur den Schwarm — der Rest ist
  // durchsichtig und soll Klicks an das weitergeben, was darunter liegt. Nur
  // das X nimmt sie an, mit etwas Rand für den Zeiger.
  useEffect(() => {
    if (inApp || !sichtbar) return
    const melden = () => {
      const x = knopf.current?.getBoundingClientRect()
      if (!x) return
      void overlayTrefferflaechen([[x.left - 4, x.top - 4, x.width + 8, x.height + 8]]).catch(() => undefined)
    }
    melden()
    window.addEventListener('resize', melden)
    return () => window.removeEventListener('resize', melden)
  }, [inApp, sichtbar])

  // Solange keine Sitzung angefordert wurde, zeigt das (ohnehin versteckte)
  // Fenster nichts — sonst blitzte beim App-Start ein leerer Schwarm auf.
  if (!sichtbar) {
    return null
  }

  // Im Schaufenster zeigt der Schwarm die geklickte Diagnose-Form und spricht
  // dabei Silben statt eines echten Pegels (`vorfuehrung`), damit „hört zu"
  // und „spricht" sich so bewegen, wie sie es im Ernstfall täten.
  const figur = schaufenster
    ? schwarmZustand({ zustand: testZustand })
    : schwarmZustand({ zustand, fehler, abgelaufen, werkzeugLaeuft })
  const ton = voiceStateTones[figur]
  const schluessel = !schaufenster && abgelaufen ? 'abgelaufen' : schaufenster ? testZustand : zustand
  // Die Erde nur, solange das Gespräch noch bei der Region ist — wie im Panel.
  const ort = !schaufenster && regionalContextActive && geoData?.coordinates ? geoData.coordinates : null
  const werkzeugSatz =
    !schaufenster && werkzeugLaeuft && werkzeug && zustand === 'denkt'
      ? t(`ai.toolsRunning.${werkzeug}`, { defaultValue: t('ai.voice.werkzeug') })
      : null
  const titel = !schaufenster && fehler ? t(fehler) : (werkzeugSatz ?? t(`ai.voice.zustand.${schluessel}`))
  const hinweis = !schaufenster && fehler ? t('ai.voice.hint.error') : t(`ai.voice.hint.${schluessel}`)
  const letzteZeilen = schaufenster ? [] : zeilen.slice(-3)

  const buehne = (
    <div
      data-zustand={figur}
      className={[
        'select-none motion-safe:animate-fade-in',
        // Kein Kasten, keine Abdunklung: der Schwarm schwebt frei über dem,
        // was darunter liegt — auf dem Desktop über den Fenstern, in der App
        // über ihrer Oberfläche, die dabei bedienbar bleibt.
        inApp
          ? 'pointer-events-none fixed inset-x-0 bottom-0 z-50 h-[380px] max-h-[75dvh] pb-[env(safe-area-inset-bottom,0px)]'
          : 'relative h-screen w-full',
      ].join(' ')}
    >
      {/* Die Leinwand reicht bis hinter die Untertitel: der Schwarm blendet
          zu ihrem Rand hin aus, statt an einer Kante abzubrechen. */}
      <Schwarm
        zustand={figur}
        pegel={pegel}
        ort={ort}
        impulse={werkzeugStarts}
        vorfuehrung={schaufenster}
        className="pointer-events-none absolute inset-x-0 top-0 bottom-10"
      />

      <button
        ref={knopf}
        type="button"
        onClick={schliessen}
        aria-label={t('mss.overlay.schliessen')}
        className={`pointer-events-auto absolute right-3 top-3 z-10 flex h-8 w-8 items-center justify-center rounded-full text-white transition-colors hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70 ${SCHATTEN_FORM}`}
      >
        <X className="h-4 w-4" strokeWidth={2.5} aria-hidden="true" />
      </button>

      <div className={`pointer-events-none absolute inset-x-5 bottom-3 flex flex-col items-center text-center ${SCHATTEN_SCHRIFT}`}>
        <div className="flex max-w-full items-center gap-2">
          <span
            className="h-1.5 w-1.5 shrink-0 rounded-full motion-safe:transition-colors motion-safe:duration-500"
            style={{
              backgroundColor: `hsl(var(--dna-voice-${ton}))`,
              boxShadow: `0 0 10px hsl(var(--dna-voice-${ton}) / 0.9)`,
            }}
            aria-hidden="true"
          />
          <p className={`min-w-0 truncate text-label-md text-white ${SAUM}`} aria-live="polite">
            {titel}
          </p>
          {ort && geoData?.location && (
            <span className="flex min-w-0 shrink items-center gap-1 text-xs text-on-surface">
              {/* `text-shadow` erreicht keine Zeichnung — die Nadel braucht den Filter. */}
              <MapPin className={`h-3 w-3 shrink-0 ${SCHATTEN_FORM}`} aria-hidden="true" />
              <span className={`truncate ${SAUM}`}>{geoData.location}</span>
            </span>
          )}
        </div>
        {/* Untertitel wie im Fernsehen: die jüngste Zeile steht unten, ältere
            wandern nach oben hinaus und verblassen dabei — aber nur, wenn sie
            wirklich hinauslaufen; eine einzelne Zeile bliebe sonst halb
            durchsichtig. Drei Zeilen zu 19 px, dazu der Saum oben und unten. */}
        <div
          className={`-mx-2 -mb-1.5 -mt-0.5 flex max-h-[69px] flex-col justify-end self-stretch overflow-hidden px-2 py-1.5 text-[13px] leading-[19px] ${
            uebervoll ? '[mask-image:linear-gradient(to_bottom,transparent_4px,#000_22px)]' : ''
          }`}
        >
          <div ref={untertitel}>
            {letzteZeilen.length > 0 ? (
              letzteZeilen.map((zeile, i) => (
                <p key={i} className={zeile.wer === 'ich' ? 'text-on-surface' : 'text-white'}>
                  <span className="mr-1 text-on-surface-variant">
                    {zeile.wer === 'ich' ? t('mss.overlay.ich') : t('mss.overlay.ki')}
                  </span>
                  {zeile.text}
                </p>
              ))
            ) : (
              <p className="text-on-surface">{hinweis}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  )

  return buehne
}
