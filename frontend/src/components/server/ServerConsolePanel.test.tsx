import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { displayConsoleLine, ServerConsolePanel } from './ServerConsolePanel'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { useToastStore } from '@/stores/toastStore'
import { FakeWebSocket, installFakeWebSocket } from '@/test/fakeWebSocket'
import type { MePermissions } from '@/types/permissions'

const ownerMe: MePermissions = {
  is_owner: true,
  role_id: null,
  role_name: null,
  global_keys: [],
  server_keys: {},
}

const readOnlyMe: MePermissions = {
  is_owner: false,
  role_id: 2,
  role_name: 'user',
  global_keys: [],
  server_keys: {
    '42': ['server.console.read'],
  },
}

function setMe(me: MePermissions | null) {
  usePermissionsStore.setState({ me, isLoading: false })
}

describe('ServerConsolePanel', () => {
  let restoreWebSocket: () => void
  let wsInstances: FakeWebSocket[]
  let fetchSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    i18n.changeLanguage('en')
    setMe(null)
    useToastStore.setState({ toasts: [] })
    localStorage.clear()
    const fake = installFakeWebSocket()
    restoreWebSocket = fake.restore
    wsInstances = fake.instances
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
    fetchSpy = vi.spyOn(global, 'fetch')
    fetchSpy.mockResolvedValue({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: () => Promise.resolve({ time_format: '24h' }),
      text: () => Promise.resolve('{"time_format":"24h"}'),
    } as Response)
  })

  afterEach(() => {
    restoreWebSocket()
    fetchSpy.mockRestore()
  })

  it('opens a WebSocket against the WS endpoint', () => {
    setMe(ownerMe)
    render(<ServerConsolePanel serverId={42} />)
    expect(wsInstances).toHaveLength(1)
    const url = wsInstances[0].url
    expect(url).toMatch(/\/api\/servers\/42\/console\/ws$/)
  })

  it('hides the input field for users without server.console.write', () => {
    setMe(readOnlyMe)
    render(<ServerConsolePanel serverId={42} />)
    expect(screen.queryByTestId('console-input')).toBeNull()
    expect(screen.queryByTestId('console-send')).toBeNull()
  })

  it('shows the input field for the owner', () => {
    setMe(ownerMe)
    render(<ServerConsolePanel serverId={42} />)
    expect(screen.getByTestId('console-input')).toBeInTheDocument()
    expect(screen.getByTestId('console-send')).toBeInTheDocument()
  })

  it('POSTs the input line to /api/servers/:id/console/input on submit', async () => {
    fetchSpy.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: () => Promise.resolve({ ok: true }),
      text: () => Promise.resolve('{"ok":true}'),
    } as Response)

    setMe(ownerMe)
    render(<ServerConsolePanel serverId={42} />)
    const input = screen.getByTestId('console-input') as HTMLInputElement
    fireEvent.change(input, { target: { value: '/auth login device' } })
    fireEvent.submit(screen.getByTestId('console-input-form'))

    const calls = fetchSpy.mock.calls as Array<[string, RequestInit]>
    await waitFor(() => expect(calls.some((call) => String(call[0]).includes('/console/input'))).toBe(true))
    const [url, options] = calls.find((call) => String(call[0]).includes('/console/input')) as [string, RequestInit]
    expect(url).toBe('/api/servers/42/console/input')
    expect(options.method).toBe('POST')
    expect(options.credentials).toBe('include')
    expect(JSON.parse(options.body as string)).toEqual({ line: '/auth login device' })
  })

  it('renders incoming WS log lines', async () => {
    setMe(ownerMe)
    render(<ServerConsolePanel serverId={42} />)
    const ws = wsInstances[0]
    act(() => { ws.simulateOpen() })
    act(() => {
      ws.simulateMessage({ text: 'Starting server...', source: 'docker', id: 1 })
      ws.simulateMessage({ text: 'Listening on port 25565', source: 'docker', id: 2 })
    })
    // 50ms flush interval: warte etwas mehr
    await new Promise((r) => setTimeout(r, 100))
    await waitFor(() => {
      expect(screen.getByText('Starting server...')).toBeInTheDocument()
      expect(screen.getByText('Listening on port 25565')).toBeInTheDocument()
    })
  })

  it('renders http links from JSON console frames safely', async () => {
    setMe(ownerMe)
    render(<ServerConsolePanel serverId={42} />)
    const ws = wsInstances[0]
    ws.simulateOpen()
    ws.simulateMessage({
      text: 'Open https://example.invalid/auth.',
      timestamp: '2026-06-01T12:00:00Z',
      source: 'docker',
      id: 1,
    })

    await waitFor(() => {
      const link = screen.getByRole('link', { name: 'https://example.invalid/auth' })
      expect(link).toHaveAttribute('target', '_blank')
      expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    })
  })


  it('copies visible console lines', async () => {
    setMe(ownerMe)
    render(<ServerConsolePanel serverId={42} />)
    const ws = wsInstances[0]
    ws.simulateOpen()
    ws.simulateMessage({ text: 'first line', source: 'docker', id: 1 })
    ws.simulateMessage({ text: '[MSM] Container msm-srv-42 gestartet', source: 'msm', id: 2 })

    await waitFor(() => {
      expect(screen.getByText('first line')).toBeInTheDocument()
      expect(screen.getByText('[MSM] Container msm-srv-42 started')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /^copy$/i }))
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      'first line\n[MSM] Container msm-srv-42 started',
    )
  })

  it('translates known MSM panel console lines when language is english', () => {
    expect(displayConsoleLine('[MSM] Container msm-srv-42 gestartet', 'en')).toBe('[MSM] Container msm-srv-42 started')
    expect(displayConsoleLine('[MSM] Hinweis: Pull für ghcr.io/demo:latest fehlgeschlagen, nutze lokales Image', 'en')).toBe('[MSM] Notice: Pull for ghcr.io/demo:latest failed, using local image')
    expect(displayConsoleLine('[MSM] Container msm-srv-42 gestartet', 'de')).toBe('[MSM] Container msm-srv-42 gestartet')
  })

  it('renders color classes for ERROR/player/ANSI lines via WS', async () => {
    setMe(ownerMe)
    const { container } = render(<ServerConsolePanel serverId={42} />)
    const ws = wsInstances[0]
    ws.simulateOpen()
    ws.simulateMessage({ text: 'FATAL crash', source: 'docker', id: 1 })
    ws.simulateMessage({ text: 'Player bar joined the game', source: 'docker', id: 2 })
    ws.simulateMessage({ text: '\x1b[33mANSI warn\x1b[0m', source: 'docker', id: 3 })
    await waitFor(() => {
      const divs = container.querySelectorAll('div.font-mono > div')
      expect(Array.from(divs).some(d => d.className.includes('text-status-destructive') && d.textContent?.includes('FATAL'))).toBe(true)
      expect(Array.from(divs).some(d => d.className.includes('text-status-success') && d.textContent?.includes('joined'))).toBe(true)
      expect(Array.from(divs).some(d => d.className.includes('text-status-warning') && d.textContent?.includes('ANSI'))).toBe(true)
    })
  })

  it('caps console logs at 2000 lines (invariant: append >2000 keeps len <=2000)', async () => {
    setMe(ownerMe)
    const { container } = render(<ServerConsolePanel serverId={42} />)
    const ws = wsInstances[0]
    ws.simulateOpen()
    for (let i = 0; i < 2100; i++) {
      ws.simulateMessage({ text: `log line ${i}`, source: 'docker', id: i + 1 })
    }
    await waitFor(() => {
      const divs = container.querySelectorAll('div.font-mono > div')
      expect(divs.length).toBeLessThanOrEqual(2000)
      expect(divs[divs.length - 1]?.textContent).toContain('log line 2099')
    })
  })

  it('reconnects with last_id query param after disconnect', async () => {
    vi.useFakeTimers()
    try {
      setMe(ownerMe)
      render(<ServerConsolePanel serverId={42} />)
      const ws1 = wsInstances[0]
      ws1.simulateOpen()
      ws1.simulateMessage({ text: 'first', source: 'docker', id: 5 })
      ws1.simulateMessage({ text: 'second', source: 'docker', id: 6 })

      // Disconnect triggert Reconnect
      await act(async () => {
        ws1.simulateClose(1006)
        await vi.advanceTimersByTimeAsync(1100) // Backoff 1s
      })

      // Zweite WS-Instanz muss existieren und last_id=6 enthalten.
      const ws2 = wsInstances[1]
      expect(ws2).toBeDefined()
      expect(ws2.url).toContain('last_id=6')
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not include last_id query param on first connect', () => {
    setMe(ownerMe)
    render(<ServerConsolePanel serverId={42} />)
    const ws = wsInstances[0]
    expect(ws.url).not.toContain('last_id')
  })
})

