import { useEffect, useRef, useState } from 'react'
import { Camera, X, AlertCircle, Zap, CheckCircle2 } from 'lucide-react'
import jsQR from 'jsqr'
import { useTranslation } from 'react-i18next'
import { Button } from '@/Singra/UI'

interface QrScannerModalProps {
  offen: boolean
  onSchliessen: () => void
  onCodeGefunden: (code: string) => void
}

export function QrScannerModal({ offen, onSchliessen, onCodeGefunden }: QrScannerModalProps) {
  const { t } = useTranslation()
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [kameraFehler, setKameraFehler] = useState<string | null>(null)
  const [aktiv, setAktiv] = useState(false)
  const [erkannt, setErkannt] = useState(false)
  const animRef = useRef<number | null>(null)

  useEffect(() => {
    if (!offen) {
      stoppen()
      return
    }

    let abgebrochen = false
    setKameraFehler(null)
    setAktiv(false)
    setErkannt(false)

    async function starten() {
      try {
        if (!navigator.mediaDevices?.getUserMedia) {
          throw new Error(t('mss.qrScanner.keineKamera', 'Kein Kamerazugriff auf diesem Gerät verfügbar.'))
        }

        // Hohe Aufloesung & kontinuierlicher Autofokus fuer schnelle Distanzerkennung
        const constraints: MediaStreamConstraints = {
          video: {
            facingMode: { ideal: 'environment' },
            width: { ideal: 1920, min: 640 },
            height: { ideal: 1080, min: 480 },
            frameRate: { ideal: 60, min: 30 },
          },
          audio: false,
        }

        const stream = await navigator.mediaDevices.getUserMedia(constraints)

        if (abgebrochen) {
          stream.getTracks().forEach((t) => t.stop())
          return
        }

        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          await videoRef.current.play().catch(() => {})
          setAktiv(true)
        }
      } catch (err: any) {
        if (!abgebrochen) {
          setKameraFehler(err.message || t('mss.qrScanner.fehler', 'Kamera konnte nicht geöffnet werden.'))
        }
      }
    }

    void starten()

    return () => {
      abgebrochen = true
      stoppen()
    }
  }, [offen, t])

  useEffect(() => {
    if (!aktiv || !offen || erkannt) return

    let detector: any = null
    if ('BarcodeDetector' in window) {
      try {
        detector = new (window as any).BarcodeDetector({ formats: ['qr_code'] })
      } catch {}
    }

    let scannt = true

    async function scannen() {
      if (!scannt || !videoRef.current || videoRef.current.readyState < 2) {
        if (scannt) animRef.current = requestAnimationFrame(scannen)
        return
      }

      const video = videoRef.current

      // 1. Prioritaet: Nativer Hardware-BarcodeDetector (falls im Browser verfuegbar)
      if (detector) {
        try {
          const codes = await detector.detect(video)
          if (codes && codes.length > 0 && codes[0].rawValue) {
            const raw = String(codes[0].rawValue).trim()
            if (raw) {
              codeGefunden(raw)
              return
            }
          }
        } catch {}
      }

      // 2. Prioritaet: Blitzschnelle jsQR-Erkennung (Ultra-Fast Canvas Analysis)
      try {
        if (!canvasRef.current) {
          canvasRef.current = document.createElement('canvas')
        }
        const canvas = canvasRef.current
        const w = video.videoWidth || 640
        const h = video.videoHeight || 480

        if (canvas.width !== w || canvas.height !== h) {
          canvas.width = w
          canvas.height = h
        }

        const ctx = canvas.getContext('2d', { willReadFrequently: true })
        if (ctx) {
          ctx.drawImage(video, 0, 0, w, h)
          const imageData = ctx.getImageData(0, 0, w, h)
          const qr = jsQR(imageData.data, imageData.width, imageData.height, {
            inversionAttempts: 'attemptBoth', // Erkennt auch invertierte & kontrastreiche QR-Codes sofort
          })

          if (qr && qr.data && qr.data.trim()) {
            codeGefunden(qr.data.trim())
            return
          }
        }
      } catch {}

      if (scannt) {
        animRef.current = requestAnimationFrame(scannen)
      }
    }

    function codeGefunden(code: string) {
      scannt = false
      setErkannt(true)
      try {
        navigator.vibrate?.([40, 30, 40])
      } catch {}

      // Kurzer visueller Bestaetigungsimpuls, dann Callback ausloesen
      setTimeout(() => {
        stoppen()
        onCodeGefunden(code)
        onSchliessen()
      }, 350)
    }

    animRef.current = requestAnimationFrame(scannen)

    return () => {
      scannt = false
      if (animRef.current) cancelAnimationFrame(animRef.current)
    }
  }, [aktiv, offen, erkannt, onCodeGefunden, onSchliessen])

  function stoppen() {
    if (animRef.current) {
      cancelAnimationFrame(animRef.current)
      animRef.current = null
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
    setAktiv(false)
  }

  if (!offen) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-md p-4 animate-in fade-in duration-200"
      role="dialog"
      aria-modal="true"
      aria-labelledby="qr-scanner-title"
    >
      <div className="relative flex w-full max-w-sm flex-col items-center gap-4 rounded-3xl border border-primary/30 bg-surface-container-high/95 p-6 shadow-[0_0_50px_rgba(56,189,248,0.2)]">
        <button
          type="button"
          onClick={onSchliessen}
          className="absolute right-4 top-4 rounded-xl p-2 text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface transition-colors"
          aria-label={t('common.close', 'Schließen')}
        >
          <X className="h-5 w-5" />
        </button>

        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Camera className="h-5 w-5" />
          </div>
          <h2 id="qr-scanner-title" className="font-headline text-base font-semibold text-on-surface">
            {t('mss.qrScanner.titel', 'Kopplungscode scannen')}
          </h2>
        </div>

        {kameraFehler ? (
          <div className="flex flex-col items-center gap-3 p-4 text-center">
            <AlertCircle className="h-12 w-12 text-status-destructive" />
            <p className="text-sm font-medium text-status-destructive">{kameraFehler}</p>
            <p className="text-xs text-on-surface-variant">
              {t('mss.qrScanner.hinweisManuell', 'Du kannst den 12-stelligen Kopplungscode jederzeit manuell abtippen.')}
            </p>
          </div>
        ) : (
          <div className="relative flex h-72 w-72 items-center justify-center overflow-hidden rounded-2xl bg-black border border-outline-variant/40 shadow-inner">
            <video
              ref={videoRef}
              playsInline
              autoPlay
              muted
              className="h-full w-full object-cover"
            />

            {/* Holografischer Sucher-Rahmen mit Laser-Scanlinie */}
            <div
              className={`pointer-events-none absolute inset-6 rounded-xl border-2 transition-all duration-300 ${
                erkannt
                  ? 'border-status-success shadow-[0_0_30px_rgba(34,197,94,0.6)] bg-status-success/15'
                  : 'border-primary/80 shadow-[0_0_20px_rgba(56,189,248,0.4)]'
              } flex flex-col justify-between p-2`}
            >
              <div className="flex justify-between">
                <span className="h-4 w-4 border-l-2 border-t-2 border-primary" />
                <span className="h-4 w-4 border-r-2 border-t-2 border-primary" />
              </div>

              {erkannt ? (
                <div className="flex items-center justify-center">
                  <CheckCircle2 className="h-12 w-12 text-status-success animate-bounce" />
                </div>
              ) : (
                <div className="h-0.5 w-full bg-gradient-to-r from-transparent via-primary to-transparent opacity-90 animate-pulse shadow-[0_0_8px_#38bdf8]" />
              )}

              <div className="flex justify-between">
                <span className="h-4 w-4 border-l-2 border-b-2 border-primary" />
                <span className="h-4 w-4 border-r-2 border-b-2 border-primary" />
              </div>
            </div>

            <div className="absolute bottom-2 flex items-center gap-1 rounded-full bg-black/60 px-3 py-1 text-[11px] font-medium text-primary backdrop-blur-sm">
              <Zap className="h-3 w-3 text-primary animate-pulse" />
              <span>Soforterkennung aktiv</span>
            </div>
          </div>
        )}

        <p className="text-center text-xs text-on-surface-variant max-w-xs">
          {t('mss.qrScanner.anweisung', 'Halte die Kamera kurz auf den im Panel angezeigten QR-Code.')}
        </p>

        <Button variant="secondary" onClick={onSchliessen} className="w-full">
          {t('common.cancel', 'Abbrechen')}
        </Button>
      </div>
    </div>
  )
}
