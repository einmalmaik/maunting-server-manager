/**
 * Tests fuer useWebSocket.
 *
 * Deckt ab:
 * - Connect, onopen -> status 'live'
 * - onMessage -> raw-Frames landen beim Consumer
 * - Backoff: 1s/2s/5s/10s bei wiederholtem Disconnect
 * - 1008-Close -> 'failed' + onError('rejected'), kein Reconnect
 * - Max-Attempts erreicht -> 'failed' + onError('failed'), kein weiterer Connect
 * - Heartbeat wird gesendet solange WS offen
 * - Cleanup beim Unmount (kein Leak, kein Reconnect)
 * - buildUrl() wird bei jedem Reconnect aufgerufen
 * - send() returnt false wenn nicht offen
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'

import { useWebSocket } from './useWebSocket'
import { FakeWebSocket, installFakeWebSocket } from '@/test/fakeWebSocket'


describe('useWebSocket', () => {
  let restore: () => void
  let instances: FakeWebSocket[]

  beforeEach(() => {
    const fake = installFakeWebSocket()
    restore = fake.restore
    instances = fake.instances
  })

  afterEach(() => {
    restore()
  })

  it('opens a WebSocket and transitions to live on onopen', () => {
    const onStatus = vi.fn()
    const { result } = renderHook(() =>
      useWebSocket({
        buildUrl: () => '/api/test/ws',
        onMessage: () => {},
        onStatusChange: onStatus,
      })
    )

    expect(instances).toHaveLength(1)
    expect(instances[0].url).toMatch(/\/api\/test\/ws$/)
    expect(result.current.status).toBe('connecting')

    act(() => {
      instances[0].simulateOpen()
    })

    expect(result.current.status).toBe('live')
    expect(onStatus).toHaveBeenCalledWith('live')
  })

  it('forwards raw onmessage strings to the consumer', () => {
    const onMessage = vi.fn()
    renderHook(() =>
      useWebSocket({
        buildUrl: () => '/api/test/ws',
        onMessage,
      })
    )
    const ws = instances[0]
    act(() => {
      ws.simulateOpen()
    })
    act(() => {
      ws.simulateMessage('{"id":1,"text":"hello"}')
    })
    expect(onMessage).toHaveBeenCalledWith('{"id":1,"text":"hello"}')
  })

  it('reconnects after disconnect with backoff and marks status reconnecting', async () => {
    vi.useFakeTimers()
    try {
      const onError = vi.fn()
      const onStatus = vi.fn()
      renderHook(() =>
        useWebSocket({
          buildUrl: () => '/api/test/ws',
          onMessage: () => {},
          onError,
          onStatusChange: onStatus,
        })
      )
      const ws1 = instances[0]
      act(() => {
        ws1.simulateOpen()
      })

      act(() => {
        ws1.simulateClose(1006)
      })
      expect(onStatus).toHaveBeenCalledWith('reconnecting')
      expect(onError).toHaveBeenCalledWith('reconnecting', { attempts: 1 })

      // Erster Backoff: 1s.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1100)
      })

      // Zweite WS-Instanz muss existieren.
      expect(instances.length).toBeGreaterThanOrEqual(2)
      const ws2 = instances[1]

      // Zweiter Disconnect, Backoff 2s, dritte Instanz.
      act(() => {
        ws2.simulateClose(1006)
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2100)
      })
      expect(instances.length).toBeGreaterThanOrEqual(3)

      // Dritter Disconnect, Backoff 5s, vierte Instanz.
      const ws3 = instances[2]
      act(() => {
        ws3.simulateClose(1006)
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5100)
      })
      expect(instances.length).toBeGreaterThanOrEqual(4)
    } finally {
      vi.useRealTimers()
    }
  })

  it('1008 close -> failed status, rejected error, no further reconnect', async () => {
    vi.useFakeTimers()
    try {
      const onError = vi.fn()
      const onStatus = vi.fn()
      const { result } = renderHook(() =>
        useWebSocket({
          buildUrl: () => '/api/test/ws',
          onMessage: () => {},
          onError,
          onStatusChange: onStatus,
        })
      )
      act(() => {
        instances[0].simulateOpen()
      })
      act(() => {
        instances[0].simulateClose(1008)
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20_000)
      })
      expect(result.current.status).toBe('failed')
      expect(onError).toHaveBeenCalledWith('rejected', expect.objectContaining({}))
      // Keine neue Instanz nach 1008.
      expect(instances).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('maxAttempts reached -> failed status, failed error, no further reconnect', async () => {
    vi.useFakeTimers()
    try {
      const onError = vi.fn()
      const onStatus = vi.fn()
      const { result } = renderHook(() =>
        useWebSocket({
          buildUrl: () => '/api/test/ws',
          onMessage: () => {},
          onError,
          onStatusChange: onStatus,
          reconnect: { maxAttempts: 3, delaysMs: [100, 100, 100] },
        })
      )
      // 3 Disconnects -> 3 Reconnect-Versuche, dann 'failed'.
      for (let i = 0; i < 3; i++) {
        act(() => {
          instances[instances.length - 1].simulateClose(1006)
        })
        await act(async () => {
          await vi.advanceTimersByTimeAsync(150)
        })
      }
      // 3. Reconnect erzeugt neue Instanz, die noch offen ist. Wir muessen
      // auch diese schliessen, um den 4. attempt-counter zu erreichen.
      act(() => {
        instances[instances.length - 1].simulateClose(1006)
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200)
      })
      expect(result.current.status).toBe('failed')
      expect(onError).toHaveBeenCalledWith('failed', expect.objectContaining({}))
    } finally {
      vi.useRealTimers()
    }
  })

  it('heartbeat payload is sent every intervalMs while open', async () => {
    vi.useFakeTimers()
    try {
      renderHook(() =>
        useWebSocket({
          buildUrl: () => '/api/test/ws',
          onMessage: () => {},
          heartbeat: { payload: 'PING', intervalMs: 500 },
        })
      )
      const ws = instances[0]
      act(() => {
        ws.simulateOpen()
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1750)
      })
      // 1750ms / 500ms = 3 volle Intervalle.
      const pings = ws.sent.filter((s) => s === 'PING')
      expect(pings.length).toBeGreaterThanOrEqual(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('heartbeat stops on disconnect and does not run after close', async () => {
    vi.useFakeTimers()
    try {
      renderHook(() =>
        useWebSocket({
          buildUrl: () => '/api/test/ws',
          onMessage: () => {},
          heartbeat: { payload: 'PING', intervalMs: 500 },
        })
      )
      const ws = instances[0]
      act(() => {
        ws.simulateOpen()
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1500)
      })
      const before = ws.sent.filter((s) => s === 'PING').length
      act(() => {
        ws.simulateClose(1006)
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000)
      })
      // Sollte nach Close keinen weiteren Ping senden.
      const after = ws.sent.filter((s) => s === 'PING').length
      expect(after).toBe(before)
    } finally {
      vi.useRealTimers()
    }
  })

  it('buildUrl is called for every reconnect (last_id resume)', async () => {
    vi.useFakeTimers()
    try {
      const buildUrl = vi.fn(() => '/api/test/ws')
      renderHook(() =>
        useWebSocket({
          buildUrl,
          onMessage: () => {},
        })
      )
      // Erster Connect.
      expect(buildUrl).toHaveBeenCalledTimes(1)
      // Disconnect -> Reconnect -> buildUrl nochmal.
      act(() => {
        instances[0].simulateClose(1006)
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1100)
      })
      expect(buildUrl).toHaveBeenCalledTimes(2)
      // Wenn Consumer buildUrl() aendert (z. B. last_id steigt), wird beim
      // naechsten Reconnect die neue URL genutzt.
      buildUrl.mockReturnValueOnce('/api/test/ws?last_id=42')
      act(() => {
        instances[1].simulateClose(1006)
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2100)
      })
      expect(buildUrl).toHaveBeenCalledTimes(3)
      const lastUrl = instances[instances.length - 1].url
      expect(lastUrl).toContain('last_id=42')
    } finally {
      vi.useRealTimers()
    }
  })

  it('send() returns false when WS is not open', () => {
    const { result } = renderHook(() =>
      useWebSocket({
        buildUrl: () => '/api/test/ws',
        onMessage: () => {},
      })
    )
    // Vor open: WebSocket.OPEN (1) ist nicht der readyState.
    expect(result.current.send('test')).toBe(false)

    const ws = instances[0]
    act(() => {
      ws.simulateOpen()
    })
    expect(result.current.send('hello')).toBe(true)
    expect(ws.sent).toContain('hello')
  })

  it('cleanup on unmount stops reconnect loop and closes the WS', () => {
    const onError = vi.fn()
    const { unmount } = renderHook(() =>
      useWebSocket({
        buildUrl: () => '/api/test/ws',
        onMessage: () => {},
        onError,
      })
    )
    const ws = instances[0]
    expect(ws.readyState).not.toBe(FakeWebSocket.CLOSED)
    unmount()
    // WS wurde geschlossen, kein Reconnect-Trigger nach Unmount.
    expect(ws.readyState).toBe(FakeWebSocket.CLOSED)
  })
})
