import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useToastStore } from '@/stores/toastStore'
import { fehlerKurztext, istSprachsucheMoeglich, useSprachsuche } from './sprachsuche'
import { istLokalesWhisperMoeglich, starteAudioAufnahme } from './lokalesWhisper'
import { SprachsucheKnopf } from '../kopf/SprachsucheKnopf'

vi.mock('./lokalesWhisper', () => ({
  istLokalesWhisperMoeglich: vi.fn(() => false),
  ladeWhisperModell: vi.fn(),
  starteAudioAufnahme: vi.fn(),
  transkribiereAudioMitWhisper: vi.fn(),
}))

class FakeSpeechRecognition {
  continuous = false
  interimResults = false
  lang = 'de-DE'
  onstart: (() => void) | null = null
  onend: (() => void) | null = null
  onerror: ((ev: { error: string }) => void) | null = null
  onresult: ((ev: any) => void) | null = null

  static instanz: FakeSpeechRecognition | null = null

  start() {
    FakeSpeechRecognition.instanz = this
    this.onstart?.()
  }

  stop() {
    this.onend?.()
  }

  abort() {
    this.onend?.()
  }
}

describe('Sprachsuche Hook & Knopf', () => {
  beforeEach(() => {
    FakeSpeechRecognition.instanz = null
    ;(window as any).SpeechRecognition = FakeSpeechRecognition
    delete (window as any).webkitSpeechRecognition
    useToastStore.getState().clearAll()
    vi.mocked(istLokalesWhisperMoeglich).mockReturnValue(false)
  })

  it('erkennt, ob Spracherkennung im Browser vorhanden ist', () => {
    expect(istSprachsucheMoeglich()).toBe(true)
    delete (window as any).SpeechRecognition
    expect(istSprachsucheMoeglich()).toBe(false)
  })

  it('schaltet Zuhören ein und liefert das gesprochene Ergebnis ohne Auto-Submit', () => {
    const onErgebnis = vi.fn()
    render(<SprachsucheKnopf onErgebnis={onErgebnis} />)

    const btn = screen.getByRole('button', { name: /Sprachsuche starten/i })
    expect(btn).toBeInTheDocument()

    // Starten
    fireEvent.click(btn)
    expect(FakeSpeechRecognition.instanz).not.toBeNull()

    // Simuliere Erkennung
    act(() => {
      FakeSpeechRecognition.instanz?.onresult?.({
        results: [
          {
            isFinal: true,
            0: { transcript: 'Neueste Nachrichten aus Berlin' },
          },
        ],
      })
    })

    expect(onErgebnis).toHaveBeenCalledWith('Neueste Nachrichten aus Berlin')
  })

  it('kürzt Fehler für den Toast auf Name plus eine Zeile', () => {
    expect(fehlerKurztext(new TypeError('Failed to fetch'))).toBe('TypeError: Failed to fetch')
    expect(fehlerKurztext(new Error(''))).toBe('Error')
    expect(fehlerKurztext('nur Text')).toBe('Fehler: nur Text')
    expect(fehlerKurztext(new Error('a\n  b\tc'))).toBe('Error: a b c')
    const lang = fehlerKurztext(new Error(`x${'y'.repeat(300)}`), 20)
    expect(lang).toBe(`Error: x${'y'.repeat(11)}…`)
    expect(lang.length).toBe(20)
  })

  it('zeigt bei fehlgeschlagener Whisper-Transkription die Ursache im Toast', async () => {
    vi.mocked(istLokalesWhisperMoeglich).mockReturnValue(true)
    vi.mocked(starteAudioAufnahme).mockResolvedValue({
      stoppen: async () => {
        throw new TypeError('Failed to fetch')
      },
      abbrechen: () => {},
    })
    const onErgebnis = vi.fn()
    render(<SprachsucheKnopf onErgebnis={onErgebnis} />)

    const btn = screen.getByRole('button', { name: /Sprachsuche starten/i })
    await act(async () => {
      fireEvent.click(btn)
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Sprachsuche beenden/i }))
    })

    expect(onErgebnis).not.toHaveBeenCalled()
    const meldungen = useToastStore.getState().toasts.map((t) => t.message)
    expect(meldungen.some((m) => m.includes('Spracheingabe fehlgeschlagen') && m.includes('Failed to fetch'))).toBe(true)
  })

  it('meldet ohne Netz einen Offline-Hinweis statt technischem Fehler', async () => {
    vi.mocked(istLokalesWhisperMoeglich).mockReturnValue(true)
    vi.mocked(starteAudioAufnahme).mockResolvedValue({
      stoppen: async () => {
        throw new TypeError('Failed to fetch')
      },
      abbrechen: () => {},
    })
    Object.defineProperty(window.navigator, 'onLine', { value: false, configurable: true })
    try {
      render(<SprachsucheKnopf onErgebnis={() => {}} />)
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /Sprachsuche starten/i }))
      })
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /Sprachsuche beenden/i }))
      })
      const meldungen = useToastStore.getState().toasts.map((t) => t.message)
      expect(meldungen.some((m) => m.includes('Kein Internet'))).toBe(true)
    } finally {
      Object.defineProperty(window.navigator, 'onLine', { value: true, configurable: true })
    }
  })

  it('fängt no-speech sauber ab ohne Fehlerzustand', () => {
    const onErgebnis = vi.fn()
    render(<SprachsucheKnopf onErgebnis={onErgebnis} />)

    const btn = screen.getByRole('button', { name: /Sprachsuche starten/i })
    fireEvent.click(btn)

    act(() => {
      FakeSpeechRecognition.instanz?.onerror?.({ error: 'no-speech' })
    })

    // Kehrt wieder in Bereitschaft zurück
    expect(screen.getByRole('button', { name: /Sprachsuche starten/i })).toBeInTheDocument()
  })
})
