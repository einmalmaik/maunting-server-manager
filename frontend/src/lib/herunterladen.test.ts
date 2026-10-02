import { afterEach, describe, expect, it, vi } from 'vitest'

import { blobHerunterladen } from './herunterladen'

describe('blobHerunterladen', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('klickt einen Anker im Dokument und widerruft die Adresse erst nach einer Minute', () => {
    vi.useFakeTimers()
    URL.createObjectURL = vi.fn(() => 'blob:probe')
    URL.revokeObjectURL = vi.fn()
    const geklickt: { name: string; imDokument: boolean }[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      geklickt.push({ name: this.download, imDokument: document.body.contains(this) })
    })

    blobHerunterladen(new Blob(['x']), 'probe.txt')

    expect(geklickt).toEqual([{ name: 'probe.txt', imDokument: true }])
    expect(document.querySelector('a[download]')).toBeNull()
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(60_000)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:probe')
  })
})
