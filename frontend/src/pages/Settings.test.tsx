import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Settings } from './Settings'
import * as client from '@/api/client'
import i18n from '@/i18n'
import { useToastStore } from '@/stores/toastStore'

vi.mock('@/api/client', () => ({ api: vi.fn() }))
vi.mock('@/hooks/useHasPermission', () => ({ useHasPermission: () => true }))

function renderSettings() {
  return render(<MemoryRouter><Settings /></MemoryRouter>)
}

describe('Settings', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en')
    useToastStore.setState({ toasts: [] })
    vi.mocked(client.api).mockReset()
  })

  it.each([null, {}, { default_language: null }])(
    'normalizes an incomplete settings response without exposing an exception (%j)',
    async (response) => {
      vi.mocked(client.api).mockResolvedValue(response as never)
      renderSettings()

      expect(await screen.findByText(/Panel[- ](?:Configuration|Konfiguration)/i)).toBeInTheDocument()
      await waitFor(() => {
        expect(useToastStore.getState().toasts).toEqual([])
      })
      expect(screen.queryByText(/startsWith/i)).not.toBeInTheDocument()
    },
  )
})
