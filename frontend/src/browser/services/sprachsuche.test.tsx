import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { istSprachsucheMoeglich, useSprachsuche } from './sprachsuche'
import { SprachsucheKnopf } from '../kopf/SprachsucheKnopf'

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
