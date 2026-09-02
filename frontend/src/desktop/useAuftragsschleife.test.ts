/**
 * Die Auftragsschleife: Melden ist nicht Ausführen.
 *
 * Beides stand einmal in demselben try-Block. Ein Netzfehler beim **Melden**
 * landete deshalb im Fehlerzweig fürs **Ausführen**, und der meldete denselben
 * Auftrag ein zweites Mal — als gescheitertes Werkzeug, mit dem
 * Transportfehler als Grund. Das fertige Ergebnis war damit weg, obwohl der
 * Rechner es hatte: aus „C: hat 113 GB frei" wurde
 * `{fehler: "Too Many Requests"}`.
 *
 * Geprüft werden die drei Aussagen, die daraus folgen:
 *
 * 1. Ein gescheiterter Transport wird nachgefasst, nicht umgedeutet.
 * 2. Bleibt es dabei, wird **nichts** erfunden — kein Fehlschlag unter dem
 *    Namen des Werkzeugs. Der Auftrag verfällt panelseitig, und das Modell
 *    erfährt genau das.
 * 3. Ein wirklich gescheitertes Werkzeug meldet weiterhin seinen Grund.
 */
import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const auftragAusfuehrenMock = vi.fn()
const naechsterAuftragMock = vi.fn()
const ergebnisMeldenMock = vi.fn()

vi.mock('./tauri', () => ({
  auftragAusfuehren: (...args: unknown[]) => auftragAusfuehrenMock(...args),
}))

vi.mock('./desktopJobs', () => ({
  naechsterAuftrag: () => naechsterAuftragMock(),
  ergebnisMelden: (...args: unknown[]) => ergebnisMeldenMock(...args),
}))

import { useAuftragsschleife } from './useAuftragsschleife'

const AUFTRAG = {
  id: 'job-1',
  tool_name: 'desktop_system',
  arguments: { aktion: 'laufwerke' },
}

const WERTE = { laufwerke: [{ laufwerk: 'C:', frei_bytes: 113434005504 }] }

describe('Auftragsschleife', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    auftragAusfuehrenMock.mockReset()
    naechsterAuftragMock.mockReset()
    ergebnisMeldenMock.mockReset()
    // Genau ein Auftrag, danach Ruhe — sonst dreht die Schleife weiter.
    naechsterAuftragMock.mockResolvedValueOnce(AUFTRAG).mockResolvedValue(null)
    auftragAusfuehrenMock.mockResolvedValue(WERTE)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('faellt bei einem gescheiterten Transport nach, statt das Ergebnis umzudeuten', async () => {
    ergebnisMeldenMock
      .mockRejectedValueOnce(new Error('Too Many Requests'))
      .mockResolvedValueOnce(undefined)

    renderHook(() => useAuftragsschleife(true))

    await waitFor(() => expect(ergebnisMeldenMock).toHaveBeenCalledTimes(2))
    // Beide Male dasselbe echte Ergebnis, beide Male ok=true.
    for (const aufruf of ergebnisMeldenMock.mock.calls) {
      expect(aufruf[1]).toBe(true)
      expect(aufruf[2]).toEqual(WERTE)
    }
  })

  it('erfindet nichts, wenn das Melden endgueltig scheitert', async () => {
    ergebnisMeldenMock.mockRejectedValue(new Error('Netz weg'))

    renderHook(() => useAuftragsschleife(true))

    // Grosszuegig: zwischen den Versuchen liegen 0,8 s und 1,6 s Wartezeit.
    await waitFor(() => expect(ergebnisMeldenMock).toHaveBeenCalledTimes(3), {
      timeout: 8000,
    })
    // Kein einziger Aufruf mit ok=false: ein Transportfehler ist kein
    // Werkzeugfehler, und der Auftrag verfaellt lieber, als falsch dazustehen.
    for (const aufruf of ergebnisMeldenMock.mock.calls) {
      expect(aufruf[1]).toBe(true)
    }
  }, 15000)

  it('meldet einen echten Werkzeugfehler weiterhin mit Grund', async () => {
    auftragAusfuehrenMock.mockRejectedValue(new Error('Laufwerke nicht abfragbar.'))
    ergebnisMeldenMock.mockResolvedValue(undefined)

    renderHook(() => useAuftragsschleife(true))

    await waitFor(() => expect(ergebnisMeldenMock).toHaveBeenCalledTimes(1))
    const [, ok, inhalt, code] = ergebnisMeldenMock.mock.calls[0]
    expect(ok).toBe(false)
    expect(inhalt).toEqual({ fehler: 'Laufwerke nicht abfragbar.' })
    expect(code).toBe('DESKTOP_TOOL_FAILED')
  })

  it('schickt die Auftragskennung mit in den Aufruf', async () => {
    // Rust legt sie in die Nutzlast der Bestätigungskarten, und erst dadurch
    // beantwortet eine Karte den Auftrag, der gefragt hat. Ohne diesen dritten
    // Parameter bliebe der Karte nur die Kennung im Zustand der Oberfläche —
    // und die stimmt nur, wenn sie rechtzeitig dort ankommt.
    renderHook(() => useAuftragsschleife(true))

    await waitFor(() => expect(auftragAusfuehrenMock).toHaveBeenCalledTimes(1))
    expect(auftragAusfuehrenMock).toHaveBeenCalledWith(
      AUFTRAG.tool_name,
      AUFTRAG.arguments,
      AUFTRAG.id,
    )
  })

  /**
   * Seit dem 23.08.2026 warten **zwei** Sorten Auftrag auf einen Menschen,
   * und die Bitte um die Übernahme ist keine eigene mehr, sondern eine Aktion
   * von `desktop_steuern`. Die Schleife muss deshalb die Argumente mitlesen —
   * ein Vergleich auf den Werkzeugnamen allein gäbe entweder jedem Klick eine
   * Karte oder der Freigabe keine.
   */
  describe('wer auf einen Menschen wartet', () => {
    async function kennung(auftrag: Record<string, unknown>) {
      naechsterAuftragMock.mockReset()
      naechsterAuftragMock.mockResolvedValueOnce(auftrag).mockResolvedValue(null)
      // `null` heisst: die Karte meldet selbst.
      auftragAusfuehrenMock.mockResolvedValue(null)
      const { result } = renderHook(() => useAuftragsschleife(true))
      await waitFor(() => expect(auftragAusfuehrenMock).toHaveBeenCalled())
      return result
    }

    it('erkennt die Bitte um die Freigabe an ihrer Aktion', async () => {
      const result = await kennung({
        id: 'job-frei',
        tool_name: 'desktop_steuern',
        arguments: { aktion: 'freigabe', anliegen: 'Fenster schliessen' },
      })
      await waitFor(() => expect(result.current).toBe('job-frei'))
    })

    it('gibt einem gewoehnlichen Klick keine Karte', async () => {
      const result = await kennung({
        id: 'job-klick',
        tool_name: 'desktop_steuern',
        arguments: { aktion: 'klick', x: 10, y: 20 },
      })
      expect(result.current).toBeNull()
    })

    it('erkennt das Aufraeumen am Werkzeug', async () => {
      const result = await kennung({
        id: 'job-weg',
        tool_name: 'desktop_aufraeumen',
        arguments: { aktion: 'papierkorb', grund: 'Platz schaffen' },
      })
      await waitFor(() => expect(result.current).toBe('job-weg'))
    })

    it('stolpert nicht ueber fehlende Argumente', async () => {
      const result = await kennung({
        id: 'job-leer',
        tool_name: 'desktop_steuern',
        arguments: {},
      })
      expect(result.current).toBeNull()
    })
  })
})
