/**
 * Die Prüfschleife nach dem Panel-Update hatte zwei Löcher.
 *
 * Die Abbruchgrenze stand im `catch`:
 *
 *     } catch {
 *       if (checkCount > 40) { ... }   // nur wenn `api` wirft
 *     }
 *
 * `/system/update/status` antwortet aber auch im Fehlerfall mit HTTP 200 und
 * `ok: false` — und nach einem fehlgeschlagenen Update bleibt
 * `update_available: true`. In beiden Fällen wirft `api` nicht, der
 * Erfolgszweig greift nicht, und die Zwei-Minuten-Grenze wurde nie erreicht:
 * das Panel fragte den Endpunkt für den Rest der Sitzung alle drei Sekunden
 * ab, jedes Mal mit einem `git fetch` auf dem Host.
 *
 * Und die Kennung des Intervalls kannte keine Aufräumfunktion — der Banner
 * steht nur auf dem Dashboard, die Schleife überlebte jede Navigation weg
 * davon und hätte irgendwann mitten in einer anderen Tätigkeit neu geladen.
 *
 * Beides prüft dieser Test: die Grenze muss auch bei sauberen Antworten
 * greifen, und das Abhängen muss die Schleife beenden.
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import i18n from '@/i18n'
import type { GitUpdateStatus } from '@/types'
import { UpdateBanner } from './UpdateBanner'

vi.mock('@/api/client', () => ({
  api: vi.fn(),
}))

const offen: GitUpdateStatus = {
  update_available: true,
  local_sha: 'aaaaaaa',
  remote_sha: 'bbbbbbb',
  branch: 'main',
  updates_automatic: false,
  ok: true,
}

/** Zählt die Abfragen von `/system/update/status`. */
function statusAbfragen(): number {
  return vi.mocked(api).mock.calls.filter((call) => call[0] === '/system/update/status').length
}

/** Rendert den Banner und drückt "Update starten"; danach läuft die Prüfschleife. */
async function updateAnstossen() {
  const ergebnis = render(<UpdateBanner />)
  const knopf = await screen.findByRole('button', { name: /Update starten/ })

  vi.useFakeTimers()
  fireEvent.click(knopf)
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
  return ergebnis
}

describe('UpdateBanner Prüfschleife', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    await i18n.changeLanguage('de')
    // Das Update bleibt offen: `api` wirft nie, `update_available` bleibt true.
    vi.mocked(api).mockImplementation(async (pfad: string) => {
      if (pfad === '/system/update/panel') return {} as never
      return offen as never
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('bricht nach zwei Minuten ab, auch wenn jede Abfrage sauber antwortet', async () => {
    await updateAnstossen()
    const vorSchleife = statusAbfragen()

    // 41 Durchläufe: 40 fragen ab, der 41. erreicht die Grenze.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000 * 41)
    })

    expect(statusAbfragen() - vorSchleife).toBe(40)
    expect(screen.getByRole('button', { name: /Update starten/ })).toBeEnabled()

    const nachAbbruch = statusAbfragen()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000 * 5)
    })
    expect(statusAbfragen()).toBe(nachAbbruch)
  })

  it('beendet die Schleife, wenn der Banner abgehängt wird', async () => {
    const { unmount } = await updateAnstossen()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    const vorAbhaengen = statusAbfragen()
    expect(vorAbhaengen).toBeGreaterThan(0)

    unmount()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000 * 10)
    })
    expect(statusAbfragen()).toBe(vorAbhaengen)
  })
})
