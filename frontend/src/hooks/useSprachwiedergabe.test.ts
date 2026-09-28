/**
 * Die Wiedergabe von Sprachnachrichten: immer höchstens eine Aufnahme.
 *
 * Bis 09/2026 stand der Anhalte-Block viermal in `Messenger.tsx`. Wer ihn an
 * einer Stelle vergass, liess zwei Nachrichten übereinander spielen.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import '@/i18n'

const { holeAnhangUrl, toastError } = vi.hoisted(() => ({
  holeAnhangUrl: vi.fn(),
  toastError: vi.fn(),
}))
vi.mock('@/components/social/ChatMediaAttachments', () => ({ holeAnhangUrl }))
vi.mock('@/stores/toastStore', () => ({ toast: { error: toastError } }))

import { useSprachwiedergabe } from './useSprachwiedergabe'

class FakeAudio {
  static alle: FakeAudio[] = []
  src: string
  playbackRate = 1
  currentTime = 0
  paused = true
  ontimeupdate: (() => void) | null = null
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(src: string) {
    this.src = src
    FakeAudio.alle.push(this)
  }
  play() {
    this.paused = false
    return Promise.resolve()
  }
  pause() {
    this.paused = true
  }
}

const bindung = { absenderId: 1, blindMailboxId: 'mb' }
const anhang = (dataUrl?: string) => ({ durationSeconds: 10, mimeType: 'audio/webm', dataUrl })
const klick = () => ({ stopPropagation: vi.fn() }) as unknown as React.MouseEvent

beforeEach(() => {
  FakeAudio.alle = []
  holeAnhangUrl.mockReset()
  toastError.mockReset()
  vi.stubGlobal('Audio', FakeAudio)
})

describe('useSprachwiedergabe', () => {
  it('hält die laufende Aufnahme an, wenn eine andere startet', async () => {
    const { result } = renderHook(() => useSprachwiedergabe())
    act(() => result.current.onTogglePlay(1, anhang('data:a'), bindung))
    await waitFor(() => expect(result.current.playingAudioId).toBe(1))
    act(() => result.current.onTogglePlay(2, anhang('data:b'), bindung))
    await waitFor(() => expect(result.current.playingAudioId).toBe(2))

    const [erste, zweite] = FakeAudio.alle
    expect(erste.paused).toBe(true)
    expect(erste.onended).toBeNull()
    expect(zweite.paused).toBe(false)
  })

  it('hält an, wenn dieselbe Nachricht noch einmal gedrückt wird', async () => {
    const { result } = renderHook(() => useSprachwiedergabe())
    act(() => result.current.onTogglePlay(1, anhang('data:a'), bindung))
    await waitFor(() => expect(result.current.playingAudioId).toBe(1))
    act(() => result.current.onTogglePlay(1, anhang('data:a'), bindung))

    expect(result.current.playingAudioId).toBeNull()
    expect(FakeAudio.alle[0].paused).toBe(true)
    expect(FakeAudio.alle).toHaveLength(1)
  })

  it('holt eine fremde Aufnahme aus dem Medienspeicher und meldet, wenn das scheitert', async () => {
    holeAnhangUrl.mockResolvedValue(null)
    const { result } = renderHook(() => useSprachwiedergabe())
    act(() => result.current.onTogglePlay(1, anhang(), bindung))

    await waitFor(() => expect(toastError).toHaveBeenCalledTimes(1))
    expect(holeAnhangUrl).toHaveBeenCalledWith(anhang(), bindung)
    expect(result.current.playingAudioId).toBeNull()
    expect(FakeAudio.alle).toHaveLength(0)
  })

  it('springt in der laufenden Aufnahme, ohne sie neu zu laden', async () => {
    const { result } = renderHook(() => useSprachwiedergabe())
    act(() => result.current.onTogglePlay(1, anhang('data:a'), bindung))
    await waitFor(() => expect(result.current.playingAudioId).toBe(1))

    const welle = {
      stopPropagation: vi.fn(),
      clientX: 25,
      currentTarget: { getBoundingClientRect: () => ({ left: 0, width: 100 }) },
    } as unknown as React.MouseEvent<HTMLDivElement>
    act(() => result.current.onSeek(1, anhang('data:a'), bindung, welle))

    expect(FakeAudio.alle).toHaveLength(1)
    expect(FakeAudio.alle[0].currentTime).toBe(2.5)
    expect(result.current.audioCurrentTime).toBe(2.5)
  })

  it('wechselt das Tempo auch an der laufenden Aufnahme', async () => {
    const { result } = renderHook(() => useSprachwiedergabe())
    act(() => result.current.onTogglePlay(1, anhang('data:a'), bindung))
    await waitFor(() => expect(result.current.playingAudioId).toBe(1))
    act(() => result.current.onCycleRate(klick()))
    act(() => result.current.onCycleRate(klick()))

    expect(result.current.audioPlaybackRate).toBe(2)
    expect(FakeAudio.alle[0].playbackRate).toBe(2)
    act(() => result.current.onCycleRate(klick()))
    expect(result.current.audioPlaybackRate).toBe(1)
  })

  it('verstummt, wenn der Messenger verlassen wird', async () => {
    const { result, unmount } = renderHook(() => useSprachwiedergabe())
    act(() => result.current.onTogglePlay(1, anhang('data:a'), bindung))
    await waitFor(() => expect(result.current.playingAudioId).toBe(1))
    unmount()
    expect(FakeAudio.alle[0].paused).toBe(true)
  })
})
