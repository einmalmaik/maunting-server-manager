import { beforeEach, describe, expect, it } from 'vitest'

const { useBildStore } = await import('./bildStore')

const PNG = 'data:image/png;base64,iVBORw0KGgo='

describe('Bildspeicher der Lesezeichen', () => {
  beforeEach(() => {
    localStorage.clear()
    useBildStore.setState({ bilder: {} })
  })

  it('ergänzt nur gültige Herkünfte und überschreibt nichts', () => {
    expect(
      useBildStore.getState().ergaenzen({
        'https://a.example': PNG,
        'https://b.example': 'data:image/svg+xml;base64,PHN2Zz4=',
        'javascript:alert(1)': PNG,
      }),
    ).toBe(1)
    expect(
      useBildStore.getState().ergaenzen({
        'https://a.example': 'data:image/png;base64,AAAA',
        'https://c.example': PNG,
      }),
    ).toBe(1)
    expect(useBildStore.getState().bilder).toEqual({
      'https://a.example': PNG,
      'https://c.example': PNG,
    })
  })

  it('behält nur die Herkünfte der übergebenen Adressen', () => {
    useBildStore.setState({
      bilder: { 'https://a.example': PNG, 'https://b.example': PNG },
    })
    useBildStore.getState().behalten(['https://a.example/seite', 'javascript:alert(1)'])
    expect(useBildStore.getState().bilder).toEqual({ 'https://a.example': PNG })
    useBildStore.getState().behalten([])
    expect(useBildStore.getState().bilder).toEqual({})
  })

  it('verwirft beschädigte Werte beim Laden aus dem localStorage', async () => {
    localStorage.setItem(
      'msb:bilder',
      JSON.stringify({
        state: {
          bilder: {
            'https://a.example': PNG,
            'javascript:alert(1)': PNG,
            'https://b.example': 'data:text/html;base64,AAAA',
          },
        },
        version: 1,
      }),
    )
    await useBildStore.persist.rehydrate()
    expect(useBildStore.getState().bilder).toEqual({ 'https://a.example': PNG })
  })
})
