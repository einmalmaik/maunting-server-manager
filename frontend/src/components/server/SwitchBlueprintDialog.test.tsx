/**
 * Der Dialog "Spiel / Blueprint wechseln" hatte zwei Fehler hintereinander.
 *
 * **Erst blieb die Liste leer.** Die Antwortform war falsch angenommen:
 *
 *     api<BlueprintListEntry[]>("/blueprints")
 *       .then((data) => {
 *         const list = Array.isArray(data) ? data : []   // immer []
 *
 * `GET /blueprints` antwortet mit `{ blueprints: [...] }`. Der Ausdruck traf
 * also **immer** den leeren Fall — ohne Fehler, ohne Hinweis. Man sah eine
 * leere Auswahl und hielt sie für die Wahrheit.
 *
 * **Dann klappte sie nach oben.** Ein natives `<select>` mit 27 Einträgen ist
 * höher als der Platz unter dem Feld, und der Browser entscheidet die Richtung
 * selbst. Im mittig stehenden Dialog klappte es nach oben. Seitdem benutzt der
 * Dialog `Dropdown` — dieselbe Auswahl wie der Rest des Panels, die nach unten
 * aufklappt und ihre Höhe selbst begrenzt.
 *
 * Beides prüft dieser Test: die echte Antwortform muss Einträge ergeben, und
 * die Einträge müssen über die Panel-Auswahl erreichbar sein.
 *
 * **Und er ging nur über den Abbrechen-Knopf wieder zu.** Escape tat nichts,
 * der Fokus blieb beim Öffnen hinter der Seite. Seitdem trägt der Dialog
 * dieselbe Tastaturbehandlung wie ResourceEditorDialog.tsx im selben Ordner:
 * Escape schließt (nicht während des Wechsels), der Fokus startet auf
 * "Abbrechen" und kehrt beim Schließen zum Auslöser zurück. Ein Escape, das
 * nur die aufgeklappte Blueprint-Auswahl schließen soll, darf den Dialog
 * dabei nicht mitnehmen.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '@/api/client'
import type { BlueprintListEntry, Server } from '@/types'
import { SwitchBlueprintDialog } from './SwitchBlueprintDialog'

vi.mock('@/api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/client')>()
  return { ...original, api: vi.fn() }
})

const blueprint = (id: string, name: string): BlueprintListEntry => ({
  id,
  name,
  category: 'Minecraft',
  author: null,
  description: null,
  origin: 'native',
  version: 1,
  image: 'itzg/minecraft-server:latest',
  source_type: 'dockerOnly',
  supports_mods: true,
  supports_steam_workshop: false,
  mod_injection: 'none',
  ports: [],
})

const server = {
  id: 101,
  name: 'MaickCraft Public',
  game_type: 'minecraft_forge',
  status: 'stopped',
} as unknown as Server

const zweiBlueprints = {
  blueprints: [
    blueprint('minecraft_forge', 'Minecraft Forge'),
    blueprint('minecraft_vanilla', 'Minecraft Vanilla'),
  ],
}

function zeichnen(onClose: () => void = () => {}) {
  return render(
    <SwitchBlueprintDialog open onClose={onClose} server={server} onSwitched={() => {}} />,
  )
}

/**
 * Der Auswahlknopf des Dropdowns — nicht der "Spiel wechseln"-Knopf.
 *
 * Bewusst ueber die Testkennung und nicht ueber die Beschriftung: die Suite
 * laeuft auf Englisch, der Dialog schreibt Deutsch als Rueckfalltext. Ein Test,
 * der an der Beschriftung haengt, prueft dann die Sprache statt das Verhalten.
 */
function auswahlKnopf() {
  return screen.getByTestId('switch-blueprint-select')
}

/**
 * Die beiden Knöpfe der Fußzeile, ohne den Auswahlknopf des Dropdowns — auch
 * hier über die Struktur statt über die Beschriftung, aus demselben Grund.
 * Reihenfolge im Dialog: Abbrechen, dann Wechseln.
 */
function fusszeilenKnoepfe() {
  return within(screen.getByRole('dialog'))
    .getAllByRole('button')
    .filter((knopf) => knopf.getAttribute('data-testid') !== 'switch-blueprint-select')
}

