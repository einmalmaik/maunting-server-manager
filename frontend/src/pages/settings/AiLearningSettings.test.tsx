import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '@/api/client'
import i18n from '@/i18n'
import { AiLearningSettings } from './AiLearningSettings'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: vi.fn(),
}))

const api = vi.mocked(client.api)

/**
 * Die Freigabeliste und die Zahl daneben kommen aus zwei Endpunkten mit zwei
 * verschiedenen Rechten: `/ai/skills/pending` verlangt `ai.skills.manage`,
 * `/ai/settings/learning` nur `panel.settings.read`. Wer die Lernstufe einstellen
 * darf, die Warteschlange aber nicht sehen, sieht deshalb eine leere Liste — und
 * darf daraus nicht die Aussage „nichts zu prüfen" ableiten.
 */
describe('AiLearningSettings', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    api.mockReset()
  })

  function antworten(pendingCount: number, pendingOk: boolean) {
    api.mockImplementation(((path: string) => {
      if (path === '/ai/settings/learning') {
        return Promise.resolve({ policy: 'review', pending_count: pendingCount })
      }
      if (path === '/ai/skills/pending') {
        return pendingOk ? Promise.resolve([]) : Promise.reject(new Error('403'))
      }
      return Promise.resolve(null)
    }) as unknown as typeof client.api)
  }

  it('nennt den Rückstau, wenn die Liste verwehrt bleibt', async () => {
    antworten(3, false)

    render(<AiLearningSettings canWrite />)

    const text = await screen.findByText(i18n.t('ai.skills.pendingHidden', { count: 3 }))
    expect(text).toBeInTheDocument()
    expect(text.textContent).toContain('3')
    expect(screen.queryByText(i18n.t('ai.skills.pendingEmpty'))).not.toBeInTheDocument()
  })

  it('meldet erst dann „nichts zu prüfen", wenn wirklich nichts wartet', async () => {
    antworten(0, true)

    render(<AiLearningSettings canWrite />)

    expect(await screen.findByText(i18n.t('ai.skills.pendingEmpty'))).toBeInTheDocument()
  })

  it('schickt bei der Freigabe den gelesenen Inhalts-Abdruck mit (TOCTOU-Schutz)', async () => {
    const zeile = {
      id: 'a3a4c1de-0000-4000-8000-000000000001',
      skill_key: 'harmlos',
      name: 'Harmlos',
      description: 'Wartet.',
      body: 'Text, der gelesen wurde.',
      scope: 'global',
      origin: 'ai',
      team_id: null,
      status: 'pending',
      enabled: true,
      created_by: 1,
      created_at: '2026-08-19T00:00:00Z',
      updated_at: '2026-08-19T00:00:00Z',
      fingerprint: 'f'.repeat(64),
    }
    api.mockImplementation(((path: string, init?: RequestInit) => {
      if (path === '/ai/settings/learning') {
        return Promise.resolve({ policy: 'review', pending_count: 1 })
      }
      if (path === '/ai/skills/pending') return Promise.resolve([zeile])
      if (path === `/ai/skills/${zeile.id}/approve`) {
        // Die Zusicherung: der Abdruck aus der Liste geht im Body zurück.
        expect(JSON.parse(String(init?.body))).toEqual({ fingerprint: zeile.fingerprint })
        return Promise.resolve({ ...zeile, status: 'active' })
      }
      return Promise.resolve(null)
    }) as unknown as typeof client.api)

    render(<AiLearningSettings canWrite />)

    const knopf = await screen.findByRole('button', { name: i18n.t('ai.skills.approve') })
    knopf.click()

    await vi.waitFor(() => {
      expect(api).toHaveBeenCalledWith(
        `/ai/skills/${zeile.id}/approve`,
        expect.objectContaining({ method: 'POST' }),
      )
    })
  })
})
