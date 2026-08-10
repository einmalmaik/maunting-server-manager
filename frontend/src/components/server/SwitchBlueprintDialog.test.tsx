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
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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

function zeichnen() {
  render(
    <SwitchBlueprintDialog open onClose={() => {}} server={server} onSwitched={() => {}} />,
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
})
