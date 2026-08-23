/**
 * Wake-Word-Kalibrierung: zehnmal einsprechen, trainieren, einschalten.
 *
 * **Das Wake-Word ist immer der Name des Assistenten** — es gibt kein
 * eigenes Wortfeld. Wer den Namen ändert (im Chat, im Panel-Profil), bekommt
 * hier den Hinweis, einmal neu zu kalibrieren; bis dahin hört das Modell
 * weiter auf den alten Namen. Der Aktiv-Schalter ist persistent
 * (konfig.json): „an" überlebt den Neustart, „aus" heißt aus — nichts
 * schaltet das Mikrofon von selbst wieder ein.
 *
 * Die Runden laufen von selbst weiter: einmal starten, zehnmal sprechen.
 * Jede Aufnahme prüft in Rust, ob wirklich gesprochen wurde (RMS-Tor in
 * `wakeword.rs`) — eine stille Runde zählt nicht, sie wird mit einer
 * Meldung wiederholt. Alles bleibt lokal: Aufnahmen und Modell liegen im
 * App-Datenverzeichnis, das Event `wakeword-erkannt` trägt nur Name und
 * Score, nie Audio.
 */
import { useEffect, useRef, useState } from 'react'
import { listen } from '@tauri-apps/api/event'
import { Mic } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, ProgressBar, Slider, Switch } from '@/Singra/UI'
import { useAuthStore } from '@/stores/authStore'
import {
  konfigLaden,
  konfigSpeichern,
  wakewordAufnehmen,
  wakewordLauschen,
  wakewordStand,
  wakewordTrainieren,
  wakewordZuruecksetzen,
  type AppKonfig,
  type WakewordStand,
} from './tauri'

/**
 * Muss zu `wakeword::AUFNAHMEN_SOLL` in Rust passen.
 *
 * Waren zehn; rustpotter nennt für Referenzmodelle ausdrücklich „3 to 8 wav
 * records", und jede weitere Aufnahme kostet Rechenzeit in jedem einzelnen
 * Vergleich.
 */
const AUFNAHMEN_SOLL = 6
/** Atempause zwischen zwei Runden — sprechen, absetzen, wieder sprechen. */
const RUNDEN_PAUSE_MS = 900
/**
 * Wie lange nach dem letzten Reglertick gewartet wird, bevor die Schwelle
 * gespeichert und der Lausch-Thread durchgestartet wird — jeder Tick einzeln
 * würde das Mikrofon im Sekundentakt schließen und öffnen.
 */
const SCHWELLE_SPEICHERN_MS = 600
/** Dieselben Grenzen wie `wakeword::schwelle_klemmen` in Rust. */
const SCHWELLE_MIN = 0.3
const SCHWELLE_MAX = 0.6

