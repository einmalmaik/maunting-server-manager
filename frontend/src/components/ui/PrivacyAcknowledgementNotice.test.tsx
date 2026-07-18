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

    expect(screen.getByRole('link', { name: i18n.t('privacyNotice.readPolicy') })).toHaveAttribute('href', '/privacy')
    expect(onVisibilityChange).toHaveBeenCalledWith(true)
    fireEvent.click(screen.getByRole('button', { name: i18n.t('privacyNotice.confirm') }))
    expect(screen.queryByText(i18n.t('privacyNotice.title'))).toBeNull()
    expect(onVisibilityChange).toHaveBeenLastCalledWith(false)
  })

  it('stays a true bottom overlay without affecting document flow and exposes its description accessibly', () => {
    render(
      <MemoryRouter>
        <PrivacyAcknowledgementNotice />
      </MemoryRouter>,
    )

    const notice = screen.getByRole('complementary', { name: i18n.t('privacyNotice.title') })
    expect(notice).toHaveClass('fixed', 'inset-x-0', 'bottom-0')
    expect(notice).not.toHaveClass('relative')
    expect(notice).toHaveAttribute('aria-describedby')
    expect(document.getElementById(notice.getAttribute('aria-describedby')!)).toHaveTextContent(
      i18n.t('privacyNotice.description').substring(0, 30)
    )
  })
})

