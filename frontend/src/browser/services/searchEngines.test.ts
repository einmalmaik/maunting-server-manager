import { describe, expect, it } from 'vitest'

import { baueZielUrl, istAdresse, searxngBasis, SUCHMASCHINEN } from './searchEngines'

describe('istAdresse', () => {
  it('erkennt Adressen mit und ohne Schema', () => {
    expect(istAdresse('https://youtube.com')).toBe(true)
    expect(istAdresse('heise.de/news')).toBe(true)
    expect(istAdresse('de.wikipedia.org')).toBe(true)
    expect(istAdresse('localhost:3000')).toBe(true)
    expect(istAdresse('192.168.1.1:8080/admin')).toBe(true)
    expect(istAdresse('münchen.de')).toBe(true)
  })

  it('nimmt Wörter und Sätze als Suche', () => {
    expect(istAdresse('wetter')).toBe(false)
    expect(istAdresse('was ist tauri')).toBe(false)
    expect(istAdresse('c++')).toBe(false)
    expect(istAdresse('javascript:alert(1)')).toBe(false)
    expect(istAdresse('file:///C:/Windows')).toBe(false)
    expect(istAdresse('3.14')).toBe(false)
  })
})

describe('baueZielUrl', () => {
  it('baut für jede Suchmaschine eine Suchadresse', () => {
    for (const s of SUCHMASCHINEN.filter((s) => s.id !== 'searxng')) {
      const ziel = baueZielUrl('hauptstadt deutschland', s.id)!
      expect(new URL(ziel).protocol).toBe('https:')
      expect(ziel).toContain('hauptstadt%20deutschland')
    }
  })

  it('ergänzt das Schema passend', () => {
    expect(baueZielUrl('youtube.com', 'google')).toBe('https://youtube.com')
    expect(baueZielUrl('localhost:8000', 'google')).toBe('http://localhost:8000')
    expect(baueZielUrl('https://github.com', 'google')).toBe('https://github.com')
  })

  it('macht aus einem gefährlichen Schema eine Suche', () => {
    expect(baueZielUrl('javascript:alert(1)', 'duckduckgo')).toBe('https://duckduckgo.com/?q=javascript%3Aalert(1)')
  })

  it('lädt bei leerer Eingabe nichts', () => {
    expect(baueZielUrl('   ', 'google')).toBeNull()
  })

  it('sucht über das eigene SearXNG und fällt ohne Adresse auf DuckDuckGo zurück', () => {
    expect(baueZielUrl('privatsphäre test', 'searxng', 'https://search.example.org/')).toBe(
      'https://search.example.org/search?q=privatsph%C3%A4re%20test',
    )
    expect(baueZielUrl('test', 'searxng', null)).toBe('https://duckduckgo.com/?q=test')
    expect(baueZielUrl('test', 'searxng', 'http://fremd.example')).toBe('https://duckduckgo.com/?q=test')
  })
})

describe('searxngBasis', () => {
  it('nimmt nur sichere Adressen ohne Zugangsdaten', () => {
    expect(searxngBasis('https://s.example/pfad/')).toBe('https://s.example/pfad')
    expect(searxngBasis('http://localhost:8888')).toBe('http://localhost:8888')
    expect(searxngBasis('http://s.example')).toBeNull()
    expect(searxngBasis('https://u:p@s.example')).toBeNull()
    expect(searxngBasis('kein link')).toBeNull()
  })
})
