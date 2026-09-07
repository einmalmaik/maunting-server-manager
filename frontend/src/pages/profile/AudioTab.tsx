import { useState, useEffect, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Mic, Volume2, Radio, Sliders, ShieldAlert } from 'lucide-react'
import { Button, Dropdown, type DropdownOption, Slider, Switch, ProgressBar } from '@/Singra/UI'
import { getAudioSettings, saveAudioSettings } from '@/lib/audioSettings'
import {
  aktuelleVerarbeitung,
  registriereAudioGeraete,
  registriereAudioVerarbeitung,
} from '@/components/ai/voice/audioGeraete'

export function AudioTab() {
  const { t } = useTranslation()

  // Devices state
  const [inputDevices, setInputDevices] = useState<DropdownOption[]>([])
  const [outputDevices, setOutputDevices] = useState<DropdownOption[]>([])
  const [selectedInputId, setSelectedInputId] = useState<string>('')
  const [selectedOutputId, setSelectedOutputId] = useState<string>('')

  // Processing settings
  const [noiseSuppression, setNoiseSuppression] = useState<boolean>(true)
  const [echoCancellation, setEchoCancellation] = useState<boolean>(true)
  const [autoGainControl, setAutoGainControl] = useState<boolean>(true)
  const [gainPercent, setGainPercent] = useState<number>(100)

  // Live Test State
  const [isTesting, setIsTesting] = useState(false)
  const [testLevel, setTestLevel] = useState(0)
  const [testError, setTestError] = useState<string | null>(null)

  const cleanupRef = useRef<(() => void) | null>(null)
  const gainNodeRef = useRef<GainNode | null>(null)
  const unmountedRef = useRef(false)
  const runIdRef = useRef(0)

  // Load saved settings on mount
  useEffect(() => {
    const saved = getAudioSettings()
    setNoiseSuppression(saved.noiseSuppression)
    setEchoCancellation(saved.echoCancellation)
    setAutoGainControl(saved.autoGainControl)
    setSelectedInputId(saved.preferredMicId || '')

    const currentVerarbeitung = aktuelleVerarbeitung()
    setGainPercent(Math.round(currentVerarbeitung.verstaerkung * 100))
  }, [])

  const loadDevices = useCallback(async () => {
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.enumerateDevices) return
    try {
      const devices = await navigator.mediaDevices.enumerateDevices()
      const inputs = devices.filter((d) => d.kind === 'audioinput')
      const outputs = devices.filter((d) => d.kind === 'audiooutput')

      setInputDevices([
        { value: '', label: t('profile.audioDeviceDefault', 'Systemstandard (Eingabe)') },
        ...inputs.map((d, index) => ({
          value: d.deviceId,
          label: d.label || `Mikrofon ${index + 1}`,
        })),
      ])

      setOutputDevices([
        { value: '', label: t('mss.audio.standard', 'Systemstandard (Ausgabe)') },
        ...outputs.map((d, index) => ({
          value: d.deviceId,
          label: d.label || `Lautsprecher ${index + 1}`,
        })),
      ])
    } catch {
      // Fallback if not permitted
    }
  }, [t])

  useEffect(() => {
    void loadDevices()
    if (typeof navigator !== 'undefined' && navigator.mediaDevices?.addEventListener) {
      navigator.mediaDevices.addEventListener('devicechange', loadDevices)
      return () => {
        navigator.mediaDevices.removeEventListener('devicechange', loadDevices)
      }
    }
  }, [loadDevices])

  // Stop test helper
  const stopTest = useCallback(() => {
    runIdRef.current += 1
    cleanupRef.current?.()
    cleanupRef.current = null
    gainNodeRef.current = null
    setIsTesting(false)
    setTestLevel(0)
  }, [])

  // Start test helper
  const startTest = useCallback(async () => {
    const runId = runIdRef.current + 1
    runIdRef.current = runId
    cleanupRef.current?.()
    cleanupRef.current = null
    setTestError(null)

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: false, // Echo cancellation is disabled during local playback test to prevent silencing
          noiseSuppression,
          autoGainControl,
          ...(selectedInputId ? { deviceId: { ideal: selectedInputId } } : {}),
        },
      })

      if (unmountedRef.current || runId !== runIdRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }

      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
      const ctx = new AudioCtx()
      if (ctx.state === 'suspended') {
        await ctx.resume().catch(() => {})
      }

      // Route to output device if sinkId supported
      if (selectedOutputId) {
        const withSink = ctx as AudioContext & { setSinkId?: (id: string) => Promise<void> }
        if (withSink.setSinkId) {
          await withSink.setSinkId(selectedOutputId).catch(() => {})
        }
      }

      const source = ctx.createMediaStreamSource(stream)
      const gain = ctx.createGain()
      gain.gain.value = gainPercent / 100
      gainNodeRef.current = gain

      const analyser = ctx.createAnalyser()
      analyser.fftSize = 512

      source.connect(gain)
      gain.connect(analyser)
      gain.connect(ctx.destination)

      const pcmBuffer = new Float32Array(analyser.fftSize)
      const interval = window.setInterval(() => {
        analyser.getFloatTimeDomainData(pcmBuffer)
        let sum = 0
        for (let i = 0; i < pcmBuffer.length; i++) {
          sum += pcmBuffer[i] * pcmBuffer[i]
        }
        const rms = Math.sqrt(sum / pcmBuffer.length)
        setTestLevel(Math.min(1, Math.max(0, rms * 4)))
      }, 80)

      cleanupRef.current = () => {
        window.clearInterval(interval)
        source.disconnect()
        gain.disconnect()
        analyser.disconnect()
        stream.getTracks().forEach((track) => track.stop())
        void ctx.close().catch(() => {})
      }

      setIsTesting(true)
    } catch {
      setTestError(t('mss.audio.testhoerenFehler', 'Testhören fehlgeschlagen. Bitte Mikrofonberechtigung prüfen.'))
      setIsTesting(false)
    }
  }, [noiseSuppression, autoGainControl, selectedInputId, selectedOutputId, gainPercent, t])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      unmountedRef.current = true
      cleanupRef.current?.()
    }
  }, [])

  // Live gain adjustment during active test
  useEffect(() => {
    if (gainNodeRef.current) {
      gainNodeRef.current.gain.value = gainPercent / 100
    }
  }, [gainPercent])

  // Restart test if constraints changed while active
  useEffect(() => {
    if (cleanupRef.current) {
      void startTest()
    }
  }, [startTest])

  const handleSelectInput = (deviceId: string) => {
    setSelectedInputId(deviceId)
    saveAudioSettings({ preferredMicId: deviceId || null })
    registriereAudioGeraete(deviceId || null, selectedOutputId || null)
  }

  const handleSelectOutput = (deviceId: string) => {
    setSelectedOutputId(deviceId)
    registriereAudioGeraete(selectedInputId || null, deviceId || null)
  }

  const handleToggleNoise = (val: boolean) => {
    setNoiseSuppression(val)
    saveAudioSettings({ noiseSuppression: val })
    registriereAudioVerarbeitung({ rauschen: val })
  }

  const handleToggleEcho = (val: boolean) => {
    setEchoCancellation(val)
    saveAudioSettings({ echoCancellation: val })
    registriereAudioVerarbeitung({ echo: val })
  }

  const handleToggleAutoGain = (val: boolean) => {
    setAutoGainControl(val)
    saveAudioSettings({ autoGainControl: val })
    registriereAudioVerarbeitung({ autogain: val })
  }

  const handleGainChange = (percent: number) => {
    setGainPercent(percent)
    registriereAudioVerarbeitung({ verstaerkung: percent / 100 })
  }

  return (
    <div className="space-y-6">
      {/* Device Selection Card */}
      <section className="msm-card p-6" aria-labelledby="audio-devices-heading">
        <div className="flex items-center justify-between gap-4 mb-4">
          <div className="flex items-center gap-2">
            <Mic className="h-5 w-5 text-secondary" aria-hidden="true" />
            <h2 id="audio-devices-heading" className="font-headline text-lg font-semibold text-on-surface">
              {t('profile.audioTitle', 'Mikrofon & Audio')}
            </h2>
          </div>
          <span
            className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium ${
              isTesting
                ? 'border-status-success/30 bg-status-success/10 text-status-success animate-pulse'
                : 'border-outline-variant bg-surface-container text-on-surface-variant'
            }`}
          >
            <Radio className="h-3.5 w-3.5" aria-hidden="true" />
            {isTesting ? t('profile.audioActive', 'Test aktiv') : t('profile.audioInactive', 'Bereit')}
          </span>
        </div>

        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-6">
          {t(
            'profile.audioDescription',
            'Konfiguriere deine Audio-Geräte für Sprachnachrichten, den KI-Sprachmodus und das Wake-Word. Änderungen werden einheitlich im gesamten System angewendet.'
          )}
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-2xl">
          <div className="space-y-1.5">
            <label
              htmlFor="audio-input-device"
              className="block font-label-md text-label-md text-on-surface-variant uppercase tracking-wider"
            >
              {t('profile.audioDeviceLabel', 'Eingabegerät (Mikrofon)')}
            </label>
            <Dropdown
              id="audio-input-device"
              value={selectedInputId}
              onChange={handleSelectInput}
              options={inputDevices}
              aria-label={t('profile.audioDeviceLabel', 'Eingabegerät (Mikrofon)')}
            />
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="audio-output-device"
              className="block font-label-md text-label-md text-on-surface-variant uppercase tracking-wider"
            >
              {t('mss.audio.ausgabe', 'Ausgabegerät (Lautsprecher)')}
            </label>
            <Dropdown
              id="audio-output-device"
              value={selectedOutputId}
              onChange={handleSelectOutput}
              options={outputDevices}
              aria-label={t('mss.audio.ausgabe', 'Ausgabegerät (Lautsprecher)')}
            />
          </div>
        </div>
      </section>

      {/* Audio Processing & Hardware Filters */}
      <section className="msm-card p-6" aria-labelledby="audio-processing-heading">
        <div className="flex items-center gap-2 mb-4">
          <Sliders className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 id="audio-processing-heading" className="font-headline text-lg font-semibold text-on-surface">
            {t('mss.audio.verarbeitung', 'Signalverarbeitung & Filter')}
          </h2>
        </div>
        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-5">
          Chromiums integrierte WebRTC-Filterreihe zur Beseitigung von Störgeräuschen und Hall in Sprachräumen und Sprachaufnahmen.
        </p>

        <div className="max-w-xl space-y-4">
          <div className="flex items-center justify-between gap-3 p-3 rounded-xl border border-outline-variant/30 bg-surface-container-low/40">
            <div>
              <span className="text-sm font-medium text-on-surface block">
                {t('profile.audioNoiseSuppression', 'Rauschunterdrückung (Noise Suppression)')}
              </span>
              <span className="text-xs text-on-surface-variant">
                Filtert Hintergrundgeräusche wie Lüfter oder Tastaturanschläge heraus.
              </span>
            </div>
            <Switch
              checked={noiseSuppression}
              onCheckedChange={handleToggleNoise}
              aria-label={t('profile.audioNoiseSuppression', 'Rauschunterdrückung')}
            />
          </div>

          <div className="flex items-center justify-between gap-3 p-3 rounded-xl border border-outline-variant/30 bg-surface-container-low/40">
            <div>
              <span className="text-sm font-medium text-on-surface block">
                {t('profile.audioEchoCancellation', 'Echounterdrückung (Echo Cancellation)')}
              </span>
              <span className="text-xs text-on-surface-variant">
                Verhindert akustische Rückkopplungen bei Lautsprechern ohne Kopfhörer.
              </span>
            </div>
            <Switch
              checked={echoCancellation}
              onCheckedChange={handleToggleEcho}
              aria-label={t('profile.audioEchoCancellation', 'Echounterdrückung')}
            />
          </div>

          <div className="flex items-center justify-between gap-3 p-3 rounded-xl border border-outline-variant/30 bg-surface-container-low/40">
            <div>
              <span className="text-sm font-medium text-on-surface block">
                {t('profile.audioAutoGain', 'Automatische Pegelanpassung (Auto Gain)')}
              </span>
              <span className="text-xs text-on-surface-variant">
                Gleicht leise und laute Sprachpassagen automatisch an ein gesundes Niveau an.
              </span>
            </div>
            <Switch
              checked={autoGainControl}
              onCheckedChange={handleToggleAutoGain}
              aria-label={t('profile.audioAutoGain', 'Automatische Pegelanpassung')}
            />
          </div>

          <div className="pt-2">
            <Slider
              value={gainPercent}
              min={25}
              max={400}
              step={5}
              onValueChange={handleGainChange}
              label={t('mss.audio.verstaerkung', 'Software-Eingangsverstärkung')}
              hint={`${gainPercent} %`}
            />
          </div>
        </div>
      </section>

      {/* Live Testhören & Pegel */}
      <section className="msm-card p-6" aria-labelledby="audio-test-heading">
        <div className="flex items-center gap-2 mb-4">
          <Volume2 className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 id="audio-test-heading" className="font-headline text-lg font-semibold text-on-surface">
            {t('mss.audio.testhoeren', 'Testhören & Mikrofon-Pegel')}
          </h2>
        </div>
        <p className="max-w-2xl font-body-md text-sm leading-6 text-on-surface-variant mb-5">
          Höre deine Stimme live über den gewählten Lautsprecher ab, um Klangqualität und Pegel zu kontrollieren. Die Echounterdrückung ist im Testlauf deaktiviert, damit deine Stimme nicht ausgefiltert wird.
        </p>

        <div className="max-w-xl space-y-4">
          <div className="flex items-center gap-3">
            <Button
              type="button"
              variant={isTesting ? 'secondary' : 'primary'}
              onClick={() => (isTesting ? stopTest() : void startTest())}
              className="gap-2 shrink-0"
            >
              <Mic className="w-4 h-4" />
              <span>{isTesting ? t('profile.audioTestStop', 'Test beenden') : t('profile.audioTestStart', 'Testhören starten')}</span>
            </Button>

            <ProgressBar
              value={isTesting ? Math.round(testLevel * 100) : null}
              ariaLabel={t('mss.audio.testhoerenPegel', 'Mikrofonpegel')}
              className="flex-1"
            />
          </div>

          {isTesting && (
            <div className="flex items-center justify-between text-xs px-1 text-on-surface-variant">
              <span>Pegel: {Math.round(testLevel * 100)}%</span>
              <span className={testLevel > 0.05 ? 'text-emerald-400 font-semibold' : 'text-on-surface-variant/60'}>
                {testLevel > 0.05 ? t('profile.audioSignalDetected', 'Signal erkannt') : 'Kein Signal'}
              </span>
            </div>
          )}

          {testError && (
            <div className="p-3 rounded-xl bg-error/10 border border-error/30 text-error text-xs flex items-center gap-2">
              <ShieldAlert className="w-4 h-4 shrink-0" />
              <span>{testError}</span>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
