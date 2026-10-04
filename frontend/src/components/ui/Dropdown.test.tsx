/**
 * Aufklapprichtung und Höhe der Panel-Auswahl.
 *
 * Gemeldet wurde: "Das Dropdown-Menü geht nach oben anstatt nach unten." Das
 * betraf ein natives `<select>` — dort entscheidet der Browser die Richtung
 * selbst, und bei einer Liste, die höher ist als der Platz unter dem Feld,
 * klappt er nach oben. In einem mittig stehenden Dialog liest sich das wie ein
 * Fehler.
 *
 * `Dropdown` entscheidet selbst. Die Regel ist: **nach unten**, außer unten ist
 * wirklich kein Platz und oben ist mehr. Und die Höhe folgt dem gemessenen
 * Platz, damit die Liste in keiner der beiden Richtungen aus dem Fenster läuft.
 *
 * jsdom liefert für `getBoundingClientRect()` grundsätzlich Nullen — ohne den
 * Stub unten misst die Komponente ein Feld der Größe 0 am Ursprung, und der
 * Test prüfte nichts.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Dropdown } from './Dropdown'
import { Dialog, DialogContent } from '@/Singra/UI/Dialog'

const OPTIONEN = [
  { value: 'a', label: 'Minecraft Forge' },
  { value: 'b', label: 'Minecraft Vanilla' },
]

/** jsdom-Fensterhöhe; die Komponente rechnet gegen `window.innerHeight`. */
const FENSTER_HOEHE = window.innerHeight

function feldBei({ top, bottom }: { top: number; bottom: number }) {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    top,
    bottom,
    left: 100,
    right: 300,
    width: 200,
    height: bottom - top,
    x: 100,
    y: top,
    toJSON: () => ({}),
  } as DOMRect)
}

function oeffnen() {
  render(
    <Dropdown data-testid="auswahl" value="a" onChange={() => {}} options={OPTIONEN} />,
  )
  fireEvent.click(screen.getByTestId('auswahl'))
  const menue = screen.getByRole('listbox').parentElement
  if (!menue) throw new Error('Menü nicht gefunden')
  return menue as HTMLElement
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Dropdown', () => {
  it('klappt nach unten, wenn darunter Platz ist', () => {
    feldBei({ top: 80, bottom: 120 })
    const menue = oeffnen()

    // Nach unten heißt: an der Unterkante des Feldes verankert, nicht an der
    // Oberkante des Fensters.
    expect(menue.style.top).toBe('128px')
    expect(menue.style.bottom).toBe('')
  })

  it('weicht nach oben aus, wenn unten kein Platz mehr ist', () => {
    // Feld ganz unten im Fenster: unter dem Feld bleiben ein paar Pixel,
    // darüber fast das ganze Fenster.
    feldBei({ top: FENSTER_HOEHE - 60, bottom: FENSTER_HOEHE - 20 })
    const menue = oeffnen()

    expect(menue.style.bottom).toBe(`${FENSTER_HOEHE - (FENSTER_HOEHE - 60) + 8}px`)
    expect(menue.style.top).toBe('')
  })

  it('begrenzt die Höhe auf den gemessenen Platz', () => {
    // 240px unter dem Feld: mehr als das Minimum (180), weniger als das
    // Maximum (320). Die Liste darf genau diesen Platz nutzen — vorher stand
    // hier ein fest verdrahtetes `max-h-64`, das die Messung überstimmte.
    feldBei({ top: FENSTER_HOEHE - 296, bottom: FENSTER_HOEHE - 256 })
    const menue = oeffnen()

    expect(menue.style.maxHeight).toBe('240px')
  })

  it('deckelt die Höhe auch bei viel Platz', () => {
    feldBei({ top: 10, bottom: 50 })
    const menue = oeffnen()

    expect(menue.style.maxHeight).toBe('320px')
  })

  /**
   * Ohne Maus war die Auswahl bisher nicht bedienbar: der Auslöser ließ sich
   * öffnen, danach passierte bei jedem Pfeiltastendruck nichts. Weil das Menü
   * per Portal an `document.body` hängt, springt Tab vom Auslöser auch nicht in
   * die Liste, sondern zum nächsten Feld des Formulars.
   */
  it('führt den Fokus mit den Pfeiltasten durch die Liste und gibt ihn bei Escape zurück', async () => {
    feldBei({ top: 80, bottom: 120 })
    const auswahl = vi.fn()
    render(
      <Dropdown
        data-testid="auswahl"
        value="a"
        onChange={auswahl}
        options={[...OPTIONEN, { value: 'c', label: 'Gesperrt', disabled: true }]}
      />,
    )

    const ausloeser = screen.getByTestId('auswahl')
    ausloeser.focus()
    fireEvent.click(ausloeser)

    const forge = screen.getByRole('option', { name: /Minecraft Forge/ })
    const vanilla = screen.getByRole('option', { name: /Minecraft Vanilla/ })
    // Beim Öffnen landet der Fokus auf der gewählten Option, nicht auf der
    // ersten — sonst verliert man beim Öffnen seinen aktuellen Wert aus dem
    // Blick.
    await waitFor(() => expect(forge).toHaveFocus())

    fireEvent.keyDown(document, { key: 'ArrowDown' })
    expect(vanilla).toHaveFocus()
    // Gesperrte Optionen werden übersprungen, deshalb geht es von der zweiten
    // wieder auf die erste zurück.
    fireEvent.keyDown(document, { key: 'ArrowDown' })
    expect(forge).toHaveFocus()
    fireEvent.keyDown(document, { key: 'End' })
    expect(vanilla).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Home' })
    expect(forge).toHaveFocus()
    fireEvent.keyDown(document, { key: 'ArrowUp' })
    expect(vanilla).toHaveFocus()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(ausloeser).toHaveFocus()
    expect(auswahl).not.toHaveBeenCalled()
  })

  it('wählt die fokussierte Option mit Enter aus', async () => {
    feldBei({ top: 80, bottom: 120 })
    const auswahl = vi.fn()
    render(<Dropdown data-testid="auswahl" value="a" onChange={auswahl} options={OPTIONEN} />)

    fireEvent.click(screen.getByTestId('auswahl'))
    const forge = screen.getByRole('option', { name: /Minecraft Forge/ })
    await waitFor(() => expect(forge).toHaveFocus())

    fireEvent.keyDown(document, { key: 'ArrowDown' })
    // Der Klick ist das, was ein Browser beim Enter auf einem fokussierten
    // <button> auslöst.
    fireEvent.click(screen.getByRole('option', { name: /Minecraft Vanilla/ }))

    expect(auswahl).toHaveBeenCalledWith('b')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })
})

