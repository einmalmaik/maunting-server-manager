import { useEffect, useRef, useState } from 'react'
import { Mic, Radio, Sliders } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  registriereAudioGeraete,
  registriereAudioVerarbeitung,
} from '@/components/ai/voice/audioGeraete'
import { Button, Dropdown, type DropdownOption, Slider, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import {
  audioGeraete,
  duckingSetzen,
  konfigLaden,
  konfigSpeichern,
  wakewordLauschen,
  type AppKonfig,
  type AudioGeraete,
} from '../tauri'
import { Testhoeren } from './Testhoeren'

const isAndroidClient = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)

/**
 * Wie lange nach der letzten Verarbeitungsänderung gewartet wird, bevor sie
 * in konfig.json landet — der Verstärkungsregler feuert je Tick. Registriert
 * (und damit hörbar) ist jede Änderung sofort, nur das Schreiben wartet.
 */
const VERARBEITUNG_SPEICHERN_MS = 400

/**
 * Der Audio-Reiter: welches Mikrofon hört, welcher Lautsprecher spricht —
 * unabhängig vom Windows-Standard. Gespeichert wird nur der Gerätename
 * (konfig.json); „Windows-Standard" heißt: dem System folgen, auch wenn es
 * sich ändert. Ein gewähltes Gerät, das gerade fehlt, fällt still auf den
 * Standard zurück — ein abgezogenes USB-Mikrofon legt nichts lahm.
 */
