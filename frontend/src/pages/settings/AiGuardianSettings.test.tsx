import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '@/api/client'
import i18n from '@/i18n'
import { AiGuardianSettings } from './AiGuardianSettings'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: vi.fn(),
}))

const api = vi.mocked(client.api)

describe('AiGuardianSettings', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    api.mockReset()
  })

  it('lädt und zeigt den Status an (initial deaktiviert)', async () => {
    api.mockImplementation(((path: string) => {
      if (path === '/ai/settings/guardian') {
        return Promise.resolve({ enabled: false })
      }
      return Promise.resolve(null)
    }) as unknown as typeof client.api)

    render(<AiGuardianSettings canWrite />)

    expect(
      await screen.findByText(i18n.t('aiSettings.guardian.title')),
    ).toBeInTheDocument()
    expect(
      screen.getByText(i18n.t('aiSettings.guardian.statusDisabled')),
    ).toBeInTheDocument()

    const sw = screen.getByRole('switch')
    expect(sw).not.toBeChecked()
    expect(sw).not.toBeDisabled()
  })

  it('schaltet die Guardian-KI-Integration ein und speichert den Wert', async () => {
    api.mockImplementation(((path: string, init?: RequestInit) => {
      if (path === '/ai/settings/guardian' && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body))
        return Promise.resolve({ enabled: body.enabled })
      }
      if (path === '/ai/settings/guardian') {
        return Promise.resolve({ enabled: false })
      }
      return Promise.resolve(null)
    }) as unknown as typeof client.api)

    render(<AiGuardianSettings canWrite />)

    const sw = await screen.findByRole('switch')
    expect(sw).not.toBeChecked()

    fireEvent.click(sw)

    expect(api).toHaveBeenCalledWith(
      '/ai/settings/guardian',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ enabled: true }),
      }),
    )

    expect(
      await screen.findByText(i18n.t('aiSettings.guardian.statusEnabled')),
    ).toBeInTheDocument()
  })

  it('deaktiviert den Schalter, wenn canWrite false ist', async () => {
    api.mockImplementation(((path: string) => {
      if (path === '/ai/settings/guardian') {
        return Promise.resolve({ enabled: true })
      }
      return Promise.resolve(null)
    }) as unknown as typeof client.api)

    render(<AiGuardianSettings canWrite={false} />)

    const sw = await screen.findByRole('switch')
    expect(sw).toBeChecked()
    expect(sw).toBeDisabled()
  })
})