export function WakewordEinrichtung() {
  const { t } = useTranslation()
  const agentName = useAuthStore((s) => s.user?.agent_name?.trim() || 'Singra')
  const [stand, setStand] = useState<WakewordStand>({
    aufnahmen: 0,
    trainiert: false,
    lauscht: false,
  })
  const [beschaeftigt, setBeschaeftigt] = useState<'kalibrierung' | 'training' | 'lauschen' | 'reset' | null>(null)
  const [meldung, setMeldung] = useState<string | null>(null)
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  /** Was der Lausch-Thread gerade hört — nur zur Anzeige (`wakeword-pegel`). */
  const [pegel, setPegel] = useState<{ rms: number; gain: number } | null>(null)
  /** Bricht die laufende Kalibrierungsschleife ab, ohne zu rendern. */
  const abbruch = useRef(false)
  const schwelleTimer = useRef<number | null>(null)

  async function standLaden(): Promise<WakewordStand | null> {
    try {
      const neu = await wakewordStand()
      setStand(neu)
      return neu
    } catch (fehler) {
      setMeldung(String(fehler))
      return null
    }
  }

  useEffect(() => {
    void standLaden()
    void konfigLaden()
      .then(setKonfig)
      .catch(() => setKonfig(null))
    const abmelden = listen<{ name: string; score: number }>('wakeword-erkannt', (ereignis) => {
      setMeldung(
        t('mss.wakeword.erkannt', {
          name: ereignis.payload.name,
          score: ereignis.payload.score.toFixed(2),
        }),
      )
    })
    // Stirbt der Lausch-Thread (Mikrofon weg, Modell kaputt), meldet er das
    // hierher — sonst stünde der Schalter auf „an", hinter dem nichts mehr
    // lauscht. Der Stand wird gleich mitgeladen, damit der Schalter umspringt.
    const abFehler = listen<{ meldung: string }>('wakeword-fehler', (ereignis) => {
      setMeldung(ereignis.payload.meldung)
      void standLaden()
    })
    // Der Lausch-Thread meldet alle 250 ms, was er hört — der Balken unten
    // zeigt, ob das Mikrofon überhaupt etwas liefert und wie stark die
    // automatische Verstärkung nachregelt.
    const abPegel = listen<{ rms: number; gain: number }>('wakeword-pegel', (ereignis) => {
      setPegel(ereignis.payload)
    })
    return () => {
      abbruch.current = true
      // Der Schwellen-Timer wird bewusst NICHT geräumt: seine Schließung ist
      // in sich geschlossen (frisches Laden, Speichern, Durchstart) — räumen
      // hieße, die letzte gezogene Schwelle wegzuwerfen.
      void abmelden.then((f) => f())
      void abFehler.then((f) => f())
      void abPegel.then((f) => f())
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /**
   * Die Empfindlichkeit: sofort anzeigen, erst nach einer Atempause speichern
   * und den Lausch-Thread durchstarten — er liest die Schwelle nur beim Start.
   *
   * Gespeichert wird über einen **frischen** Konfigurationsstand, nicht über
   * den React-State: der stammt vom Mount, während der Aktiv-Schalter daneben
   * `wakeword_aktiv` direkt in Rust umschreibt. Der alte Weg (`{ ...konfig }`)
   * hätte ein gerade abgeschaltetes Wake-Word wieder auf „an" zurückgeschrieben
   * — und nichts darf das Mikrofon von selbst wieder einschalten. Aus demselben
   * frischen Stand kommt auch die Entscheidung über den Durchstart.
   */
  function schwelleZiehen(wert: number) {
    if (!konfig) return
    setKonfig({ ...konfig, wakeword_schwelle: wert })
    if (schwelleTimer.current !== null) window.clearTimeout(schwelleTimer.current)
    schwelleTimer.current = window.setTimeout(() => {
      schwelleTimer.current = null
      void (async () => {
        try {
          const aktuell = await konfigLaden()
          await konfigSpeichern({ ...aktuell, wakeword_schwelle: wert })
          if (aktuell.wakeword_aktiv) {
            await wakewordLauschen(false)
            await wakewordLauschen(true)
            await standLaden()
          }
        } catch (fehler) {
          setMeldung(String(fehler))
        }
      })()
    }, SCHWELLE_SPEICHERN_MS)
  }

  /**
   * Die Kalibrierung als Schleife: aufnehmen, kurz durchatmen, weiter — bis
   * zehn gute Aufnahmen da sind. Eine stille Runde wirft in Rust einen Fehler;
   * sie wird gemeldet und **dieselbe** Nummer erneut versucht, nicht gezählt.
   */
  async function kalibrieren() {
    setBeschaeftigt('kalibrierung')
    setMeldung(null)
    abbruch.current = false
    try {
      let aktueller = await standLaden()
      let fehlversuche = 0
      while (
        !abbruch.current &&
        aktueller !== null &&
        aktueller.aufnahmen < AUFNAHMEN_SOLL &&
        fehlversuche < 3
      ) {
        const nummer = aktueller.aufnahmen + 1
        setMeldung(t('mss.wakeword.sprichJetzt', { nummer, gesamt: AUFNAHMEN_SOLL }))
        try {
          await wakewordAufnehmen(nummer)
          fehlversuche = 0
        } catch (fehler) {
          fehlversuche += 1
          setMeldung(String(fehler))
          await new Promise((weiter) => setTimeout(weiter, RUNDEN_PAUSE_MS))
          continue
        }
        aktueller = await standLaden()
        if (aktueller && aktueller.aufnahmen < AUFNAHMEN_SOLL) {
          await new Promise((weiter) => setTimeout(weiter, RUNDEN_PAUSE_MS))
        }
      }
      if (aktueller && aktueller.aufnahmen >= AUFNAHMEN_SOLL) {
        setMeldung(t('mss.wakeword.kalibrierungFertig'))
      }
    } finally {
      setBeschaeftigt(null)
    }
  }

  async function aktion(name: 'training' | 'lauschen' | 'reset', tun: () => Promise<unknown>) {
    setBeschaeftigt(name)
    setMeldung(null)
    try {
      await tun()
      await standLaden()
    } catch (fehler) {
      setMeldung(String(fehler))
    } finally {
      setBeschaeftigt(null)
    }
  }

  const laeuftKalibrierung = beschaeftigt === 'kalibrierung'

  return (
    <section className="msm-card flex flex-col gap-4 p-5" aria-label={t('mss.wakeword.titel')}>
      <div className="flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-sm font-medium text-on-surface">
          <Mic className="h-4 w-4 text-primary" />
          {t('mss.wakeword.titel')}
        </h2>
        <span className="text-xs text-on-surface-variant">
          {t('mss.wakeword.stand', { aufnahmen: stand.aufnahmen, gesamt: AUFNAHMEN_SOLL })}
        </span>
      </div>

      {'geraet' in stand && !stand.geraet && (
        <p className="msm-alert-warning">{t('mss.wakeword.keinMikrofon')}</p>
      )}
      {'geraet' in stand && stand.geraet && (
        <p className="text-xs text-on-surface-variant">
          {t('mss.wakeword.mikrofon', { name: stand.geraet })}
        </p>
      )}

      {/* Kein Wortfeld: das Wake-Word ist immer der Name des Assistenten. */}
      <p className="text-sm text-on-surface">
        {t('mss.wakeword.wortIstName', { name: agentName })}
      </p>

      {/* Der Name hat sich seit dem Training geändert — anbieten, nie
          erzwingen: das Modell hört bis zur Neukalibrierung auf den alten. */}
      {stand.trainiert && stand.wort && stand.wort !== agentName && (
        <p className="msm-alert-warning">
          {t('mss.wakeword.neuKalibrieren', { alt: stand.wort, neu: agentName })}
        </p>
      )}

      {/* Die Kalibrierung stammt aus der Zeit der festen 2,5-s-Aufnahmen.
          Anbieten, nicht erzwingen — aber deutlich: an überlangen Vorlagen
          ändert weder der Empfindlichkeitsregler noch sonst etwas. */}
      {stand.veraltet === true && (
        <p className="msm-alert-warning">{t('mss.wakeword.verfahrenAlt', { gesamt: AUFNAHMEN_SOLL })}</p>
      )}

      <div className="flex flex-wrap gap-2">
        {laeuftKalibrierung ? (
          <Button
            variant="secondary"
            onClick={() => {
              abbruch.current = true
            }}
          >
            {t('mss.wakeword.abbrechen')}
          </Button>
        ) : (
          <Button
            onClick={() => void kalibrieren()}
            disabled={beschaeftigt !== null || stand.lauscht || stand.aufnahmen >= AUFNAHMEN_SOLL}
          >
            {stand.aufnahmen > 0
              ? t('mss.wakeword.weiterKalibrieren')
              : t('mss.wakeword.kalibrierungStarten')}
          </Button>
        )}
        <Button
          variant="secondary"
          onClick={() => void aktion('training', () => wakewordTrainieren(agentName))}
          disabled={beschaeftigt !== null || stand.aufnahmen < 3}
        >
          {beschaeftigt === 'training' ? t('mss.wakeword.trainiert') : t('mss.wakeword.trainieren')}
        </Button>
        <Button
          variant="ghost"
          onClick={() => void aktion('reset', () => wakewordZuruecksetzen())}
          disabled={beschaeftigt !== null || (stand.aufnahmen === 0 && !stand.trainiert)}
        >
          {t('mss.wakeword.zuruecksetzen')}
        </Button>
      </div>

      {/* Der eine, persistente Schalter: „an" überlebt den App-Neustart,
          „aus" heißt physisch aus — kein Pfad schaltet das Mikrofon von
          selbst wieder ein (konfig.wakeword_aktiv, gelesen nur beim Start). */}
      <div className="flex items-center justify-between gap-3 border-t border-outline-variant/40 pt-4">
        <div className="min-w-0">
          <p className="text-sm text-on-surface">{t('mss.wakeword.aktiv')}</p>
          <p className="text-xs text-on-surface-variant">
            {t('mss.wakeword.aktivHinweis', { name: stand.wort ?? agentName })}
          </p>
        </div>
        <Switch
          checked={stand.aktiv ?? stand.lauscht}
          disabled={beschaeftigt !== null || !stand.trainiert}
          onCheckedChange={(an) => void aktion('lauschen', () => wakewordLauschen(an))}
          aria-label={t('mss.wakeword.aktiv')}
        />
      </div>
      {/* Schalter an, aber kein Thread dahinter: der Lausch-Thread ist
          gestorben (Mikrofon weg, Modell kaputt) — sagen statt so tun. */}
      {stand.aktiv === true && !stand.lauscht && beschaeftigt === null && (
        <p className="msm-alert-warning">{t('mss.wakeword.lauschtNicht')}</p>
      )}

      <div className="flex flex-col gap-2 border-t border-outline-variant/40 pt-4">
        <Slider
          value={Math.round((konfig?.wakeword_schwelle ?? 0.45) * 100)}
          min={SCHWELLE_MIN * 100}
          max={SCHWELLE_MAX * 100}
          step={1}
          disabled={konfig === null}
          onValueChange={(wert) => schwelleZiehen(wert / 100)}
          label={t('mss.wakeword.schwelle')}
          hint={(konfig?.wakeword_schwelle ?? 0.45).toFixed(2)}
        />
        <p className="text-xs text-on-surface-variant">{t('mss.wakeword.schwelleHinweis')}</p>
        {/* Nur solange wirklich gelauscht wird — ein eingefrorener Balken
            sähe aus wie ein hängendes Mikrofon. */}
        {stand.lauscht && pegel && (
          <ProgressBar
            value={Math.min(100, Math.round(pegel.rms * 4 * 100))}
            label={t('mss.wakeword.pegel')}
            hint={`×${pegel.gain.toFixed(1)}`}
          />
        )}
      </div>

      {meldung && (
        <p className="text-xs text-on-surface-variant" aria-live="polite">
          {meldung}
        </p>
      )}
      <p className="text-xs text-on-surface-variant/70">{t('mss.wakeword.datenschutz')}</p>
    </section>
  )
}
