import { beforeEach, describe, expect, it } from 'vitest'

import { useDownloadsStore } from './downloadsStore'

const melden = (nr: number, stand: 'start' | 'pruefung' | 'fertig' | 'fehler', datei: string | null = null) =>
  useDownloadsStore.getState().ereignis({ art: 'download', id: 'tab-a', nr, url: `https://a.example/${nr}`, datei, stand })

describe('Downloads', () => {
  beforeEach(() => useDownloadsStore.setState({ downloads: [], neu: 0 }))

  it('führt jeden Schritt unter derselben Nummer und zählt nur neue', () => {
    melden(1, 'start')
    melden(1, 'pruefung')
    melden(1, 'fertig', 'D:/Downloads/a.pdf')
    melden(2, 'start')
    const { downloads, neu } = useDownloadsStore.getState()
    expect(neu).toBe(2)
    expect(downloads.map((d) => [d.nr, d.stand, d.datei])).toEqual([
      [2, 'start', null],
      [1, 'fertig', 'D:/Downloads/a.pdf'],
    ])
  })

  it('behält die Datei, wenn ein späterer Schritt keine nennt', () => {
    melden(1, 'fertig', 'D:/Downloads/a.pdf')
    melden(1, 'fehler')
    expect(useDownloadsStore.getState().downloads[0].datei).toBe('D:/Downloads/a.pdf')
  })

  it('leert nur, was nicht mehr läuft', () => {
    melden(1, 'fertig', 'a')
    melden(2, 'start')
    melden(3, 'pruefung')
    melden(4, 'fehler')
    useDownloadsStore.getState().leeren()
    expect(useDownloadsStore.getState().downloads.map((d) => d.nr)).toEqual([3, 2])
  })
})
