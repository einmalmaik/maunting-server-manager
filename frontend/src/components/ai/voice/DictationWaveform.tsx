import { useEffect, useRef } from 'react'

import type { Aufnahme } from './audioAufnahme'

interface DictationWaveformProps {
  aufnahme: Aufnahme | null
  className?: string
}

/**
 * Farb-Tokens der Design-DNA für die Diktier-Wellenform:
 * Cyan-Leuchten (--primary 187 82% 58%) mit sekundären Akzenten.
 */
const FARBE_PRIMAR = '187 85% 60%'
const FARBE_SEKUNDAR = '196 90% 68%'
const FARBE_GLANZ = '180 100% 85%'

export function DictationWaveform({ aufnahme, className = '' }: DictationWaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const aufnahmeRef = useRef<Aufnahme | null>(aufnahme)
  aufnahmeRef.current = aufnahme

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    let laeuft = true
    let animId = 0
    let weichPegel = 0
    let phase = 0

    // Geglättete Frequenzbänder für geschmeidige Übergänge (32 Bänder)
    const bandCount = 32
    const geglaetteteBander = new Float32Array(bandCount)

    const resize = () => {
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      const width = Math.max(120, Math.floor(rect.width))
      const height = Math.max(32, Math.floor(rect.height))

      if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
        canvas.width = width * dpr
        canvas.height = height * dpr
      }
    }

    resize()
    const observer = new ResizeObserver(() => resize())
    observer.observe(canvas)

    const prefersReducedMotion =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches

    const zeichne = (zeit: number) => {
      if (!laeuft || !canvas || !ctx) return

      const w = canvas.width
      const h = canvas.height
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      const cssW = w / dpr
      const cssH = h / dpr

      ctx.save()
      ctx.scale(dpr, dpr)
      ctx.clearRect(0, 0, cssW, cssH)

      const instanz = aufnahmeRef.current
      const rawPegel = instanz ? instanz.pegel() : 0
      weichPegel += (rawPegel - weichPegel) * 0.18

      // Frequenzdaten holen, falls vorhanden
      const freq = instanz?.frequenzen ? instanz.frequenzen() : null
      if (freq && freq.length > 0) {
        const schritt = Math.max(1, Math.floor(freq.length / bandCount))
        for (let i = 0; i < bandCount; i++) {
          const rawVal = freq[i * schritt] / 255
          geglaetteteBander[i] += (rawVal - geglaetteteBander[i]) * 0.25
        }
      } else {
        for (let i = 0; i < bandCount; i++) {
          const sim = Math.sin(zeit * 0.003 + i * 0.3) * 0.5 + 0.5
          geglaetteteBander[i] += (sim * weichPegel - geglaetteteBander[i]) * 0.15
        }
      }

      if (!prefersReducedMotion) {
        phase += 0.04 + weichPegel * 0.08
      }

      const midY = cssH / 2

      // ── 1. Hintergrund-Glimmen ───────────────────────────────────────
      const glowGrad = ctx.createRadialGradient(
        cssW / 2,
        midY,
        4,
        cssW / 2,
        midY,
        Math.max(20, cssW * 0.45),
      )
      glowGrad.addColorStop(0, `hsl(${FARBE_PRIMAR} / ${0.12 + weichPegel * 0.2})`)
      glowGrad.addColorStop(1, 'hsl(187 85% 60% / 0)')
      ctx.fillStyle = glowGrad
      ctx.fillRect(0, 0, cssW, cssH)

      // ── 2. Sanfte Sinus-Wellenbänder ─────────────────────────────────
      ctx.globalCompositeOperation = 'lighter'
      const wellen = [
        { frequenz: 0.02, amp: 0.35, farbe: FARBE_SEKUNDAR, alpha: 0.35, shift: 0 },
        { frequenz: 0.035, amp: 0.55, farbe: FARBE_PRIMAR, alpha: 0.6, shift: 1.5 },
        { frequenz: 0.05, amp: 0.4, farbe: FARBE_GLANZ, alpha: 0.75, shift: 3.0 },
      ]

      for (const welle of wellen) {
        ctx.beginPath()
        const maxAmp = (cssH * 0.38) * (0.15 + weichPegel * 0.85) * welle.amp
        for (let x = 0; x <= cssW; x += 3) {
          const normX = (x / cssW) * 2 - 1 // -1 .. 1
          const glocke = Math.exp(-normX * normX * 3.2) // Dämpfung zu den Rändern
          const y = midY + Math.sin(x * welle.frequenz + phase + welle.shift) * maxAmp * glocke
          if (x === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)
        }

        const strokeGrad = ctx.createLinearGradient(0, 0, cssW, 0)
        strokeGrad.addColorStop(0, `hsl(${welle.farbe} / 0)`)
        strokeGrad.addColorStop(0.5, `hsl(${welle.farbe} / ${welle.alpha * (0.6 + weichPegel * 0.4)})`)
        strokeGrad.addColorStop(1, `hsl(${welle.farbe} / 0)`)

        ctx.strokeStyle = strokeGrad
        ctx.lineWidth = 1.8
        ctx.stroke()
      }

      // ── 3. Reaktive Frequenz-Säulen im Zentrum ────────────────────────
      ctx.globalCompositeOperation = 'source-over'
      const activeBars = Math.min(bandCount, Math.floor(cssW / 9))
      const barSpacing = cssW / (activeBars + 1)
      const barWidth = Math.max(2, Math.min(4.5, barSpacing * 0.45))

      for (let i = 0; i < activeBars; i++) {
        const x = (i + 1) * barSpacing
        const distFromCenter = Math.abs((i / (activeBars - 1)) * 2 - 1)
        const envelope = Math.cos((distFromCenter * Math.PI) / 2) // Schön mittig betont

        const bandIdx = Math.floor((i / activeBars) * bandCount)
        const rawAmp = geglaetteteBander[bandIdx] || 0
        const amp = (0.08 + rawAmp * 0.92 + weichPegel * 0.3) * envelope
        const barHeight = Math.max(3, amp * (cssH * 0.75))

        const topY = midY - barHeight / 2
        const radius = barWidth / 2

        const barGrad = ctx.createLinearGradient(x, topY, x, topY + barHeight)
        barGrad.addColorStop(0, `hsl(${FARBE_GLANZ} / ${0.7 + weichPegel * 0.3})`)
        barGrad.addColorStop(0.5, `hsl(${FARBE_PRIMAR} / ${0.85 + weichPegel * 0.15})`)
        barGrad.addColorStop(1, `hsl(${FARBE_SEKUNDAR} / ${0.6 + weichPegel * 0.4})`)

        ctx.fillStyle = barGrad
        ctx.beginPath()
        if (ctx.roundRect) {
          ctx.roundRect(x - radius, topY, barWidth, barHeight, radius)
        } else {
          ctx.rect(x - radius, topY, barWidth, barHeight)
        }
        ctx.fill()
      }

      ctx.restore()
      animId = requestAnimationFrame(zeichne)
    }

    animId = requestAnimationFrame(zeichne)

    return () => {
      laeuft = false
      cancelAnimationFrame(animId)
      observer.disconnect()
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className={`h-full w-full select-none pointer-events-none ${className}`}
      aria-hidden="true"
    />
  )
}
