import React, { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Dialog,
  DialogContent,
  Button,
} from '@/Singra/UI'
import {
  RefreshCw,
  SwitchCamera,
  X,
  Check,
  AlertCircle,
  Upload,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react'
import { STEMPEL_STILE, stempelTexte, stempleFoto, type StempelStil } from './augenblickStempel'

interface CameraSnapshotModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCapture: (dataUrl: string) => void
  /** Eigene Überschrift, etwa für einen Augenblick. */
  titel?: string
  /** Eigene Beschriftung des Bestätigungsknopfs. */
  bestaetigen?: string
  /** Ein Augenblick ist ein Foto von jetzt: keine Datei, auch nicht, wenn die Kamera ausfällt. */
  nurKamera?: boolean
  /** Nach dem Auslösen lassen sich Uhrzeit und Datum ins Foto stempeln. */
  mitStempel?: boolean
}

/** Runder Knopf über dem Kamerabild — hell auf dunklem Schleier, egal was die Kamera zeigt. */
const bildknopf =
  'w-11 h-11 shrink-0 rounded-full bg-black/40 text-white backdrop-blur-md flex items-center justify-center transition-colors hover:bg-black/55 active:scale-95'

/**
 * Die Kamera als Vollbild am Telefon, als Karte am Rechner.
 *
 * Eine Datei wählt man hier nicht: die Anrufer haben dafür ihren eigenen
 * Knopf. Nur wenn die Kamera ausfällt, bietet das normale Foto die Datei als
 * Ausweg an — ein Augenblick nicht.
 */
