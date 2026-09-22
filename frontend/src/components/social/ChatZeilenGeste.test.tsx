/**
 * Die Zusicherung aus dem Modulkopf: **die Geste ist nie der einzige Weg.**
 *
 * Sie stand dort, war aber nicht eingelöst. Das Menü hing allein am Langdruck,
 * und `useLangdruck` steigt bei `pointerType === 'mouse'` sofort aus. Am
 * Rechner gab es damit überhaupt keinen Zugang zu Anheften, Archivieren und
 * Stummschalten. Genau das prüft diese Datei — und nebenbei, dass der neue
 * Knopf im unsichtbaren Zustand keine Klicks abfängt, denn er liegt über dem
 * Ungelesen-Abzeichen.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

import i18n from '@/i18n'
import { ChatZeilenGeste } from './ChatZeilenGeste'

beforeAll(async () => {
  await i18n.changeLanguage('de')
})

function zeichne(overrides: Partial<Parameters<typeof ChatZeilenGeste>[0]> = {}) {
  const onMenue = vi.fn()
  const onAnheften = vi.fn()
  const onArchivieren = vi.fn()
  render(
    <ChatZeilenGeste
      angeheftet={false}
      archiviert={false}
      onAnheften={onAnheften}
      onArchivieren={onArchivieren}
      onMenue={onMenue}
      {...overrides}
    >
      <button type="button">Anna</button>
    </ChatZeilenGeste>,
  )
  return { onMenue, onAnheften, onArchivieren }
}

describe('ChatZeilenGeste', () => {
  it('öffnet das Menü über einen Knopf, ohne Geste', () => {
    const { onMenue } = zeichne()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('messenger.chatActions') }))
    expect(onMenue).toHaveBeenCalledTimes(1)
  })

  it('lässt den Klick nicht zur Zeile durch', () => {
    // Der Knopf liegt über der Zeile. Ohne `stopPropagation` würde derselbe
    // Klick zusätzlich den Chat öffnen.
    const zeilenKlick = vi.fn()
    const onMenue = vi.fn()
    render(
      <div onClick={zeilenKlick}>
        <ChatZeilenGeste
          angeheftet={false}
          archiviert={false}
          onAnheften={vi.fn()}
          onArchivieren={vi.fn()}
          onMenue={onMenue}
        >
          <button type="button">Anna</button>
        </ChatZeilenGeste>
      </div>,
    )
    fireEvent.click(screen.getByRole('button', { name: i18n.t('messenger.chatActions') }))
    expect(onMenue).toHaveBeenCalledTimes(1)
    expect(zeilenKlick).not.toHaveBeenCalled()
  })

  it('fängt keine Klicks ab, solange er unsichtbar ist', () => {
    zeichne()
    const knopf = screen.getByRole('button', { name: i18n.t('messenger.chatActions') })
    const huelle = knopf.parentElement as HTMLElement
    // Unsichtbar heißt hier auch: durchlässig. Sonst schluckt die Fläche über
    // dem Ungelesen-Abzeichen den Tipper, der den Chat öffnen sollte.
    expect(huelle.className).toContain('opacity-0')
    expect(huelle.className).toContain('pointer-events-none')
    expect(huelle.className).toContain('group-hover:pointer-events-auto')
    expect(huelle.className).toContain('focus-within:pointer-events-auto')
  })

  it('trägt einen Namen für Vorlesegeräte', () => {
    zeichne()
    expect(
      screen.getByRole('button', { name: i18n.t('messenger.chatActions') }),
    ).toHaveAttribute('aria-label', i18n.t('messenger.chatActions'))
  })
})
