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

  /**
   * Modellausgabe ist Fremdtext, und sie hat vorher Serverlogs, Configs und
   * Dateianhänge gelesen. Wer dort HTML unterbringt, schreibt in unsere Seite,
   * sobald jemand `rehype-raw` einbindet oder auf `dangerouslySetInnerHTML`
   * umstellt — beides sieht wie eine Verbesserung aus („die Formatierung geht
   * verloren"). Dieser Test ist die Stelle, an der es auffällt.
   */
  it('lässt rohes HTML aus der Modellantwort Text bleiben', () => {
    const { container } = render(
      <AiMarkdown
        content={'<img src=x onerror="alert(1)">\n\nEin <b>fetter</b> Satz.\n\n<script>alert(2)</script>'}
      />,
    )

    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
    // Verschluckt wird es aber auch nicht: was das Modell geschrieben hat, soll
    // lesbar dastehen — nur eben als Zeichen und nicht als Markup.
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">')
    expect(container.textContent).toContain('<b>fetter</b>')
  })

  it('gibt einem vom Modell erzeugten Link weder Fenster noch Herkunft mit', () => {
    render(<AiMarkdown content={'[Zur Anleitung](https://example.invalid/pfad)'} />)

    const link = screen.getByRole('link', { name: 'Zur Anleitung' })
    // Ein neues Fenster, damit ein Klick die Panelsitzung nicht ersetzt.
    expect(link).toHaveAttribute('target', '_blank')
    // `noopener` nimmt der Zielseite `window.opener`, über den sie unsere Seite
    // sonst umleiten könnte; `noreferrer` hält die Panel-URL zurück, in der
    // Server-IDs stehen. Beide gehören zusammen — `target="_blank"` ohne sie ist
    // eine offene Tür für einen Link, den ein Modell aus einem Serverlog
    // abgeschrieben hat.
    expect(link).toHaveAttribute('rel', 'noreferrer noopener')
  })
})
