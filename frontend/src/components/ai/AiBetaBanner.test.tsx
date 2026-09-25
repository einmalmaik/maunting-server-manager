import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AiBetaBanner, AI_IS_BETA } from './AiBetaBanner'

describe('AiBetaBanner', () => {
  it('rendert den Beta-Hinweis im aktiven Beta-Modus', () => {
    render(<AiBetaBanner />)

    if (AI_IS_BETA) {
      expect(screen.getByRole('status')).toBeInTheDocument()
      expect(screen.getByText('Beta')).toBeInTheDocument()
    }
  })
})