describe('SwitchBlueprintDialog', () => {
  beforeEach(() => {
    vi.mocked(client.api).mockReset()
  })

  it('füllt die Auswahl aus der tatsächlichen Antwortform', async () => {
    vi.mocked(client.api).mockResolvedValue(zweiBlueprints)
    zeichnen()

    await waitFor(() => expect(auswahlKnopf()).toBeEnabled())
    fireEvent.click(auswahlKnopf())

    const optionen = await screen.findAllByRole('option')
    expect(optionen.map((o) => o.textContent)).toEqual([
      'Minecraft Forge (Minecraft)',
      'Minecraft Vanilla (Minecraft)',
    ])
  })

  it('wählt vor, was nicht der aktuelle Blueprint ist', async () => {
    // Sonst steht der Knopf auf "schon aktiv" und der Benutzer muss erst
    // umstellen, bevor er umstellen kann.
    vi.mocked(client.api).mockResolvedValue(zweiBlueprints)
    zeichnen()

    await waitFor(() => {
      expect(auswahlKnopf()).toHaveTextContent('Minecraft Vanilla (Minecraft)')
    })
  })

  it('bleibt bei einer leeren Liste bedienbar', async () => {
    vi.mocked(client.api).mockResolvedValue({ blueprints: [] })
    zeichnen()

    await waitFor(() => expect(auswahlKnopf()).toBeInTheDocument())
    fireEvent.click(auswahlKnopf())
    expect(screen.queryAllByRole('option')).toHaveLength(0)
  })

  it('meldet sich als Dialog', async () => {
    vi.mocked(client.api).mockResolvedValue(zweiBlueprints)
    zeichnen()

    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    await waitFor(() => expect(auswahlKnopf()).toBeEnabled())
  })

  it('schließt bei Escape', async () => {
    vi.mocked(client.api).mockResolvedValue(zweiBlueprints)
    const schliessen = vi.fn()
    zeichnen(schliessen)

    await waitFor(() => expect(auswahlKnopf()).toBeEnabled())
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(schliessen).toHaveBeenCalledTimes(1)
  })

  it('setzt den Fokus beim Öffnen auf Abbrechen und gibt ihn beim Schließen zurück', async () => {
    vi.mocked(client.api).mockResolvedValue(zweiBlueprints)
    const ausloeser = document.createElement('button')
    document.body.appendChild(ausloeser)
    ausloeser.focus()

    const { rerender } = zeichnen()
    await waitFor(() => expect(fusszeilenKnoepfe()[0]).toHaveFocus())

    rerender(
      <SwitchBlueprintDialog
        open={false}
        onClose={() => {}}
        server={server}
        onSwitched={() => {}}
      />,
    )
    expect(ausloeser).toHaveFocus()

    ausloeser.remove()
  })

  it('lässt Tab nicht aus dem Dialog fallen', async () => {
    vi.mocked(client.api).mockResolvedValue(zweiBlueprints)
    zeichnen()

    await waitFor(() => expect(auswahlKnopf()).toBeEnabled())
    const wechseln = fusszeilenKnoepfe()[1]
    wechseln.focus()
    fireEvent.keyDown(window, { key: 'Tab' })

    expect(auswahlKnopf()).toHaveFocus()
  })

  it('schließt nicht bei Escape, während der Wechsel läuft', async () => {
    // Der zweite Aufruf ist der Wechsel selbst; er bleibt hängen, damit der
    // Dialog im Zustand "wird gewechselt" stehen bleibt.
    vi.mocked(client.api).mockImplementation(((pfad: string) =>
      pfad === '/blueprints'
        ? Promise.resolve(zweiBlueprints)
        : new Promise(() => {})) as typeof client.api)
    const schliessen = vi.fn()
    zeichnen(schliessen)

    await waitFor(() => expect(auswahlKnopf()).toBeEnabled())
    fireEvent.click(fusszeilenKnoepfe()[1])
    await waitFor(() => expect(fusszeilenKnoepfe()[0]).toBeDisabled())

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(schliessen).not.toHaveBeenCalled()
  })

  it('nimmt bei Escape aus der offenen Auswahl nur die Liste, nicht den Dialog', async () => {
    vi.mocked(client.api).mockResolvedValue(zweiBlueprints)
    const schliessen = vi.fn()
    zeichnen(schliessen)

    await waitFor(() => expect(auswahlKnopf()).toBeEnabled())
    fireEvent.click(auswahlKnopf())
    const optionen = await screen.findAllByRole('option')

    fireEvent.keyDown(optionen[0], { key: 'Escape' })

    await waitFor(() => expect(screen.queryAllByRole('option')).toHaveLength(0))
    expect(schliessen).not.toHaveBeenCalled()
  })
})
