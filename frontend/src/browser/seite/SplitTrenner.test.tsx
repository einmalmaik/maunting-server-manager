import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { SplitTrenner } from './SplitTrenner'

const leiste = () => screen.getByRole('button', { name: /tauschen/i }).parentElement!
const trenner = () => screen.getByRole('separator')

describe('Trenner der geteilten Ansicht', () => {
  it('legt die Knöpfe bei senkrechtem Trenner untereinander in den Griff', () => {
    render(<SplitTrenner richtung="horizontal" anteil={0.5} onAnteilAendern={() => {}} containerRef={{ current: null }} />)

    // `flex-row` allein macht nichts: ohne `flex` steht der Behälter als Block da
    // und die Knöpfe stapeln sich untereinander.
    expect(leiste().className).toMatch(/(^|\s)flex(\s|$)/)
    expect(leiste().className).toContain('flex-col')
    expect(leiste().className).toContain('left-1/2')
    expect(trenner().className).toContain('w-10')
  })

  it('legt die Knöpfe bei waagerechtem Trenner nebeneinander in den Griff', () => {
    render(<SplitTrenner richtung="vertikal" anteil={0.5} onAnteilAendern={() => {}} containerRef={{ current: null }} />)

    expect(leiste().className).toMatch(/(^|\s)flex(\s|$)/)
    expect(leiste().className).toContain('flex-row')
    expect(leiste().className).toContain('top-1/2')
    expect(trenner().className).toContain('h-10')
  })

  it('zeigt die Knöpfe ohne Hover und ohne Mausbewegung', () => {
    render(<SplitTrenner richtung="horizontal" anteil={0.5} onAnteilAendern={() => {}} containerRef={{ current: null }} />)

    expect(leiste().className).not.toContain('opacity-0')
    expect(leiste().className).not.toContain('pointer-events-none')
  })
})
