import { describe, expect, it, vi } from 'vitest'
import {
  entpackeTabTransfer,
  FENSTER_ID,
  istTabUebernommen,
  lauscheTabTransfer,
  packeTabTransfer,
  sendeTabUebernommen,
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
})
