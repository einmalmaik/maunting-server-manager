import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi } from '@/api/ai'
import i18n from '@/i18n'
import { confirm } from '@/stores/confirmStore'
import { SearchEngineTab } from './SearchEngineTab'

vi.mock('@/api/ai', () => ({
  aiApi: {
    getWebSearchStatus: vi.fn(),
    setWebSearchConfig: vi.fn(),
  },
}))

vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: vi.fn().mockReturnValue(true),
}))

vi.mock('@/stores/confirmStore', () => ({
  confirm: vi.fn().mockResolvedValue(true),
}))

describe('SearchEngineTab', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.clearAllMocks()
  })

  it('displays default local sidecar status as active when running', async () => {
    vi.mocked(aiApi.getWebSearchStatus).mockResolvedValue({
      configured: true,
      has_api_key: false,
      has_google_cse: false,
      searxng_url: 'http://127.0.0.1:8888',
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: true,
      custom_searxng_url: null,
      sidecar_running: true,
      external_apis_on_custom: false,
    })

    render(<SearchEngineTab />)

    await waitFor(() => {
      expect(screen.getByTestId('searxng-sidecar-status')).toBeInTheDocument()
    })

    const statusBadge = screen.getByTestId('searxng-sidecar-status')
    expect(statusBadge.textContent).toContain('Lokaler SearXNG-Sidecar aktiv')
    expect(statusBadge.textContent).toContain('http://127.0.0.1:8888')

    const input = screen.getByLabelText('SearXNG-Endpunkt-URL (Optional)') as HTMLInputElement
    expect(input.value).toBe('')
    expect(input.placeholder).toContain('Standard: http://127.0.0.1:8888')
    expect(screen.queryByRole('button', { name: 'Löschen' })).not.toBeInTheDocument()
  })

  it('displays custom endpoint status and allows resetting to default sidecar', async () => {
    vi.mocked(aiApi.getWebSearchStatus).mockResolvedValue({
      configured: true,
      has_api_key: false,
      has_google_cse: false,
      searxng_url: 'http://custom-searxng.lan:8080',
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: false,
      custom_searxng_url: 'http://custom-searxng.lan:8080',
      sidecar_running: true,
      external_apis_on_custom: false,
    })

    vi.mocked(aiApi.setWebSearchConfig).mockResolvedValue({
      configured: true,
      has_api_key: false,
      has_google_cse: false,
      searxng_url: 'http://127.0.0.1:8888',
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: true,
      custom_searxng_url: null,
      sidecar_running: true,
      external_apis_on_custom: false,
    })

    render(<SearchEngineTab />)

    await waitFor(() => {
      expect(screen.getByTestId('searxng-sidecar-status')).toBeInTheDocument()
    })

    const statusBadge = screen.getByTestId('searxng-sidecar-status')
    expect(statusBadge.textContent).toContain('Benutzerdefinierter Endpunkt aktiv')
    expect(statusBadge.textContent).toContain('http://custom-searxng.lan:8080')

    const input = screen.getByLabelText('SearXNG-Endpunkt-URL (Optional)') as HTMLInputElement
    expect(input.value).toBe('http://custom-searxng.lan:8080')

    const deleteButton = screen.getByRole('button', { name: 'Löschen' })
    expect(deleteButton).toHaveAttribute('aria-description', 'Auf Standard zurücksetzen')
    expect(document.body.querySelectorAll('[title]')).toHaveLength(0)

    fireEvent.click(deleteButton)

    await waitFor(() => {
      expect(confirm).toHaveBeenCalled()
      expect(aiApi.setWebSearchConfig).toHaveBeenCalledWith({ searxngUrl: '' })
    })
  })

  it('saves and deletes Brave API key', async () => {
    vi.mocked(aiApi.getWebSearchStatus).mockResolvedValue({
      configured: false,
      has_api_key: false,
      has_google_cse: false,
      searxng_url: null,
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: true,
      custom_searxng_url: null,
      sidecar_running: false,
      external_apis_on_custom: false,
    })

    vi.mocked(aiApi.setWebSearchConfig).mockResolvedValue({
      configured: true,
      has_api_key: true,
      has_google_cse: false,
      searxng_url: null,
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: true,
      custom_searxng_url: null,
      sidecar_running: false,
      external_apis_on_custom: false,
    })

    render(<SearchEngineTab />)

    await waitFor(() => {
      expect(screen.getByLabelText('Brave-Search-API-Schlüssel (Optional)')).toBeInTheDocument()
    })

    const apiKeyInput = screen.getByLabelText('Brave-Search-API-Schlüssel (Optional)')
    fireEvent.change(apiKeyInput, { target: { value: 'BSA_test_key_12345' } })

    const saveButtons = screen.getAllByRole('button', { name: /speichern/i })
    fireEvent.click(saveButtons[1]) // Save Brave key

    await waitFor(() => {
      expect(aiApi.setWebSearchConfig).toHaveBeenCalledWith({ apiKey: 'BSA_test_key_12345' })
    })
  })

  it('saves and deletes Google CSE credentials', async () => {
    vi.mocked(aiApi.getWebSearchStatus).mockResolvedValue({
      configured: false,
      has_api_key: false,
      has_google_cse: false,
      searxng_url: null,
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: true,
      custom_searxng_url: null,
      sidecar_running: false,
      external_apis_on_custom: false,
    })

    vi.mocked(aiApi.setWebSearchConfig).mockResolvedValue({
      configured: true,
      has_api_key: false,
      has_google_cse: true,
      google_cse_cx: 'test-cx-123',
      searxng_url: null,
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: true,
      custom_searxng_url: null,
      sidecar_running: false,
      external_apis_on_custom: false,
    })

    render(<SearchEngineTab />)

    await waitFor(() => {
      expect(screen.getByLabelText('Google API-Schlüssel')).toBeInTheDocument()
    })

    const gKeyInput = screen.getByLabelText('Google API-Schlüssel')
    const gCxInput = screen.getByLabelText('Suchmaschinen-ID (cx)')

    fireEvent.change(gKeyInput, { target: { value: 'AIzaSyGoogleKey123' } })
    fireEvent.change(gCxInput, { target: { value: 'test-cx-123' } })

    const saveButtons = screen.getAllByRole('button', { name: /speichern/i })
    fireEvent.click(saveButtons[2]) // Save Google CSE

    await waitFor(() => {
      expect(aiApi.setWebSearchConfig).toHaveBeenCalledWith({
        googleCseApiKey: 'AIzaSyGoogleKey123',
        googleCseCx: 'test-cx-123',
      })
    })
  })
})
