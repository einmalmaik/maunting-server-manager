/**
 * Der Story-Betrachter schaltet nach 6 Sekunden weiter und schliesst nach der
 * letzten Story.
 *
 * Bis 09/2026 geschah beides im Updater von `setProgress`. Der läuft beim
 * Rendern, und das Schliessen änderte dort den Zustand der Seite; React warnte
 * mit „Cannot update a component while rendering a different component".
 */

import { act, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import type { ChatStoryItem } from '@/api/social'
import { StoryViewerModal } from './StoryViewerModal'

const story = (id: number, content: string): ChatStoryItem => ({
  id,
  user_id: 2,
  username: 'bert',
  content,
  background: 'violet',
  created_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  is_self: false,
})

/** Wie die Messenger-Seite: der offene Zustand liegt beim Elternteil. */
function Seite({ stories, onZu }: { stories: ChatStoryItem[]; onZu: () => void }) {
  const [offen, setOffen] = useState(false)
  return (
    <>
      <button onClick={() => setOffen(true)}>öffnen</button>
      <StoryViewerModal
        stories={stories}
        open={offen}
        onOpenChange={(o) => {
          setOffen(o)
          if (!o) onZu()
        }}
      />
    </>
  )
}

let fehler: ReturnType<typeof vi.spyOn>

beforeAll(async () => {
  await i18n.changeLanguage('de')
})
beforeEach(() => {
  vi.useFakeTimers()
  fehler = vi.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => {
  fehler.mockRestore()
  vi.useRealTimers()
})

const warte = (ms: number) => act(() => vi.advanceTimersByTime(ms))

describe('StoryViewerModal', () => {
  it('schaltet nach 6 Sekunden weiter und schliesst nach der letzten, ohne Renderwarnung', () => {
    const onZu = vi.fn()
    render(<Seite stories={[story(1, 'erste Story'), story(2, 'zweite Story')]} onZu={onZu} />)
    fireEvent.click(screen.getByText('öffnen'))
    expect(screen.getByText('erste Story')).toBeInTheDocument()

    warte(5_000)
    expect(screen.getByText('erste Story')).toBeInTheDocument()
    warte(1_200)
    expect(screen.getByText('zweite Story')).toBeInTheDocument()

    warte(6_200)
    expect(screen.queryByText('zweite Story')).not.toBeInTheDocument()
    expect(onZu).toHaveBeenCalledTimes(1)
    expect(fehler).not.toHaveBeenCalled()
  })

  it('beginnt nach dem Wiederöffnen von vorn, statt sofort zu schliessen', () => {
    const onZu = vi.fn()
    render(<Seite stories={[story(1, 'einzige Story')]} onZu={onZu} />)
    fireEvent.click(screen.getByText('öffnen'))
    warte(6_200)
    expect(onZu).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByText('öffnen'))
    warte(1_000)
    expect(screen.getByText('einzige Story')).toBeInTheDocument()
    expect(onZu).toHaveBeenCalledTimes(1)
    expect(fehler).not.toHaveBeenCalled()
  })

  it('behauptet keine Ende-zu-Ende-Verschlüsselung', () => {
    // Bis 29.09.2026 stand im Kopf „Ende-zu-Ende“ mit Schloss. Stories liest
    // das Backend aber mit, die Datenschutzerklärung sagt das auch so.
    render(<Seite stories={[story(1, 'eine Story')]} onZu={vi.fn()} />)
    fireEvent.click(screen.getByText('öffnen'))
    expect(screen.getByText('eine Story')).toBeInTheDocument()
    expect(screen.queryByText(/Ende-zu-Ende|verschlüsselt/i)).not.toBeInTheDocument()
  })

  it('nennt den Löschknopf der eigenen Story ohne natives title', () => {
    render(<Seite stories={[{ ...story(1, 'meine Story'), is_self: true }]} onZu={vi.fn()} />)
    fireEvent.click(screen.getByText('öffnen'))
    expect(screen.getByRole('button', { name: i18n.t('social.story.delete') })).toBeInTheDocument()
    expect(document.body.querySelectorAll('[title]')).toHaveLength(0)
  })

  it('zeigt jede Story im Hochformat 9:16, ob Text oder Foto', () => {
    // Bis 02.10.2026 stand dort aspect-16/9, das Tailwind 3 nicht kennt: die Größe hing am Inhalt.
    const foto = { ...story(2, 'mit Foto'), media_url: 'data:image/gif;base64,R0lGODlhAQABAAAAACw=' }
    render(<Seite stories={[story(1, 'nur Text'), foto]} onZu={vi.fn()} />)
    fireEvent.click(screen.getByText('öffnen'))
    expect(screen.getByText('nur Text').closest('[class*="aspect-"]')).toHaveClass('aspect-[9/16]')
    warte(6_000)
    expect(screen.getByText('mit Foto').closest('[class*="aspect-"]')).toHaveClass('aspect-[9/16]')
  })
})
