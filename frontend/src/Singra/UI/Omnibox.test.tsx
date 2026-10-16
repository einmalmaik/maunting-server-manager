import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Omnibox } from './Omnibox'

describe('Omnibox (Design-DNA)', () => {
  it('rendert Kinder, linke und rechte Slots korrekt', () => {
    render(
      <Omnibox
        links={<span data-testid="links">Icon</span>}
        rechts={<span data-testid="rechts">Aktion</span>}
      >
        <input data-testid="eingabe" placeholder="Adresse oder Suche" />
      </Omnibox>,
    )

    expect(screen.getByTestId('links')).toBeInTheDocument()
    expect(screen.getByTestId('rechts')).toBeInTheDocument()
    expect(screen.getByTestId('eingabe')).toBeInTheDocument()
  })

  it('traegt die Design-DNA-Klasse msm-omnibox', () => {
    const { container } = render(
      <Omnibox>
        <input />
      </Omnibox>,
    )
    expect(container.firstChild).toHaveClass('msm-omnibox')
  })
})
