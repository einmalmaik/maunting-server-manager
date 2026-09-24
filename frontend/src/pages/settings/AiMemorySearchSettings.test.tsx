import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AiMemorySearchPolicy } from '@/api/ai'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { AiMemorySearchSettings } from './AiMemorySearchSettings'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: vi.fn(),
}))

const api = vi.mocked(client.api)
const PFAD = '/ai/settings/memory-search'

function antworte(policy: AiMemorySearchPolicy) {
  api.mockImplementation(((path: string, init?: RequestInit) => {
    if (path === PFAD && init?.method === 'PUT') {
      const { fallback } = JSON.parse(String(init.body))
      return Promise.resolve({
        ...policy,
        fallback,
        ready: policy.local_ready || policy.available.includes(fallback),
      })
    }
    if (path === PFAD) return Promise.resolve(policy)
    return Promise.resolve(null)
  }) as unknown as typeof client.api)
}

const auswahl = () => screen.findByRole('button', { name: i18n.t('ai.memorySearch.choice') })

describe('AiMemorySearchSettings', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    api.mockReset()
  })

  it('steht ab Werk auf Aus und sagt, dass die Suche ohne Modell nicht rechnet', async () => {
    antworte({ fallback: 'off', available: ['openai'], local_ready: false, ready: false })

    render(<AiMemorySearchSettings canWrite />)

    expect(await auswahl()).toHaveTextContent(i18n.t('ai.memorySearch.options.off'))
    expect(screen.getByText(i18n.t('ai.memorySearch.warning'))).toBeInTheDocument()
    expect(screen.getByText(new RegExp(i18n.t('ai.memorySearch.searchOff')))).toBeInTheDocument()
  })

  it('wählt OpenAI und speichert genau diesen Anbieter', async () => {
    antworte({ fallback: 'off', available: ['openai'], local_ready: false, ready: false })

    render(<AiMemorySearchSettings canWrite />)
    fireEvent.click(await auswahl())
    fireEvent.click(screen.getByRole('option', { name: new RegExp(i18n.t('ai.memorySearch.options.openai')) }))

    expect(api).toHaveBeenCalledWith(
      PFAD,
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ fallback: 'openai' }) }),
    )
    expect(
      await screen.findByText(new RegExp(i18n.t('ai.memorySearch.fallbackActive', { provider: 'OpenAI' }))),
    ).toBeInTheDocument()
  })

  it('sagt, wenn der gewählte Anbieter keinen Zugang hat', async () => {
    antworte({ fallback: 'google', available: [], local_ready: false, ready: false })

    render(<AiMemorySearchSettings canWrite />)

    expect(
      await screen.findByText(new RegExp(i18n.t('ai.memorySearch.noAccess', { provider: 'Google AI Studio' }))),
    ).toBeInTheDocument()
  })

  it('sagt bei laufendem lokalem Modell, dass die Wahl nichts bewirkt', async () => {
    antworte({ fallback: 'off', available: [], local_ready: true, ready: true })

    render(<AiMemorySearchSettings canWrite={false} />)

    expect(await screen.findByText(i18n.t('ai.memorySearch.localReady'))).toBeInTheDocument()
    expect(await auswahl()).toBeDisabled()
  })
})
