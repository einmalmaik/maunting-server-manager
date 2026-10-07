import { describe, it, expect } from 'vitest'
import { istUrl, baueZielUrl, SEARCH_ENGINES } from './searchEngines'

describe('searchEngines', () => {
  describe('istUrl', () => {
    it('erkennt vollständige URLs mit Schema', () => {
      expect(istUrl('https://youtube.com')).toBe(true)
      expect(istUrl('http://heise.de/news')).toBe(true)
      expect(istUrl('about:blank')).toBe(true)
    })

    it('erkennt Domains ohne Schema', () => {
      expect(istUrl('youtube.com')).toBe(true)
      expect(istUrl('github.com/repo')).toBe(true)
      expect(istUrl('localhost:3000')).toBe(true)
      expect(istUrl('192.168.1.1:8080')).toBe(true)
    })

    it('erkennt Suchbegriffe mit Leerzeichen als Nicht-URLs', () => {
      expect(istUrl('was ist die hauptstadt von deutschland')).toBe(false)
      expect(istUrl('tauri v2 browser tutorial')).toBe(false)
      expect(istUrl('msm panel')).toBe(false)
    })
  })

  describe('baueZielUrl', () => {
    it('erzeugt Suchanfragen für Standard-Suchmaschinen', () => {
      const query = 'hauptstadt deutschland'
      expect(baueZielUrl(query, 'google')).toBe(
        'https://www.google.com/search?q=hauptstadt%20deutschland'
      )
      expect(baueZielUrl(query, 'ecosia')).toBe(
        'https://www.ecosia.org/search?q=hauptstadt%20deutschland'
      )
      expect(baueZielUrl(query, 'duckduckgo')).toBe(
        'https://duckduckgo.com/?q=hauptstadt%20deutschland'
      )
      expect(baueZielUrl(query, 'brave')).toBe(
        'https://search.brave.com/search?q=hauptstadt%20deutschland'
      )
    })

    it('leitet URLs direkt weiter und ergänzt https falls nötig', () => {
      expect(baueZielUrl('youtube.com')).toBe('https://youtube.com')
      expect(baueZielUrl('https://github.com')).toBe('https://github.com')
      expect(baueZielUrl('about:blank')).toBe('about:blank')
    })

    it('unterstützt selbstgehostetes SearXNG', () => {
      expect(baueZielUrl('privatsphäre test', 'searxng', 'https://search.msm.lan')).toBe(
        'https://search.msm.lan/search?q=privatsph%C3%A4re%20test'
      )
    })
  })
})
