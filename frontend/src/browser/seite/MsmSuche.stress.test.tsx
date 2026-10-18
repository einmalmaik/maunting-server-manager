import { fireEvent, render, screen, waitFor, act } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { schutz, api } = vi.hoisted(() => ({
  schutz: { stand: null as unknown, wirft: false, haengt: false },
  api: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string) => {
    if (befehl !== 'schutz_stand') return Promise.resolve(null)
    if (schutz.haengt) return new Promise(() => {})
    return schutz.wirft ? Promise.reject(new Error('kaputt')) : Promise.resolve(schutz.stand)
  },
}))

vi.mock('@/api/client', async (original) => ({ ...(await original<object>()), api }))

const { SanitizedApiError } = await import('@/api/client')
const { MsmSuche, gemerktLeeren } = await import('./MsmSuche')
const { useTabsStore } = await import('../services/tabsStore')
const { msmSucheAdresse } = await import('../services/intern')

function erstelleTab(begriff: string, id = 'tab-stress') {
  return {
    ...useTabsStore.getState().tabs[0],
    id,
    url: msmSucheAdresse(begriff),
    nativDa: false,
  }
}

const trefferErgebnis = (titel: string) => ({
  treffer: [{ titel, url: `https://example.com/${encodeURIComponent(titel)}`, inhalt: `Inhalt von ${titel}` }],
  vorschlaege: [],
})

