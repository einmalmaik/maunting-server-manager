import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const postfach = vi.hoisted(() => ({
  verarbeitePostfach: vi.fn(async () => ({ verarbeitet: 0, quittiert: 0 })),
}))
const ratchet = vi.hoisted(() => ({
  sendeAusstehendeZustellungen: vi.fn(async () => undefined),
}))
const krypto = vi.hoisted(() => ({
  deriveBlindMailboxId: vi.fn(async () => 'mb-2'),
  deriveGroupBlindMailboxId: vi.fn(async (id: number) => `mb-g${id}`),
  getCachedBlindMailboxId: vi.fn(() => null),
  getCachedGroupBlindMailboxId: vi.fn(() => null),
}))
const gruppen = vi.hoisted(() => ({
  ladeGruppenzustand: vi.fn(async () => ({ art: 'keine' })),
}))
const rollen = vi.hoisted(() => ({
  rechteAusGruppe: vi.fn(() => new Set<string>()),
}))

vi.mock('@/services/gespraech/postfach', () => postfach)
vi.mock('@/services/ratchetSitzung', () => ratchet)
vi.mock('@/services/e2eeCrypto', () => krypto)
vi.mock('@/services/gruppenKonfig', () => gruppen)
vi.mock('@/services/gruppenRollen', () => rollen)

import { usePostfachWache, type PostfachWacheOptionen } from './usePostfachWache'

const identitaet = {
  state: 'ready' as const,
  decryptionKeys: ['k'],
}

function optionen(zusatz: Partial<PostfachWacheOptionen> = {}): PostfachWacheOptionen {
  return {
    eigeneId: 1,
    freunde: [{ id: 2, user_id: 2 }],
    gruppen: [],
    identitaet: identitaet as never,
    gesperrt: false,
    t: (s: string) => s,
    markAsRead: () => {},
    setVerfallSekunden: () => {},
    ...zusatz,
  }
}

/** Ein paar Mikrotasks, damit der angestoßene Lauf bis zum Aufruf kommt. */
async function durchatmen(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  postfach.verarbeitePostfach.mockResolvedValue({ verarbeitet: 0, quittiert: 0 })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('usePostfachWache', () => {
  it('läuft sofort und übergibt die Gespräche samt Rückrufen', async () => {
    const melden = vi.fn()
    const blockiert = vi.fn(() => false)
    renderHook(() =>
      usePostfachWache(
        optionen({
          beiNeuerNachricht: melden,
          istBlockiert: blockiert,
        }),
      ),
    )
    await durchatmen()

    expect(postfach.verarbeitePostfach).toHaveBeenCalledTimes(1)
    const lauf = postfach.verarbeitePostfach.mock.calls[0][0] as Record<string, unknown>
    expect(lauf.eigeneId).toBe(1)
    expect(lauf.gespraeche).toEqual([
      expect.objectContaining({ mailbox: 'mb-2', drKontext: { eigeneId: 1, peerId: 2 } }),
    ])
    expect(lauf.beiNeuerNachricht).toBe(melden)
    expect(lauf.istBlockiert).toBe(blockiert)
  })

  it('läuft nicht, solange der Messenger gesperrt ist', async () => {
    renderHook(() => usePostfachWache(optionen({ gesperrt: true })))
    await durchatmen()

    expect(postfach.verarbeitePostfach).not.toHaveBeenCalled()
  })

  it('stößt beim Sync-Ereignis sofort einen weiteren Lauf an', async () => {
    renderHook(() => usePostfachWache(optionen()))
    await durchatmen()
    expect(postfach.verarbeitePostfach).toHaveBeenCalledTimes(1)

    window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: { type: 'e2ee_blind_message' } }))
    await act(async () => {
      await new Promise((fertig) => setTimeout(fertig, 300))
    })

    expect(postfach.verarbeitePostfach).toHaveBeenCalledTimes(2)
  })

  it('lässt während eines Laufs keinen zweiten zu', async () => {
    let fertig: (wert: { verarbeitet: number; quittiert: number }) => void = () => {}
    postfach.verarbeitePostfach.mockReturnValue(
      new Promise((aufloesen) => {
        fertig = aufloesen as never
      }),
    )

    renderHook(() => usePostfachWache(optionen()))
    await durchatmen()
    expect(postfach.verarbeitePostfach).toHaveBeenCalledTimes(1)

    window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: { type: 'e2ee_blind_message' } }))
    await act(async () => {
      await new Promise((auf) => setTimeout(auf, 300))
    })

    expect(postfach.verarbeitePostfach).toHaveBeenCalledTimes(1)

    await act(async () => {
      fertig({ verarbeitet: 0, quittiert: 0 })
    })
  })

  it('nimmt das nächste Sync-Ereignis nicht mehr an, wenn die App zu ist', async () => {
    const { unmount } = renderHook(() => usePostfachWache(optionen()))
    await durchatmen()
    postfach.verarbeitePostfach.mockClear()

    unmount()
    window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: { type: 'e2ee_blind_message' } }))
    await act(async () => {
      await new Promise((auf) => setTimeout(auf, 300))
    })

    expect(postfach.verarbeitePostfach).not.toHaveBeenCalled()
  })
})
