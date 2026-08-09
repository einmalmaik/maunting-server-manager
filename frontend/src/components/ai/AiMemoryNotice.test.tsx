import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AiMemoryNotice } from './AiMemoryNotice'
import { aiApi } from '@/api/ai'
import i18n from '@/i18n'

vi.mock('@/api/ai', () => ({
  aiApi: { answerMemoryNotice: vi.fn() },
}))

vi.mock('@/stores/toastStore', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

/**
 * Der Hinweis ist die einzige Stelle, an der ein Benutzer erfaehrt, dass die KI
 * sich etwas merken kann, **bevor** sie es tut. Drei Antworten, drei
 * unterschiedliche Nachwirkungen — hier wird geprueft, dass jede beim Backend
 * ankommt und nicht versehentlich eine andere ausloest.
 */
describe('AiMemoryNotice', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(aiApi.answerMemoryNotice).mockReset().mockResolvedValue({
      enabled: false, notice_due: false, notice_hidden: false,
    })
  })

  it('nennt die Uebertragung an den Anbieter, nicht nur die Speicherung', async () => {
    render(<AiMemoryNotice onAnswered={vi.fn()} />)
    // Der entscheidende Satz: die Notizen verlassen das Panel. Wer nur
    // "wird gespeichert" liest, trifft eine schlechter informierte Entscheidung.
    expect(screen.getByText(/KI-Anbieter/)).toBeInTheDocument()
  })

  it('schaltet bei "Aktivieren" ein und meldet das nach oben', async () => {
    vi.mocked(aiApi.answerMemoryNotice).mockResolvedValue({
      enabled: true, notice_due: false, notice_hidden: false,
    })
    const onAnswered = vi.fn()
    render(<AiMemoryNotice onAnswered={onAnswered} />)

    fireEvent.click(screen.getByRole('button', { name: 'Aktivieren' }))

    await waitFor(() => expect(aiApi.answerMemoryNotice).toHaveBeenCalledWith(true, false))
    expect(onAnswered).toHaveBeenCalledWith(true)
  })

  it('verschiebt bei "Spaeter", ohne den Hinweis dauerhaft abzustellen', async () => {
    render(<AiMemoryNotice onAnswered={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Später' }))

    await waitFor(() => expect(aiApi.answerMemoryNotice).toHaveBeenCalledWith(false, false))
  })

  it('stellt bei "Nicht mehr fragen" den Hinweis ab, aber schaltet nichts ein', async () => {
    render(<AiMemoryNotice onAnswered={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: /Nicht mehr fragen/ }))

    await waitFor(() => expect(aiApi.answerMemoryNotice).toHaveBeenCalledWith(false, true))
  })

  it('bleibt bedienbar, wenn das Speichern fehlschlaegt', async () => {
    vi.mocked(aiApi.answerMemoryNotice).mockRejectedValue(new Error('offline'))
    const onAnswered = vi.fn()
    render(<AiMemoryNotice onAnswered={onAnswered} />)

    fireEvent.click(screen.getByRole('button', { name: 'Aktivieren' }))

    // Kein stiller Ausblendevorgang: der Hinweis verschwindet nur, wenn die
    // Antwort tatsaechlich gespeichert wurde.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Aktivieren' })).toBeEnabled())
    expect(onAnswered).not.toHaveBeenCalled()
  })
})
