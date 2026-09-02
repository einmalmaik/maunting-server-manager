import React, { useState, useRef, useEffect } from 'react'
import jsQR from 'jsqr'
import { Camera, Upload, Keyboard, X, AlertCircle, CheckCircle2 } from 'lucide-react'
import { Button } from '@/Singra/UI'
import { parseOtpauthUri } from './totpEngine'

interface QrScannerModalProps {
  isOpen: boolean
  onClose: () => void
  onDetected: (payload: { secret: string; issuer?: string; account?: string }) => void
}

export const QrScannerModal: React.FC<QrScannerModalProps> = ({
  isOpen,
  onClose,
  onDetected,
}) => {
  const [activeTab, setActiveTab] = useState<'camera' | 'upload' | 'manual'>('camera')
  const [cameraError, setCameraError] = useState<string | null>(null)
  const [isScanning, setIsScanning] = useState(false)
  const [manualCode, setManualCode] = useState('')
  const [scanFeedback, setScanFeedback] = useState<string | null>(null)

  const videoRef = useRef<HTMLVideoElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const animFrameRef = useRef<number | null>(null)

  // Kamera starten
  const startCamera = async () => {
    setCameraError(null)
    setScanFeedback(null)

    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error('Kamera-Zugriff wird von diesem Gerät nicht unterstützt.')
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment', width: { ideal: 640 }, height: { ideal: 480 } },
        audio: false,
      })

      streamRef.current = stream

      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play()
        setIsScanning(true)
        scanFrame()
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Kamera konnte nicht gestartet werden.'
      setCameraError(msg)
      setIsScanning(false)
    }
  }

  // Kamera stoppen
  const stopCamera = () => {
    setIsScanning(false)
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current)
      animFrameRef.current = null
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null
    }
  }

  // Kontinuierliche Frame-Analyse
  const scanFrame = () => {
    if (!videoRef.current || !canvasRef.current) return

    const video = videoRef.current
    const canvas = canvasRef.current

    if (video.readyState === video.HAVE_ENOUGH_DATA) {
      canvas.width = video.videoWidth
      canvas.height = video.videoHeight

      const ctx = canvas.getContext('2d', { willReadFrequently: true })
      if (ctx) {
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height)
        const code = jsQR(imageData.data, imageData.width, imageData.height, {
          inversionAttempts: 'dontInvert',
        })

        if (code && code.data) {
          handleFoundCode(code.data)
          return
        }
      }
    }

    animFrameRef.current = requestAnimationFrame(scanFrame)
  }

  // Code-Verarbeitung (URI oder Base32)
  const handleFoundCode = (rawText: string) => {
    const trimmed = rawText.trim()
    const parsed = parseOtpauthUri(trimmed)

    if (parsed) {
      setScanFeedback(`Erkannt: ${parsed.issuer || '2FA'} (${parsed.label || 'Konto'})`)
      stopCamera()
      setTimeout(() => {
        onDetected({
          secret: parsed.secret,
          issuer: parsed.issuer,
          account: parsed.label,
        })
        onClose()
      }, 500)
      return
    }

    // Falls reines Secret eingegeben/gescannt wurde (z. B. Base32 Zeichen)
    const cleanSecret = trimmed.replace(/\s+/g, '').toUpperCase()
    if (/^[A-Z2-7]{8,}$/.test(cleanSecret)) {
      setScanFeedback('2FA-Schlüssel erfolgreich erkannt.')
      stopCamera()
      setTimeout(() => {
        onDetected({ secret: cleanSecret })
        onClose()
      }, 500)
      return
    }

    setScanFeedback('Unbekanntes QR-Code-Format.')
  }

  // Bilddatei per Dropzone oder Dateiauswahl dekodieren
  const handleImageUpload = (file: File) => {
    setScanFeedback(null)
    setCameraError(null)

    const reader = new FileReader()
    reader.onload = (e) => {
      const img = new Image()
      img.onload = () => {
        const canvas = document.createElement('canvas')
        canvas.width = img.width
        canvas.height = img.height
        const ctx = canvas.getContext('2d')
        if (!ctx) return

        ctx.drawImage(img, 0, 0, img.width, img.height)
        const imageData = ctx.getImageData(0, 0, img.width, img.height)
        const code = jsQR(imageData.data, imageData.width, imageData.height, {
          inversionAttempts: 'attemptBoth',
        })

        if (code && code.data) {
          handleFoundCode(code.data)
        } else {
          setCameraError('Kein gültiger QR-Code im hochgeladenen Bild gefunden.')
        }
      }
      img.src = e.target?.result as string
    }
    reader.readAsDataURL(file)
  }

  // Lifecycle
  useEffect(() => {
    if (isOpen && activeTab === 'camera') {
      void startCamera()
    } else {
      stopCamera()
    }

    return () => {
      stopCamera()
    }
  }, [isOpen, activeTab])

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <div className="relative w-full max-w-md rounded-2xl bg-surface-container border border-outline-variant/30 shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-outline-variant/20 bg-surface-container-low">
          <div className="flex items-center gap-2.5">
            <Camera className="h-5 w-5 text-primary" />
            <h3 className="text-base font-semibold text-on-surface">QR-Code scannen</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-on-surface-variant hover:bg-surface-container hover:text-on-surface transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Tab-Leiste */}
        <div className="flex border-b border-outline-variant/20 bg-surface-container-low/50 p-1 gap-1">
          <button
            type="button"
            onClick={() => setActiveTab('camera')}
            className={`flex-1 flex items-center justify-center gap-2 py-2 text-xs font-medium rounded-lg transition-colors ${
              activeTab === 'camera'
                ? 'bg-surface-container text-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            <Camera className="h-3.5 w-3.5" />
            <span>Kamera</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('upload')}
            className={`flex-1 flex items-center justify-center gap-2 py-2 text-xs font-medium rounded-lg transition-colors ${
              activeTab === 'upload'
                ? 'bg-surface-container text-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            <Upload className="h-3.5 w-3.5" />
            <span>Bild hochladen</span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('manual')}
            className={`flex-1 flex items-center justify-center gap-2 py-2 text-xs font-medium rounded-lg transition-colors ${
              activeTab === 'manual'
                ? 'bg-surface-container text-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            <Keyboard className="h-3.5 w-3.5" />
            <span>Code eingeben</span>
          </button>
        </div>

        {/* Content */}
        <div className="p-5">
          {/* Status Feedback */}
          {scanFeedback && (
            <div className="mb-4 flex items-center gap-2 rounded-xl bg-status-success/15 border border-status-success/30 px-3.5 py-2.5 text-xs text-status-success">
              <CheckCircle2 className="h-4 w-4 shrink-0" />
              <span>{scanFeedback}</span>
            </div>
          )}

          {cameraError && (
            <div className="mb-4 flex items-center gap-2 rounded-xl bg-status-error/15 border border-status-error/30 px-3.5 py-2.5 text-xs text-status-error">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>{cameraError}</span>
            </div>
          )}

          {/* TAB 1: KAMERA */}
          {activeTab === 'camera' && (
            <div className="space-y-3">
              <div className="relative aspect-square w-full overflow-hidden rounded-xl bg-surface-container-lowest border border-outline-variant/30 flex items-center justify-center">
                <video
                  ref={videoRef}
                  playsInline
                  muted
                  className="h-full w-full object-cover"
                />
                <canvas ref={canvasRef} className="hidden" />

                {/* Viewfinder Overlay */}
                {isScanning && (
                  <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                    <div className="w-48 h-48 rounded-xl border-2 border-primary/80 shadow-[0_0_0_9999px_rgba(0,0,0,0.4)] relative">
                      <div className="absolute top-0 left-0 w-4 h-4 border-t-2 border-l-2 border-primary" />
                      <div className="absolute top-0 right-0 w-4 h-4 border-t-2 border-r-2 border-primary" />
                      <div className="absolute bottom-0 left-0 w-4 h-4 border-b-2 border-l-2 border-primary" />
                      <div className="absolute bottom-0 right-0 w-4 h-4 border-b-2 border-r-2 border-primary" />
                    </div>
                  </div>
                )}
              </div>
              <p className="text-center text-xs text-on-surface-variant">
                Halte den QR-Code deiner 2FA-Einrichtung vor die Kamera.
              </p>
            </div>
          )}

          {/* TAB 2: BILD HOCHLADEN */}
          {activeTab === 'upload' && (
            <div className="space-y-4">
              <label
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault()
                  if (e.dataTransfer.files && e.dataTransfer.files[0]) {
                    handleImageUpload(e.dataTransfer.files[0])
                  }
                }}
                className="flex flex-col items-center justify-center aspect-video w-full rounded-xl border-2 border-dashed border-outline-variant/40 bg-surface-container-low hover:bg-surface-container/80 transition-colors cursor-pointer p-6 text-center"
              >
                <Upload className="h-8 w-8 text-primary mb-2" />
                <span className="text-xs font-semibold text-on-surface">
                  Screenshot oder Bild hier ablegen
                </span>
                <span className="text-[11px] text-on-surface-variant mt-0.5">
                  oder klicken zum Auswählen (PNG, JPG)
                </span>
                <input
                  type="file"
                  accept="image/*"
                  onChange={(e) => {
                    if (e.target.files && e.target.files[0]) {
                      handleImageUpload(e.target.files[0])
                    }
                  }}
                  className="hidden"
                />
              </label>
              <p className="text-xs text-center text-on-surface-variant">
                Lade einen Screenshot des QR-Codes von Google, Discord, GitHub etc. hoch.
              </p>
            </div>
          )}

          {/* TAB 3: MANUELLER CODE */}
          {activeTab === 'manual' && (
            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-on-surface mb-1.5">
                  2FA-Geheimschlüssel oder Link
                </label>
                <input
                  type="text"
                  value={manualCode}
                  onChange={(e) => setManualCode(e.target.value)}
                  placeholder="z. B. JBSWY3DPEHPK3PXP oder otpauth://..."
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3.5 py-2.5 text-xs text-on-surface font-mono placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary"
                />
              </div>

              <Button
                onClick={() => handleFoundCode(manualCode)}
                disabled={!manualCode.trim()}
                className="w-full bg-primary text-on-primary"
              >
                Schlüssel übernehmen
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
