import { beforeEach, describe, expect, it, vi } from 'vitest'

const claimAchievement = vi.fn()
vi.mock('@/api/social', () => ({ claimAchievement: (id: string) => claimAchievement(id) }))

async function frischerHelfer() {
  vi.resetModules()
  return (await import('@/lib/errungenschaft')).meldeErrungenschaft
}

const warte = () => new Promise((fertig) => setTimeout(fertig, 0))

describe('meldeErrungenschaft', () => {
  beforeEach(() => {
    localStorage.clear()
    claimAchievement.mockReset()
  })

  it('meldet ohne Konto nichts', async () => {
    const melde = await frischerHelfer()
    const { setzeAngemeldetesKonto: setze } = await import('@/lib/angemeldetesKonto')
    setze(null)
    melde('starter_hotkeys')
    await warte()
    expect(claimAchievement).not.toHaveBeenCalled()
  })

  it('meldet je Konto einmal und merkt erst nach der Annahme', async () => {
    const melde = await frischerHelfer()
    const { setzeAngemeldetesKonto: setze } = await import('@/lib/angemeldetesKonto')
    setze(7)
    claimAchievement.mockResolvedValue({ unlocked: true })
    melde('starter_hotkeys')
    melde('starter_hotkeys')
    await warte()
    await warte()
    expect(claimAchievement).toHaveBeenCalledTimes(1)
    expect(JSON.parse(localStorage.getItem('msm:errungenschaften:7') || '[]')).toEqual(['starter_hotkeys'])

    const neu = await frischerHelfer()
    neu('starter_hotkeys')
    await warte()
    expect(claimAchievement).toHaveBeenCalledTimes(1)
  })

  it('wiederholt eine gescheiterte Meldung in der nächsten Sitzung', async () => {
    const melde = await frischerHelfer()
    const { setzeAngemeldetesKonto: setze } = await import('@/lib/angemeldetesKonto')
    setze(8)
    claimAchievement.mockRejectedValueOnce(new Error('offline'))
    melde('social_voice_memo')
    await warte()
    await warte()
    expect(localStorage.getItem('msm:errungenschaften:8')).toBeNull()

    const neu = await frischerHelfer()
    const { setzeAngemeldetesKonto: setzeNeu } = await import('@/lib/angemeldetesKonto')
    setzeNeu(8)
    claimAchievement.mockResolvedValue({ unlocked: true })
    neu('social_voice_memo')
    await warte()
    await warte()
    expect(claimAchievement).toHaveBeenCalledTimes(2)
  })
})
