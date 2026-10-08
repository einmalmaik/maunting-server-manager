import { afterEach, describe, expect, it } from 'vitest'

import { gestenleisteUebernehmen } from './gestenleiste'

type Fenster = Window & {
  MsmRand?: { unten(): number; oben?(): number; links?(): number; rechts?(): number }
  __msmGestenleiste?: (px: number) => void
  __msmRand?: (seite: string, px: number) => void
}

describe('gestenleisteUebernehmen', () => {
  afterEach(() => {
    delete (window as Fenster).MsmRand
    delete (window as Fenster).__msmGestenleiste
    delete (window as Fenster).__msmRand
    for (const v of ['--msm-gestenleiste', '--msm-statusleiste', '--msm-rand-links', '--msm-rand-rechts']) document.documentElement.style.removeProperty(v)
  })

  it('übernimmt die Höhe aus der App beim Start und bei jeder Änderung', () => {
    // Im Emulator (WebView 124) meldete env(safe-area-inset-bottom) 0, die Fußleisten lagen unter der Gestenleiste.
    ;(window as Fenster).MsmRand = { unten: () => 24 }
    gestenleisteUebernehmen()
    expect(document.documentElement.style.getPropertyValue('--msm-gestenleiste')).toBe('24px')

    ;(window as Fenster).__msmGestenleiste!(48)
    expect(document.documentElement.style.getPropertyValue('--msm-gestenleiste')).toBe('48px')
  })

  it('übernimmt Statusleiste und seitliche Ränder, die der WebView im Querformat verschweigt', () => {
    // Im Emulator (WebView 124, Querformat) meldete env(safe-area-inset-top) 0 unter der Statusleiste.
    ;(window as Fenster).MsmRand = { unten: () => 24, oben: () => 24, links: () => 52, rechts: () => 0 }
    gestenleisteUebernehmen()
    const stil = document.documentElement.style
    expect([stil.getPropertyValue('--msm-statusleiste'), stil.getPropertyValue('--msm-rand-links'), stil.getPropertyValue('--msm-rand-rechts')]).toEqual(['24px', '52px', '0px'])

    ;(window as Fenster).__msmRand!('rechts', 48)
    ;(window as Fenster).__msmRand!('woanders', 9)
    expect(stil.getPropertyValue('--msm-rand-rechts')).toBe('48px')
    expect(stil.getPropertyValue('--msm-woanders')).toBe('')
  })

  it('lässt im Browser und am Rechner alles beim env()-Wert', () => {
    gestenleisteUebernehmen()
    expect(document.documentElement.style.getPropertyValue('--msm-gestenleiste')).toBe('')
    ;(window as Fenster).__msmGestenleiste!(Number.NaN)
    expect(document.documentElement.style.getPropertyValue('--msm-gestenleiste')).toBe('')
  })

  it('kein Bauteil liest den unteren Rand noch direkt aus env()', () => {
    // Sonst fehlt dort die Gestenleiste alter WebViews wieder.
    const quellen = import.meta.glob(['/src/**/*.tsx', '!/src/**/*.test.tsx'], { query: '?raw', import: 'default', eager: true }) as Record<string, string>
    const treffer = Object.entries(quellen).filter(([, text]) => text.includes('env(safe-area-inset-bottom')).map(([pfad]) => pfad)
    expect(treffer).toEqual([])
  })
})
