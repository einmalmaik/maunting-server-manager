import { describe, expect, it, vi } from 'vitest'
import {
  istLokalesWhisperMoeglich,
  resampleZu16k,
  transkribiereAudioMitWhisper,
  trimmeStille,
} from './lokalesWhisper'

describe('lokalesWhisper On-Device Speech Recognition', () => {
  describe('resampleZu16k', () => {
    it('gibt das Original unverändert zurück, wenn die Abtastrate bereits 16 kHz ist', () => {
      const audio = new Float32Array([0.1, 0.2, 0.3])
      const res = resampleZu16k(audio, 16000)
      expect(res).toBe(audio)
    })

    it('behandelt leere Buffer sauber', () => {
      const audio = new Float32Array([])
      const res = resampleZu16k(audio, 48000)
      expect(res.length).toBe(0)
    })

    it('skaliert 48 kHz (Handy-/Standard-Audio) auf 16 kHz herunter', () => {
      // 48.000 Samples entspricht bei 16 kHz 16.000 Samples (3:1 Verhältnis)
      const laenge48k = 480
      const audio = new Float32Array(laenge48k)
      for (let i = 0; i < laenge48k; i++) {
        audio[i] = Math.sin((i / laenge48k) * Math.PI * 2)
      }

      const res = resampleZu16k(audio, 48000)
      expect(res.length).toBe(160)
      expect(Math.abs(res[0])).toBeLessThan(0.01)
    })

    it('skaliert 8 kHz auf 16 kHz herauf', () => {
      const laenge8k = 80
      const audio = new Float32Array(laenge8k)
      audio.fill(0.5)

      const res = resampleZu16k(audio, 8000)
      expect(res.length).toBe(160)
      expect(res[50]).toBeCloseTo(0.5, 2)
    })
  })

  describe('istLokalesWhisperMoeglich', () => {
    it('liefert false, wenn navigator.mediaDevices fehlt', () => {
      const alt = navigator.mediaDevices
      Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true })
      expect(istLokalesWhisperMoeglich()).toBe(false)
      Object.defineProperty(navigator, 'mediaDevices', { value: alt, configurable: true })
    })

    it('liefert true, wenn getUserMedia und AudioContext vorhanden sind', () => {
      const altMedia = navigator.mediaDevices
      const altAudioCtx = (window as any).AudioContext

      Object.defineProperty(navigator, 'mediaDevices', {
        value: { getUserMedia: vi.fn() },
        configurable: true,
      })
      ;(window as any).AudioContext = class {}

      expect(istLokalesWhisperMoeglich()).toBe(true)

      Object.defineProperty(navigator, 'mediaDevices', { value: altMedia, configurable: true })
      ;(window as any).AudioContext = altAudioCtx
    })
  })

  describe('trimmeStille', () => {
    it('behält leeren Buffer bei', () => {
      const res = trimmeStille(new Float32Array(0))
      expect(res.length).toBe(0)
    })

    it('schneidet führende und nachlaufende Stille ab', () => {
      const audio = new Float32Array(25000)
      for (let i = 10000; i < 15000; i++) {
        audio[i] = 0.5
      }
      const getrimmt = trimmeStille(audio)
      expect(getrimmt.length).toBeLessThan(audio.length)
      expect(getrimmt.length).toBeGreaterThanOrEqual(5000)
    })
  })

  describe('transkribiereAudioMitWhisper', () => {
    it('bricht bei reiner Stille oder zu geringem Pegel sofort ohne Modellabruf ab', async () => {
      const stille = new Float32Array(16000)
      stille.fill(0.00001)

      const ergebnis = await transkribiereAudioMitWhisper(stille)
      expect(ergebnis).toBe('')
    })

    it('liefert bei leerem Audio-Buffer einen leeren String zurück', async () => {
      const leer = new Float32Array(0)
      const ergebnis = await transkribiereAudioMitWhisper(leer)
      expect(ergebnis).toBe('')
    })
  })
})
