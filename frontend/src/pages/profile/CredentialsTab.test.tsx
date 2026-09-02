import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { credentialsApi, type UserCredential } from '@/api/credentials'
import i18n from '@/i18n'
import { CredentialsTab } from './CredentialsTab'

vi.mock('@/api/credentials', () => ({
  credentialsApi: {
    listMine: vi.fn(),
    save: vi.fn(),
    remove: vi.fn(),
    listForServer: vi.fn(),
    bind: vi.fn(),
    readPolicy: vi.fn(),
    updatePolicy: vi.fn(),
  },
}))

const steam: UserCredential = {
  id: 1,
  kind: 'steam_account',
  label: 'Hauptkonto',
  username: 'kunde42',
  secret_hint: '...1234',
  updated_at: '2026-08-07T10:00:00Z',
}

describe('CredentialsTab', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(credentialsApi.listMine).mockReset().mockResolvedValue([steam])
    vi.mocked(credentialsApi.save).mockReset().mockResolvedValue(steam)
  })

  it('shows only the hint, never a secret', async () => {
    render(<CredentialsTab />)

    expect(await screen.findByText(/Hauptkonto/)).toBeInTheDocument()
    expect(screen.getByText(/\.\.\.1234/)).toBeInTheDocument()
    expect(screen.queryByDisplayValue(/1234/)).not.toBeInTheDocument()
  })

  it('clears the secret field after saving so it never lingers in the form', async () => {
    render(<CredentialsTab />)
    await screen.findByText(/Hauptkonto/)

    fireEvent.change(screen.getByLabelText('Bezeichnung'), { target: { value: 'Zweitkonto' } })
    fireEvent.change(screen.getByLabelText('Benutzername'), { target: { value: 'kunde43' } })
    const secretInput = screen.getByLabelText('Geheimnis')
    fireEvent.change(secretInput, { target: { value: 'neues-geheimnis' } })
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() => expect(credentialsApi.save).toHaveBeenCalledWith(
      expect.objectContaining({ secret: 'neues-geheimnis', username: 'kunde43' }),
    ))
    await waitFor(() => expect(secretInput).toHaveValue(''))
  })

  it('requires a username for a steam account', async () => {
    render(<CredentialsTab />)
    await screen.findByText(/Hauptkonto/)

    fireEvent.change(screen.getByLabelText('Bezeichnung'), { target: { value: 'Ohne Name' } })
    fireEvent.change(screen.getByLabelText('Geheimnis'), { target: { value: 'geheim' } })

    expect(screen.getByRole('button', { name: 'Speichern' })).toBeDisabled()
  })
})
