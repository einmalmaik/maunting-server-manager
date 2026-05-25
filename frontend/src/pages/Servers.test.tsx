import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Servers } from './Servers'
import * as client from '@/api/client'
import i18n from '@/i18n'
import type { GameInfo } from '@/types'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

vi.mock('@/hooks/useHostInterfaces', () => ({
  useHostInterfaces: () => ({ interfaces: [], defaultBindIp: '' }),
}))

vi.mock('@/hooks/useHasPermission', () => ({
  useHasPermission: () => true,
}))

const GAMES: GameInfo[] = [
  {
    id: 'dayz',
    name: 'DayZ',
    platform: 'linux',
    mod_support: true,
    supports_steam_workshop: true,
    ports: [
      { name: 'game', protocol: 'udp' },
      { name: 'query', protocol: 'udp' },
      { name: 'rcon', protocol: 'tcp' },
    ],
    source: 'native',
  },
  {
    id: 'voice_only',
    name: 'Voice Only',
    platform: 'linux',
    mod_support: false,
    supports_steam_workshop: false,
    ports: [
      { name: 'voice', protocol: 'udp' },
    ],
    source: 'community',
  },
]

function mockApi(games: GameInfo[]) {
  vi.mocked(client.api).mockImplementation(async (path: string) => {
    if (path === '/servers') return [] as any
    if (path === '/system/games') return games as any
    return undefined as any
  })
}

function renderServers() {
  return render(
    <MemoryRouter>
      <Servers />
    </MemoryRouter>,
  )
}

describe('Servers create form — dynamic port fields', () => {
  beforeEach(async () => {
    vi.mocked(client.api).mockReset()
    await i18n.changeLanguage('en')
  })

  it('renders three port inputs for DayZ (game/query/rcon)', async () => {
    mockApi(GAMES)
    renderServers()

    // Warten bis /system/games abgerufen wurde (Mock-Aufruf).
    await waitFor(() => {
      expect(vi.mocked(client.api)).toHaveBeenCalledWith('/system/games')
    })

    // Modal oeffnen — vorher gibt es keine <option>-Elemente.
    const createButtons = screen.getAllByRole('button', { name: /server erstellen|create server/i })
    fireEvent.click(createButtons[0])

    // Default game_type 'conan_exiles_ue5' kennt unser Mock nicht — Fallback rendert.
    // Wir wechseln explizit auf DayZ.
    const selects = screen.getAllByRole('combobox')
    fireEvent.change(selects[0], { target: { value: 'dayz' } })

    const fields = screen.getByTestId('port-fields')
    expect(within(fields).getByTestId('port-input-game')).toBeInTheDocument()
    expect(within(fields).getByTestId('port-input-query')).toBeInTheDocument()
    expect(within(fields).getByTestId('port-input-rcon')).toBeInTheDocument()
  })

  it('renders no port grid for a voice-only blueprint', async () => {
    mockApi(GAMES)
    renderServers()
    await waitFor(() => {
      expect(vi.mocked(client.api)).toHaveBeenCalledWith('/system/games')
    })

    const createButtons = screen.getAllByRole('button', { name: /server erstellen|create server/i })
    fireEvent.click(createButtons[0])

    const selects = screen.getAllByRole('combobox')
    fireEvent.change(selects[0], { target: { value: 'voice_only' } })

    // 'voice' ist NICHT im roleToField-Mapping enthalten (das Mapping schreibt
    // nur in server.game_port/query_port/rcon_port). Folge: bei einer reinen
    // Voice-Blueprint wird das gesamte Port-Field-Grid versteckt (KISS).
    expect(screen.queryByTestId('port-fields')).not.toBeInTheDocument()
  })
})