describe('Dropdown in einem Dialog', () => {
  it('schließt mit Escape nur die Liste, nicht den Dialog darunter', async () => {
    // Bis 02.10.2026 hörte der Dialog Escape am Dokument und hielt sich für
    // den obersten: Escape schloss die Liste und den Dialog mitsamt Eingaben.
    await waitFor(() => expect(window.history.state?.msmTiefe ?? 0).toBe(0))
    const dialogZu = vi.fn()
    render(
      <Dialog open onOpenChange={dialogZu}>
        <DialogContent>
          <Dropdown aria-label="Spiel" value="a" onChange={() => {}} options={OPTIONEN} />
        </DialogContent>
      </Dialog>,
    )
    const knopf = screen.getByRole('button', { name: 'Spiel' })
    fireEvent.click(knopf)
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    fireEvent.keyDown(knopf, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(dialogZu).not.toHaveBeenCalled()
  })
})

describe('Dropdown in der App-Sprache', () => {
  afterEach(async () => {
    const { default: i18n } = await import('@/i18n')
    await i18n.changeLanguage('de')
  })

  it('zeigt Platzhalter, Suche und leere Liste in der App-Sprache', async () => {
    // Bis 03.10.2026 standen „Auswählen“, „Suchen …“ und „Keine Einträge“ fest
    // im Baustein, auch in der englischen App.
    const { default: i18n } = await import('@/i18n')
    await i18n.changeLanguage('en')
    render(<Dropdown aria-label="Spiel" value={null} onChange={() => {}} options={[]} searchable />)
    const knopf = screen.getByRole('button', { name: 'Spiel' })
    expect(knopf).toHaveTextContent(i18n.t('common.auswaehlen'))
    expect(knopf).not.toHaveTextContent('Auswählen')
    fireEvent.click(knopf)
    expect(await screen.findByPlaceholderText(i18n.t('common.suchenPunkte'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('common.keineEintraege'))).toBeInTheDocument()
  })
})

describe('Dropdown Suche und Tastaturnavigation', () => {
  it('filtert Optionen bei Eingabe und wählt mit Enter direkt aus', async () => {
    feldBei({ top: 80, bottom: 120 })
    const auswahl = vi.fn()
    const optionen = [
      { value: 'gpt-4o', label: 'gpt-4o' },
      { value: 'gemini-2.5-flash', label: 'gemini-2.5-flash' },
      { value: 'claude-3-5-sonnet', label: 'claude-3-5-sonnet' },
    ]
    render(
      <Dropdown
        aria-label="Modell"
        value={null}
        onChange={auswahl}
        options={optionen}
        searchable
      />,
    )
    const knopf = screen.getByRole('button', { name: 'Modell' })
    fireEvent.click(knopf)

    const suchfeld = await screen.findByPlaceholderText('Suchen …')
    await waitFor(() => expect(suchfeld).toHaveFocus())

    // Filtern nach "gemini"
    fireEvent.change(suchfeld, { target: { value: 'gemini' } })
    expect(screen.getByRole('option', { name: 'gemini-2.5-flash' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'gpt-4o' })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'claude-3-5-sonnet' })).not.toBeInTheDocument()

    // Enter wählt den ersten passenden Treffer aus
    fireEvent.keyDown(suchfeld, { key: 'Enter' })
    expect(auswahl).toHaveBeenCalledWith('gemini-2.5-flash')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('schließt die Liste bei Escape aus dem Suchfeld und leert die Suche', async () => {
    feldBei({ top: 80, bottom: 120 })
    render(
      <Dropdown
        aria-label="Modell"
        value={null}
        onChange={() => {}}
        options={[{ value: 'a', label: 'Modell Alpha' }, { value: 'b', label: 'Modell Beta' }]}
        searchable
      />,
    )
    const knopf = screen.getByRole('button', { name: 'Modell' })
    fireEvent.click(knopf)

    const suchfeld = await screen.findByPlaceholderText('Suchen …')
    fireEvent.change(suchfeld, { target: { value: 'Alpha' } })
    expect(screen.getByRole('option', { name: 'Modell Alpha' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Modell Beta' })).not.toBeInTheDocument()

    fireEvent.keyDown(suchfeld, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(knopf).toHaveFocus()

    // Erneutes Öffnen zeigt wieder alle Optionen (Suche wurde zurückgesetzt)
    fireEvent.click(knopf)
    expect(await screen.findByRole('option', { name: 'Modell Alpha' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Modell Beta' })).toBeInTheDocument()
  })
})

