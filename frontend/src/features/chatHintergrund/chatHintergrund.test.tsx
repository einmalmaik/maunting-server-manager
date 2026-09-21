import { act, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import i18n from '@/i18n'
import { Blatteintrag, Blattknopf } from '@/Singra/UI'

import { ChatHintergrund } from './ChatHintergrund'
import { ChatHintergrundDialog } from './ChatHintergrundDialog'
import {
  hintergrundSchluessel,
  ladeChatHintergrund,
  speichereChatHintergrund,
  STANDARD_HINTERGRUND,
} from './speicher'
import { HINTERGRUND_VORLAGEN } from './vorlagen'

beforeAll(async () => {
  await i18n.changeLanguage('de')
})

beforeEach(() => {
  localStorage.clear()
})

/** Ein Fenster mit Knopf, wie es eine Fläche einbaut. */
function Probeflaeche({ bereich }: { bereich: 'messenger' | 'ki' }) {
  const [offen, setzeOffen] = useState(false)
  return (
    <div className="relative">
      <ChatHintergrund bereich={bereich} />
      <button type="button" onClick={() => setzeOffen(true)}>
        einstellen
      </button>
      <ChatHintergrundDialog bereich={bereich} offen={offen} onOffenChange={setzeOffen} />
    </div>
  )
}

describe('Speicher', () => {
  it('hält die Bereiche auseinander', () => {
    speichereChatHintergrund('messenger', { preset: 'midnight', dimLevel: 40 })

    expect(ladeChatHintergrund('messenger').preset).toBe('midnight')
    expect(ladeChatHintergrund('ki')).toEqual(STANDARD_HINTERGRUND)
    expect(localStorage.getItem(hintergrundSchluessel('ki'))).toBeNull()
  })

  it('übernimmt den alten Messenger-Schlüssel einmalig', () => {
    // Genau die Form, die vor dem Umbau im Browser lag.
    localStorage.setItem(
      'msm_chat_wallpaper_config',
      JSON.stringify({ preset: 'petrol', dimLevel: 10 }),
    )

    expect(ladeChatHintergrund('messenger')).toEqual({
      preset: 'petrol',
      customDataUrl: undefined,
      dimLevel: 10,
    })
    // Danach steht die Wahl unter dem neuen Namen, der alte ist geräumt.
    expect(localStorage.getItem('msm_chat_wallpaper_config')).toBeNull()
    expect(ladeChatHintergrund('messenger').preset).toBe('petrol')
  })

  /**
   * Der Grund für die Schlüsselnamen, und kein theoretischer: der Messenger
   * ruft `scrubPlaintextStorage()` bei jedem Öffnen, und das räumt alles unter
   * `msm_chat_` weg. Unter dem alten Namen war der Hintergrund danach fort.
   */
  it('überlebt die Klartext-Putzkolonne des Messengers', async () => {
    const { scrubPlaintextStorage } = await import('@/services/e2eeCrypto')

    speichereChatHintergrund('messenger', { preset: 'midnight', dimLevel: 30 })
    speichereChatHintergrund('ki', { preset: 'petrol', dimLevel: 5 })

    scrubPlaintextStorage()

    expect(ladeChatHintergrund('messenger').preset).toBe('midnight')
    expect(ladeChatHintergrund('ki').preset).toBe('petrol')
  })

  it('hält sich aus dem Namensraum der Putzkolonne heraus', () => {
    for (const bereich of ['messenger', 'ki'] as const) {
      expect(hintergrundSchluessel(bereich).startsWith('msm_chat_')).toBe(false)
    }
  })

  it('fällt auf den Standard zurück, wo nichts Brauchbares steht', () => {
    localStorage.setItem(hintergrundSchluessel('ki'), '{kein json')
    expect(ladeChatHintergrund('ki')).toEqual(STANDARD_HINTERGRUND)

    localStorage.setItem(hintergrundSchluessel('ki'), JSON.stringify({ preset: 'neon' }))
    expect(ladeChatHintergrund('ki').preset).toBe(STANDARD_HINTERGRUND.preset)

    // Ein „eigenes Bild" ohne Bild ist kein Hintergrund, sondern ein Loch.
    localStorage.setItem(
      hintergrundSchluessel('ki'),
      JSON.stringify({ preset: 'custom', dimLevel: 25 }),
    )
    expect(ladeChatHintergrund('ki').preset).toBe(STANDARD_HINTERGRUND.preset)
  })
})

describe('Schicht', () => {
  it('zeigt die gespeicherte Vorlage samt Abdunkelung', () => {
    speichereChatHintergrund('ki', { preset: 'petrol', dimLevel: 40 })
    const { container } = render(<ChatHintergrund bereich="ki" />)

    expect(container.querySelector('[data-chat-hintergrund="petrol"]')).not.toBeNull()
    expect(container.querySelector('[data-chat-hintergrund-abdunkeln="40"]')).not.toBeNull()
  })

  it('folgt einer Änderung, ohne neu aufgebaut zu werden', () => {
    // Zwei Flächen desselben Bereichs — im KI-Bereich ist das der Chat und
    // das Guardian-Fenster nebenan.
    const { container } = render(
      <>
        <ChatHintergrund bereich="ki" />
        <ChatHintergrund bereich="ki" />
      </>,
    )
    expect(container.querySelectorAll('[data-chat-hintergrund="cyber"]')).toHaveLength(2)

    act(() => {
      speichereChatHintergrund('ki', { preset: 'midnight', dimLevel: 0 })
    })

    expect(container.querySelectorAll('[data-chat-hintergrund="midnight"]')).toHaveLength(2)
    // Ohne Abdunkelung auch keine Schicht dafür.
    expect(container.querySelector('[data-chat-hintergrund-abdunkeln]')).toBeNull()
  })

  it('lässt einen fremden Bereich in Ruhe', () => {
    const { container } = render(<ChatHintergrund bereich="messenger" />)

    act(() => {
      speichereChatHintergrund('ki', { preset: 'midnight', dimLevel: 0 })
    })

    expect(container.querySelector('[data-chat-hintergrund="cyber"]')).not.toBeNull()
  })
})

describe('Einstellungsfenster', () => {
  it('bietet jede Vorlage des Katalogs an', () => {
    render(<Probeflaeche bereich="ki" />)
    fireEvent.click(screen.getByText('einstellen'))

    for (const vorlage of HINTERGRUND_VORLAGEN) {
      expect(screen.getByText(i18n.t(vorlage.nameKey))).toBeInTheDocument()
    }
  })

  it('schreibt nur in seinen eigenen Bereich', () => {
    speichereChatHintergrund('messenger', { preset: 'petrol', dimLevel: 15 })

    render(<Probeflaeche bereich="ki" />)
    fireEvent.click(screen.getByText('einstellen'))
    fireEvent.click(screen.getByText(i18n.t('social.wallpaper.midnight')))
    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.apply') }))

    expect(ladeChatHintergrund('ki').preset).toBe('midnight')
    expect(ladeChatHintergrund('messenger').preset).toBe('petrol')
  })

  it('zeigt die neue Wahl sofort auf der Fläche', () => {
    const { container } = render(<Probeflaeche bereich="messenger" />)
    fireEvent.click(screen.getByText('einstellen'))
    fireEvent.click(screen.getByText(i18n.t('social.wallpaper.minimal')))
    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.apply') }))

    expect(screen.queryByText(i18n.t('social.wallpaper.presets'))).not.toBeInTheDocument()
    expect(container.querySelector('[data-chat-hintergrund="minimal"]')).not.toBeNull()
  })

  it('beginnt jedes Mal bei dem, was gespeichert ist', () => {
    render(<Probeflaeche bereich="ki" />)

    fireEvent.click(screen.getByText('einstellen'))
    fireEvent.click(screen.getByText(i18n.t('social.wallpaper.petrol')))
    // Abbrechen statt übernehmen — die Wahl darf nicht hängenbleiben.
    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.cancel') }))
    fireEvent.click(screen.getByText('einstellen'))

    const kacheln = screen.getAllByRole('button', { pressed: true })
    expect(kacheln).toHaveLength(1)
    expect(kacheln[0]).toHaveTextContent(i18n.t('social.wallpaper.cyber'))
  })

  it('setzt auf den Standard zurück', () => {
    speichereChatHintergrund('ki', { preset: 'midnight', dimLevel: 70 })

    render(<Probeflaeche bereich="ki" />)
    fireEvent.click(screen.getByText('einstellen'))
    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.reset') }))

    expect(ladeChatHintergrund('ki')).toEqual({
      preset: STANDARD_HINTERGRUND.preset,
      customDataUrl: undefined,
      dimLevel: STANDARD_HINTERGRUND.dimLevel,
    })
  })

  it('weist eine Datei ab, die kein Bild ist', () => {
    render(<Probeflaeche bereich="ki" />)
    fireEvent.click(screen.getByText('einstellen'))

    const feld = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(feld, {
      target: { files: [new File(['x'], 'notiz.txt', { type: 'text/plain' })] },
    })

    expect(screen.getByText(i18n.t('social.wallpaper.badType'))).toBeInTheDocument()
    expect(ladeChatHintergrund('ki').preset).toBe(STANDARD_HINTERGRUND.preset)
  })
})

/**
 * So bauen beide Flächen den Einstieg ein: eine Zeile im Blattmenü, das
 * Fenster daneben. Stünde das Fenster **im** Menü, nähme das sich schliessende
 * Menü es mit — der Klick täte dann sichtbar nichts.
 */
describe('Einstieg über das Blattmenü', () => {
  function Probemenue({ bereich }: { bereich: 'messenger' | 'ki' }) {
    const [offen, setzeOffen] = useState(false)
    return (
      <>
        <Blattknopf label="Weitere Einstellungen" titel="Chat">
          {(schliessen) => (
            <Blatteintrag
              label={i18n.t('social.wallpaper.title')}
              onClick={() => {
                schliessen()
                setzeOffen(true)
              }}
            />
          )}
        </Blattknopf>
        <ChatHintergrundDialog bereich={bereich} offen={offen} onOffenChange={setzeOffen} />
      </>
    )
  }

  it('öffnet das Fenster, und das Menü ist danach zu', () => {
    render(<Probemenue bereich="ki" />)
    expect(screen.queryByText(i18n.t('social.wallpaper.presets'))).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Weitere Einstellungen' }))
    fireEvent.click(screen.getByText(i18n.t('social.wallpaper.title')))

    expect(screen.getByText(i18n.t('social.wallpaper.presets'))).toBeInTheDocument()
    // Die Zeile selbst ist weg — das Menü hat zugemacht, das Fenster blieb.
    expect(screen.getAllByText(i18n.t('social.wallpaper.title'))).toHaveLength(1)
  })
})
