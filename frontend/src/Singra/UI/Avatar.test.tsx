import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { Avatar } from './Avatar'

describe('Avatar Component', () => {
  it('renders initials when no src is provided', () => {
    render(<Avatar name="Max Mustermann" />)
    expect(screen.getByText('MA')).toBeInTheDocument()
  })

  it('renders image when src is provided', () => {
    render(<Avatar src="/api/auth/avatar/avatar_1_test.png" name="Max Mustermann" />)
    const img = screen.getByRole('img')
    expect(img).toBeInTheDocument()
    expect(img).toHaveAttribute('src', '/api/auth/avatar/avatar_1_test.png')
  })

  it('falls back to initials when image triggers onError', () => {
    const { rerender } = render(<Avatar src="/api/auth/avatar/broken.png" name="Max Mustermann" />)
    const img = screen.getByRole('img')
    fireEvent.error(img)

    // After error, initials should be displayed
    expect(screen.getByText('MA')).toBeInTheDocument()

    // When src/resolvedSrc updates to a new valid source, hasError should reset
    rerender(<Avatar src="/api/auth/avatar/new_valid.png" name="Max Mustermann" />)
    const newImg = screen.getByRole('img')
    expect(newImg).toBeInTheDocument()
    expect(newImg).toHaveAttribute('src', '/api/auth/avatar/new_valid.png')
  })

  it('resolves relative URLs with custom resolveUrl if provided', () => {
    const customResolver = vi.fn((url: string) => `https://panel.example.com${url}`)
    render(
      <Avatar
        src="/api/auth/avatar/avatar_1_test.png"
        name="Max Mustermann"
        resolveUrl={customResolver}
      />
    )
    expect(customResolver).toHaveBeenCalledWith('/api/auth/avatar/avatar_1_test.png')
    const img = screen.getByRole('img')
    expect(img).toHaveAttribute('src', 'https://panel.example.com/api/auth/avatar/avatar_1_test.png')
  })
})