describe('Adversarial Stress Test: MsmSuche Performance & Stabilität (R2)', () => {
  beforeEach(() => {
    ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
    gemerktLeeren()
    api.mockReset()
    schutz.stand = { regeln: { aktiv: false, kategorien: [] }, beschaedigt: false }
    schutz.wirft = false
    schutz.haengt = false
    useTabsStore.setState({ tabs: [erstelleTab('Test')], aktivId: 'tab-stress', geschlossen: [] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe('1. Rapid category and keyword switching (AbortController & Race Condition Prevention)', () => {
    it('rapid category switching aborts intermediate requests and out-of-order resolution never overwrites state', async () => {
      const capturedSignals: AbortSignal[] = []
      const deferredResolvers: Record<string, (val: any) => void> = {}

      api.mockImplementation((pfad: string, optionen?: { body?: string; signal?: AbortSignal }) => {
        if (pfad === '/browser/suche/autocomplete' || pfad === '/browser/suche/wissen') {
          return Promise.resolve({ vorschlaege: [], wissen: null })
        }
        if (pfad === '/browser/suche') {
          const daten = JSON.parse(optionen?.body ?? '{}')
          const kat = daten.kategorie || 'web'
          if (optionen?.signal) {
            capturedSignals.push(optionen.signal)
          }

          return new Promise((resolve) => {
            deferredResolvers[kat] = resolve
            // Listen to abort
            optionen?.signal?.addEventListener('abort', () => {
              // Simulating network abort
            })
          })
        }
        return Promise.resolve({})
      })

      const { rerender } = render(<MsmSuche tab={erstelleTab('Tauri')} />)

      // Initial request for 'web'
      await waitFor(() => expect(capturedSignals.length).toBe(1))
      expect(capturedSignals[0].aborted).toBe(false)

      // Rapidly switch categories: 'images' -> 'videos' -> 'news'
      const bilderTab = screen.getByRole('tab', { name: 'Bilder' })
      const videosTab = screen.getByRole('tab', { name: 'Videos' })
      const newsTab = screen.getByRole('tab', { name: 'News' })

      act(() => {
        fireEvent.click(bilderTab)
      })
      await waitFor(() => expect(capturedSignals.length).toBe(2))
      // Web request should have been aborted
      expect(capturedSignals[0].aborted).toBe(true)
      expect(capturedSignals[1].aborted).toBe(false)

      act(() => {
        fireEvent.click(videosTab)
      })
      await waitFor(() => expect(capturedSignals.length).toBe(3))
      // Bilder request should have been aborted
      expect(capturedSignals[1].aborted).toBe(true)
      expect(capturedSignals[2].aborted).toBe(false)

      act(() => {
        fireEvent.click(newsTab)
      })
      await waitFor(() => expect(capturedSignals.length).toBe(4))
      // Videos request should have been aborted
      expect(capturedSignals[2].aborted).toBe(true)
      expect(capturedSignals[3].aborted).toBe(false)

      // Out-of-order resolution attack:
      // First, resolve News (the latest request)
      await act(async () => {
        deferredResolvers['news'](trefferErgebnis('NEWS_SIEGER'))
      })

      expect(await screen.findByText('NEWS_SIEGER')).toBeInTheDocument()

      // Now resolve the older, superseded requests (Bilder and Videos)
      await act(async () => {
        deferredResolvers['images'](trefferErgebnis('BILDER_VERSPÄTET'))
        deferredResolvers['videos'](trefferErgebnis('VIDEOS_VERSPÄTET'))
      })

      // Ensure the UI was NOT overwritten by the superseded responses
      expect(screen.queryByText('BILDER_VERSPÄTET')).not.toBeInTheDocument()
      expect(screen.queryByText('VIDEOS_VERSPÄTET')).not.toBeInTheDocument()
      expect(screen.getByText('NEWS_SIEGER')).toBeInTheDocument()

      // Ensure no error state ("Suche fehlgeschlagen") was triggered by the aborted requests
      expect(screen.queryByText('Suche fehlgeschlagen')).not.toBeInTheDocument()
    })

    it('rapid keyword switching aborts stale requests and renders only the latest query', async () => {
      const capturedSignals: AbortSignal[] = []
      const deferredResolvers: Record<string, (val: any) => void> = {}

      api.mockImplementation((pfad: string, optionen?: { body?: string; signal?: AbortSignal }) => {
        if (pfad === '/browser/suche/autocomplete' || pfad === '/browser/suche/wissen') {
          return Promise.resolve({ vorschlaege: [], wissen: null })
        }
        if (pfad === '/browser/suche') {
          const daten = JSON.parse(optionen?.body ?? '{}')
          const query = daten.q
          if (optionen?.signal) {
            capturedSignals.push(optionen.signal)
          }

          return new Promise((resolve) => {
            deferredResolvers[query] = resolve
          })
        }
        return Promise.resolve({})
      })

      const tab1 = erstelleTab('alpha')
      const { rerender } = render(<MsmSuche tab={tab1} />)
      await waitFor(() => expect(capturedSignals.length).toBe(1))
      expect(capturedSignals[0].aborted).toBe(false)

      // Rapidly switch tab url to 'beta' then 'gamma'
      const tab2 = erstelleTab('beta')
      rerender(<MsmSuche tab={tab2} />)
      await waitFor(() => expect(capturedSignals.length).toBe(2))
      expect(capturedSignals[0].aborted).toBe(true)

      const tab3 = erstelleTab('gamma')
      rerender(<MsmSuche tab={tab3} />)
      await waitFor(() => expect(capturedSignals.length).toBe(3))
      expect(capturedSignals[1].aborted).toBe(true)
      expect(capturedSignals[2].aborted).toBe(false)

      // Resolve in reverse order: gamma first, then alpha
      await act(async () => {
        deferredResolvers['gamma'](trefferErgebnis('GAMMA_FINAL'))
      })
      expect(await screen.findByText('GAMMA_FINAL')).toBeInTheDocument()

      await act(async () => {
        deferredResolvers['alpha'](trefferErgebnis('ALPHA_STALE'))
      })

      expect(screen.queryByText('ALPHA_STALE')).not.toBeInTheDocument()
      expect(screen.getByText('GAMMA_FINAL')).toBeInTheDocument()
    })
  })

  describe('2. Timeout and offline fallbacks (No infinite spinner, graceful recovery)', () => {
    it('request taking longer than 12s triggers timeout, aborts signal, and transitions out of loading state', async () => {
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
      let capturedSignal: AbortSignal | null = null

      api.mockImplementation((pfad: string, optionen?: { signal?: AbortSignal }) => {
        if (pfad === '/browser/suche') {
          capturedSignal = optionen?.signal || null
          return new Promise((_, reject) => {
            optionen?.signal?.addEventListener('abort', () => {
              const err = new DOMException('Search request timed out', 'TimeoutError')
              reject(err)
            })
          })
        }
        return Promise.resolve({ vorschlaege: [], wissen: null })
      })

      render(<MsmSuche tab={erstelleTab('TimeoutTest')} />)

      // Allow microtasks (nativ.schutzStand) to execute and trigger api call
      await vi.advanceTimersByTimeAsync(10)

      expect(screen.getByRole('status')).toBeInTheDocument()
      expect(capturedSignal).not.toBeNull()
      expect((capturedSignal as unknown as AbortSignal).aborted).toBe(false)

      // Fast-forward time past 12s timeout budget
      await vi.advanceTimersByTimeAsync(12005)

      // Abort signal should now be aborted due to timeout
      expect((capturedSignal as unknown as AbortSignal).aborted).toBe(true)

      // UI must transition away from loading spinner to offline/timeout fallback
      expect(screen.queryByText('Suche läuft…')).not.toBeInTheDocument()
      expect(screen.getByText('Keine Verbindung')).toBeInTheDocument()
    })

    it('network failure displays offline fallback and automatically recovers when back online', async () => {
      let versuche = 0
      api.mockImplementation((pfad: string) => {
        if (pfad === '/browser/suche') {
          versuche++
          if (versuche === 1) {
            return Promise.reject(new TypeError('Failed to fetch'))
          }
          return Promise.resolve(trefferErgebnis('ONLINE_TREFFER'))
        }
        return Promise.resolve({ vorschlaege: [], wissen: null })
      })

      render(<MsmSuche tab={erstelleTab('OfflineTest')} />)

      // Should show offline error message
      expect(await screen.findByText('Keine Verbindung')).toBeInTheDocument()
      expect(screen.queryByText('Suche läuft…')).not.toBeInTheDocument()

      // Dispatch 'online' event
      act(() => {
        window.dispatchEvent(new Event('online'))
      })

      // Should automatically retry and render recovered results
      expect(await screen.findByText('ONLINE_TREFFER')).toBeInTheDocument()
      expect(screen.queryByText('Keine Verbindung')).not.toBeInTheDocument()
    })
  })

  describe('3. 429 Rate limit mapping and UI notification', () => {
    it('HTTP 429 with code BROWSER_SUCHE_ZU_VIELE maps to zuViele: true and shows rate limit text', async () => {
      let versuche = 0
      api.mockImplementation((pfad: string) => {
        if (pfad === '/browser/suche') {
          versuche++
          if (versuche === 1) {
            return Promise.reject(
              new SanitizedApiError('Zu viele Anfragen', {
                status: 429,
                code: 'BROWSER_SUCHE_ZU_VIELE',
              }),
            )
          }
          return Promise.resolve(trefferErgebnis('NACH_WARTEZEIT'))
        }
        return Promise.resolve({ vorschlaege: [], wissen: null })
      })

      render(<MsmSuche tab={erstelleTab('RateLimitTest')} />)

      // Should display rate limit notice
      expect(await screen.findByText(/In einer Minute geht es weiter/)).toBeInTheDocument()
      expect(screen.queryByText('Keine Verbindung')).not.toBeInTheDocument()

      // Retry button should be clickable and recover
      const erneutButton = screen.getByRole('button', { name: 'Erneut versuchen' })
      act(() => {
        fireEvent.click(erneutButton)
      })

      expect(await screen.findByText('NACH_WARTEZEIT')).toBeInTheDocument()
      expect(screen.queryByText(/In einer Minute geht es weiter/)).not.toBeInTheDocument()
    })

    it('HTTP 429 with status 429 but missing code still flags zuViele: true', async () => {
      api.mockImplementation((pfad: string) => {
        if (pfad === '/browser/suche') {
          return Promise.reject(
            new SanitizedApiError('Rate limited', {
              status: 429,
              code: undefined,
            }),
          )
        }
        return Promise.resolve({ vorschlaege: [], wissen: null })
      })

      render(<MsmSuche tab={erstelleTab('RateLimitNoCodeTest')} />)

      expect(await screen.findByText(/In einer Minute geht es weiter/)).toBeInTheDocument()
    })
  })

  describe('4. Non-blocking IPC Protection Stand (Promise.race fallback)', () => {
    it('stalled schutzStand() hanging > 1.5s resolves via timeout and executes search with safe defaults', async () => {
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
      schutz.haengt = true
      const capturedRequests: Record<string, unknown>[] = []

      api.mockImplementation((pfad: string, optionen?: { body?: string }) => {
        if (pfad === '/browser/suche') {
          capturedRequests.push(JSON.parse(optionen?.body ?? '{}'))
          return Promise.resolve(trefferErgebnis('FALLBACK_TREFFER'))
        }
        return Promise.resolve({ vorschlaege: [], wissen: null })
      })

      render(<MsmSuche tab={erstelleTab('IpcHangTest')} />)

      // At t = 1000ms, search fetch must NOT yet have been triggered (waiting on IPC)
      await vi.advanceTimersByTimeAsync(1000)
      expect(capturedRequests.length).toBe(0)

      // At t = 1500ms, the 1.5s timeout fires, falling back to safe default true
      await vi.advanceTimersByTimeAsync(505)
      expect(capturedRequests.length).toBe(1)
      expect(capturedRequests[0].q).toBe('IpcHangTest')
      expect(capturedRequests[0].sicher).toBe(true)

      vi.useRealTimers()
    })

    it('rejecting schutzStand() recovers immediately without stalling and applies safe defaults', async () => {
      schutz.wirft = true
      const capturedRequests: Record<string, unknown>[] = []

      api.mockImplementation((pfad: string, optionen?: { body?: string }) => {
        if (pfad === '/browser/suche') {
          capturedRequests.push(JSON.parse(optionen?.body ?? '{}'))
          return Promise.resolve(trefferErgebnis('IPC_REJECT_RECOVERY'))
        }
        return Promise.resolve({ vorschlaege: [], wissen: null })
      })

      render(<MsmSuche tab={erstelleTab('IpcRejectTest')} />)

      await waitFor(() => expect(capturedRequests.length).toBe(1))
      expect(capturedRequests[0].q).toBe('IpcRejectTest')
      expect(capturedRequests[0].sicher).toBe(true)
      expect(await screen.findByText('IPC_REJECT_RECOVERY')).toBeInTheDocument()
    })
  })
})
