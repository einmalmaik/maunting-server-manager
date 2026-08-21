/**
 * Wake-Word-Kalibrierung: zehnmal einsprechen, trainieren, lauschen.
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

import { Button } from '@/Singra/UI'
import { Input } from '@/components/ui/Input'
import { useAuthStore } from '@/stores/authStore'
import {
  wakewordAufnehmen,
  wakewordLauschen,
  wakewordStand,
  wakewordTrainieren,
  wakewordZuruecksetzen,
  type WakewordStand,
} from './tauri'

const AUFNAHMEN_SOLL = 10
/** Atempause zwischen zwei Runden — sprechen, absetzen, wieder sprechen. */
const RUNDEN_PAUSE_MS = 900

export function WakewordEinrichtung() {
  const { t } = useTranslation()
  const agentName = useAuthStore((s) => s.user?.agent_name?.trim() || 'Singra')
  const [stand, setStand] = useState<WakewordStand>({
    aufnahmen: 0,
    trainiert: false,
    lauscht: false,
  })
  const [wort, setWort] = useState(agentName)
  const [beschaeftigt, setBeschaeftigt] = useState<'kalibrierung' | 'training' | 'lauschen' | 'reset' | null>(null)
  const [meldung, setMeldung] = useState<string | null>(null)
  /** Bricht die laufende Kalibrierungsschleife ab, ohne zu rendern. */
  const abbruch = useRef(false)

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
    const abmelden = listen<{ name: string; score: number }>('wakeword-erkannt', (ereignis) => {
      setMeldung(
        t('mss.wakeword.erkannt', {
          name: ereignis.payload.name,
          score: ereignis.payload.score.toFixed(2),
        }),
      )
    })
    return () => {
      abbruch.current = true
      void abmelden.then((f) => f())
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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

      <Input
        id="mss-wakeword-wort"
        label={t('mss.wakeword.wortLabel')}
        value={wort}
        onChange={(e) => setWort(e.target.value)}
        placeholder={agentName}
      />

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
          onClick={() => void aktion('training', () => wakewordTrainieren(wort))}
          disabled={beschaeftigt !== null || stand.aufnahmen < 3 || wort.trim() === ''}
        >
          {beschaeftigt === 'training' ? t('mss.wakeword.trainiert') : t('mss.wakeword.trainieren')}
        </Button>
        <Button
          variant="secondary"
          onClick={() => void aktion('lauschen', () => wakewordLauschen(!stand.lauscht))}
          disabled={beschaeftigt !== null || !stand.trainiert}
        >
          {stand.lauscht ? t('mss.wakeword.lauschenStoppen') : t('mss.wakeword.lauschenStarten')}
        </Button>
        <Button
          variant="ghost"
          onClick={() => void aktion('reset', () => wakewordZuruecksetzen())}
          disabled={beschaeftigt !== null || (stand.aufnahmen === 0 && !stand.trainiert)}
        >
          {t('mss.wakeword.zuruecksetzen')}
        </Button>
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
