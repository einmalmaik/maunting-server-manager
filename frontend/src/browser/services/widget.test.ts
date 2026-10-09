/**
 * Was das Such-Widget anstößt, öffnet der Browser in einem neuen Tab: das
 * Gesagte als Suche, ein Foto bei der Bildsuche der gewählten Suchmaschine,
 * einen Link aus einer anderen App als Seite.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useToastStore } from '@/stores/toastStore'

import { useEinstellungenStore } from './einstellungenStore'
import { nativ } from './nativ'
import { SUCHMASCHINEN } from './searchEngines'
import { useTabsStore } from './tabsStore'
import { widgetAusfuehren } from './widget'

describe('Such-Widget', () => {
  const adresszeile = vi.fn()
  const bildsuche = vi.spyOn(nativ, 'bildsuche')
  const widgetStand = vi.spyOn(nativ, 'widgetStand')

  beforeEach(() => {
    adresszeile.mockClear()
    bildsuche.mockClear().mockResolvedValue(null)
    widgetStand.mockClear().mockResolvedValue(null)
    useTabsStore.setState({ tabs: [], aktivId: '' })
    useEinstellungenStore.setState({ suchmaschine: 'duckduckgo' })
  })

  it('sucht das Gesagte in einem neuen Tab, eine genannte Adresse öffnet es', () => {
    widgetAusfuehren({ art: 'text', text: 'wetter berlin' }, adresszeile)
    widgetAusfuehren({ art: 'text', text: 'example.com' }, adresszeile)
    expect(useTabsStore.getState().tabs.map((t) => t.url)).toEqual([
      'https://duckduckgo.com/?q=wetter%20berlin',
      // HTTPS versucht der Browser selbst, mit Rückfall (`tabs/https.rs`).
      'http://example.com',
    ])
  })

  it('öffnet einen Link aus einer anderen App in einem neuen Tab, nur Webseiten', () => {
    widgetAusfuehren({ art: 'link', url: 'https://example.com/artikel?id=3' }, adresszeile)
    widgetAusfuehren({ art: 'link', url: 'javascript:alert(1)' }, adresszeile)
    widgetAusfuehren({ art: 'link', url: 'file:///sdcard/Download/a.html' }, adresszeile)
    expect(useTabsStore.getState().tabs.map((t) => t.url)).toEqual(['https://example.com/artikel?id=3'])
    expect(adresszeile).not.toHaveBeenCalled()
  })

  it('öffnet für die Suche einen leeren Tab mit der Adresszeile', () => {
    widgetAusfuehren({ art: 'suche' }, adresszeile)
    expect(useTabsStore.getState().tabs).toHaveLength(1)
    expect(adresszeile).toHaveBeenCalled()
  })

  it('schickt ein Foto an die Bildsuche der gewählten Suchmaschine', () => {
    useEinstellungenStore.setState({ suchmaschine: 'bing' })
    widgetAusfuehren({ art: 'bild' }, adresszeile)
    const tab = useTabsStore.getState().tabs[0]
    expect(bildsuche).toHaveBeenCalledWith(tab.id, false, {
      url: 'https://www.bing.com/images/search?view=detailv2&iss=sbiupload',
      feld: 'imageBin',
      base64: true,
    })
  })

  it('sagt es, wenn die Suchmaschine inzwischen keine Bilder sucht, und lässt das Foto nicht liegen', () => {
    widgetAusfuehren({ art: 'bild' }, adresszeile)
    expect(bildsuche).not.toHaveBeenCalled()
    // `widgetStand(false)` blendet die Kamera aus und löscht das wartende Foto.
    expect(widgetStand).toHaveBeenCalledWith(false)
    expect(useTabsStore.getState().tabs).toHaveLength(0)
    expect(useToastStore.getState().toasts.at(-1)?.message).toMatch(/sucht keine Bilder/)
  })

  it('zeigt die Kamera nur für Google und Bing', () => {
    expect(SUCHMASCHINEN.filter((s) => s.bild).map((s) => s.id)).toEqual(['google', 'bing'])
    for (const s of SUCHMASCHINEN) if (s.bild) expect(s.bild.url).toMatch(/^https:\/\//)
  })
})
