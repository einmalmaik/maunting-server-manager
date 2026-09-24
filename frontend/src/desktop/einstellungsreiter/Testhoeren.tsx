import { useCallback, useEffect, useRef, useState } from 'react'
import { Mic, ShieldAlert, Volume2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  aktuelleVerarbeitung,
  ausgabeGeraetId,
  eingabeGeraetId,
  type AudioVerarbeitung,
} from '@/components/ai/voice/audioGeraete'
import { Button, ProgressBar } from '@/Singra/UI'

/**
 * Testhören wie in Discord: das eigene Mikrofon auf den gewählten Lautsprecher
 * legen und dabei den Pegel sehen — mit genau der Verarbeitung, die auch die
 * Sprachsitzung nähme. Einzige Abweichung: die Echounterdrückung ist im Test
 * aus, weil sie sonst die eigene Wiedergabe als „Echo" erkennt und wegfiltert —
 * man hörte sich leiser werden, je länger man spricht. Alles bleibt im
 * Chromium-Prozess, nichts davon geht ins Netz.
 */
export function Testhoeren({
  verarbeitung,
  onTestZustand,
}: {
  verarbeitung: AudioVerarbeitung
  onTestZustand?: (aktiv: boolean) => void
}) {
  const { t } = useTranslation()
  const [laeuft, setLaeuft] = useState(false)
  const [pegel, setPegel] = useState(0)
  const [fehler, setFehler] = useState<string | null>(null)
  const aufraeumen = useRef<(() => void) | null>(null)
  const gainKnoten = useRef<GainNode | null>(null)
  const verlassen = useRef(false)
  const startNummer = useRef(0)

  const stoppen = useCallback(() => {
    startNummer.current += 1
    aufraeumen.current?.()
    aufraeumen.current = null
    gainKnoten.current = null
    setLaeuft(false)
    setPegel(0)
    onTestZustand?.(false)
  }, [onTestZustand])

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
      if (kontext.state === 'suspended') {
        await kontext.resume().catch(() => {})
      }
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
      const analyser = kontext.createAnalyser()
      quelle.connect(gain)
      gain.connect(analyser)
      gain.connect(kontext.destination)
      const puffer = new Float32Array(analyser.fftSize)
      const takt = window.setInterval(() => {
        analyser.getFloatTimeDomainData(puffer)
        let summe = 0
        for (let i = 0; i < puffer.length; i += 1) summe += puffer[i] * puffer[i]
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
      onTestZustand?.(true)
    } catch {
      setFehler(t('mss.audio.testhoerenFehler'))
      setLaeuft(false)
      onTestZustand?.(false)
    }
  }, [verarbeitung.rauschen, verarbeitung.autogain, t, onTestZustand])

  useEffect(() => {
    if (gainKnoten.current) gainKnoten.current.gain.value = aktuelleVerarbeitung().verstaerkung
  }, [verarbeitung.verstaerkung])

  useEffect(() => {
    if (aufraeumen.current) void starten()
  }, [starten])

  useEffect(() => () => {
    verlassen.current = true
    aufraeumen.current?.()
  }, [])

  return (
    <section className="msm-card p-6" aria-labelledby="audio-test-heading">
      <div className="flex items-center gap-2 mb-4">
        <Volume2 className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h2 id="audio-test-heading" className="font-headline text-title-lg font-semibold text-on-surface">
          {t('mss.audio.testhoeren')}
        </h2>
      </div>
      <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-5">
        {t('mss.audio.testhoerenHinweis')}
      </p>

      <div className="max-w-xl space-y-4">
        <div className="flex items-center gap-3">
          <Button
            type="button"
            variant={laeuft ? 'secondary' : 'primary'}
            onClick={() => (laeuft ? stoppen() : void starten())}
            className="gap-2 shrink-0"
          >
            <Mic className="w-4 h-4" />
            <span>{laeuft ? t('profile.audioTestStop') : t('profile.audioTestStart')}</span>
          </Button>

          <ProgressBar
            value={laeuft ? Math.round(pegel * 100) : null}
            ariaLabel={t('mss.audio.testhoerenPegel')}
            className="flex-1"
          />
        </div>

        {laeuft && (
          <div className="flex items-center justify-between text-xs px-1 text-on-surface-variant">
            <span>Pegel: {Math.round(pegel * 100)}%</span>
            <span className={pegel > 0.05 ? 'text-status-success font-semibold' : 'text-on-surface-variant/60'}>
              {pegel > 0.05 ? t('profile.audioSignalDetected') : 'Kein Signal'}
            </span>
          </div>
        )}

        {fehler && (
          <div className="p-3 rounded-xl bg-error/10 border border-error/30 text-error text-xs flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 shrink-0" />
            <span>{fehler}</span>
          </div>
        )}
      </div>
    </section>
  )
}
