/**
 * Shared WebSocket-Mock fuer Frontend-Tests.
 *
 * jsdom liefert kein natives WebSocket. Wir stub'en eine minimale Klasse,
 * die den Konstruktor und Lifecycle protokolliert. Tests koennen ueber
 * FakeWebSocket.instances[i] auf Instanzen zugreifen und mit simulateOpen /
 * simulateMessage / simulateClose Server-Frames simulieren.
 *
 * Wird sowohl von useWebSocket.test.ts als auch von
 * ServerConsolePanel.test.tsx verwendet.
 */
export class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  url: string
  readyState: number = FakeWebSocket.CONNECTING
  sent: string[] = []
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(code?: number): void {
    if (this.readyState === FakeWebSocket.CLOSED) return
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code: code ?? 1000, reason: '' } as CloseEvent)
  }

  // Test-Helpers
  simulateOpen(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.({} as Event)
  }

  simulateMessage(data: unknown): void {
    this.onmessage?.({
      data: typeof data === 'string' ? data : JSON.stringify(data),
    } as MessageEvent)
  }

  simulateClose(code = 1006): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code, reason: '' } as CloseEvent)
  }
}

/**
 * Installiert die FakeWebSocket-Klasse als globaler WebSocket und stellt
 * sicher, dass vor jedem Test instances[] leer ist und der Original-WebSocket
 * danach wieder hergestellt wird.
 */
export function installFakeWebSocket(): {
  restore: () => void
  instances: FakeWebSocket[]
} {
  const original = (globalThis as { WebSocket?: typeof WebSocket }).WebSocket
  ;(globalThis as { WebSocket?: unknown }).WebSocket =
    FakeWebSocket as unknown as typeof WebSocket
  FakeWebSocket.instances = []
  return {
    instances: FakeWebSocket.instances,
    restore: () => {
      if (original) {
        ;(globalThis as { WebSocket?: unknown }).WebSocket = original
      } else {
        delete (globalThis as { WebSocket?: unknown }).WebSocket
      }
    },
  }
}
