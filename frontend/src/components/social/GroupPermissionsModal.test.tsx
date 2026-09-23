/**
 * Der Rauswurf im Rechte-Dialog.
 *
 * Seit 09/2026 erneuert der Server beim Rauswurf den Einladungscode — sonst
 * wäre er eine Formalie: ein Klick auf den alten Link, und der Hinausgeworfene
 * ist wieder drin. Der Dialog muss den neuen Code weiterreichen; mit dem alten
 * stünde bis zum nächsten Laden ein toter Link auf dem Schirm.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ChatGroupItem } from '@/api/social'

const { rauswurf } = vi.hoisted(() => ({
  rauswurf: vi.fn(async () => ({ success: true, message: 'ok', invite_code: 'neuer-code' as string | null })),
}))

vi.mock('@/api/social', () => ({
  getGroupMembers: vi.fn(async () => [
    { user_id: 1, username: 'anna', role: 'owner', permissions: null },
    { user_id: 2, username: 'bert', role: 'member', permissions: null },
  ]),
  updateGroupMemberRole: vi.fn(),
  kickGroupMember: rauswurf,
  updateGroupPermissions: vi.fn(),
}))

vi.mock('@/services/e2eeCrypto', () => ({
  deriveGroupBlindMailboxId: vi.fn(async (g: number) => `gruppe-${g}`),
}))

vi.mock('@/services/gruppenKonfig', () => ({
  aendereGruppenzustand: vi.fn(),
  ladeGruppenzustand: vi.fn(async () => ({ art: 'leer' })),
  leererGruppenzustand: vi.fn(() => ({ rollen: [], zuordnung: {} })),
}))

vi.mock('@/stores/confirmStore', () => ({ confirm: vi.fn(async () => true) }))

const { GroupPermissionsModal } = await import('./GroupPermissionsModal')
const i18n = (await import('@/i18n')).default

function gruppe(): ChatGroupItem {
  return {
    id: 7,
    name: 'Testgruppe',
    invite_code: 'alter-code',
    owner_user_id: 1,
    member_count: 2,
    role: 'owner',
    default_permissions: 'send_messages,invite_members',
    created_at: '2026-09-01T00:00:00Z',
    members: [
      { user_id: 1, username: 'anna', role: 'owner' } as any,
      { user_id: 2, username: 'bert', role: 'member' } as any,
    ],
  }
}

describe('GroupPermissionsModal — Rauswurf', () => {
  beforeEach(() => {
    rauswurf.mockClear()
  })

  async function werfeBertHinaus() {
    const onGroupUpdated = vi.fn()
    render(
      <GroupPermissionsModal
        open
        onOpenChange={() => {}}
        group={gruppe()}
        currentUserId={1}
        onGroupUpdated={onGroupUpdated}
      />,
    )
    const knopf = await screen.findByLabelText(
      i18n.t('social.groupRoles.kickAria', { name: 'bert' }),
    )
    fireEvent.click(knopf)
    await waitFor(() => expect(onGroupUpdated).toHaveBeenCalled())
    return onGroupUpdated.mock.calls[0][0] as ChatGroupItem
  }

  it('reicht den neuen Einladungscode weiter und nimmt das Mitglied heraus', async () => {
    const neu = await werfeBertHinaus()

    expect(rauswurf).toHaveBeenCalledWith(7, 2)
    expect(neu.invite_code).toBe('neuer-code')
    expect(neu.members.map((m) => m.user_id)).toEqual([1])
    expect(neu.member_count).toBe(1)
  })

  it('lässt keinen alten Code stehen, wenn der Server keinen herausgibt', async () => {
    // Wer nur hinauswerfen darf, bekommt den neuen Code nicht. Der alte gilt
    // aber auch nicht mehr — ihn weiter anzuzeigen hiesse, einen toten Link
    // anzubieten.
    rauswurf.mockResolvedValueOnce({ success: true, message: 'ok', invite_code: null })

    const neu = await werfeBertHinaus()

    expect(neu.invite_code).toBeNull()
  })
})
