import { describe, expect, it } from 'vitest'

import { interneAdresse, interneSeite, istWebseite } from './intern'

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
})
