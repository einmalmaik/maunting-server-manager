import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))

const { useVerlaufStore, vorschlaege } = await import('./verlaufStore')
const { ADRESSE_MAX } = await import('./ablage')

describe('Verlauf', () => {
  beforeEach(() => {
    vi.useRealTimers()
    useVerlaufStore.setState({ verlauf: [], lesezeichen: [] })
  })

  it('fasst Besuche derselben Adresse binnen einer Minute zusammen', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const v = useVerlaufStore.getState()
    v.besucht('https://a.example/', 'A')
    vi.setSystemTime(1_030_000)
    v.besucht('https://a.example/', '')
    vi.setSystemTime(1_200_000)
    v.besucht('https://a.example/', 'A neu')
    expect(useVerlaufStore.getState().verlauf.map((e) => [e.titel, e.zeit])).toEqual([
      ['A neu', 1_200_000],
      ['A', 1_030_000],
    ])
  })

  it('nimmt keine überlange Adresse auf', () => {
    useVerlaufStore.getState().besucht(`https://a.example/${'x'.repeat(ADRESSE_MAX)}`, 'lang')
    expect(useVerlaufStore.getState().verlauf).toEqual([])
  })

  it('trägt einen Titel nur beim neuesten Eintrag nach', () => {
    const v = useVerlaufStore.getState()
    v.besucht('https://a.example/', '')
    v.besucht('https://b.example/', '')
    v.titelNachtragen('https://a.example/', 'A')
    v.titelNachtragen('https://b.example/', 'B')
    expect(useVerlaufStore.getState().verlauf.map((e) => e.titel)).toEqual(['B', ''])
  })

  it('schaltet Lesezeichen um und verschiebt sie in den Grenzen der Liste', () => {
    const v = useVerlaufStore.getState()
    v.lesezeichenUmschalten('https://a.example/', 'A')
    v.lesezeichenUmschalten('https://b.example/', '')
    v.lesezeichenUmschalten('https://c.example/', 'C')
    v.lesezeichenVerschieben(0, 99)
    expect(useVerlaufStore.getState().lesezeichen.map((e) => e.titel)).toEqual(['https://b.example/', 'A', 'C'])
    v.lesezeichenUmschalten('https://a.example/', 'A')
    expect(useVerlaufStore.getState().lesezeichen.map((e) => e.url)).toEqual(['https://b.example/', 'https://c.example/'])
  })

  it('schlägt Lesezeichen vor dem Verlauf vor, jede Adresse einmal', () => {
    const e = (url: string, titel: string) => ({ url, titel, zeit: 0 })
    const treffer = vorschlaege('beispiel', [e('https://beispiel.example/', 'V'), e('https://x.example/', 'Beispiel')], [e('https://beispiel.example/', 'L')])
    expect(treffer.map((t) => t.titel)).toEqual(['L', 'Beispiel'])
    expect(vorschlaege('  ', [e('https://a.example/', 'a')], [])).toEqual([])
  })
})
