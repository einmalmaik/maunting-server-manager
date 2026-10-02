import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Pfadleiste } from './Pfadleiste'

/** Ein DataTransfer, wie ihn der Browser beim Ziehen herausgibt: Typen ja, Inhalt erst beim Ablegen. */
function daten(types: string[]) {
  return { types, getData: () => '', files: [] } as unknown as DataTransfer
}

const teile = [
  { key: 'a', label: 'Verträge' },
  { key: 'b', label: 'Miete' },
]

describe('Pfadleiste', () => {
  it('navigiert zu Stamm und Ebenen und markiert die geöffnete', () => {
    const onWaehlen = vi.fn()
    render(<Pfadleiste label="Pfad" stamm={{ key: '', label: 'Stamm' }} teile={teile} onWaehlen={onWaehlen} hochLabel="Hoch" />)

    expect(screen.getByRole('button', { name: 'Miete' })).toHaveAttribute('aria-current', 'page')
    fireEvent.click(screen.getByRole('button', { name: 'Stamm' }))
    fireEvent.click(screen.getByRole('button', { name: 'Verträge' }))
    fireEvent.click(screen.getByRole('button', { name: 'Hoch' }))
    expect(onWaehlen.mock.calls).toEqual([[''], ['a'], ['a']])
  })

  it('nimmt nur an, was kannAblegen erlaubt, und markiert das Ziel', () => {
    const onAblegen = vi.fn()
    const kannAblegen = (key: string) => key !== 'b'
    render(<Pfadleiste label="Pfad" stamm={{ key: '', label: 'Stamm' }} teile={teile} onWaehlen={vi.fn()} kannAblegen={kannAblegen} onAblegen={onAblegen} />)

    const vertraege = screen.getByRole('button', { name: 'Verträge' })
    const erlaubt = fireEvent.dragOver(vertraege, { dataTransfer: daten(['Files']) })
    expect(erlaubt).toBe(false) // preventDefault: der Browser erlaubt das Ablegen
    expect(vertraege.className).toContain('ring-primary/50')
    fireEvent.drop(vertraege, { dataTransfer: daten(['Files']) })
    expect(onAblegen).toHaveBeenCalledWith('a', expect.anything())
    expect(vertraege.className).not.toContain('ring-primary/50')

    const miete = screen.getByRole('button', { name: 'Miete' })
    expect(fireEvent.dragOver(miete, { dataTransfer: daten(['Files']) })).toBe(true)
    expect(miete.className).not.toContain('ring-primary/50')
  })

  it('färbt ein Ziel nur als Ziel, auch wenn es die offene Ebene ist, und den Stamm immer gleich', () => {
    render(<Pfadleiste label="Pfad" stamm={{ key: '', label: 'Stamm' }} teile={teile} onWaehlen={vi.fn()} onAblegen={vi.fn()} />)

    const miete = screen.getByRole('button', { name: 'Miete' })
    expect(miete.className).toContain('text-on-surface')
    fireEvent.dragOver(miete, { dataTransfer: daten(['Files']) })
    expect(miete.className).toContain('text-primary')
    expect(miete.className).not.toContain('text-on-surface')

    render(<Pfadleiste label="Wurzel" stamm={{ key: '', label: 'Oben' }} teile={[]} onWaehlen={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Oben' }).className).toContain('text-secondary')
    expect(screen.getByRole('button', { name: 'Oben' }).className).not.toContain('text-on-surface')
  })

  it('macht auf dem Telefon jeden Teil und den Hoch-Knopf zum 44-px-Ziel', () => {
    render(<Pfadleiste label="Pfad" stamm={{ key: '', label: 'Stamm' }} teile={teile} onWaehlen={vi.fn()} hochLabel="Hoch" />)
    for (const name of ['Stamm', 'Verträge', 'Miete']) {
      const klassen = screen.getByRole('button', { name }).className.split(/\s+/)
      expect(klassen).toContain('max-sm:min-h-11')
      expect(klassen).toContain('max-sm:min-w-11')
    }
    expect(screen.getByRole('button', { name: 'Hoch' }).className.split(/\s+/)).toEqual(
      expect.arrayContaining(['max-md:h-11', 'max-md:w-11']),
    )
  })

  it('ist ohne onAblegen kein Ablageziel', () => {
    render(<Pfadleiste label="Pfad" stamm={{ key: '', label: 'Stamm' }} teile={teile} onWaehlen={vi.fn()} />)
    expect(fireEvent.dragOver(screen.getByRole('button', { name: 'Stamm' }), { dataTransfer: daten(['Files']) })).toBe(true)
  })
})