export function AudioEinstellungen() {
  const { t } = useTranslation()
  const [geraete, setGeraete] = useState<AudioGeraete | null>(null)
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  const [duckt, setDuckt] = useState(false)
  const [istAmTesten, setIstAmTesten] = useState(false)
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
    const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)
    const standardLabel = isAndroid
      ? standard
        ? t('mss.audio.standardSystemMit', { name: standard })
        : t('mss.audio.standardSystem')
      : standard
        ? t('mss.audio.standardMit', { name: standard })
        : t('mss.audio.standard')

    const options: DropdownOption[] = [
      { value: '', label: standardLabel },
      ...(wert !== '' && !liste.includes(wert)
        ? [{ value: wert, label: t('mss.audio.fehlt', { name: wert }) }]
        : []),
      ...liste.map((name) => ({ value: name, label: name })),
    ]

    return (
      <div className="w-full">
        <Dropdown
          value={wert}
          onChange={(val) => void waehlen(feld, val)}
          options={options}
          disabled={konfig === null}
          aria-label={t(`mss.audio.${feld === 'audio_eingabe' ? 'eingabe' : 'ausgabe'}`)}
        />
      </div>
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

  const gainPercent = Math.round((konfig?.audio_verstaerkung ?? 1) * 100)

  return (
    <div className="space-y-6">
      {/* 1. Geräte-Auswahl Karte */}
      <section className="msm-card p-6" aria-labelledby="audio-devices-heading">
        <div className="flex items-center justify-between gap-4 mb-4">
          <div className="flex items-center gap-2">
            <Mic className="h-5 w-5 text-secondary" aria-hidden="true" />
            <h2 id="audio-devices-heading" className="font-headline text-title-lg font-semibold text-on-surface">
              {t('profile.audioTitle')}
            </h2>
          </div>
          <span
            className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium ${
              istAmTesten
                ? 'border-status-success/30 bg-status-success/10 text-status-success animate-pulse'
                : 'border-outline-variant bg-surface-container text-on-surface-variant'
            }`}
          >
            <Radio className="h-3.5 w-3.5" aria-hidden="true" />
            {istAmTesten ? t('profile.audioActive') : t('profile.audioInactive')}
          </span>
        </div>

        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-6">
          {t('profile.audioDescription')}
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-2xl">
          <div className="space-y-1.5">
            <label className="block font-label-md text-label-md text-on-surface-variant uppercase tracking-wider">
              {t('profile.audioDeviceLabel')}
            </label>
            {auswahl('audio_eingabe', geraete?.eingaenge ?? [], geraete?.standard_eingang ?? null)}
          </div>

          <div className="space-y-1.5">
            <label className="block font-label-md text-label-md text-on-surface-variant uppercase tracking-wider">
              {t('mss.audio.ausgabe')}
            </label>
            {auswahl('audio_ausgabe', geraete?.ausgaenge ?? [], geraete?.standard_ausgang ?? null)}
          </div>
        </div>

        {!isAndroidClient && (
          <div className="flex items-center justify-between gap-3 border-t border-outline-variant/30 pt-4 mt-6 max-w-2xl">
            <div>
              <span className="text-xs font-medium text-on-surface block">{t('mss.audio.ducking')}</span>
              <span className="text-label-sm text-on-surface-variant">
                {t('mss.audio.duckingHinweis')}
              </span>
            </div>
            <Button variant="secondary" size="sm" onClick={() => void duckingTesten()} disabled={duckt}>
              {duckt
                ? t('mss.einstellungen.duckingLaeuft')
                : t('mss.einstellungen.duckingTesten')}
            </Button>
          </div>
        )}
      </section>

      {/* 2. Signalverarbeitung & Filter */}
      <section className="msm-card p-6" aria-labelledby="audio-processing-heading">
        <div className="flex items-center gap-2 mb-4">
          <Sliders className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 id="audio-processing-heading" className="font-headline text-title-lg font-semibold text-on-surface">
            {t('mss.audio.verarbeitung')}
          </h2>
        </div>
        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-5">
          {t('mss.audio.verarbeitungHinweis')}
        </p>

        <div className="max-w-xl space-y-4">
          <div className="flex items-center justify-between gap-3 p-3 rounded-xl border border-outline-variant/30 bg-surface-container-low/40">
            <div>
              <span className="text-sm font-medium text-on-surface block">
                {t('profile.audioNoiseSuppression')}
              </span>
              <span className="text-xs text-on-surface-variant">
                {t('mss.audio.rauschenHinweis')}
              </span>
            </div>
            <Switch
              checked={konfig?.audio_rauschen ?? true}
              disabled={konfig === null}
              onCheckedChange={(an) => void verarbeitungSetzen('audio_rauschen', an)}
              aria-label={t('profile.audioNoiseSuppression')}
            />
          </div>

          <div className="flex items-center justify-between gap-3 p-3 rounded-xl border border-outline-variant/30 bg-surface-container-low/40">
            <div>
              <span className="text-sm font-medium text-on-surface block">
                {t('profile.audioEchoCancellation')}
              </span>
              <span className="text-xs text-on-surface-variant">
                {t('mss.audio.echoHinweis')}
              </span>
            </div>
            <Switch
              checked={konfig?.audio_echo ?? true}
              disabled={konfig === null}
              onCheckedChange={(an) => void verarbeitungSetzen('audio_echo', an)}
              aria-label={t('profile.audioEchoCancellation')}
            />
          </div>

          <div className="flex items-center justify-between gap-3 p-3 rounded-xl border border-outline-variant/30 bg-surface-container-low/40">
            <div>
              <span className="text-sm font-medium text-on-surface block">
                {t('profile.audioAutoGain')}
              </span>
              <span className="text-xs text-on-surface-variant">
                Gleicht leise und laute Sprachpassagen automatisch an ein gesundes Niveau an.
              </span>
            </div>
            <Switch
              checked={konfig?.audio_autogain ?? true}
              disabled={konfig === null}
              onCheckedChange={(an) => void verarbeitungSetzen('audio_autogain', an)}
              aria-label={t('profile.audioAutoGain')}
            />
          </div>

          <div className="pt-2">
            <Slider
              value={gainPercent}
              min={25}
              max={400}
              step={5}
              disabled={konfig === null}
              onValueChange={(prozent) => void verarbeitungSetzen('audio_verstaerkung', prozent / 100)}
              label={t('mss.audio.verstaerkung')}
              hint={`${gainPercent} %`}
            />
          </div>
        </div>
      </section>

      {/* 3. Testhören & Mikrofon-Pegel */}
      <Testhoeren
        verarbeitung={{
          echo: konfig?.audio_echo ?? true,
          rauschen: konfig?.audio_rauschen ?? true,
          autogain: konfig?.audio_autogain ?? true,
          verstaerkung: konfig?.audio_verstaerkung ?? 1,
        }}
        onTestZustand={setIstAmTesten}
      />
    </div>
  )
}
