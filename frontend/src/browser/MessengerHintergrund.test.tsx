import { render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const wache = vi.hoisted(() => ({ usePostfachWache: vi.fn() }))
const identitaet = vi.hoisted(() => ({
  resolveIdentity: vi.fn(async () => ({ state: 'ready', decryptionKeys: ['k'] })),
}))
const social = vi.hoisted(() => ({
  getFriends: vi.fn(async () => [{ id: 2, user_id: 2, username: 'Beta' }]),
  getGroups: vi.fn(async () => []),
}))
const melden = vi.hoisted(() => ({ meldeChat: vi.fn() }))

vi.mock('@/hooks/usePostfachWache', () => wache)
vi.mock('@/services/e2eeIdentity', () => ({
  IDENTITY_LOADING: { state: 'loading' },
  ...identitaet,
}))
vi.mock('@/api/social', () => social)
vi.mock('@/services/benachrichtigung/chatMeldung', () => melden)

import { useAuthStore } from '@/stores/authStore'
import { useMessengerSperre } from '@/services/messengerSperre'

import { MessengerHintergrund } from './MessengerHintergrund'

beforeEach(() => {
  vi.clearAllMocks()
  useAuthStore.setState({
    isAuthenticated: true,
    user: { id: 1, username: 'alpha' } as never,
  })
  useMessengerSperre.setState({ eingerichtet: false, entsperrt: true })
})

describe('MessengerHintergrund', () => {
  it('lädt Identität und Kontakte und startet die Wache', async () => {
    render(<MessengerHintergrund />)

    await waitFor(() => expect(wache.usePostfachWache).toHaveBeenCalled())
    expect(identitaet.resolveIdentity).toHaveBeenCalledWith(1)
    expect(social.getFriends).toHaveBeenCalled()
    expect(social.getGroups).toHaveBeenCalled()

    const letzte = wache.usePostfachWache.mock.calls.at(-1)![0] as Record<string, unknown>
    expect(letzte).toMatchObject({
      eigeneId: 1,
      gesperrt: false,
      identitaet: { state: 'ready' },
    })
    expect(letzte.freunde).toEqual([expect.objectContaining({ user_id: 2 })])
  })

  it('läuft nicht, solange der Messenger gesperrt ist', async () => {
    useMessengerSperre.setState({ eingerichtet: true, entsperrt: false })

    render(<MessengerHintergrund />)

    await waitFor(() =>
      expect(wache.usePostfachWache).toHaveBeenCalledWith(
        expect.objectContaining({ gesperrt: true, identitaet: { state: 'loading' } }),
      ),
    )
    expect(identitaet.resolveIdentity).not.toHaveBeenCalled()
    expect(social.getFriends).not.toHaveBeenCalled()
  })

  it('meldet neue Postfachzeilen über die gemeinsame Meldestelle', async () => {
    render(<MessengerHintergrund />)
    await waitFor(() => expect(wache.usePostfachWache).toHaveBeenCalled())

    const letzte = wache.usePostfachWache.mock.calls.at(-1)![0] as {
      beiNeuerNachricht: (
        gespraech: { mailbox: string; group: null },
        nachrichten: readonly { clientUuid?: string; senderId?: number }[],
      ) => void
    }
    letzte.beiNeuerNachricht(
      { mailbox: 'mb-2', group: null },
      [{ clientUuid: 'u-1', senderId: 2 }],
    )

    expect(melden.meldeChat).toHaveBeenCalledWith(
      expect.objectContaining({
        mailbox: 'mb-2',
        clientUuid: 'u-1',
        senderId: 2,
        istGruppe: false,
      }),
    )
  })
})
