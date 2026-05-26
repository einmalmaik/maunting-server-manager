import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ServerConsolePanel } from './ServerConsolePanel'
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
    FakeEventSource.instances = []
    originalEventSource = (globalThis as { EventSource?: typeof EventSource }).EventSource
    ;(globalThis as { EventSource?: unknown }).EventSource = FakeEventSource as unknown as typeof EventSource
    fetchSpy = vi.spyOn(global, 'fetch')
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

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled())
    const [url, options] = fetchSpy.mock.calls[0] as [string, RequestInit]
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
})
