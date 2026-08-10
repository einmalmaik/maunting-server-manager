import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { AiTab } from './AiTab'

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: vi.fn().mockResolvedValue([]) }
})

vi.mock('@/stores/confirmStore', () => ({ confirm: vi.fn().mockResolvedValue(true) }))
vi.mock('@/stores/toastStore', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

/**
 * Der KI-Tab im Profil ist der Ort des **persönlichen** Wissens — und nur der.
 *
 * Vorher stand die Skill-Verwaltung darunter, obwohl ein Skill nie „für dieses
 * Profil" gilt: er gehört einem Team oder dem ganzen Panel. Wer hier etwas
 * eintrug, schrieb je nach Auswahl unbemerkt für alle.
 */
describe('Profil → KI', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    usePermissionsStore.setState({
      me: {
        is_owner: false, role_id: null, role_name: null,
        // Beide Rechte gesetzt: die Abwesenheit der Skills soll an der Seite
        // liegen und nicht daran, dass jemand sie ohnehin nicht sähe.
        global_keys: ['ai.memory.use', 'ai.skills.use', 'ai.skills.manage'],
        server_keys: {},
      },
      isLoading: false, error: null,
    })
  })

  it('zeigt das persönliche Gedächtnis', async () => {
    render(<MemoryRouter><AiTab /></MemoryRouter>)
    expect(await screen.findByLabelText('Persönliches KI-Memory')).toBeInTheDocument()
  })

  it('zeigt keine Skill-Verwaltung mehr, sondern den Weg dorthin', async () => {
    render(<MemoryRouter><AiTab /></MemoryRouter>)
    await screen.findByLabelText('Persönliches KI-Memory')

    await waitFor(() => expect(screen.queryByLabelText('Skills')).not.toBeInTheDocument())
    expect(screen.queryByLabelText('Skills des Assistenten')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Teams/ })).toHaveAttribute('href', '/teams')
  })
})
