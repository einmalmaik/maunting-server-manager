import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Register } from './Register'
import * as client from '@/api/client'

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: vi.fn() }
})

describe('Register', () => {
  beforeEach(() => {
    vi.mocked(client.api).mockReset()
    vi.mocked(client.api).mockImplementation(async (path: string) => {
      if (path === '/auth/captcha-config') return { enabled: false, provider: 'none', site_key: '' } as any
      return [] as any
    })
  })

  it('nennt jedes Feld mit seiner Überschrift', async () => {
    // Bis 03.10.2026 kam der Name aus dem Platzhalter: „admin“, „admin@example.com“.
    render(
      <MemoryRouter>
        <Register />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('textbox', { name: 'Benutzername' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'E-Mail' })).toHaveAttribute('type', 'email')
    expect(screen.getByLabelText('Passwort')).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText('Passwort bestätigen')).toHaveAttribute('type', 'password')
  })
})
