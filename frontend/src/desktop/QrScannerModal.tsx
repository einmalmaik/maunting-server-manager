import { useEffect, useRef, useState } from 'react'
import { Camera, X, AlertCircle } from 'lucide-react'
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
  const streamRef = useRef<MediaStream | null>(null)
  const [kameraFehler, setKameraFehler] = useState<string | null>(null)
  const [aktiv, setAktiv] = useState(false)
  const animRef = useRef<number | null>(null)

  useEffect(() => {
    if (!offen) {
      stoppen()
      return
    }

    let abgebrochen = false
    setKameraFehler(null)
    setAktiv(false)

    async function starten() {
      try {
        if (!navigator.mediaDevices?.getUserMedia) {
          throw new Error(t('mss.qrScanner.keineKamera', 'Kein Kamerazugriff auf diesem Gerät verfuegbar.'))
        }

        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' } },
          audio: false,
        })

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
    if (!aktiv || !offen) return

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

      if (detector) {
        try {
          const codes = await detector.detect(videoRef.current)
          if (codes && codes.length > 0 && codes[0].rawValue) {
            const raw = String(codes[0].rawValue).trim()
            if (raw) {
              scannt = false
              stoppen()
              onCodeGefunden(raw)
              onSchliessen()
              return
            }
          }
        } catch {}
      }

      if (scannt) {
        animRef.current = requestAnimationFrame(scannen)
      }
    }

    animRef.current = requestAnimationFrame(scannen)

    return () => {
      scannt = false
      if (animRef.current) cancelAnimationFrame(animRef.current)
    }
  }, [aktiv, offen, onCodeGefunden, onSchliessen])

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
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="qr-scanner-title"
    >
      <div className="relative flex w-full max-w-sm flex-col items-center gap-4 rounded-2xl border border-outline-variant/40 bg-surface-container-high p-5 shadow-2xl">
        <button
          type="button"
          onClick={onSchliessen}
          className="absolute right-3.5 top-3.5 rounded-lg p-1.5 text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface transition-colors"
          aria-label={t('common.close', 'Schließen')}
        >
          <X className="h-5 w-5" />
        </button>

        <div className="flex items-center gap-2">
          <Camera className="h-5 w-5 text-primary" />
          <h2 id="qr-scanner-title" className="font-headline text-base font-semibold text-on-surface">
            {t('mss.qrScanner.titel', 'Kopplungscode scannen')}
          </h2>
        </div>

        {kameraFehler ? (
          <div className="flex flex-col items-center gap-3 p-4 text-center">
            <AlertCircle className="h-10 w-10 text-status-destructive" />
            <p className="text-sm text-status-destructive">{kameraFehler}</p>
            <p className="text-xs text-on-surface-variant">
              {t('mss.qrScanner.hinweisManuell', 'Du kannst den 12-stelligen Kopplungscode jederzeit manuell abtippen.')}
            </p>
          </div>
        ) : (
          <div className="relative flex h-64 w-64 items-center justify-center overflow-hidden rounded-xl bg-black border border-outline-variant/50">
            <video
              ref={videoRef}
              playsInline
              autoPlay
              muted
              className="h-full w-full object-cover"
            />
            {/* Sucher-Rahmen per Design-DNA */}
            <div className="pointer-events-none absolute inset-6 rounded-lg border-2 border-primary/80 shadow-[0_0_15px_rgba(56,189,248,0.35)] flex flex-col justify-between p-1.5">
              <div className="flex justify-between">
                <span className="h-3 w-3 border-l-2 border-t-2 border-primary" />
                <span className="h-3 w-3 border-r-2 border-t-2 border-primary" />
              </div>
              <div className="h-0.5 w-full bg-gradient-to-r from-transparent via-primary to-transparent opacity-80 animate-pulse" />
              <div className="flex justify-between">
                <span className="h-3 w-3 border-l-2 border-b-2 border-primary" />
                <span className="h-3 w-3 border-r-2 border-b-2 border-primary" />
              </div>
            </div>
          </div>
        )}

        <p className="text-center text-xs text-on-surface-variant max-w-xs">
          {t('mss.qrScanner.anweisung', 'Halte die Kamera auf den im Panel angezeigten QR-Code.')}
        </p>

        <Button variant="secondary" onClick={onSchliessen} className="w-full">
          {t('common.cancel', 'Abbrechen')}
        </Button>
      </div>
    </div>
  )
}
