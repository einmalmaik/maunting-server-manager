import { afterEach, describe, expect, it, vi } from 'vitest'

import { geraeteArtSetzen } from '@/desktop/geraeteArt'
import { geraeteBezeichnung } from './geraeteBezeichnung'

/**
 * Der Name, unter dem dieses Gerät in der Geräteliste steht.
 *
 * Er entsteht ohne Zutun des Benutzers und landet in der Datenbank, also in der
 * Liste **jedes** Geräts dieses Kontos. Zwei Dinge müssen deshalb stimmen: er
 * sagt, worum es geht („Web-Frontend“, nicht „Unbenanntes Gerät“), und er passt
 * in die 64 Zeichen, die der Server annimmt.
 */

const WIN_CHROME =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
const ANDROID_CHROME =
  'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'
const IPHONE_SAFARI =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
const EDGE = `${WIN_CHROME} Edg/120.0.0.0`

function mitUserAgent(ua: string) {
  vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(ua)
}

function alsApp(art: 'desktop' | 'browser') {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  geraeteArtSetzen(art)
}

afterEach(() => {
  vi.restoreAllMocks()
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
  geraeteArtSetzen('desktop')
})

describe('geraeteBezeichnung', () => {
  it('nennt das Web-Frontend beim Namen, samt Browser und System', () => {
    mitUserAgent(WIN_CHROME)
    expect(geraeteBezeichnung()).toBe('Web-Frontend · Chrome/Windows')
  })

  it('erkennt Android und iOS', () => {
    mitUserAgent(ANDROID_CHROME)
    expect(geraeteBezeichnung()).toBe('Web-Frontend · Chrome/Android')

    mitUserAgent(IPHONE_SAFARI)
    expect(geraeteBezeichnung()).toBe('Web-Frontend · Safari/iOS')
  })

  it('nimmt den genaueren Browser, wenn zwei in der Kennung stehen', () => {
    // Edge und Opera tragen „Chrome“ mit; ohne die Reihenfolge stünde überall
    // „Chrome“.
    mitUserAgent(EDGE)
    expect(geraeteBezeichnung()).toBe('Web-Frontend · Edge/Windows')
  })

  it('bleibt bei einer unbrauchbaren Kennung beim Produktnamen', () => {
    mitUserAgent('')
    expect(geraeteBezeichnung()).toBe('Web-Frontend')
  })

  it('unterscheidet MSS-App und Pondys Browser', () => {
    mitUserAgent(WIN_CHROME)

    alsApp('desktop')
    expect(geraeteBezeichnung()).toBe('MSS-App · Windows')

    alsApp('browser')
    expect(geraeteBezeichnung()).toBe('Pondys Browser · Windows')
  })

  it('bleibt unter der Grenze des Servers', () => {
    mitUserAgent(WIN_CHROME)
    expect(geraeteBezeichnung().length).toBeLessThanOrEqual(64)
  })
})
