import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { PrivacyAcknowledgementNotice } from './PrivacyAcknowledgementNotice'
import i18n from '@/i18n'

describe('PrivacyAcknowledgementNotice', () => {
  beforeEach(() => {
    i18n.changeLanguage('de')
    window.localStorage.clear()
  })

  it('links to the privacy page and hides after acknowledgement', () => {
    render(
      <MemoryRouter>
        <PrivacyAcknowledgementNotice />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'Datenschutzerklärung lesen' })).toHaveAttribute('href', '/privacy')
    fireEvent.click(screen.getByRole('button', { name: 'Verstanden' }))
    expect(screen.queryByText('Wir respektieren deine Privatsphäre')).toBeNull()
  })
})
