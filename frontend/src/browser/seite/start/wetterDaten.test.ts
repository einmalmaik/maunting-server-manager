import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({ api: vi.fn() }))

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))
vi.mock('@/api/client', async (original) => ({ ...(await original<object>()), api }))

const { useWetter } = await import('./wetterDaten')
const { useEinstellungenStore } = await import('../../services/einstellungenStore')
const { useSitzung } = await import('../../services/sitzung')

const ORT = { name: 'Berlin', land: 'DE', lat: 52.52, lon: 13.41 }

const STAND = {
  jetzt: { temp: 18, lage: 'klar', wind: 3.2, feuchte: 55, regen: 0, wolken: 10, zeit: '2026-10-22T12:00:00+00:00' },
  stunden: [{ stunde: 3, zeit: '2026-10-22T15:00:00+00:00', temp: 17, lage: 'klar' }],
  tage: [{ datum: '2026-10-22', min: 10, max: 18, lage: 'klar' }],
  quelle: 'NOAA GFS 0.50°',
  stand: '2026-10-22T06:00:00+00:00',
}

const flush = async () => {
  await act(async () => {
    await Promise.resolve()
  })
}

let nummer = 0
const neuerOrt = () => {
  nummer += 1
  return { ...ORT, lat: 40 + nummer / 100 }
}

beforeEach(() => {
  localStorage.clear()
  api.mockReset()
  useSitzung.setState({ stand: 'an' })
  useEinstellungenStore.setState({ wetterOrt: neuerOrt(), wetterEinheit: 'celsius' })
})

describe('Wetterdaten vom eigenen Server', () => {
  it('verwirft eine Antwort ohne brauchbaren Stand und schreibt keinen Spiegel', async () => {
    api.mockResolvedValue({ jetzt: { temp: 'heiß', lage: 'klar' }, stunden: [], tage: [] })
    const { result } = renderHook(() => useWetter())

    await waitFor(() => expect(api).toHaveBeenCalledTimes(1))
    await flush()

    expect(result.current).toEqual({ stand: null, fehler: null })
    expect(localStorage.getItem('msb:wetter')).toBeNull()
  })

  it('verwirft auch eine Antwort, deren Stunden keine Liste sind', async () => {
    api.mockResolvedValue({ jetzt: STAND.jetzt, stunden: 'kaputt', tage: [] })
    const { result } = renderHook(() => useWetter())

    await waitFor(() => expect(api).toHaveBeenCalledTimes(1))
    await flush()

    expect(result.current.stand).toBeNull()
    expect(localStorage.getItem('msb:wetter')).toBeNull()
  })

  it('meldet einen nicht eingerichteten Sidecar getrennt von einem allgemeinen Fehler', async () => {
    api.mockRejectedValueOnce({ code: 'BROWSER_WETTER_NICHT_EINGERICHTET' })
    const first = renderHook(() => useWetter())
    await waitFor(() => expect(first.result.current.fehler).toBe('nichtEingerichtet'))
    first.unmount()

    useEinstellungenStore.setState({ wetterOrt: neuerOrt() })
    api.mockRejectedValueOnce(new Error('Netz weg'))
    const second = renderHook(() => useWetter())
    await waitFor(() => expect(second.result.current.fehler).toBe('fehler'))
  })

  it('zeigt ohne Kopplung den letzten Spiegel und fragt den Server nicht', () => {
    const ort = useEinstellungenStore.getState().wetterOrt!
    localStorage.setItem('msb:wetter', JSON.stringify({ ort: `${ort.lat.toFixed(2)},${ort.lon.toFixed(2)}`, stand: STAND }))
    useSitzung.setState({ stand: 'aus' })

    const { result } = renderHook(() => useWetter())

    expect(result.current).toEqual({ stand: STAND, fehler: 'ungekoppelt' })
    expect(api).not.toHaveBeenCalled()
  })

  it('übergeht einen kaputten oder fremden Spiegel, ohne abzustürzen', async () => {
    localStorage.setItem('msb:wetter', '{kaputt')
    api.mockResolvedValue(STAND)

    const erster = renderHook(() => useWetter())
    expect(erster.result.current).toEqual({ stand: null, fehler: null })
    await waitFor(() => expect(erster.result.current.stand).toEqual(STAND))
    erster.unmount()

    localStorage.setItem('msb:wetter', JSON.stringify({ ort: '1.00,2.00', stand: STAND }))
    useSitzung.setState({ stand: 'aus' })
    useEinstellungenStore.setState({ wetterOrt: neuerOrt() })
    const zweiter = renderHook(() => useWetter())
    expect(zweiter.result.current).toEqual({ stand: null, fehler: 'ungekoppelt' })
  })

  it('schickt nur Koordinaten an die eigene Wetterroute', async () => {
    const ort = useEinstellungenStore.getState().wetterOrt!
    api.mockResolvedValue(STAND)

    renderHook(() => useWetter())
    await waitFor(() => expect(api).toHaveBeenCalledTimes(1))

    expect(api).toHaveBeenCalledWith('/browser/wetter', {
      method: 'POST',
      body: JSON.stringify({ lat: ort.lat, lon: ort.lon }),
    })
  })

  it('fragt denselben Ort innerhalb von 30 Minuten nicht erneut', async () => {
    useEinstellungenStore.setState({ wetterOrt: { ...ORT, lat: 44.44 } })
    api.mockResolvedValue(STAND)

    const erster = renderHook(() => useWetter())
    await waitFor(() => expect(api).toHaveBeenCalledTimes(1))
    erster.unmount()

    const zweiter = renderHook(() => useWetter())
    await flush()

    expect(api).toHaveBeenCalledTimes(1)
    expect(zweiter.result.current.stand).toEqual(STAND)
  })

  it('fragt nach einem Fehler beim nächsten Einhängen erneut', async () => {
    const ort = { ...ORT, lat: 45.55 }
    useEinstellungenStore.setState({ wetterOrt: ort })
    api.mockRejectedValueOnce(new Error('Netz weg')).mockResolvedValue(STAND)

    const erster = renderHook(() => useWetter())
    await waitFor(() => expect(erster.result.current.fehler).toBe('fehler'))
    erster.unmount()

    const zweiter = renderHook(() => useWetter())
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(zweiter.result.current.stand).toEqual(STAND))
  })

  it('fragt ohne Ort oder Kopplung gar nicht', () => {
    useEinstellungenStore.setState({ wetterOrt: null })
    const ohneOrt = renderHook(() => useWetter())
    expect(ohneOrt.result.current).toEqual({ stand: null, fehler: null })

    useEinstellungenStore.setState({ wetterOrt: neuerOrt() })
    useSitzung.setState({ stand: 'pruefen' })
    const ohneKopplung = renderHook(() => useWetter())
    expect(ohneKopplung.result.current).toEqual({ stand: null, fehler: 'ungekoppelt' })

    expect(api).not.toHaveBeenCalled()
  })
})
