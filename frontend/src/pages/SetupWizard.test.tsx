import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { SetupWizard } from './SetupWizard'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    api: vi.fn(),
  }
})

function openOwnerStep(emailConfigured: boolean) {
  render(
    <MemoryRouter>
      <SetupWizard onComplete={vi.fn()} emailConfigured={emailConfigured} />
    </MemoryRouter>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'Loslegen' }))
}

function fillOwnerFields() {
  fireEvent.change(screen.getByLabelText('Benutzername'), { target: { value: 'owner' } })
  fireEvent.change(screen.getByLabelText('E-Mail'), { target: { value: 'owner@example.com' } })
  fireEvent.change(screen.getByLabelText('Passwort', { selector: '#setup-owner-password' }), {
    target: { value: 'OwnerPassword123!' },
  })
  fireEvent.change(screen.getByLabelText('Passwort bestätigen'), {
    target: { value: 'OwnerPassword123!' },
  })
}

function setupRequestBody() {
  const call = vi.mocked(client.api).mock.calls.find(([path]) => path === '/auth/setup')
  expect(call).toBeDefined()
  return JSON.parse(String(call?.[1]?.body))
}

describe('SetupWizard email delivery setup', () => {
  beforeEach(async () => {
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockResolvedValue({ requires_verification: true, message: 'ok' })
    await i18n.changeLanguage('de')
  })

  it('submits Resend as the recommended initial email configuration', async () => {
    openOwnerStep(false)
    fillOwnerFields()
    fireEvent.change(screen.getByLabelText('Absenderadresse'), {
      target: { value: 'panel@example.com' },
    })
    fireEvent.change(screen.getByLabelText('Resend API-Key'), {
      target: { value: 're_test_setup_key' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Owner erstellen' }))

    await waitFor(() => expect(
      vi.mocked(client.api).mock.calls.some(([path]) => path === '/auth/setup'),
    ).toBe(true))
    expect(setupRequestBody()).toEqual({
      username: 'owner',
      email: 'owner@example.com',
      password: 'OwnerPassword123!',
      email_config: {
        provider: 'resend',
        from_address: 'panel@example.com',
        resend_api_key: 're_test_setup_key',
      },
    })
  })

  it('does not render or submit email credentials when delivery is already configured', async () => {
    openOwnerStep(true)
    fillOwnerFields()

    expect(screen.queryByText('E-Mail-Versand')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Owner erstellen' }))

    await waitFor(() => expect(
      vi.mocked(client.api).mock.calls.some(([path]) => path === '/auth/setup'),
    ).toBe(true))
    expect(setupRequestBody()).toEqual({
      username: 'owner',
      email: 'owner@example.com',
      password: 'OwnerPassword123!',
    })
  })
})
