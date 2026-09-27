/**
 * Das Anruffenster lädt nach, nicht mit dem Startbündel. Der Empfang von
 * Anrufereignissen darf daran nicht hängen: ohne Fenster muss ein eingehender
 * Anruf trotzdem im Store ankommen, sonst klingelt nie etwas.
 */

import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('./CallOverlay', () => ({
  CallOverlay: () => <div>anruffenster</div>,
}))

import { useCallStore } from '@/stores/useCallStore'
import { AnrufEbene } from './AnrufEbene'

const ursprung = useCallStore.getState()

afterEach(() => {
  cleanup()
  useCallStore.setState(ursprung, true)
})

describe('AnrufEbene', () => {
  it('nimmt Anrufereignisse an, solange kein Fenster geladen ist', () => {
    const annehmen = vi.fn()
    useCallStore.setState({ state: 'idle', handleCallSyncEvent: annehmen })

    const { container } = render(<AnrufEbene />)
    expect(container).toBeEmptyDOMElement()

    const ereignis = { type: 'call_invite', raum: 'r1' }
    act(() => {
      window.dispatchEvent(new CustomEvent('msm:sync-event', { detail: ereignis }))
    })

    expect(annehmen).toHaveBeenCalledWith(ereignis)
  })

  it('zeigt das Fenster, sobald ein Anruf nicht mehr ruht', async () => {
    useCallStore.setState({ state: 'idle' })
    render(<AnrufEbene />)
    expect(screen.queryByText('anruffenster')).toBeNull()

    act(() => useCallStore.setState({ state: 'incoming' }))

    expect(await screen.findByText('anruffenster')).toBeInTheDocument()
  })
})
