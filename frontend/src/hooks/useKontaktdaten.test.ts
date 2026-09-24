/**
 * Die Listen der Seitenleiste und ihr offener Abzug in `localStorage`.
 *
 * Der Abzug liegt unversiegelt auf der Platte. Gruppennamen und die Liste, mit
 * wem dieses Konto schreibt, haben darin nichts zu suchen: beides liegt
 * versiegelt an anderer Stelle, und ein offener Abzug daneben machte das
 * Versiegeln sinnlos.
 */

import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  getFriends: vi.fn(),
  getGroups: vi.fn(),
  getStories: vi.fn(),
  getPublicProfiles: vi.fn(),
  teamsList: vi.fn(),
  teamsGet: vi.fn(),
  gespraechsListe: vi.fn(),
  benenneGruppen: vi.fn(),
}))
vi.mock('@/api/social', () => ({
  getFriends: api.getFriends,
  getGroups: api.getGroups,
  getStories: api.getStories,
  getPublicProfiles: api.getPublicProfiles,
}))
vi.mock('@/api/teams', () => ({ teamsApi: { list: api.teamsList, get: api.teamsGet } }))
vi.mock('@/services/gespraechsListe', () => ({ gespraechsListe: api.gespraechsListe }))
vi.mock('@/services/gruppenName', () => ({ benenneGruppen: api.benenneGruppen }))

import { useKontaktdaten } from './useKontaktdaten'

const ICH = 7
const CACHE = 'msm:chat_contacts_cache'

beforeEach(() => {
  localStorage.clear()
  api.getFriends.mockReset().mockResolvedValue([{ id: 1, username: 'bert' }])
  api.getGroups.mockReset().mockResolvedValue([{ id: 3, name: null }])
  api.getStories.mockReset().mockResolvedValue([])
  api.getPublicProfiles.mockReset().mockResolvedValue([])
  api.teamsList.mockReset().mockResolvedValue([{ id: 9, name: 'Ops' }])
  api.teamsGet.mockReset().mockResolvedValue({ members: [{ user_id: ICH }, { user_id: 2 }] })
  api.gespraechsListe.mockReset().mockResolvedValue([{ other_user_id: 1 }])
  api.benenneGruppen.mockReset().mockImplementation(async (g: object[]) => g.map((x) => ({ ...x, name: 'Geheim' })))
})

describe('useKontaktdaten', () => {
  it('legt Gruppennamen und Gesprächsliste nie in den offenen Abzug', async () => {
    const { result } = renderHook(() => useKontaktdaten(ICH, false))
    await waitFor(() => expect(localStorage.getItem(CACHE)).not.toBeNull())

    expect(result.current.groups[0].name).toBe('Geheim')
    expect(result.current.directChats).toHaveLength(1)
    const abzug = JSON.parse(localStorage.getItem(CACHE)!)
    expect(abzug.groups).toEqual([{ id: 3, name: null }])
    expect(abzug.directChats).toEqual([])
    expect(JSON.stringify(abzug)).not.toContain('Geheim')
  })

  it('zeigt den Abzug sofort und lässt das eigene Konto aus den Teammitgliedern', async () => {
    localStorage.setItem(CACHE, JSON.stringify({ friends: [{ id: 5, username: 'alt' }] }))
    const { result } = renderHook(() => useKontaktdaten(ICH, false))
    expect(result.current.friends).toEqual([{ id: 5, username: 'alt' }])

    await waitFor(() => expect(result.current.teamMembers).toHaveLength(1))
    expect(result.current.teamMembers[0]).toMatchObject({ member: { user_id: 2 }, teamName: 'Ops' })
  })

  it('lädt nach dem Entsperren sofort neu', async () => {
    const { rerender } = renderHook(({ gesperrt }) => useKontaktdaten(ICH, gesperrt), {
      initialProps: { gesperrt: true },
    })
    await waitFor(() => expect(api.getGroups).toHaveBeenCalledTimes(1))
    rerender({ gesperrt: false })
    await waitFor(() => expect(api.getGroups).toHaveBeenCalledTimes(2))
  })
})
