import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Ablageflaeche } from './Ablageflaeche'

describe('Ablageflaeche', () => {
  it('nennt, was beim Loslassen geschieht, und lässt Klicks durch', () => {
    const { container } = render(<Ablageflaeche text="Loslassen zum Hochladen" hinweis="Wird verschlüsselt" />)
    expect(screen.getByText('Loslassen zum Hochladen')).toBeInTheDocument()
    expect(screen.getByText('Wird verschlüsselt')).toBeInTheDocument()
    // Darunter liegende Ordnerzeilen bleiben selbst Ablageziel.
    expect(container.firstElementChild).toHaveClass('pointer-events-none')
  })
})
