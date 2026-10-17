import { describe, it, expect } from 'vitest'
import { istTrackingParameter, bereinigeUrl } from './linkReiniger'

describe('linkReiniger', () => {
  describe('istTrackingParameter', () => {
    it('erkennt utm-Parameter jeglicher Art', () => {
      expect(istTrackingParameter('utm_source')).toBe(true)
      expect(istTrackingParameter('utm_medium')).toBe(true)
      expect(istTrackingParameter('utm_campaign')).toBe(true)
      expect(istTrackingParameter('UTM_TERM')).toBe(true)
      expect(istTrackingParameter('utm_content')).toBe(true)
    })

    it('erkennt Klick-IDs gängiger Netzwerke', () => {
      expect(istTrackingParameter('fbclid')).toBe(true)
      expect(istTrackingParameter('gclid')).toBe(true)
      expect(istTrackingParameter('msclkid')).toBe(true)
      expect(istTrackingParameter('twclid')).toBe(true)
      expect(istTrackingParameter('ttclid')).toBe(true)
      expect(istTrackingParameter('igshid')).toBe(true)
    })

    it('erkennt YouTube si & feature Token', () => {
      expect(istTrackingParameter('si')).toBe(true)
      expect(istTrackingParameter('feature')).toBe(true)
    })

    it('erkennt Amazon Klick- und Empfehlungsdaten', () => {
      expect(istTrackingParameter('pd_rd_r')).toBe(true)
      expect(istTrackingParameter('pd_rd_w')).toBe(true)
      expect(istTrackingParameter('pf_rd_p')).toBe(true)
    })

    it('behaelt funktionale Parameter unangetastet', () => {
      expect(istTrackingParameter('v')).toBe(false)
      expect(istTrackingParameter('t')).toBe(false)
      expect(istTrackingParameter('q')).toBe(false)
      expect(istTrackingParameter('page')).toBe(false)
      expect(istTrackingParameter('tag')).toBe(false) // Amazon Affiliate Tag
      expect(istTrackingParameter('id')).toBe(false)
    })
  })

  describe('bereinigeUrl', () => {
    it('entfernt si-Tracking von YouTube-Links und behaelt Zeitstempel', () => {
      const res = bereinigeUrl('https://youtu.be/dQw4w9WgXcQ?si=abc123xyz&t=42')
      expect(res.bereinigt).toBe(true)
      expect(res.url).toBe('https://youtu.be/dQw4w9WgXcQ?t=42')
    })

    it('entfernt vollstaendig utm-Parameter ohne restliches Fragezeichen', () => {
      const res = bereinigeUrl('https://example.com/artikel?utm_source=twitter&utm_medium=social')
      expect(res.bereinigt).toBe(true)
      expect(res.url).toBe('https://example.com/artikel')
    })

    it('behaelt Fragment-Identifier (#kapitel) bei', () => {
      const res = bereinigeUrl('https://docs.rs/tauri/latest?utm_source=feed#features')
      expect(res.bereinigt).toBe(true)
      expect(res.url).toBe('https://docs.rs/tauri/latest#features')
    })

    it('entfernt Klick-IDs aus Suchergebnissen, behaelt die Suchanfrage', () => {
      const res = bereinigeUrl('https://www.google.com/search?q=rust+lang&gclid=12345&ved=abcdef')
      expect(res.bereinigt).toBe(true)
      expect(res.url).toBe('https://www.google.com/search?q=rust+lang')
    })

    it('behaelt Amazon-Partner-Tags (tag=...), entfernt aber pd_rd-Tracking', () => {
      const res = bereinigeUrl('https://www.amazon.de/dp/B08N5WRWNW?tag=meintag-21&pd_rd_w=123&pd_rd_r=456')
      expect(res.bereinigt).toBe(true)
      expect(res.url).toBe('https://www.amazon.de/dp/B08N5WRWNW?tag=meintag-21')
    })

    it('laesst saubere URLs unveraendert (bereinigt: false)', () => {
      const cleanUrl = 'https://github.com/tauri-apps/tauri?tab=readme-ov-file'
      const res = bereinigeUrl(cleanUrl)
      expect(res.bereinigt).toBe(false)
      expect(res.url).toBe(cleanUrl)
    })

    it('laesst Nicht-URLs oder ungueltige Strings unveraendert', () => {
      expect(bereinigeUrl('ein suchbegriff mit utm_source').bereinigt).toBe(false)
      expect(bereinigeUrl('').bereinigt).toBe(false)
      expect(bereinigeUrl('ftp://example.com?si=123').bereinigt).toBe(false)
    })
  })
})
