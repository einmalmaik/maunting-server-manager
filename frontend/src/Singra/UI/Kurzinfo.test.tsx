import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Kurzinfo } from './Kurzinfo'

describe('Kurzinfo', () => {
  it('zeigt den Namen als Blase statt per title und lässt ihn nur den Knopf vorlesen', () => {
    render(
      <Kurzinfo text="Kalender" className="xl:hidden">
        <button type="button" aria-label="Kalender">K</button>
      </Kurzinfo>,
    )

    const knopf = screen.getByRole('button', { name: 'Kalender' })
    const blase = knopf.parentElement!.querySelector('[aria-hidden="true"]')!
    expect(blase).toHaveAttribute('data-kurzinfo', 'Kalender')
    expect(blase).toHaveTextContent('')
    expect(screen.getAllByText('K')).toHaveLength(1)
    expect(blase).toHaveClass('opacity-0', 'xl:hidden', 'pointer-events-none')
    // Nur bei Maus und Tastatur, nie bei Berührung.
    expect(blase.className).toContain('[@media(hover:hover)]:group-hover/kurzinfo:opacity-100')
    expect(blase.className).toContain('group-has-[:focus-visible]/kurzinfo:opacity-100')
    expect(document.querySelectorAll('[title]')).toHaveLength(0)
  })
})
