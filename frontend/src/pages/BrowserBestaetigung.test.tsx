import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import i18n from '@/i18n'

const { api, passkeyBestaetigen } = vi.hoisted(() => ({ api: vi.fn(), passkeyBestaetigen: vi.fn() }))

vi.mock('@/api/client', async (original) => ({
  ...(await original<typeof import('@/api/client')>()),
  api,
}))
vi.mock('@/services/passkeyService', () => ({ passkeyBestaetigen }))

import { BrowserBestaetigung } from './BrowserBestaetigung'

const KENNUNG = 'k'.repeat(32)
const OPTIONEN = { challenge: 'YWJj', rpId: 'panel.example', allowCredentials: [] }

describe('BrowserBestaetigung', () => {
  beforeEach(() => {
    window.location.hash = KENNUNG
    api.mockReset()
    passkeyBestaetigen.mockReset().mockResolvedValue({ id: 'p', type: 'public-key' })
  })
  afterEach(() => {
    window.location.hash = ''
  })

  it('zeigt Zweck und drei Zahlen und schickt die gewählte Zahl mit dem Passkey', async () => {
    api.mockImplementation(async (pfad: string) =>
      pfad === '/auth/passkey/browser/optionen' ? { zweck: 'data_export', auswahl: [12, 47, 83], optionen: OPTIONEN } : {},
    )
    render(<BrowserBestaetigung />)

    expect(await screen.findByText(i18n.t('auth.browserBestaetigung.forAction', {
      aktion: i18n.t('auth.browserBestaetigung.purpose.data_export'),
    }))).toBeInTheDocument()
    expect(api).toHaveBeenCalledWith('/auth/passkey/browser/optionen', expect.objectContaining({
      body: JSON.stringify({ vorgang: KENNUNG }),
    }))
    for (const zahl of ['12', '47', '83']) expect(screen.getByRole('button', { name: zahl })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '47' }))

    await screen.findByText(i18n.t('auth.browserBestaetigung.done'))
    expect(passkeyBestaetigen).toHaveBeenCalledWith(OPTIONEN)
    expect(api).toHaveBeenLastCalledWith('/auth/passkey/browser/bestaetigen', expect.objectContaining({
      body: JSON.stringify({ vorgang: KENNUNG, zahl: 47, passkey: { id: 'p', type: 'public-key' } }),
    }))
  })

  it('ohne Kennung fragt sie nichts ab', async () => {
    window.location.hash = ''
    render(<BrowserBestaetigung />)
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(i18n.t('auth.browserBestaetigung.expired')))
    expect(api).not.toHaveBeenCalled()
  })
})
