import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '@/api/client'
import i18n from '@/i18n'
import { AiMemorySearchSettings } from './AiMemorySearchSettings'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: vi.fn(),
}))

const api = vi.mocked(client.api)
const PFAD = '/ai/settings/memory-search'

function antworte(policy: { google_fallback: boolean; local_ready: boolean; ready: boolean }) {
  api.mockImplementation(((path: string, init?: RequestInit) => {
    if (path === PFAD && init?.method === 'PUT') {
      const body = JSON.parse(String(init.body))
      return Promise.resolve({ ...policy, google_fallback: body.google_fallback, ready: body.google_fallback })
    }
    if (path === PFAD) return Promise.resolve(policy)
    return Promise.resolve(null)
  }) as unknown as typeof client.api)
}

describe('AiMemorySearchSettings', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    api.mockReset()
  })

  it('steht ab Werk auf aus und sagt, dass die Suche ohne Modell nicht rechnet', async () => {
    antworte({ google_fallback: false, local_ready: false, ready: false })

    render(<AiMemorySearchSettings canWrite />)

    const schalter = await screen.findByRole('switch')
    expect(schalter).not.toBeChecked()
    expect(screen.getByText(i18n.t('ai.memorySearch.warning'))).toBeInTheDocument()
    expect(screen.getByText(new RegExp(i18n.t('ai.memorySearch.searchOff')))).toBeInTheDocument()
  })

  it('schaltet den Rückfall ein und speichert genau diesen Wert', async () => {
    antworte({ google_fallback: false, local_ready: false, ready: false })

    render(<AiMemorySearchSettings canWrite />)
    fireEvent.click(await screen.findByRole('switch'))

    expect(api).toHaveBeenCalledWith(
      PFAD,
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ google_fallback: true }) }),
    )
    expect(await screen.findByText(new RegExp(i18n.t('ai.memorySearch.fallbackActive')))).toBeInTheDocument()
  })

  it('sagt bei laufendem lokalem Modell, dass der Schalter nichts bewirkt', async () => {
    antworte({ google_fallback: false, local_ready: true, ready: true })

    render(<AiMemorySearchSettings canWrite={false} />)

    expect(await screen.findByText(i18n.t('ai.memorySearch.localReady'))).toBeInTheDocument()
    expect(screen.getByRole('switch')).toBeDisabled()
  })
})
