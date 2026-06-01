import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { displayConsoleLine, ServerConsolePanel } from './ServerConsolePanel'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { useToastStore } from '@/stores/toastStore'
import type { MePermissions } from '@/types/permissions'

/**
 * jsdom liefert keinen EventSource. Wir stub'en eine minimale Klasse, die
 * den Konstruktor protokolliert — fuer den Read-Pfad reicht das, da die Tests
 * den stdin-Pfad fokussieren (das ist der security-kritische Teil).
 */
class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  listeners: Record<string, (ev: MessageEvent) => void> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(name: string, fn: (ev: MessageEvent) => void) {
    this.listeners[name] = fn
  }

  close() {
    // no-op
  }
}

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
    // Nur read, kein write. So sieht der typische Spectator-User aus.
    '42': ['server.console.read'],
  },
}

function setMe(me: MePermissions | null) {
  usePermissionsStore.setState({ me, isLoading: false })
}

describe('ServerConsolePanel', () => {
  let originalEventSource: typeof EventSource | undefined
  let fetchSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    i18n.changeLanguage('en')
    setMe(null)
    useToastStore.setState({ toasts: [] })
    localStorage.clear()
    FakeEventSource.instances = []
    originalEventSource = (globalThis as { EventSource?: typeof EventSource }).EventSource
    ;(globalThis as { EventSource?: unknown }).EventSource = FakeEventSource as unknown as typeof EventSource
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
    ;(globalThis as { EventSource?: unknown }).EventSource = originalEventSource
    fetchSpy.mockRestore()
  })

  it('opens an EventSource against the SSE stream endpoint', () => {
    setMe(ownerMe)
    render(<ServerConsolePanel serverId={42} />)
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(FakeEventSource.instances[0].url).toBe('/api/servers/42/console/stream')
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

  it('renders incoming SSE log lines', async () => {
    setMe(ownerMe)
    render(<ServerConsolePanel serverId={42} />)
    const es = FakeEventSource.instances[0]
    es.onmessage?.({ data: 'Starting server...' } as MessageEvent)
    es.onmessage?.({ data: 'Listening on port 25565' } as MessageEvent)
    await waitFor(() => {
      expect(screen.getByText('Starting server...')).toBeInTheDocument()
      expect(screen.getByText('Listening on port 25565')).toBeInTheDocument()
    })
  })

  it('renders http links from JSON console frames safely', async () => {
    setMe(ownerMe)
    render(<ServerConsolePanel serverId={42} />)
    const es = FakeEventSource.instances[0]
    es.onmessage?.({
      data: JSON.stringify({
        line: 'Open https://example.invalid/auth.',
        timestamp: '2026-06-01T12:00:00Z',
        source: 'docker',
      }),
      lastEventId: '1',
    } as MessageEvent)

    await waitFor(() => {
      const link = screen.getByRole('link', { name: 'https://example.invalid/auth' })
      expect(link).toHaveAttribute('target', '_blank')
      expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    })
  })

  it('keeps the console cleared after remount while showing new lines', async () => {
    setMe(ownerMe)
    const { unmount } = render(<ServerConsolePanel serverId={42} />)
    let es = FakeEventSource.instances[0]
    es.onmessage?.({ data: 'old line 1' } as MessageEvent)
    es.onmessage?.({ data: 'old line 2' } as MessageEvent)
    await waitFor(() => {
      expect(screen.getByText('old line 1')).toBeInTheDocument()
      expect(screen.getByText('old line 2')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /clear/i }))
    await waitFor(() => {
      expect(screen.queryByText('old line 1')).toBeNull()
      expect(screen.queryByText('old line 2')).toBeNull()
      expect(screen.getByText('No logs available yet.')).toBeInTheDocument()
    })

    unmount()
    render(<ServerConsolePanel serverId={42} />)
    es = FakeEventSource.instances[FakeEventSource.instances.length - 1]
    expect(es.url).toBe('/api/servers/42/console/stream?after=2')
    es.onmessage?.({ data: 'old line 1', lastEventId: '1' } as MessageEvent)
    es.onmessage?.({ data: 'old line 2', lastEventId: '2' } as MessageEvent)
    es.onmessage?.({ data: 'new line after clear', lastEventId: '3' } as MessageEvent)

    await waitFor(() => {
      expect(screen.queryByText('old line 1')).toBeNull()
      expect(screen.queryByText('old line 2')).toBeNull()
      expect(screen.getByText('new line after clear')).toBeInTheDocument()
    })
  })

  it('drops buffered lines that arrived just before clear was clicked', async () => {
    setMe(ownerMe)
    render(<ServerConsolePanel serverId={42} />)
    const es = FakeEventSource.instances[0]
    es.onmessage?.({ data: 'buffered old line' } as MessageEvent)

    fireEvent.click(screen.getByRole('button', { name: /clear/i }))
    await new Promise((resolve) => setTimeout(resolve, 80))
    expect(screen.queryByText('buffered old line')).toBeNull()

    es.onmessage?.({ data: 'stale line from old stream' } as MessageEvent)
    expect(screen.queryByText('stale line from old stream')).toBeNull()

    const nextEs = FakeEventSource.instances[FakeEventSource.instances.length - 1]
    nextEs.onmessage?.({ data: 'fresh line' } as MessageEvent)
    await waitFor(() => {
      expect(screen.getByText('fresh line')).toBeInTheDocument()
    })
  })

  it('copies visible console lines', async () => {
    setMe(ownerMe)
    render(<ServerConsolePanel serverId={42} />)
    const es = FakeEventSource.instances[0]
    es.onmessage?.({ data: 'first line' } as MessageEvent)
    es.onmessage?.({ data: '[MSM] Container msm-srv-42 gestartet' } as MessageEvent)

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

  // === Neue Tests für zentrale colorizeOutput (Coverage für alle Töne + Player + ANSI per review/tests.md) ===
  // (Direct require skipped for ESM/vitest compat; render assertions below cover classification + DNA classes + ANSI via onmessage)
  it('renders color classes for ERROR/player/ANSI lines via SSE', async () => {
    setMe(ownerMe)
    const { container } = render(<ServerConsolePanel serverId={42} />)
    const es = FakeEventSource.instances[0]
    es.onmessage?.({ data: 'FATAL crash' } as MessageEvent)
    es.onmessage?.({ data: 'Player bar joined the game' } as MessageEvent)
    es.onmessage?.({ data: '\x1b[33mANSI warn\x1b[0m' } as MessageEvent)
    await waitFor(() => {
      const divs = container.querySelectorAll('div.font-mono > div')
      // At least the 3 fed lines present with correct DNA classes
      expect(Array.from(divs).some(d => d.className.includes('text-status-destructive') && d.textContent?.includes('FATAL'))).toBe(true)
      expect(Array.from(divs).some(d => d.className.includes('text-status-success') && d.textContent?.includes('joined'))).toBe(true)
      expect(Array.from(divs).some(d => d.className.includes('text-status-warning') && d.textContent?.includes('ANSI'))).toBe(true)
    })
  })

  it('caps console logs at 2000 lines (invariant: append >2000 keeps len <=2000)', async () => {
    setMe(ownerMe)
    const { container } = render(<ServerConsolePanel serverId={42} />)
    const es = FakeEventSource.instances[0]
    // Fire >2000 lines to exercise the KISS slice(-2000) cap in onmessage (prevents unbounded state per review)
    for (let i = 0; i < 2100; i++) {
      es.onmessage?.({ data: `log line ${i}` } as MessageEvent)
    }
    await waitFor(() => {
      const divs = container.querySelectorAll('div.font-mono > div')
      // Invariant: never more than cap in rendered output (state + slice protects memory/re-renders)
      expect(divs.length).toBeLessThanOrEqual(2000)
      // Last line should be from the tail (most recent)
      expect(divs[divs.length - 1]?.textContent).toContain('log line 2099')
    })
  })
})
