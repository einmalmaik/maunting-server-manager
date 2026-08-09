/**
 * Der Dialog "Spiel / Blueprint wechseln" zeigte ein leeres Dropdown.
 *
 * Die Ursache war eine falsche Annahme über die Antwortform:
 *
 *     api<BlueprintListEntry[]>("/blueprints")
 *       .then((data) => {
 *         const list = Array.isArray(data) ? data : []   // immer []
 *
 * `GET /blueprints` antwortet mit `{ blueprints: [...] }`. Der Ausdruck traf
 * also **immer** den leeren Fall — ohne Fehler, ohne Hinweis. Man sah eine
 * leere Auswahl und hielt sie für die Wahrheit.
 *
 * Genau das prüft dieser Test: die echte Antwortform muss Einträge ergeben.
 */
import { render, screen, waitFor } from '@testing-library/react'
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

describe('SwitchBlueprintDialog', () => {
  beforeEach(() => {
    vi.mocked(client.api).mockReset()
  })

  it('füllt das Dropdown aus der tatsächlichen Antwortform', async () => {
    vi.mocked(client.api).mockResolvedValue({
      blueprints: [
        blueprint('minecraft_forge', 'Minecraft Forge'),
        blueprint('minecraft_vanilla', 'Minecraft Vanilla'),
      ],
    })

    render(
      <SwitchBlueprintDialog
        open
        onClose={() => {}}
        server={server}
        onSwitched={() => {}}
      />,
    )

    await waitFor(() => {
      expect(screen.getByRole('combobox')).toBeInTheDocument()
    })
    const optionen = screen.getAllByRole('option')
    expect(optionen).toHaveLength(2)
    expect(optionen.map((o) => o.textContent)).toEqual([
      'Minecraft Forge (Minecraft)',
      'Minecraft Vanilla (Minecraft)',
    ])
  })

  it('wählt vor, was nicht der aktuelle Blueprint ist', async () => {
    // Sonst steht der Knopf auf "schon aktiv" und der Benutzer muss erst
    // umstellen, bevor er umstellen kann.
    vi.mocked(client.api).mockResolvedValue({
      blueprints: [
        blueprint('minecraft_forge', 'Minecraft Forge'),
        blueprint('minecraft_vanilla', 'Minecraft Vanilla'),
      ],
    })

    render(
      <SwitchBlueprintDialog
        open
        onClose={() => {}}
        server={server}
        onSwitched={() => {}}
      />,
    )

    await waitFor(() => {
      expect(screen.getByRole('combobox')).toHaveValue('minecraft_vanilla')
    })
  })

  it('bleibt bei einer leeren Liste bedienbar', async () => {
    vi.mocked(client.api).mockResolvedValue({ blueprints: [] })

    render(
      <SwitchBlueprintDialog
        open
        onClose={() => {}}
        server={server}
        onSwitched={() => {}}
      />,
    )

    await waitFor(() => {
      expect(screen.getByRole('combobox')).toBeInTheDocument()
    })
    expect(screen.queryAllByRole('option')).toHaveLength(0)
  })
})
