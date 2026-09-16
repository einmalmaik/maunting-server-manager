import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { aiApi } from '@/api/ai'
import i18n from '@/i18n'
import { confirm } from '@/stores/confirmStore'
import { AiWebSearchSettings } from './AiWebSearchSettings'

vi.mock('@/api/ai', () => ({
  aiApi: {
    getWebSearchStatus: vi.fn(),
    setWebSearchConfig: vi.fn(),
  },
}))

vi.mock('@/stores/confirmStore', () => ({
  confirm: vi.fn().mockResolvedValue(true),
}))

describe('AiWebSearchSettings', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.clearAllMocks()
  })

  it('displays default local sidecar status as active when running', async () => {
    vi.mocked(aiApi.getWebSearchStatus).mockResolvedValue({
      configured: true,
      has_api_key: false,
      searxng_url: 'http://127.0.0.1:8888',
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: true,
      custom_searxng_url: null,
      sidecar_running: true,
    })

    render(<AiWebSearchSettings canWrite={true} />)

    await waitFor(() => {
      expect(screen.getByTestId('searxng-sidecar-status')).toBeInTheDocument()
    })

    const statusBadge = screen.getByTestId('searxng-sidecar-status')
    expect(statusBadge.textContent).toContain('Lokaler SearXNG-Sidecar aktiv')
    expect(statusBadge.textContent).toContain('http://127.0.0.1:8888')

    const input = screen.getByLabelText('SearXNG-Endpunkt-URL (Optional)') as HTMLInputElement
    expect(input.value).toBe('')
    expect(input.placeholder).toContain('Standard: http://127.0.0.1:8888')
    // No delete button when using default sidecar
    expect(screen.queryByTitle('Auf Standard zurücksetzen')).not.toBeInTheDocument()
  })

  it('displays default sidecar status as inactive/ready when not running', async () => {
    vi.mocked(aiApi.getWebSearchStatus).mockResolvedValue({
      configured: false,
      has_api_key: false,
      searxng_url: null,
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: true,
      custom_searxng_url: null,
      sidecar_running: false,
    })

    render(<AiWebSearchSettings canWrite={true} />)

    await waitFor(() => {
      expect(screen.getByTestId('searxng-sidecar-status')).toBeInTheDocument()
    })

    const statusBadge = screen.getByTestId('searxng-sidecar-status')
    expect(statusBadge.textContent).toContain('Lokaler SearXNG-Sidecar bereit')
    expect(statusBadge.textContent).toContain('http://127.0.0.1:8888')
  })

  it('displays custom endpoint status and allows resetting to default sidecar', async () => {
    vi.mocked(aiApi.getWebSearchStatus).mockResolvedValue({
      configured: true,
      has_api_key: false,
      searxng_url: 'http://custom-searxng.lan:8080',
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: false,
      custom_searxng_url: 'http://custom-searxng.lan:8080',
      sidecar_running: true,
    })

    vi.mocked(aiApi.setWebSearchConfig).mockResolvedValue({
      configured: true,
      has_api_key: false,
      searxng_url: 'http://127.0.0.1:8888',
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: true,
      custom_searxng_url: null,
      sidecar_running: true,
    })

    render(<AiWebSearchSettings canWrite={true} />)

    await waitFor(() => {
      expect(screen.getByTestId('searxng-sidecar-status')).toBeInTheDocument()
    })

    const statusBadge = screen.getByTestId('searxng-sidecar-status')
    expect(statusBadge.textContent).toContain('Benutzerdefinierter Endpunkt aktiv')
    expect(statusBadge.textContent).toContain('http://custom-searxng.lan:8080')

    const input = screen.getByLabelText('SearXNG-Endpunkt-URL (Optional)') as HTMLInputElement
    expect(input.value).toBe('http://custom-searxng.lan:8080')

    // Click delete to reset to default
    const deleteButton = screen.getByTitle('Auf Standard zurücksetzen')
    expect(deleteButton).toBeInTheDocument()

    fireEvent.click(deleteButton)

    await waitFor(() => {
      expect(confirm).toHaveBeenCalled()
      expect(aiApi.setWebSearchConfig).toHaveBeenCalledWith({ searxngUrl: '' })
    })

    await waitFor(() => {
      expect(screen.getByTestId('searxng-sidecar-status').textContent).toContain('Lokaler SearXNG-Sidecar aktiv')
      expect(input.value).toBe('')
    })
  })

  it('saves custom SearXNG endpoint and updates UI', async () => {
    vi.mocked(aiApi.getWebSearchStatus).mockResolvedValue({
      configured: true,
      has_api_key: false,
      searxng_url: 'http://127.0.0.1:8888',
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: true,
      custom_searxng_url: null,
      sidecar_running: true,
    })

    vi.mocked(aiApi.setWebSearchConfig).mockResolvedValue({
      configured: true,
      has_api_key: false,
      searxng_url: 'http://my-searxng:8080',
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: false,
      custom_searxng_url: 'http://my-searxng:8080',
      sidecar_running: true,
    })

    render(<AiWebSearchSettings canWrite={true} />)

    await waitFor(() => {
      expect(screen.getByTestId('searxng-sidecar-status')).toBeInTheDocument()
    })

    const input = screen.getByLabelText('SearXNG-Endpunkt-URL (Optional)')
    fireEvent.change(input, { target: { value: 'http://my-searxng:8080' } })

    const saveButtons = screen.getAllByRole('button', { name: /speichern/i })
    // The second save button is for SearXNG
    fireEvent.click(saveButtons[1])

    await waitFor(() => {
      expect(aiApi.setWebSearchConfig).toHaveBeenCalledWith({ searxngUrl: 'http://my-searxng:8080' })
    })

    await waitFor(() => {
      expect(screen.getByTestId('searxng-sidecar-status').textContent).toContain('Benutzerdefinierter Endpunkt aktiv')
    })
  })

  it('saves and deletes Brave API key', async () => {
    vi.mocked(aiApi.getWebSearchStatus).mockResolvedValue({
      configured: false,
      has_api_key: false,
      searxng_url: null,
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: true,
      custom_searxng_url: null,
      sidecar_running: false,
    })

    vi.mocked(aiApi.setWebSearchConfig).mockResolvedValue({
      configured: true,
      has_api_key: true,
      searxng_url: null,
      default_searxng_url: 'http://127.0.0.1:8888',
      is_default_searxng: true,
      custom_searxng_url: null,
      sidecar_running: false,
    })

    render(<AiWebSearchSettings canWrite={true} />)

    await waitFor(() => {
      expect(screen.getByLabelText('Brave-Search-API-Schlüssel (Optional)')).toBeInTheDocument()
    })

    const apiKeyInput = screen.getByLabelText('Brave-Search-API-Schlüssel (Optional)')
    fireEvent.change(apiKeyInput, { target: { value: 'BSA_test_key_12345' } })

    const saveButtons = screen.getAllByRole('button', { name: /speichern/i })
    fireEvent.click(saveButtons[0])

    await waitFor(() => {
      expect(aiApi.setWebSearchConfig).toHaveBeenCalledWith({ apiKey: 'BSA_test_key_12345' })
    })
  })
})
