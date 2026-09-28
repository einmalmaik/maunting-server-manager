/**
 * Die Ansicht über alle Chats, vor allem die Frage „an mich".
 *
 * Ob aus `@everyone` eine Erwähnung wird, entscheidet das Recht des Absenders
 * in genau der Gruppe, zu der die Mailbox gehört. Das Verzeichnis dazu liest
 * der Hook selbst aus dem Store.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { sammleAnMich, sammleMarkierte, sucheUeberall } = vi.hoisted(() => ({
  sammleAnMich: vi.fn(),
  sammleMarkierte: vi.fn(),
  sucheUeberall: vi.fn(),
}))
vi.mock('@/services/verlaufSuche', () => ({ sammleAnMich, sammleMarkierte, sucheUeberall }))

import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import { useUeberallAnsicht } from './useUeberallAnsicht'

const ICH = 7
const gruppe = {
  id: 3,
  members: [
    { user_id: 8, can_mention_everyone: true },
    { user_id: 9, can_mention_everyone: false },
  ],
} as never

beforeEach(() => {
  sammleAnMich.mockReset().mockResolvedValue({ chats: [], gesperrt: false })
  sammleMarkierte.mockReset().mockResolvedValue({ chats: [], gesperrt: false })
  sucheUeberall.mockReset().mockResolvedValue({ chats: [], gesperrt: true })
  useMessengerNotificationStore.setState({
    mailboxDirectory: { gmb: { isGroup: true, groupId: 3 } as never },
  })
})

async function gemeint() {
  const { result } = renderHook(() => useUeberallAnsicht(ICH, [gruppe]))
  await act(() => result.current.oeffne('anMich'))
  return sammleAnMich.mock.calls[0][1] as (m: object, mid: string) => boolean
}

describe('useUeberallAnsicht', () => {
  it('zählt eine Antwort auf mich wie eine Erwähnung', async () => {
    const pruefe = await gemeint()
    expect(pruefe({ senderId: 9, antwortAuf: { absenderId: ICH } }, 'dm')).toBe(true)
    expect(pruefe({ senderId: 9, antwortAuf: { absenderId: 5 } }, 'dm')).toBe(false)
    expect(pruefe({ senderId: 9, erwaehnungen: [ICH] }, 'dm')).toBe(true)
  })

  it('lässt @everyone nur gelten, wenn der Absender in dieser Gruppe alle wecken darf', async () => {
    const pruefe = await gemeint()
    expect(pruefe({ senderId: 8, erwaehntAlle: true }, 'gmb')).toBe(true)
    expect(pruefe({ senderId: 9, erwaehntAlle: true }, 'gmb')).toBe(false)
    // Ohne Gruppe im Verzeichnis gibt es kein Recht, also keine Erwähnung.
    expect(pruefe({ senderId: 8, erwaehntAlle: true }, 'unbekannt')).toBe(false)
  })

  it('zeigt „wird durchgesehen" nur, solange die Durchsicht läuft, auch wenn sie scheitert', async () => {
    let fertig!: (v: unknown) => void
    sammleMarkierte.mockReturnValue(new Promise((r) => (fertig = r)))
    const { result } = renderHook(() => useUeberallAnsicht(ICH, []))
    let lauf!: Promise<void>
    act(() => {
      lauf = result.current.oeffne('markiert')
    })
    expect(result.current).toMatchObject({ art: 'markiert', laeuft: true })
    await act(async () => {
      fertig({ chats: [{ blindMailboxId: 'x', treffer: [] }], gesperrt: false })
      await lauf
    })
    expect(result.current.laeuft).toBe(false)
    expect(result.current.chats).toHaveLength(1)

    sucheUeberall.mockRejectedValue(new Error('kaputt'))
    await act(() => result.current.oeffne('suche', 'hallo').catch(() => {}))
    await waitFor(() => expect(result.current.laeuft).toBe(false))
    expect(result.current).toMatchObject({ art: 'suche', frage: 'hallo', chats: [] })

    act(() => result.current.schliesse())
    expect(result.current.art).toBe('aus')
  })
})
