import React, { useState, useEffect, useRef } from 'react'
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
}

export function CameraSnapshotModal({
  open,
  onOpenChange,
  onCapture,
}: CameraSnapshotModalProps) {
  const [stream, setStream] = useState<MediaStream | null>(null)
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
      setError('Kamera-API in diesem Browser nicht unterstützt.')
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
        setError('Kamerazugriff wurde verweigert. Bitte erlaube die Kameranutzung im Browser.')
      } else {
        setError('Kamera konnte nicht gestartet werden. Nutze stattdessen Datei hochladen.')
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
      <DialogContent className="max-w-md p-4 bg-surface border-outline-variant/30 flex flex-col">
        <div className="flex items-center justify-between pb-2 border-b border-outline-variant/20 mb-3">
          <div className="flex items-center gap-2">
            <Camera className="w-4 h-4 text-primary" />
            <span className="font-headline text-body-sm font-bold text-primary">Foto aufnehmen</span>
          </div>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="p-1 rounded-md text-on-surface-variant hover:text-on-surface"
            aria-label="Schließen"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Video / Snapshot Area */}
        <div className="relative aspect-4/3 w-full bg-black/90 rounded-xl overflow-hidden flex items-center justify-center border border-outline-variant/20">
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
            <div className="p-4 text-center space-y-2">
              <AlertCircle className="w-8 h-8 text-amber-400 mx-auto" />
              <p className="text-xs text-white/90">{error}</p>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => fallbackFileInputRef.current?.click()}
                className="mt-2 text-xs gap-1.5"
              >
                <Upload className="w-3.5 h-3.5" />
                <span>Foto aus Datei wählen</span>
              </Button>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2 text-white/70">
              <RefreshCw className="w-6 h-6 animate-spin text-primary" />
              <span className="text-xs">Kamera wird initialisiert …</span>
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

        {/* Action Controls */}
        <div className="flex items-center justify-between mt-3 pt-2">
          {capturedPhoto ? (
            <>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={handleRetake}
                className="text-xs gap-1.5"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                <span>Wiederholen</span>
              </Button>
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={handleUsePhoto}
                className="text-xs gap-1.5 px-4"
              >
                <Check className="w-3.5 h-3.5" />
                <span>Foto verwenden</span>
              </Button>
            </>
          ) : (
            <>
              <div className="flex items-center gap-1.5">
                {hasMultipleCameras && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={handleFlipCamera}
                    className="text-xs gap-1 text-on-surface-variant hover:text-primary"
                    title="Kamera wechseln"
                  >
                    <RefreshCw className="w-3.5 h-3.5" />
                    <span>Wechseln</span>
                  </Button>
                )}
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => fallbackFileInputRef.current?.click()}
                  className="text-xs gap-1 text-on-surface-variant hover:text-primary"
                  title="Aus Datei wählen"
                >
                  <Upload className="w-3.5 h-3.5" />
                  <span>Datei</span>
                </Button>
              </div>

              {stream && (
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  onClick={handleTakePhoto}
                  className="text-xs gap-1.5 px-5 font-semibold"
                >
                  <Camera className="w-3.5 h-3.5" />
                  <span>Auslösen</span>
                </Button>
              )}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
