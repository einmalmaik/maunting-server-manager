import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi } from '@/api/ai'
import i18n from '@/i18n'
import { AiWebSearchSettings } from './AiWebSearchSettings'

vi.mock('@/api/ai', () => ({
  aiApi: {
    getWebSearchStatus: vi.fn(),
    setWebSearchConfig: vi.fn(),
  },
}))

describe('AiWebSearchSettings', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.clearAllMocks()
  })

  it('renders web search toggle and active status', async () => {
    vi.mocked(aiApi.getWebSearchStatus).mockResolvedValue({
      configured: true,
      search_engine_configured: true,
      ai_enabled: true,
      has_api_key: true,
      has_google_cse: false,
      sidecar_running: true,
    })

    render(<AiWebSearchSettings canWrite={true} />)

    await waitFor(() => {
      expect(screen.getByText('KI-Suche aktiv')).toBeInTheDocument()
    })

    expect(screen.getByRole('switch', { name: 'Maunting Search für Singra (KI) freigeben' })).toHaveAttribute(
      'aria-checked',
      'true',
    )
    expect(screen.getByText(/Aktive Quellen in Maunting Search/)).toBeInTheDocument()
  })

  it('toggles AI web search when clicked', async () => {
    vi.mocked(aiApi.getWebSearchStatus).mockResolvedValue({
      configured: true,
      search_engine_configured: true,
      ai_enabled: true,
      has_api_key: false,
      has_google_cse: false,
      sidecar_running: true,
    })

    vi.mocked(aiApi.setWebSearchConfig).mockResolvedValue({
      configured: true,
      search_engine_configured: true,
      ai_enabled: false,
      has_api_key: false,
      has_google_cse: false,
      sidecar_running: true,
    })

    render(<AiWebSearchSettings canWrite={true} />)

    await waitFor(() => {
      expect(screen.getByText('KI-Suche aktiv')).toBeInTheDocument()
    })

    const toggle = screen.getByRole('switch', { name: 'Maunting Search für Singra (KI) freigeben' })
    fireEvent.click(toggle)

    await waitFor(() => {
      expect(aiApi.setWebSearchConfig).toHaveBeenCalledWith({ aiEnabled: false })
    })

    await waitFor(() => {
      expect(screen.getByText('KI-Suche deaktiviert')).toBeInTheDocument()
    })
  })

  it('dispatches open-settings-tab event or calls callback when button clicked', async () => {
    vi.mocked(aiApi.getWebSearchStatus).mockResolvedValue({
      configured: true,
      search_engine_configured: true,
      ai_enabled: true,
      has_api_key: false,
      has_google_cse: false,
      sidecar_running: false,
    })

    const onOpen = vi.fn()
    render(<AiWebSearchSettings canWrite={true} onOpenSearchEngineTab={onOpen} />)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Suchmaschine & APIs konfigurieren/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /Suchmaschine & APIs konfigurieren/i }))
    expect(onOpen).toHaveBeenCalledTimes(1)
  })
})
