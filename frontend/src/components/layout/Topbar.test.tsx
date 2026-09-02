import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { Topbar } from './Topbar'
import i18n from '@/i18n'

describe('Topbar', () => {
  it('renders mobile navigation trigger and handles click', () => {
    i18n.changeLanguage('de')
    const handleOpen = vi.fn()
    render(
      <MemoryRouter>
        <Topbar onOpenNavigation={handleOpen} />
      </MemoryRouter>,
    )

    const menuButton = screen.getByRole('button', { name: /navigation/i })
    expect(menuButton).toBeInTheDocument()
    fireEvent.click(menuButton)
    expect(handleOpen).toHaveBeenCalledTimes(1)
  })
})

