import { fireEvent, render, screen } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { SprachLeiste } from './SprachLeiste'
import type { Sprachzeile, Sprachzustand } from './useSprachsitzung'

const starten = vi.fn()
const beenden = vi.fn()

let sitzung: {
  zustand: Sprachzustand
  zeilen: Sprachzeile[]
  werkzeug: string | null
  fehler: string | null
}

vi.mock('./useSprachsitzung', () => ({
  useSprachsitzung: () => ({ ...sitzung, starten, beenden }),
}))

/**
 * Der Hook ist ersetzt, weil hier die Anzeige geprueft wird und nicht die
 * Sitzung — die hat ihren eigenen Test. Was bleibt, ist die Frage: sagt die
 * Leiste dem Menschen die Wahrheit darueber, was gerade mit seinem Mikrofon
 * passiert?
 */
function leiste(teil: Partial<typeof sitzung> = {}) {
  sitzung = { zustand: 'aus', zeilen: [], werkzeug: null, fehler: null, ...teil }
  return render(<SprachLeiste />)
}

describe('SprachLeiste', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('de')
  })

  beforeEach(() => {
    starten.mockClear()
    beenden.mockClear()
  })

  it('hat fuer jeden Zustand einen uebersetzten Satz', () => {
    const zustaende: Sprachzustand[] = ['aus', 'verbindet', 'bereit', 'hoert', 'denkt', 'spricht']

    // Ein fehlender Schluessel kommt bei i18next als der Schluessel selbst
    // zurueck. Genau das faengt diese Zusicherung ab — sonst stuende im Panel
    // woertlich `ai.voice.zustand.hoert`.
    for (const zustand of zustaende) {
      const schluessel = `ai.voice.zustand.${zustand}`
      expect(i18n.t(schluessel)).not.toBe(schluessel)
    }
    for (const schluessel of ['ai.voice.start', 'ai.voice.stop', 'ai.voice.errors.microphone']) {
      expect(i18n.t(schluessel)).not.toBe(schluessel)
    }
  })

  it('bietet im Ruhezustand das Starten an', () => {
    leiste()

    const knopf = screen.getByRole('button', { name: i18n.t('ai.voice.start') })
    expect(knopf).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(knopf)
    expect(starten).toHaveBeenCalledOnce()
  })

  it('bietet in laufender Sitzung das Beenden an', () => {
    leiste({ zustand: 'hoert' })

    const knopf = screen.getByRole('button', { name: i18n.t('ai.voice.stop') })
    expect(knopf).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(knopf)
    expect(beenden).toHaveBeenCalledOnce()
  })

  it('benennt den laufenden Zustand', () => {
    leiste({ zustand: 'denkt' })

    expect(screen.getByText(i18n.t('ai.voice.zustand.denkt'))).toBeInTheDocument()
  })

  it('zeigt den Fehler statt des Zustands', () => {
    leiste({ zustand: 'aus', fehler: 'ai.voice.errors.microphone' })

    expect(screen.getByText(i18n.t('ai.voice.errors.microphone'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('ai.voice.zustand.aus'))).not.toBeInTheDocument()
  })

  it('zeigt nur den Werkzeugnamen', () => {
    leiste({ zustand: 'denkt', werkzeug: 'read_server_status' })

    // Argumente tragen Serverkennungen und Pfade. Sie gehoeren nicht in eine
    // Anzeige, die nebenbei mitlaeuft.
    expect(screen.getByText('read_server_status')).toBeInTheDocument()
  })

  it('haelt den Wortwechsel vorlesbar', () => {
    leiste({
      zustand: 'spricht',
      zeilen: [
        { wer: 'ich', text: 'welche server laufen?' },
        { wer: 'ki', text: 'Zwei laufen.' },
      ],
    })

    // `aria-live` ist hier keine Kuer: wer nicht hoert — weil der Ton aus ist
    // oder weil er nicht hoeren kann — bekommt die Antwort nur hierueber.
    const bereich = screen.getByText(/Zwei laufen\./).closest('[aria-live]')
    expect(bereich).toHaveAttribute('aria-live', 'polite')
    expect(screen.getByText(/welche server laufen\?/)).toBeInTheDocument()
  })

  it('zeigt den Wortwechsel nicht, solange nichts laeuft', () => {
    leiste({ zustand: 'aus', zeilen: [{ wer: 'ki', text: 'von vorhin' }] })

    expect(screen.queryByText(/von vorhin/)).not.toBeInTheDocument()
  })
})
