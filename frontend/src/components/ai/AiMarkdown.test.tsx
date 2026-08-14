import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { AiMarkdown } from './AiMarkdown'

/**
 * Die Markdown-Ausgabe ist die Hauptfläche des KI-Features. Lange, gegliederte
 * Antworten benutzen Überschriften bis Ebene 6 — die fielen vorher auf die
 * nackten Elemente durch, und Tailwinds Preflight setzt deren Größe, Gewicht
 * und Abstand zurück. Eine Überschrift sah dann genauso aus wie der Absatz
 * darüber und klebte an ihm.
 *
 * Die Ebenen sind bewusst um zwei nach unten verschoben, damit vom Modell
 * erzeugter Text die Gliederung der Panelseite nicht kapert.
 */
describe('AiMarkdown', () => {
  it('verschiebt die Überschriftenebenen nach unten', () => {
    render(<AiMarkdown content={'# Eins\n\n## Zwei\n\n### Drei'} />)

    expect(screen.getByRole('heading', { level: 3, name: 'Eins' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 4, name: 'Zwei' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 5, name: 'Drei' })).toBeInTheDocument()
  })

  it('hebt auch Überschriften ab Ebene 4 sichtbar hervor', () => {
    render(<AiMarkdown content={'#### Vier\n\n##### Fünf\n\n###### Sechs'} />)

    for (const name of ['Vier', 'Fünf', 'Sechs']) {
      const überschrift = screen.getByRole('heading', { level: 6, name })
      expect(überschrift.className).toContain('font-semibold')
      expect(überschrift.className).toContain('font-headline')
    }
  })
})
