import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { StoryFableBadge } from './StoryFableBadge'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

describe('StoryFableBadge', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the apk download link when enabled', async () => {
    vi.mocked(client.api).mockResolvedValue({ story_fable_download_enabled: true })

    render(<StoryFableBadge />)

    expect(await screen.findByText('Story Fable')).toBeInTheDocument()
    const link = screen.getByRole('link')
    expect(link).toHaveAttribute(
      'href',
      'https://github.com/einmalmaik/MFS/releases/latest/download/MauntingStoryFable.apk'
    )
  })

  it('does not render when disabled', async () => {
    vi.mocked(client.api).mockResolvedValue({ story_fable_download_enabled: false })

    render(<StoryFableBadge />)

    await waitFor(() => {
      expect(screen.queryByText('Story Fable')).not.toBeInTheDocument()
    })
  })

  it('stays hidden when the setting is missing', async () => {
    // Anders als beim MSS-Banner ist die Vorgabe aus: Der Link zeigt auf ein Release, das erst
    // veroeffentlicht werden muss. Ein Hinweis, der auf eine 404-Seite fuehrt, ist schlechter
    // als gar keiner (AGENTS.md 11.9).
    vi.mocked(client.api).mockResolvedValue({})

    render(<StoryFableBadge />)

    await waitFor(() => {
      expect(screen.queryByText('Story Fable')).not.toBeInTheDocument()
    })
  })

  it('stays hidden when the settings request fails', async () => {
    vi.mocked(client.api).mockRejectedValue(new Error('offline'))

    render(<StoryFableBadge />)

    await waitFor(() => {
      expect(screen.queryByText('Story Fable')).not.toBeInTheDocument()
    })
  })
})
