import { describe, expect, it } from 'vitest'

import { interneAdresse, interneSeite, istWebseite, msmSucheAdresse, msmSucheBegriff } from './intern'

describe('intern', () => {
  it('nennt nur http und https Webseite', () => {
    expect(istWebseite('https://seite.example/')).toBe(true)
    expect(istWebseite('HTTP://seite.example/')).toBe(true)
    // Bis 09.10.2026 hieß es `startsWith('http')`, und `httpx:` galt als Webseite.
    expect(istWebseite('httpx://seite.example/')).toBe(false)
    expect(istWebseite('msb://einstellungen')).toBe(false)
    expect(istWebseite('')).toBe(false)
  })

  it('liest eigene Seiten mit Unterpfad', () => {
    expect(interneSeite(interneAdresse('einstellungen', 'suche'))).toEqual({ seite: 'einstellungen', teil: 'suche' })
    expect(interneSeite('msb://unbekannt')).toBeNull()
    expect(interneSeite('https://einstellungen')).toBeNull()
  })

  it('trägt den Suchbegriff in seiner Schreibweise in der Adresse', () => {
    const adresse = msmSucheAdresse('  C++ & Rust? #1  ')
    expect(interneSeite(adresse)).toEqual({ seite: 'suche', teil: null })
    expect(msmSucheBegriff(adresse)).toBe('C++ & Rust? #1')
    expect(msmSucheBegriff(msmSucheAdresse('Köln WETTER'))).toBe('Köln WETTER')
  })

  it('liest einen Begriff nur aus der Suche', () => {
    expect(msmSucheBegriff('msb://suche')).toBeNull()
    expect(msmSucheBegriff('msb://suche?q=%20%20')).toBeNull()
    expect(msmSucheBegriff('msb://einstellungen?q=x')).toBeNull()
    expect(msmSucheBegriff('https://suche.example/?q=x')).toBeNull()
    expect(msmSucheBegriff('MSB://SUCHE?q=Gro%C3%9F')).toBeNull()
    expect(msmSucheBegriff('msb://Suche?q=Gro%C3%9F')).toBe('Groß')
  })
})
