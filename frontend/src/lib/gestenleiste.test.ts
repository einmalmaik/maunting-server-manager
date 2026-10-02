import { afterEach, describe, expect, it } from 'vitest'

import { gestenleisteUebernehmen } from './gestenleiste'

type Fenster = Window & { MsmRand?: { unten(): number }; __msmGestenleiste?: (px: number) => void }

describe('gestenleisteUebernehmen', () => {
  afterEach(() => {
    delete (window as Fenster).MsmRand
    delete (window as Fenster).__msmGestenleiste
    document.documentElement.style.removeProperty('--msm-gestenleiste')
  })

  it('übernimmt die Höhe aus der App beim Start und bei jeder Änderung', () => {
    // Im Emulator (WebView 124) meldete env(safe-area-inset-bottom) 0, die Fußleisten lagen unter der Gestenleiste.
    ;(window as Fenster).MsmRand = { unten: () => 24 }
    gestenleisteUebernehmen()
    expect(document.documentElement.style.getPropertyValue('--msm-gestenleiste')).toBe('24px')

    ;(window as Fenster).__msmGestenleiste!(48)
    expect(document.documentElement.style.getPropertyValue('--msm-gestenleiste')).toBe('48px')
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
