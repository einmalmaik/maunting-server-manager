import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BrowserDownloadBadge } from './BrowserDownloadBadge'
import * as client from '@/api/client'
import * as geraeteArtModul from '@/desktop/geraeteArt'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

vi.mock('@/desktop/geraeteArt', () => ({
  geraeteArt: vi.fn(() => 'desktop'),
}))

describe('BrowserDownloadBadge', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(geraeteArtModul.geraeteArt).mockReturnValue('desktop')
  })

  it('renders the setup.exe download link on desktop when enabled', async () => {
    vi.mocked(client.api).mockResolvedValue({ browser_download_enabled: true })

    render(<BrowserDownloadBadge />)

    expect(await screen.findByText('MSB Browser')).toBeInTheDocument()
    const link = screen.getByRole('link')
    expect(link).toHaveAttribute(
      'href',
      'https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/MauntingSecureBrowser-Setup.exe'
    )
  })

  it('renders the apk download link when on Android', async () => {
    vi.mocked(client.api).mockResolvedValue({ browser_download_enabled: true })
    const originalUserAgent = navigator.userAgent
    Object.defineProperty(navigator, 'userAgent', {
      value: 'Mozilla/5.0 (Linux; Android 14; Pixel 8)',
      configurable: true,
    })

    try {
      render(<BrowserDownloadBadge />)
      expect(await screen.findByText('MSB Mobile')).toBeInTheDocument()
      const link = screen.getByRole('link')
      expect(link).toHaveAttribute(
        'href',
        'https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/MauntingSecureBrowser.apk'
      )
    } finally {
      Object.defineProperty(navigator, 'userAgent', {
        value: originalUserAgent,
        configurable: true,
      })
    }
  })

  it('does not render when disabled via settings', async () => {
    vi.mocked(client.api).mockResolvedValue({ browser_download_enabled: false })

    render(<BrowserDownloadBadge />)

    await waitFor(() => {
      expect(screen.queryByText('MSB Browser')).not.toBeInTheDocument()
    })
  })

  it('auto-hides when already running inside MSB (geraeteArt === browser)', async () => {
    vi.mocked(client.api).mockResolvedValue({ browser_download_enabled: true })
    vi.mocked(geraeteArtModul.geraeteArt).mockReturnValue('browser')

    render(<BrowserDownloadBadge />)

    await waitFor(() => {
      expect(screen.queryByText('MSB Browser')).not.toBeInTheDocument()
    })
  })

  it('renders by default (true) when setting is omitted', async () => {
    vi.mocked(client.api).mockResolvedValue({})

    render(<BrowserDownloadBadge />)

    expect(await screen.findByText('MSB Browser')).toBeInTheDocument()
  })

  it('stays visible with fallback true when settings request fails', async () => {
    vi.mocked(client.api).mockRejectedValue(new Error('offline'))

    render(<BrowserDownloadBadge />)

    expect(await screen.findByText('MSB Browser')).toBeInTheDocument()
  })
})
