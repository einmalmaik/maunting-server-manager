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
      <DialogContent
        showCloseButton={false}
        className="max-w-md w-full max-h-[90dvh] p-0 overflow-hidden bg-zinc-950 text-white border border-zinc-800 shadow-2xl flex flex-col my-auto"
      >
        {/* Top Header Bar */}
        <div className="px-4 py-2.5 sm:py-3 bg-zinc-900/80 backdrop-blur border-b border-zinc-800 flex items-center justify-between shrink-0 z-10">
          <div className="flex items-center gap-2">
            <Camera className="w-4 h-4 text-emerald-400" />
            <span className="font-headline text-body-sm font-semibold text-white">
              {capturedPhoto ? 'Foto-Vorschau' : 'Foto aufnehmen'}
            </span>
          </div>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="p-1.5 rounded-full text-zinc-400 hover:text-white hover:bg-zinc-800/80 transition-colors"
            aria-label="Schließen"
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
              <AlertCircle className="w-10 h-10 text-amber-400 mx-auto" />
              <p className="text-xs text-zinc-300 leading-relaxed">{error}</p>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => fallbackFileInputRef.current?.click()}
                className="mt-2 text-xs gap-1.5 w-full justify-center"
              >
                <Upload className="w-4 h-4" />
                <span>Foto aus Datei wählen</span>
              </Button>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2.5 text-zinc-400">
              <RefreshCw className="w-7 h-7 animate-spin text-emerald-400" />
              <span className="text-xs">Kamera wird gestartet …</span>
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
        <div className="px-6 py-3.5 sm:py-5 bg-zinc-900 border-t border-zinc-800 flex items-center justify-between shrink-0">
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
                <span>Wiederholen</span>
              </Button>
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={handleUsePhoto}
                className="text-xs gap-1.5 flex-1 justify-center py-2.5 font-semibold shadow-lg"
              >
                <Check className="w-4 h-4" />
                <span>Foto verwenden</span>
              </Button>
            </div>
          ) : (
            <div className="flex items-center justify-around w-full">
              {/* Left: Gallery / Upload */}
              <button
                type="button"
                onClick={() => fallbackFileInputRef.current?.click()}
                className="flex flex-col items-center gap-1 p-2 rounded-xl text-zinc-400 hover:text-white hover:bg-zinc-800/60 transition-colors"
                title="Aus Datei wählen"
                aria-label="Aus Datei wählen"
              >
                <div className="w-10 h-10 rounded-xl bg-zinc-800 flex items-center justify-center border border-zinc-700">
                  <Upload className="w-5 h-5" />
                </div>
                <span className="text-[10px]">Galerie</span>
              </button>

              {/* Center: Big WhatsApp Shutter Button */}
              {stream ? (
                <button
                  type="button"
                  onClick={handleTakePhoto}
                  className="w-18 h-18 rounded-full border-4 border-white flex items-center justify-center p-1 bg-white/20 active:scale-90 hover:bg-white/30 transition-all shadow-2xl cursor-pointer"
                  title="Foto aufnehmen"
                  aria-label="Foto auslösen"
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
                className="flex flex-col items-center gap-1 p-2 rounded-xl text-zinc-400 hover:text-white hover:bg-zinc-800/60 transition-colors disabled:opacity-30 disabled:pointer-events-none"
                title="Kamera wechseln"
                aria-label="Kamera wechseln"
              >
                <div className="w-10 h-10 rounded-xl bg-zinc-800 flex items-center justify-center border border-zinc-700">
                  <RefreshCw className="w-5 h-5" />
                </div>
                <span className="text-[10px]">Wechseln</span>
              </button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
