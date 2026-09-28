/**
 * Die Profilseite eines anderen Kontos.
 *
 * Bis 09/2026 rief keine Oberfläche `getProfile` auf: Errungenschaften und
 * Nutzungszeit anderer waren nirgends zu sehen. Was die Seite zeigt, entscheidet
 * der Server; verborgen heißt 404, und die Seite sagt dann nicht, ob es das
 * Konto gibt.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { SanitizedApiError } from '@/api/client'
import type { PublicProfileResponse } from '@/api/social'
import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'
import { Benutzerprofil } from './Benutzerprofil'

const { antwort, initiateCall } = vi.hoisted(() => ({
  antwort: { profil: null as PublicProfileResponse | null, fehler: null as Error | null },
  initiateCall: vi.fn(async () => undefined),
}))

vi.mock('@/api/social', () => ({
  getProfile: vi.fn(async () => {
    if (antwort.fehler) throw antwort.fehler
    return antwort.profil
  }),
  getFriends: vi.fn(async () => []),
  sendFriendRequest: vi.fn(async () => ({ success: true, message: '' })),
}))

vi.mock('@/stores/useCallStore', () => ({
  useCallStore: { getState: () => ({ initiateCall }) },
}))

vi.mock('@/components/social/FunkenBadge', () => ({
  FunkenAbzeichen: () => null,
}))

function profil(teil: Partial<PublicProfileResponse> = {}): PublicProfileResponse {
  return {
    user_id: 7,
    username: 'mara',
    avatar_url: null,
    privacy: 'public',
    is_friend: false,
    member_since: '2026-03-01',
    presence: { status: 'online', device_type: 'web', activity_label: null },
    stats: {
      total_achievements: 2,
      unlocked_achievements: 1,
      total_points: 30,
      earned_points: 10,
      active_time_seconds: 5400 + 600,
      active_time_by_category: { ai_chat: 5400, general: 600 },
      achievements_unlocked: 1,
      total_activity_seconds: 6000,
      categories: { ai_chat: 5400, general: 600 },
    },
    achievements: [
      { id: 'a', title: 'Erster Server', description: '', category: 'servers', points: 10, icon: 'server', unlocked: true },
      { id: 'b', title: 'Tausend Befehle', description: '', category: 'activity', points: 20, icon: 'zap', unlocked: false },
    ],
    ...teil,
  }
}

function zeige(pfad = '/user/7') {
  return render(
    <MemoryRouter initialEntries={[pfad]}>
      <Routes>
        <Route path="/user/:userId" element={<Benutzerprofil />} />
        <Route path="/chat" element={<p>Chat geöffnet</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeAll(async () => {
  await i18n.changeLanguage('de')
})

beforeEach(() => {
  antwort.profil = profil()
  antwort.fehler = null
  initiateCall.mockClear()
  useAuthStore.setState({ user: { id: 1, username: 'ich' } as never })
})

describe('Benutzerprofil', () => {
  it('zeigt Nutzungszeit je Bereich und nur die freigeschalteten Errungenschaften zuerst', async () => {
    zeige()
    expect(await screen.findByRole('heading', { name: 'mara' })).toBeInTheDocument()
    expect(screen.getByText(i18n.t('social.activityCategory.ai_chat'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('social.milestones.durationHours', { hours: 1, minutes: 30 }))).toBeInTheDocument()
    expect(screen.getByText('Erster Server')).toBeInTheDocument()
    expect(screen.queryByText('Tausend Befehle')).not.toBeInTheDocument()
    expect(screen.getByText(i18n.t('social.profile.memberSince', { date: 'März 2026' }))).toBeInTheDocument()
  })

  it('bietet Fremden keinen Anruf, aber Chat und Freundschaftsanfrage', async () => {
    zeige()
    await screen.findByRole('heading', { name: 'mara' })
    expect(screen.queryByText(i18n.t('social.profile.call'))).not.toBeInTheDocument()
    expect(screen.getByText(i18n.t('messenger.sendFriendRequest'))).toBeInTheDocument()
    fireEvent.click(screen.getByText(i18n.t('social.profile.chat')))
    expect(await screen.findByText('Chat geöffnet')).toBeInTheDocument()
  })

  it('ruft einen Freund an', async () => {
    antwort.profil = profil({ is_friend: true })
    zeige()
    fireEvent.click(await screen.findByText(i18n.t('social.profile.call')))
    await waitFor(() =>
      expect(initiateCall).toHaveBeenCalledWith({ userId: 7, username: 'mara', avatarUrl: null }, 'audio'),
    )
  })

  it('sagt bei 404 nur „nicht verfügbar", ohne Namen', async () => {
    antwort.fehler = new SanitizedApiError('Benutzer nicht gefunden', { status: 404 })
    zeige()
    expect(await screen.findByText(i18n.t('social.profile.unavailableTitle'))).toBeInTheDocument()
    expect(screen.queryByText('mara')).not.toBeInTheDocument()
    expect(screen.queryByText(i18n.t('common.retry'))).not.toBeInTheDocument()
  })

  it('bietet bei anderen Fehlern einen neuen Versuch', async () => {
    antwort.fehler = new SanitizedApiError('Serverfehler', { status: 500 })
    zeige()
    expect(await screen.findByText(i18n.t('social.profile.loadFailed'))).toBeInTheDocument()
    antwort.fehler = null
    fireEvent.click(screen.getByText(i18n.t('common.retry')))
    expect(await screen.findByRole('heading', { name: 'mara' })).toBeInTheDocument()
  })

  it('zeigt auf dem eigenen Profil weder Chat noch Anruf', async () => {
    antwort.profil = profil({ user_id: 1, username: 'ich', is_friend: true })
    zeige('/user/1')
    await screen.findByRole('heading', { name: 'ich' })
    expect(screen.queryByText(i18n.t('social.profile.chat'))).not.toBeInTheDocument()
    expect(screen.queryByText(i18n.t('social.profile.call'))).not.toBeInTheDocument()
  })
})
