import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { api } from '@/api/client'
import { DevicesTab } from './DevicesTab'

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    api: vi.fn((path: string) => {
      if (path === '/auth/devices/pairing') {
        return Promise.resolve({
          code: 'ABCD-EFGH-JKLM',
          expires_at: '2026-08-26T20:30:00Z',
          label: 'Arbeitsrechner',
          qr_data_uri: 'data:image/svg+xml;utf8,<svg></svg>',
        })
      }
      if (path === '/auth/devices') {
        return Promise.resolve([
          { family: 'fam-1', label: 'Arbeitsrechner', paired_at: '2026-08-21T10:00:00Z' },
        ])
      }
      return Promise.resolve({})
    }),
  }
})

describe('DevicesTab', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    await i18n.changeLanguage('de')
  })

  it('erzeugt einen Kopplungscode mit QR-Code und zeigt ihn genau einmal', async () => {
    render(
      <MemoryRouter>
        <DevicesTab />
      </MemoryRouter>,
    )

    const feld = await screen.findByLabelText('Name des Geräts')
    fireEvent.change(feld, { target: { value: 'Arbeitsrechner' } })
    fireEvent.click(screen.getByRole('button', { name: 'Code erzeugen' }))

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith('/auth/devices/pairing', {
        method: 'POST',
        body: JSON.stringify({ label: 'Arbeitsrechner' }),
      }),
    )

    expect(await screen.findByText('ABCD-EFGH-JKLM')).toBeInTheDocument()
    expect(await screen.findByAltText('QR-Code')).toBeInTheDocument()
  })

  it('nennt die API-Adresse neben dem Code, nicht in einer Anleitung', async () => {
    render(
      <MemoryRouter>
        <DevicesTab />
      </MemoryRouter>,
    )
    expect(await screen.findByLabelText('Diese Adresse in der App eintragen')).toBeInTheDocument()
  })

  it('listet gekoppelte Geräte und entzieht einzeln', async () => {
    render(
      <MemoryRouter>
        <DevicesTab />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Arbeitsrechner')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Zugang entziehen/ }))

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith('/auth/devices/fam-1', { method: 'DELETE' }),
    )
  })
})
