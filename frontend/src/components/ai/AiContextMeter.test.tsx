import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { AiContextMeter } from './AiContextMeter'
import type { AiContextStatus } from '@/api/ai'
import i18n from '@/i18n'

const BASIS: AiContextStatus = {
  known: true,
  window_tokens: 128_000,
  usable_tokens: 100_000,
  used_tokens: 25_000,
  compaction_percent: 75,
  summarized: false,
}

/**
 * Der Ring ist die einzige Stelle, an der jemand sehen kann, wie viel Gespräch
 * die KI noch wörtlich vor sich hat und wann zusammengefasst wird. Beides war
 * vorher unsichtbar: der Chat faltete, zeigte eine Zeile darüber, und niemand
 * konnte erkennen, warum jetzt und nicht später.
 *
 * Die wichtigste Zusage steht im letzten Test — bei unbekanntem Fenster darf
 * **kein** Prozentwert erscheinen. Ein geschätzter sähe aus wie ein gemessener.
 */
describe('AiContextMeter', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
  })

  it('zeigt nichts, solange die Zahlen nicht da sind', () => {
    const { container } = render(<AiContextMeter status={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('nennt Fenster, Belegung und Faltmarke', () => {
    render(<AiContextMeter status={BASIS} />)

    const beschriftung = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(beschriftung).toMatch(/128k/)
    expect(beschriftung).toMatch(/25k/)
    // Der Anteil, nicht die rohe Tokenzahl: 25.000 von 100.000 nutzbaren.
    expect(beschriftung).toMatch(/25 %/)
    expect(beschriftung).toMatch(/75 %/)
  })

  it('meldet, wenn bereits ein Teil zusammengefasst ist', () => {
    render(<AiContextMeter status={{ ...BASIS, summarized: true }} />)

    expect(screen.getByRole('tooltip')).toHaveTextContent(/zusammengefasst/i)
  })

  it('erfindet keinen Prozentwert, wenn das Fenster unbekannt ist', () => {
    render(<AiContextMeter status={{ ...BASIS, known: false, window_tokens: null }} />)

    const beschriftung = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(beschriftung).not.toMatch(/%/)
    expect(beschriftung).toMatch(/nicht bekannt/)
  })

  it('färbt den Ring nach der eingestellten Marke, nicht nach einer Tokenzahl', () => {
    // Die Marke ist ein Prozentsatz und hängt deshalb nicht am Fenster: bei
    // 75 % Faltmarke warnt der Ring ab 75 % Belegung, egal wie groß das
    // Fenster ist.
    const ruhig = render(<AiContextMeter status={{ ...BASIS, usable_tokens: 200_000, used_tokens: 100_000 }} />)
    expect(ruhig.container.querySelector('[role="img"]')?.className).toContain('text-on-surface-variant')

    const gewarnt = render(<AiContextMeter status={{ ...BASIS, usable_tokens: 200_000, used_tokens: 160_000 }} />)
    expect(gewarnt.container.querySelector('[role="img"]')?.className).toContain('text-status-warning')
  })

  it('kappt die Anzeige bei voller Belegung statt über hundert zu laufen', () => {
    render(<AiContextMeter status={{ ...BASIS, used_tokens: 250_000 }} />)

    const beschriftung = screen.getByRole('img').getAttribute('aria-label') ?? ''
    expect(beschriftung).toMatch(/100 %/)
    expect(beschriftung).not.toMatch(/250 %/)
  })
})
