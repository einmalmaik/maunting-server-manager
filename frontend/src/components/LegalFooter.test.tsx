import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { LegalFooter } from './LegalFooter'

describe('LegalFooter', () => {
  it('keeps Datenschutz visible and does not render Impressum', () => {
    render(
      <MemoryRouter>
        <LegalFooter version="v1.2.3" />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'Datenschutz' })).toHaveAttribute('href', '/privacy')
    expect(screen.queryByText(/Impressum/i)).toBeNull()
    expect(screen.getByText(/Maunting Server Manager v1.2.3/)).toBeInTheDocument()
  })
})
