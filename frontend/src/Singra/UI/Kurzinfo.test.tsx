import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Kurzinfo } from './Kurzinfo'

describe('Kurzinfo', () => {
  it('zeigt den Namen als Blase statt per title und lässt ihn nur den Knopf vorlesen', () => {
    render(
      <Kurzinfo text="Kalender" className="xl:!hidden">
        <button type="button" aria-label="Kalender">K</button>
      </Kurzinfo>,
    )

    const knopf = screen.getByRole('button', { name: 'Kalender' })
    const blase = knopf.parentElement!.querySelector('[aria-hidden="true"]')!
    expect(blase).toHaveAttribute('data-kurzinfo', 'Kalender')
    expect(blase).toHaveTextContent('')
    expect(screen.getAllByText('K')).toHaveLength(1)
    // Bis zum Zeigen hidden: auch unsichtbar zählte sie sonst zur Rollfläche.
    expect(blase).toHaveClass('hidden', 'xl:!hidden', 'pointer-events-none')
    // Nur bei Maus und Tastatur, nie bei Berührung.
    expect(blase.className).toContain('[@media(hover:hover)]:group-hover/kurzinfo:block')
    expect(blase.className).toContain('group-has-[:focus-visible]/kurzinfo:block')
    expect(document.querySelectorAll('[title]')).toHaveLength(0)
  })

  it('steht in Fußleisten über dem Knopf, am linken Rand bündig und bricht lange Hinweise um', () => {
    // Bis 02.10.2026 nur unter dem Knopf und einzeilig: in der Anrufleiste lag die Blase
    // außerhalb des Fensters, ein langer Hinweis lief über den Rand.
    render(
      <Kurzinfo text="Erst den Server stoppen" lage="oben" seite="anfang">
        <button type="button">Wechseln</button>
      </Kurzinfo>,
    )
    const blase = screen.getByRole('button').parentElement!.querySelector('[data-kurzinfo]')!
    expect(blase).toHaveClass('bottom-full', 'mb-1.5', 'left-0', 'w-max', 'max-w-64')
    expect(blase).not.toHaveClass('top-full', 'whitespace-nowrap', '-translate-x-1/2')
  })

  it('lässt eine Position aus aussen gelten', () => {
    // relative steht im gebauten CSS hinter absolute und gewann: die Hülle blieb im Fluss.
    const { container } = render(
      <>
        <Kurzinfo text="Online" aussen="absolute bottom-0 right-0"><span>a</span></Kurzinfo>
        <Kurzinfo text="Mehr" aussen="ml-auto md:absolute"><span>b</span></Kurzinfo>
      </>,
    )
    const [frei, imFluss] = Array.from(container.querySelectorAll('.group\\/kurzinfo'))
    expect(frei).toHaveClass('absolute')
    expect(frei).not.toHaveClass('relative')
    expect(imFluss).toHaveClass('relative', 'md:absolute')
  })
})
