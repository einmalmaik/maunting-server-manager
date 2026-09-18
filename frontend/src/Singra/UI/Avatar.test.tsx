import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { Avatar } from './Avatar'

describe('Avatar Component', () => {
  it('renders initials when no src is provided', () => {
    render(<Avatar name="Max Mustermann" />)
    expect(screen.getByText('MA')).toBeInTheDocument()
  })

  it('hält Kasten und Kreis auf derselben Größe', () => {
    // Vom Betreiber im Anruf gemeldet: „das Logo ist nicht mittig im Kreis".
    // Die Größe aus `size` mass bis 09/2026 den inneren Kreis, `className` den
    // äusseren Kasten. Wer beides setzte — und das musste jeder, der mehr als
    // `xl` brauchte —, bekam beides verschieden gross: der Kreis sass oben
    // links im Kasten, und ein Ring, der sich am Kasten ausrichtete, lag
    // sichtbar daneben. Deshalb misst der Kasten, und der Kreis füllt ihn.
    const { container } = render(<Avatar name="Max Mustermann" size="2xl" className="border-4" />)
    const kasten = container.firstElementChild as HTMLElement
    const kreis = kasten.firstElementChild as HTMLElement

    expect(kasten.className).toContain('h-28')
    expect(kasten.className).toContain('w-28')
    expect(kasten.className).toContain('border-4')
    // Der Kreis hat kein eigenes Mass mehr, er nimmt das des Kastens.
    expect(kreis.className).toContain('h-full')
    expect(kreis.className).toContain('w-full')
    expect(kreis.className).not.toMatch(/\bh-\d/)
  })

  it('renders image when src is provided', () => {
    render(<Avatar src="/api/auth/avatar/avatar_1_test.png" name="Max Mustermann" />)
    const img = screen.getByRole('img')
    expect(img).toBeInTheDocument()
    expect(img).toHaveAttribute('src', '/api/auth/avatar/avatar_1_test.png')
  })

  it('falls back to initials when image triggers onError and fallback fails', async () => {
    const { rerender } = render(<Avatar src="/api/auth/avatar/broken.png" name="Max Mustermann" />)
    const img = screen.getByRole('img')
    fireEvent.error(img)

    // After error and failed fallback fetch, initials should be displayed
    expect(await screen.findByText('MA')).toBeInTheDocument()

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
