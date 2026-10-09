import { describe, expect, it } from 'vitest'

import { leererTab, webviewZeigen } from './tab'

describe('webviewZeigen', () => {
  const seite = { ...leererTab('tab-a'), url: 'https://seite.example/', nativDa: true }

  it('zeigt die Webview nur für eine geladene Seite ohne Fehler', () => {
    expect(webviewZeigen(seite)).toBe(true)
    expect(webviewZeigen({ ...seite, nativDa: false })).toBe(false)
    expect(webviewZeigen({ ...seite, url: '' })).toBe(false)
    expect(webviewZeigen({ ...seite, url: 'msb://einstellungen' })).toBe(false)
    expect(webviewZeigen({ ...seite, abgestuerzt: true })).toBe(false)
    expect(webviewZeigen({ ...seite, fehler: 'gesperrt' })).toBe(false)
  })
})
