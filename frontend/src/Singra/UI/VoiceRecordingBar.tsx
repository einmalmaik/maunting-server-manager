import React, { useEffect, useState } from 'react'

export interface VoiceRecordingBarProps {
  durationSeconds?: number
  durationLabel?: string
  statusLabel?: string
  extraInfo?: React.ReactNode
  /** Live audio stream for dynamic reactive visualization */
  stream?: MediaStream | null
  /** Optional manual audio level between 0 and 1 */
  audioLevel?: number
  onCancel?: () => void
  onConfirm?: () => void
  cancelLabel?: string
  confirmLabel?: string
  cancelIcon?: React.ReactNode
  confirmIcon?: React.ReactNode
  className?: string
  variant?: 'danger' | 'primary' | 'neutral'
}

function defaultFormatDuration(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s < 10 ? '0' : ''}${s}`
}

/**
 * VoiceRecordingBar - High-End Sprachaufnahme-Leiste der Maunting Design-DNA.
 * 
 * Gestaltet nach modernen Studio-Standards:
 * - Ruhige, elegante Typografie mit sauber ausgerichteter Tabular-Ziffernanzeige (font-sans)
 * - Sanft pulsierender Live-Mikrofon-Punkt mit weichem Glow
 * - Dynamische Soundbars / Voice-Waveform im Zentrum, die lebendig auf Sprache reagiert
 * - Glasklare, reizarme Buttons mit optimalen Tastatur- und Touch-Flächen
 */
export function VoiceRecordingBar({
  durationSeconds,
  durationLabel,
  statusLabel = 'Jetzt sprechen …',
  extraInfo,
  stream,
  audioLevel: externalLevel,
  onCancel,
  onConfirm,
  cancelLabel = 'Abbrechen',
  confirmLabel = 'Senden',
  cancelIcon,
  confirmIcon,
  className = '',
  variant = 'danger',
}: VoiceRecordingBarProps) {
  const formattedTime =
    durationLabel ??
    (durationSeconds !== undefined ? defaultFormatDuration(durationSeconds) : '0:00')

  // Audio level analysis from MediaStream if supplied
  const [internalLevel, setInternalLevel] = useState(0)

  useEffect(() => {
    if (!stream) return
    let audioCtx: AudioContext | null = null
    let analyser: AnalyserNode | null = null
    let source: MediaStreamAudioSourceNode | null = null
    let rafId = 0
    let running = true

    try {
      const AudioContextClass =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
      if (AudioContextClass) {
        audioCtx = new AudioContextClass()
        analyser = audioCtx.createAnalyser()
        analyser.fftSize = 64
        analyser.smoothingTimeConstant = 0.4
        source = audioCtx.createMediaStreamSource(stream)
        source.connect(analyser)

        const pcmData = new Uint8Array(analyser.frequencyBinCount)

        const tick = () => {
          if (!running) return
          if (analyser) {
            analyser.getByteFrequencyData(pcmData)
            let sum = 0
            for (let i = 0; i < pcmData.length; i++) {
              sum += pcmData[i]
            }
            const avg = sum / pcmData.length / 255
            setInternalLevel(avg)
          }
          rafId = requestAnimationFrame(tick)
        }
        rafId = requestAnimationFrame(tick)
      }
    } catch {
      // Graceful fallback to harmonic sine animation
    }

    return () => {
      running = false
      cancelAnimationFrame(rafId)
      try {
        source?.disconnect()
        analyser?.disconnect()
        audioCtx?.close()
      } catch {}
    }
  }, [stream])

  const activeLevel = externalLevel !== undefined ? externalLevel : internalLevel

  // Visual Styling Palettes
  const isDanger = variant === 'danger'
  const isPrimary = variant === 'primary'

  const dotColor = isDanger
    ? 'bg-status-danger shadow-[0_0_8px_hsl(0_70%_55%/0.6)]'
    : isPrimary
    ? 'bg-primary shadow-[0_0_8px_hsl(187_85%_60%/0.6)]'
    : 'bg-status-success shadow-[0_0_8px_hsl(158_64%_52%/0.6)]'

  const barColor = isDanger
    ? 'bg-status-danger/75'
    : isPrimary
    ? 'bg-primary/75'
    : 'bg-status-success/75'

  const glowBorder = isDanger
    ? 'border-status-danger/25 bg-status-danger/[0.06]'
    : isPrimary
    ? 'border-primary/25 bg-primary/[0.06]'
    : 'border-status-success/25 bg-status-success/[0.06]'

  // 14 fine-tuned voice bars
  const barCount = 14

  return (
    <div
      className={`relative flex items-center justify-between gap-3 px-3.5 py-2 rounded-2xl border backdrop-blur-md transition-all select-none ${glowBorder} ${className}`}
      role="status"
      aria-live="polite"
    >
      {/* Left: Indicator + Timer */}
      <div className="flex items-center gap-2.5 shrink-0 min-w-0">
        <div className="flex items-center gap-2 rounded-full border border-white/10 bg-black/25 px-2.5 py-1 backdrop-blur-xs">
          <span className="relative flex h-2 w-2 items-center justify-center">
            <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-60 ${dotColor}`} />
            <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${dotColor}`} />
          </span>
          <span className="font-sans text-xs font-medium tracking-tight text-white/90 tabular-nums">
            {formattedTime}
          </span>
        </div>

        {statusLabel && (
          <span className="text-xs text-white/60 font-medium truncate hidden md:inline">
            {statusLabel}
          </span>
        )}

        {extraInfo}
      </div>

      {/* Center: Elegant Animated Voice Bars */}
      <div
        className="flex items-center justify-center gap-[3px] flex-1 max-w-[180px] h-6 px-2 mx-auto overflow-hidden opacity-90"
        aria-hidden="true"
      >
        {Array.from({ length: barCount }).map((_, i) => {
          const centerDist = Math.abs(i - (barCount - 1) / 2) / ((barCount - 1) / 2)
          const bellCurve = Math.max(0.25, 1 - centerDist * 0.65)
          const scale =
            activeLevel > 0.02
              ? Math.min(1, Math.max(0.15, (activeLevel * 2.8 + Math.sin(i * 1.3) * 0.15) * bellCurve))
              : 0.18 + Math.sin(i * 0.8) * 0.08

          return (
            <span
              key={i}
              className={`w-[3px] rounded-full transition-all duration-75 ${barColor}`}
              style={{
                height: `${Math.max(4, Math.round(scale * 22))}px`,
                opacity: 0.4 + scale * 0.6,
              }}
            />
          )
        })}
      </div>

      {/* Right: Actions */}
      <div className="flex items-center gap-1.5 shrink-0">
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="h-8 px-3 text-xs rounded-xl font-medium text-white/60 hover:text-status-danger hover:bg-status-danger/10 active:scale-95 transition-all flex items-center gap-1.5 cursor-pointer"
            title={cancelLabel}
            aria-label={cancelLabel}
          >
            {cancelIcon}
            <span className="hidden xs:inline">{cancelLabel}</span>
          </button>
        )}

        {onConfirm && (
          <button
            type="button"
            onClick={onConfirm}
            className={`h-8 px-3.5 text-xs rounded-xl font-medium text-white active:scale-95 transition-all flex items-center gap-1.5 shadow-sm cursor-pointer ${
              isDanger
                ? 'bg-status-danger hover:bg-status-danger/90 text-white shadow-status-danger/20'
                : 'bg-primary hover:bg-primary/90 text-on-primary shadow-primary/20'
            }`}
            title={confirmLabel}
            aria-label={confirmLabel}
          >
            {confirmIcon}
            <span>{confirmLabel}</span>
          </button>
        )}
      </div>
    </div>
  )
}

VoiceRecordingBar.displayName = 'VoiceRecordingBar'

