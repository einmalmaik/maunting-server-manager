/**
 * Eine Sprachnachricht aufnehmen, ohne die Seite zu rendern.
 *
 * Geprüft wird, was schiefgehen darf und was nicht: Verworfen heißt, dass
 * nichts gesendet wird. Und das Mikrofon ist danach frei, auch wenn der
 * Messenger mitten in der Aufnahme verlassen wird.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import '@/i18n'

const { sendTypingSignal, toastError } = vi.hoisted(() => ({
  sendTypingSignal: vi.fn(),
  toastError: vi.fn(),
}))
vi.mock('@/api/social', () => ({ sendTypingSignal }))
vi.mock('@/lib/audioSettings', () => ({ getAudioTrackConstraints: () => ({}) }))
vi.mock('@/stores/toastStore', () => ({ toast: { error: toastError } }))

import { useSprachaufnahme } from './useSprachaufnahme'

class FakeRecorder {
  static isTypeSupported = (mime: string) => mime === 'audio/webm'
  state: 'inactive' | 'recording' = 'inactive'
  mimeType = 'audio/webm'
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  start() {
    this.state = 'recording'
    this.ondataavailable?.({ data: new Blob(['ton'], { type: 'audio/webm' }) })
  }
  stop() {
    this.state = 'inactive'
    this.onstop?.()
  }
}

let spur: { stop: ReturnType<typeof vi.fn> }

beforeEach(() => {
  sendTypingSignal.mockReset().mockResolvedValue(undefined)
  toastError.mockReset()
  spur = { stop: vi.fn() }
  vi.stubGlobal('MediaRecorder', FakeRecorder)
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [spur] }) },
  })
})

function aufnahme(blindMailboxId = 'mb') {
  const onFertig = vi.fn()
  const hook = renderHook(() => useSprachaufnahme({ blindMailboxId, onFertig }))
  return { ...hook, onFertig }
}

describe('useSprachaufnahme', () => {
  it('schickt die fertige Aufnahme an onFertig und gibt das Mikrofon frei', async () => {
    const { result, onFertig } = aufnahme()
    await act(() => result.current.starte())
    expect(result.current.laeuft).toBe(true)
    expect(result.current.stream).not.toBeNull()

    act(() => result.current.beende(true))
    await waitFor(() => expect(onFertig).toHaveBeenCalledTimes(1))
    const audio = onFertig.mock.calls[0][0]
    expect(audio.mimeType).toBe('audio/webm')
    expect(audio.durationSeconds).toBe(1)
    expect(audio.dataUrl).toMatch(/^data:audio\/webm/)
    expect(spur.stop).toHaveBeenCalled()
    expect(result.current.laeuft).toBe(false)
  })

  it('sendet nichts, wenn die Aufnahme verworfen wird', async () => {
    const { result, onFertig } = aufnahme()
    await act(() => result.current.starte())
    act(() => result.current.beende(false))

    await new Promise((r) => setTimeout(r, 20))
    expect(onFertig).not.toHaveBeenCalled()
    expect(spur.stop).toHaveBeenCalled()
  })

  it('meldet Aufnehmen und Ende an die Mailbox, ohne Gespräch aber nicht', async () => {
    const mit = aufnahme('mb')
    await act(() => mit.result.current.starte())
    act(() => mit.result.current.beende(false))
    expect(sendTypingSignal.mock.calls.map((c) => c[0])).toEqual([
      { blind_mailbox_id: 'mb', status: 'recording' },
      { blind_mailbox_id: 'mb', status: 'idle' },
    ])

    sendTypingSignal.mockClear()
    const ohne = aufnahme('')
    await act(() => ohne.result.current.starte())
    act(() => ohne.result.current.beende(false))
    expect(sendTypingSignal).not.toHaveBeenCalled()
  })

  it('gibt das Mikrofon frei, wenn der Messenger mitten in der Aufnahme verlassen wird', async () => {
    const { result, unmount } = aufnahme()
    await act(() => result.current.starte())
    unmount()
    expect(spur.stop).toHaveBeenCalled()
  })

  it('meldet ein verweigertes Mikrofon, statt aufzunehmen', async () => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn().mockRejectedValue(new Error('verweigert')) },
    })
    const { result } = aufnahme()
    await act(() => result.current.starte())
    expect(toastError).toHaveBeenCalledTimes(1)
    expect(result.current.laeuft).toBe(false)
  })
})
