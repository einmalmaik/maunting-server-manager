/**
 * Stories: wer Ungesehenes hat, steht vorn, und „gesehen" bleibt auf dem Gerät.
 */

import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { useStoryAnsicht } from './useStoryAnsicht'

const ICH = 1
const story = (id: number, user_id: number) => ({ id, user_id, username: `u${user_id}` }) as never
const STORIES = [story(10, ICH), story(20, 2), story(30, 3), story(31, 3)]
const KONTAKTE = [{ userId: 3, username: 'carla', avatarUrl: 'c.png' }]

beforeEach(() => {
  localStorage.clear()
})

describe('useStoryAnsicht', () => {
  it('trennt eigene von fremden und nennt Kontakte beim Namen', () => {
    const { result } = renderHook(() => useStoryAnsicht(STORIES, ICH, KONTAKTE))
    expect(result.current.eigene.map((s) => s.id)).toEqual([10])
    expect(result.current.freunde.map((g) => g.userId)).toEqual([2, 3])
    expect(result.current.freunde[1]).toMatchObject({ username: 'carla', avatarUrl: 'c.png', hasUnseen: true })
  })

  it('merkt Gesehenes lokal und schiebt Ungesehenes nach vorn', () => {
    const { result } = renderHook(() => useStoryAnsicht(STORIES, ICH, KONTAKTE))
    act(() => result.current.betrachter.oeffne(result.current.freunde[0].stories))

    expect(result.current.betrachter.offen).toBe(true)
    expect(result.current.betrachter.stories.map((s) => s.id)).toEqual([20])
    expect(result.current.freunde.map((g) => [g.userId, g.hasUnseen])).toEqual([
      [3, true],
      [2, false],
    ])
    expect(JSON.parse(localStorage.getItem('msm_seen_story_ids')!)).toEqual([20])

    // Beim nächsten Öffnen der Seite ist es noch gesehen.
    const neu = renderHook(() => useStoryAnsicht(STORIES, ICH, KONTAKTE))
    expect(neu.result.current.freunde[1]).toMatchObject({ userId: 2, hasUnseen: false })
  })

  it('vergisst ein Kamerafoto, sobald die Erstellung schliesst oder als Text neu beginnt', () => {
    const { result } = renderHook(() => useStoryAnsicht([], ICH, []))
    act(() => result.current.erstellung.mitFoto('data:foto'))
    expect(result.current.erstellung).toMatchObject({ offen: true, modus: 'photo', fotoUrl: 'data:foto' })

    act(() => result.current.erstellung.setOffen(false))
    expect(result.current.erstellung).toMatchObject({ offen: false, fotoUrl: null })

    act(() => result.current.erstellung.mitFoto('data:foto'))
    act(() => result.current.erstellung.mitText())
    expect(result.current.erstellung).toMatchObject({ offen: true, modus: 'text', fotoUrl: null })
  })
})
