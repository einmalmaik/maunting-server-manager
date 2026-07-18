import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PrivacyAcknowledgementNotice } from './PrivacyAcknowledgementNotice'
import i18n from '@/i18n'

describe('PrivacyAcknowledgementNotice', () => {
  beforeEach(() => {
    i18n.changeLanguage('de')
    window.localStorage.clear()
  })

  it('links to the privacy page and hides after acknowledgement', () => {
    const onVisibilityChange = vi.fn()
    render(
      <MemoryRouter>
        <PrivacyAcknowledgementNotice onVisibilityChange={onVisibilityChange} />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'Datenschutzerklärung lesen' })).toHaveAttribute('href', '/privacy')
    expect(onVisibilityChange).toHaveBeenCalledWith(true)
    fireEvent.click(screen.getByRole('button', { name: 'Verstanden' }))
    expect(screen.queryByText('Wir respektieren deine Privatsphäre')).toBeNull()
    expect(onVisibilityChange).toHaveBeenLastCalledWith(false)
  })

  it('stays a true bottom overlay without affecting document flow and exposes its description accessibly', () => {
    render(
      <MemoryRouter>
        <PrivacyAcknowledgementNotice />
      </MemoryRouter>,
    )

    const notice = screen.getByRole('complementary', { name: 'Wir respektieren deine Privatsphäre' })
    expect(notice).toHaveClass('fixed', 'inset-x-0', 'bottom-0')
    expect(notice).not.toHaveClass('relative')
    expect(notice).toHaveAttribute('aria-describedby')
    expect(document.getElementById(notice.getAttribute('aria-describedby')!)).toHaveTextContent(/technisch notwendige Cookies/)
  })
})
