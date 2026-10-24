import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FakeAudioContext, installFakeAudio } from '@/test/fakeAudio'

let audio: ReturnType<typeof installFakeAudio>

beforeEach(() => {
  vi.resetModules()
  audio = installFakeAudio()
})

afterEach(() => audio.restore())

describe('Hinweiston', () => {
  it('verwendet einen Audio-Kontext für mehrere Töne', async () => {
    const { playNotificationChime } = await import('./messengerNotificationStore')

    playNotificationChime()
    playNotificationChime()

    expect(FakeAudioContext.instances).toHaveLength(1)
    expect(FakeAudioContext.instances[0].oszillatoren).toHaveLength(2)
  })

  it('wartet bei gesperrtem Kontext auf resume, bevor es startet', async () => {
    audio.restore()
    audio = installFakeAudio({ gesperrt: true })
    const { playNotificationChime } = await import('./messengerNotificationStore')

    playNotificationChime()
    // Noch nicht geplant: der Oszillator verfiele, solange resume() läuft.
    expect(FakeAudioContext.instances[0].oszillatoren).toHaveLength(0)

    await Promise.resolve()
    await Promise.resolve()

    expect(FakeAudioContext.instances[0].resumeAufrufe).toBe(1)
    expect(FakeAudioContext.instances[0].oszillatoren).toHaveLength(1)
  })

  it('bleibt still, wenn der Browser die Sperre nicht aufhebt', async () => {
    audio.restore()
    audio = installFakeAudio({ gesperrt: true, entsperrenScheitert: true })
    const { playNotificationChime } = await import('./messengerNotificationStore')

    playNotificationChime()
    await Promise.resolve()
    await Promise.resolve()

    expect(FakeAudioContext.instances[0].oszillatoren).toHaveLength(0)
  })

  it('hebt die Sperre beim ersten Nutzergriff auf', async () => {
    audio.restore()
    audio = installFakeAudio({ gesperrt: true })
    const { chimeEntsperren, playNotificationChime } = await import(
      './messengerNotificationStore'
    )

    chimeEntsperren()
    window.dispatchEvent(new Event('pointerdown'))
    await Promise.resolve()

    expect(FakeAudioContext.instances[0].resumeAufrufe).toBe(1)

    playNotificationChime()
    expect(FakeAudioContext.instances[0].oszillatoren).toHaveLength(1)
  })
})
