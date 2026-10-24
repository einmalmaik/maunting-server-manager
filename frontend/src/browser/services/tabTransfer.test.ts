import { describe, expect, it, vi } from 'vitest'
import {
  entpackeTabTransfer,
  FENSTER_ID,
  istTabUebernommen,
  lauscheTabTransfer,
  lauscheTabZiehNachrichten,
  packeTabTransfer,
  sendeTabUebernommen,
  sendeTabZiehNachricht,
  TAB_MIME_TYP,
  type TabTransferDaten,
} from './tabTransfer'

describe('tabTransfer Service', () => {
  it('packt und entpackt TabTransferDaten korrekt', () => {
    const daten: TabTransferDaten = {
      ursprungFensterId: FENSTER_ID,
      tabId: 'tab-42',
      url: 'https://github.com/',
      titel: 'GitHub',
      favicon: 'https://github.com/favicon.ico',
      privat: false,
    }

    const json = packeTabTransfer(daten)
    const dt = {
      getData(type: string) {
        if (type === TAB_MIME_TYP) return json
        return ''
      },
    } as unknown as DataTransfer

    const entpackt = entpackeTabTransfer(dt)
    expect(entpackt).toEqual(daten)
  })

  it('fällt bei externem Text auf URL-Fallback zurück', () => {
    const dt = {
      getData(type: string) {
        if (type === 'text/plain') return 'https://news.ycombinator.com/'
        return ''
      },
    } as unknown as DataTransfer

    const entpackt = entpackeTabTransfer(dt)
    expect(entpackt).toMatchObject({
      ursprungFensterId: 'extern',
      tabId: 'extern',
      url: 'https://news.ycombinator.com/',
    })
  })

  it('nimmt fremde Text-Drops nur als Webseite oder eigene Seite an', () => {
    const mitText = (text: string) =>
      ({
        getData(type: string) {
          return type === 'text/plain' ? text : ''
        },
      }) as unknown as DataTransfer

    for (const fremd of ['javascript:alert(1)', 'file:///C:/Windows/win.ini', 'data:text/html,x', 'ftp://example.org/', 'mailto:a@b']) {
      expect(entpackeTabTransfer(mitText(fremd))).toBeNull()
    }
    expect(entpackeTabTransfer(mitText('pontis://einstellungen'))?.url).toBe('pontis://einstellungen')
    expect(entpackeTabTransfer(mitText('msb://einstellungen'))?.url).toBe('msb://einstellungen')
  })

  it('meldet und erkennt übernommene Tabs über localStorage und BroadcastChannel', () => {
    const tabId = 'tab-test-101'
    expect(istTabUebernommen(tabId)).toBe(false)

    let empfangen: { ursprungFensterId: string; tabId: string } | null = null
    const abmelden = lauscheTabTransfer((info) => {
      empfangen = info
    })

    sendeTabUebernommen(FENSTER_ID, tabId)

    expect(istTabUebernommen(tabId)).toBe(true)

    abmelden()
  })

  it('überträgt Live-Zieh-Nachrichten zwischen Fenstern', async () => {
    let empfangeneNachricht: unknown = null
    const abmelden = lauscheTabZiehNachrichten((msg: unknown) => {
      empfangeneNachricht = msg
    })

    const testMsg = {
      art: 'ziehen',
      ursprungFensterId: 'anderes-fenster-456',
      tabId: 'tab-999',
      url: 'https://youtube.com',
      titel: 'YouTube',
      favicon: null,
      privat: false,
      screenX: 400,
      screenY: 20,
    }

    sendeTabZiehNachricht(testMsg)
    await new Promise((r) => setTimeout(r, 20))

    expect(empfangeneNachricht).toEqual(testMsg)
    abmelden()
  })
})
