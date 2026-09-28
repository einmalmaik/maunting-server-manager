import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MessengerBetaBanner, MESSENGER_IS_BETA } from './MessengerBetaBanner'

describe('MessengerBetaBanner', () => {
  it('rendert den Beta-Hinweis im aktiven Beta-Modus', () => {
    render(<MessengerBetaBanner />)

    if (MESSENGER_IS_BETA) {
      expect(screen.getByRole('status')).toBeInTheDocument()
      expect(screen.getByText('Beta')).toBeInTheDocument()
    }
  })
})