export function CameraSnapshotModal({
  open,
  onOpenChange,
  onCapture,
  titel,
  bestaetigen,
  nurKamera = false,
  mitStempel = false,
}: CameraSnapshotModalProps) {
  const { t, i18n } = useTranslation()

  const [stream, setStream] = useState<MediaStream | null>(null)
  // Schlüssel, kein fertiger Satz: ein Sprachwechsel soll auch die
  // stehengebliebene Fehlermeldung mitnehmen.
  const [error, setError] = useState<string | null>(null)
  const [capturedPhoto, setCapturedPhoto] = useState<string | null>(null)
  const [facingMode, setFacingMode] = useState<'user' | 'environment'>('user')
  const [hasMultipleCameras, setHasMultipleCameras] = useState(false)
  // Der Stil bleibt über „Wiederholen" hinweg stehen; der Zeitpunkt ist der des Auslösens.
  const [stil, setStil] = useState<StempelStil>('ohne')
  const [ausgeloest, setAusgeloest] = useState<Date | null>(null)
  const [gestempelt, setGestempelt] = useState<{ quelle: string; stil: StempelStil; bild: string } | null>(null)
  const wischStart = useRef<number | null>(null)
  // Seitenverhältnis des Suchers beim Auslösen — der Stempel richtet sich danach.
  const [sicht, setSicht] = useState<number | undefined>(undefined)
  const sucherRef = useRef<HTMLDivElement>(null)

  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const fallbackFileInputRef = useRef<HTMLInputElement>(null)
  // Der laufende Strom im Ref, nicht nur im Zustand: das Aufräumen eines
  // Effekts sieht sonst den Strom seines eigenen Renderlaufs, nicht den
  // aktuellen, und die Kamera bliebe an.
  const streamRef = useRef<MediaStream | null>(null)
  // Jeder Start zählt hoch. Kommt ein Strom an, nachdem das Fenster schon zu
  // oder die Kamera gewechselt ist, wird er sofort beendet.
  const startNummer = useRef(0)

  const stopStream = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    setStream(null)
  }

  const startCamera = async (mode: 'user' | 'environment') => {
    const nummer = ++startNummer.current
    stopStream()
    setError(null)
    setCapturedPhoto(null)

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setError('social.camera.unsupported')
      return
    }

    try {
      const devices = await navigator.mediaDevices.enumerateDevices().catch(() => [])
      setHasMultipleCameras(devices.filter((d) => d.kind === 'videoinput').length > 1)

      const s = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: mode,
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      })
      if (nummer !== startNummer.current) {
        s.getTracks().forEach((track) => track.stop())
        return
      }
      streamRef.current = s
      setStream(s)
    } catch (err) {
      if (nummer !== startNummer.current) return
      const msg = err instanceof Error ? err.name : 'Unknown'
      if (msg === 'NotAllowedError' || msg === 'PermissionDeniedError') {
        setError('social.camera.denied')
      } else {
        setError('social.camera.failed')
      }
    }
  }

  useEffect(() => {
    if (open) {
      void startCamera(facingMode)
    } else {
      startNummer.current++
      stopStream()
      setCapturedPhoto(null)
      setError(null)
    }
    return () => {
      startNummer.current++
      stopStream()
    }
  }, [open, facingMode])

  // Das <video> entsteht erst mit dem Strom. Beim Start gibt es darum noch
  // kein Element, dem man ihn geben könnte — das geschieht hier.
  useEffect(() => {
    if (videoRef.current && stream) videoRef.current.srcObject = stream
  }, [stream, capturedPhoto])

  useEffect(() => {
    if (!mitStempel || !capturedPhoto || stil === 'ohne' || !ausgeloest) return
    let aktuell = true
    stempleFoto(capturedPhoto, stil, stempelTexte(ausgeloest, i18n.language), sicht)
      .then((bild) => {
        if (aktuell) setGestempelt({ quelle: capturedPhoto, stil, bild })
      })
      .catch(() => {
        // Ein Stempel, der nicht gelingt, darf den Augenblick nicht aufhalten.
        if (aktuell) setStil('ohne')
      })
    return () => {
      aktuell = false
    }
  }, [mitStempel, capturedPhoto, stil, ausgeloest, sicht, i18n.language])

  // Das Bild, das gezeigt und gesendet wird. `null` heisst: der Stempel wird noch gezeichnet.
  const fertigesBild =
    !capturedPhoto || !mitStempel || stil === 'ohne'
      ? capturedPhoto
      : gestempelt && gestempelt.quelle === capturedPhoto && gestempelt.stil === stil
        ? gestempelt.bild
        : null

  const wechsleStil = (schritt: 1 | -1) => {
    setStil((vorher) => {
      const i = STEMPEL_STILE.indexOf(vorher)
      return STEMPEL_STILE[(i + schritt + STEMPEL_STILE.length) % STEMPEL_STILE.length]
    })
  }

  const handleFlipCamera = () => {
    setFacingMode((vorher) => (vorher === 'user' ? 'environment' : 'user'))
  }

  const handleTakePhoto = () => {
    if (!videoRef.current || !canvasRef.current) return
    const video = videoRef.current
    const canvas = canvasRef.current

    canvas.width = video.videoWidth || 640
    canvas.height = video.videoHeight || 480
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    if (facingMode === 'user') {
      ctx.translate(canvas.width, 0)
      ctx.scale(-1, 1)
    }
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
    const dataUrl = canvas.toDataURL('image/jpeg', 0.88)
    setAusgeloest(new Date())
    const rahmen = sucherRef.current?.getBoundingClientRect()
    setSicht(rahmen && rahmen.height > 0 ? rahmen.width / rahmen.height : undefined)
    setCapturedPhoto(dataUrl)
    stopStream()
  }

  const handleRetake = () => {
    setCapturedPhoto(null)
    void startCamera(facingMode)
  }

  const handleUsePhoto = () => {
    if (fertigesBild) {
      onCapture(fertigesBild)
      onOpenChange(false)
    }
  }

  const handleFallbackFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        setCapturedPhoto(reader.result)
      }
    }
    reader.readAsDataURL(file)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        overlayClassName="p-0 sm:p-4"
        className="max-sm:max-w-none sm:max-w-md h-[100dvh] sm:h-auto rounded-none sm:rounded-2xl border-0 sm:border sm:border-outline-variant/40 p-0 bg-surface-container-lowest text-on-surface shadow-2xl"
      >
        <div ref={sucherRef} className="relative flex-1 min-h-0 w-full sm:flex-none sm:aspect-[3/4] sm:max-h-[85dvh] overflow-hidden select-none">
          {capturedPhoto ? (
            <img
              src={fertigesBild ?? capturedPhoto}
              alt={t('social.camera.preview')}
              className="absolute inset-0 w-full h-full object-cover touch-pan-y"
              draggable={false}
              onPointerDown={mitStempel ? (e) => (wischStart.current = e.clientX) : undefined}
              onPointerUp={
                mitStempel
                  ? (e) => {
                      const start = wischStart.current
                      wischStart.current = null
                      if (start === null || Math.abs(e.clientX - start) < 40) return
                      wechsleStil(e.clientX < start ? 1 : -1)
                    }
                  : undefined
              }
            />
          ) : stream ? (
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              className={`absolute inset-0 w-full h-full object-cover ${facingMode === 'user' ? 'scale-x-[-1]' : ''}`}
            />
          ) : error ? (
            <div className="absolute inset-0 flex items-center justify-center p-6">
              <div className="max-w-xs text-center space-y-3">
                <AlertCircle className="w-10 h-10 text-status-warning mx-auto" />
                <p className="text-sm text-on-surface leading-relaxed">{t(error)}</p>
                {nurKamera ? (
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => void startCamera(facingMode)}
                    className="w-full"
                  >
                    <RefreshCw className="w-4 h-4" />
                    <span>{t('common.retry')}</span>
                  </Button>
                ) : (
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => fallbackFileInputRef.current?.click()}
                    className="w-full"
                  >
                    <Upload className="w-4 h-4" />
                    <span>{t('social.camera.fromFile')}</span>
                  </Button>
                )}
              </div>
            </div>
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2.5 text-on-surface-variant">
              <RefreshCw className="w-7 h-7 animate-spin text-primary" />
              <span className="text-xs">{t('social.camera.starting')}</span>
            </div>
          )}

          <canvas ref={canvasRef} className="hidden" />
          {!nurKamera && (
            <input
              ref={fallbackFileInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              className="hidden"
              onChange={handleFallbackFileChange}
            />
          )}

          {/* Oben: Schliessen und Überschrift, über dem Bild. */}
          <div className="absolute inset-x-0 top-0 grid grid-cols-[2.75rem_1fr_2.75rem] items-center gap-2 px-3 pt-[max(0.75rem,env(safe-area-inset-top))] pb-6 bg-gradient-to-b from-black/45 to-transparent">
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className={bildknopf}
              aria-label={t('common.close')}
            >
              <X className="w-5 h-5" />
            </button>
            <span className="truncate text-center text-sm font-semibold text-white drop-shadow">
              {titel ?? (capturedPhoto ? t('social.camera.preview') : t('social.camera.take'))}
            </span>
          </div>

          {/* Unten: drei gleich breite Spalten, damit der Auslöser genau mittig steht. */}
          <div className="absolute inset-x-0 bottom-0 px-4 pt-10 pb-[max(1.25rem,env(safe-area-inset-bottom))] bg-gradient-to-t from-black/55 to-transparent">
            {capturedPhoto ? (
              <div className="space-y-3">
                {mitStempel && (
                  <div className="flex items-center justify-center gap-3">
                    <button type="button" onClick={() => wechsleStil(-1)} className={bildknopf} aria-label={t('messenger.moment.stamp.prev')}>
                      <ChevronLeft className="w-5 h-5" />
                    </button>
                    <span className="min-w-[8rem] text-center text-sm font-semibold text-white drop-shadow" aria-live="polite">
                      {t(`messenger.moment.stamp.${stil}`)}
                    </span>
                    <button type="button" onClick={() => wechsleStil(1)} className={bildknopf} aria-label={t('messenger.moment.stamp.next')}>
                      <ChevronRight className="w-5 h-5" />
                    </button>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-3">
                  <Button type="button" variant="secondary" size="lg" onClick={handleRetake}>
                    <RefreshCw className="w-4 h-4" />
                    <span>{t('social.camera.retake')}</span>
                  </Button>
                  <Button type="button" variant="primary" size="lg" onClick={handleUsePhoto} disabled={!fertigesBild} className="font-semibold">
                    <Check className="w-4 h-4" />
                    <span>{bestaetigen ?? t('social.camera.use')}</span>
                  </Button>
                </div>
              </div>
            ) : error ? null : (
              <div className="grid grid-cols-3 items-center justify-items-center">
                <span />
                <button
                  type="button"
                  onClick={handleTakePhoto}
                  disabled={!stream}
                  className="w-[4.5rem] h-[4.5rem] rounded-full border-4 border-white p-1 bg-white/20 transition-all active:scale-90 hover:bg-white/30 disabled:opacity-40 disabled:pointer-events-none"
                  aria-label={t('social.camera.take')}
                >
                  <span className="block w-full h-full rounded-full bg-white" />
                </button>
                {stream && hasMultipleCameras ? (
                  <button
                    type="button"
                    onClick={handleFlipCamera}
                    className={bildknopf}
                    aria-label={t('social.camera.flip')}
                  >
                    <SwitchCamera className="w-5 h-5" />
                  </button>
                ) : (
                  <span />
                )}
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
