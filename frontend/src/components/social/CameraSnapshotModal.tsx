import React, { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Dialog,
  DialogContent,
  Button,
} from '@/Singra/UI'
import {
  Camera,
  RefreshCw,
  X,
  Check,
  AlertCircle,
  Upload,
} from 'lucide-react'

interface CameraSnapshotModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCapture: (dataUrl: string) => void
  /** Eigene Überschrift, etwa für einen Augenblick. */
  titel?: string
  /** Eigene Beschriftung des Bestätigungsknopfs. */
  bestaetigen?: string
  /** Ohne Galerie: ein Augenblick ist ein Foto von jetzt. Fällt die Kamera aus, bleibt die Datei. */
  nurKamera?: boolean
}

export function CameraSnapshotModal({
  open,
  onOpenChange,
  onCapture,
  titel,
  bestaetigen,
  nurKamera = false,
}: CameraSnapshotModalProps) {
  const { t } = useTranslation()

  const [stream, setStream] = useState<MediaStream | null>(null)
  // Schlüssel, kein fertiger Satz: ein Sprachwechsel soll auch die
  // stehengebliebene Fehlermeldung mitnehmen.
  const [error, setError] = useState<string | null>(null)
  const [capturedPhoto, setCapturedPhoto] = useState<string | null>(null)
  const [facingMode, setFacingMode] = useState<'user' | 'environment'>('user')
  const [hasMultipleCameras, setHasMultipleCameras] = useState(false)

  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const fallbackFileInputRef = useRef<HTMLInputElement>(null)

  const stopStream = () => {
    if (stream) {
      stream.getTracks().forEach((track) => track.stop())
      setStream(null)
    }
  }

  const startCamera = async (mode: 'user' | 'environment') => {
    stopStream()
    setError(null)
    setCapturedPhoto(null)

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setError('social.camera.unsupported')
      return
    }

    try {
      const devices = await navigator.mediaDevices.enumerateDevices().catch(() => [])
      const videoInputs = devices.filter((d) => d.kind === 'audioinput' ? false : d.kind === 'videoinput')
      setHasMultipleCameras(videoInputs.length > 1)

      const s = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: mode,
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      })
      setStream(s)
      if (videoRef.current) {
        videoRef.current.srcObject = s
      }
    } catch (err) {
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
      stopStream()
      setCapturedPhoto(null)
      setError(null)
    }
    return () => {
      stopStream()
    }
  }, [open, facingMode])

  const handleFlipCamera = () => {
    const nextMode = facingMode === 'user' ? 'environment' : 'user'
    setFacingMode(nextMode)
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
    setCapturedPhoto(dataUrl)
    stopStream()
  }

  const handleRetake = () => {
    setCapturedPhoto(null)
    void startCamera(facingMode)
  }

  const handleUsePhoto = () => {
    if (capturedPhoto) {
      onCapture(capturedPhoto)
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
        className="max-w-md w-full max-h-[90dvh] p-0 overflow-hidden bg-surface-container-lowest text-on-surface border border-outline-variant/40 shadow-2xl flex flex-col my-auto"
      >
        {/* Top Header Bar */}
        <div className="px-4 py-2.5 sm:py-3 bg-surface-container/80 backdrop-blur border-b border-outline-variant/40 flex items-center justify-between shrink-0 z-10">
          <div className="flex items-center gap-2">
            <Camera className="w-4 h-4 text-status-success" />
            <span className="font-headline text-body-sm font-semibold text-on-surface">
              {titel ?? (capturedPhoto ? t('social.camera.preview') : t('social.camera.take'))}
            </span>
          </div>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="p-1.5 rounded-full text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/80 transition-colors"
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Video / Snapshot Viewfinder Area */}
        <div className="relative flex-1 min-h-0 w-full max-h-[60dvh] sm:aspect-[3/4] bg-black flex items-center justify-center overflow-hidden select-none">
          {capturedPhoto ? (
            <img src={capturedPhoto} alt="Snapshot" className="w-full h-full object-cover" />
          ) : stream ? (
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              className={`w-full h-full object-cover ${facingMode === 'user' ? 'scale-x-[-1]' : ''}`}
            />
          ) : error ? (
            <div className="p-6 text-center space-y-3 max-w-xs">
              <AlertCircle className="w-10 h-10 text-status-warning mx-auto" />
              <p className="text-xs text-on-surface leading-relaxed">{t(error)}</p>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => fallbackFileInputRef.current?.click()}
                className="mt-2 text-xs gap-1.5 w-full justify-center"
              >
                <Upload className="w-4 h-4" />
                <span>{t('social.camera.fromFile')}</span>
              </Button>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2.5 text-on-surface-variant">
              <RefreshCw className="w-7 h-7 animate-spin text-status-success" />
              <span className="text-xs">{t('social.camera.starting')}</span>
            </div>
          )}

          {/* Hidden Canvas for capture */}
          <canvas ref={canvasRef} className="hidden" />

          {/* Hidden File Input for Mobile Fallback */}
          <input
            ref={fallbackFileInputRef}
            type="file"
            accept="image/*"
            capture="environment"
            className="hidden"
            onChange={handleFallbackFileChange}
          />
        </div>

        {/* WhatsApp-Style Bottom Controls Bar */}
        <div className="px-6 py-3.5 sm:py-5 bg-surface-container border-t border-outline-variant/40 flex items-center justify-between shrink-0">
          {capturedPhoto ? (
            <div className="flex items-center justify-between w-full gap-3">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={handleRetake}
                className="text-xs gap-1.5 flex-1 justify-center py-2.5"
              >
                <RefreshCw className="w-4 h-4" />
                <span>{t('social.camera.retake')}</span>
              </Button>
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={handleUsePhoto}
                className="text-xs gap-1.5 flex-1 justify-center py-2.5 font-semibold shadow-lg"
              >
                <Check className="w-4 h-4" />
                <span>{bestaetigen ?? t('social.camera.use')}</span>
              </Button>
            </div>
          ) : (
            <div className="flex items-center justify-around w-full">
              {/* Left: Gallery / Upload */}
              {nurKamera ? (
                <div className="w-14" />
              ) : (
              <button
                type="button"
                onClick={() => fallbackFileInputRef.current?.click()}
                className="flex flex-col items-center gap-1 p-2 rounded-xl text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60 transition-colors"
                title={t('social.camera.fromFile')}
                aria-label={t('social.camera.fromFile')}
              >
                <div className="w-10 h-10 rounded-xl bg-surface-container-high flex items-center justify-center border border-outline-variant/40">
                  <Upload className="w-5 h-5" />
                </div>
                <span className="text-label-sm">{t('social.camera.gallery')}</span>
              </button>
              )}

              {/* Center: Big WhatsApp Shutter Button */}
              {stream ? (
                <button
                  type="button"
                  onClick={handleTakePhoto}
                  className="w-18 h-18 rounded-full border-4 border-white flex items-center justify-center p-1 bg-white/20 active:scale-90 hover:bg-white/30 transition-all shadow-2xl cursor-pointer"
                  title={t('social.camera.take')}
                  aria-label={t('social.camera.take')}
                >
                  <div className="w-14 h-14 rounded-full bg-white shadow-inner" />
                </button>
              ) : (
                <div className="w-18 h-18" />
              )}

              {/* Right: Flip Camera */}
              <button
                type="button"
                onClick={handleFlipCamera}
                disabled={!stream || !hasMultipleCameras}
                className="flex flex-col items-center gap-1 p-2 rounded-xl text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60 transition-colors disabled:opacity-30 disabled:pointer-events-none"
                title={t('social.camera.flip')}
                aria-label={t('social.camera.flip')}
              >
                <div className="w-10 h-10 rounded-xl bg-surface-container-high flex items-center justify-center border border-outline-variant/40">
                  <RefreshCw className="w-5 h-5" />
                </div>
                <span className="text-label-sm">{t('social.camera.flipShort')}</span>
              </button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
