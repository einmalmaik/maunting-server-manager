import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { DesktopAppDownloadBadge } from './DesktopAppDownloadBadge'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

describe('DesktopAppDownloadBadge', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders windows download link when enabled on desktop', async () => {
    vi.mocked(client.api).mockResolvedValue({ desktop_app_download_enabled: true })

    render(<DesktopAppDownloadBadge />)

    expect(await screen.findByText('MSS Desktop')).toBeInTheDocument()
    const link = screen.getByRole('link')
    expect(link).toHaveAttribute(
      'href',
      'https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/MauntingSmartSystem-Setup.exe'
    )
  })

  it('renders android apk link when on android device', async () => {
    vi.mocked(client.api).mockResolvedValue({ desktop_app_download_enabled: true })
    const originalUserAgent = navigator.userAgent
    Object.defineProperty(navigator, 'userAgent', {
      value: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36',
      configurable: true,
    })

    render(<DesktopAppDownloadBadge />)

    expect(await screen.findByText('MSS Mobile App')).toBeInTheDocument()
    const link = screen.getByRole('link')
    expect(link).toHaveAttribute(
      'href',
      'https://github.com/einmalmaik/maunting-server-manager/releases/latest/download/MauntingSmartSystem.apk'
    )

    Object.defineProperty(navigator, 'userAgent', {
      value: originalUserAgent,
      configurable: true,
    })
  })

  it('does not render when disabled', async () => {
    vi.mocked(client.api).mockResolvedValue({ desktop_app_download_enabled: false })

    render(<DesktopAppDownloadBadge />)

    await waitFor(() => {
      expect(screen.queryByText('MSS Desktop')).not.toBeInTheDocument()
    })
  })
})
