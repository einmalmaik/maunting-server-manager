import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api, android } = vi.hoisted(() => ({ api: vi.fn(), android: vi.fn(() => true) }))

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))
vi.mock('@/api/client', async (original) => ({ ...(await original<object>()), api }))
vi.mock('./plattform', async (original) => ({ ...(await original<object>()), istAndroid: android }))

const { nativ } = await import('./nativ')
const { useEinstellungenStore } = await import('./einstellungenStore')
const { useSitzung } = await import('./sitzung')
const { useWetterWidget, wetterWidgetAnheften } = await import('./wetterWidget')

const ORT = { name: 'Berlin', land: 'DE', lat: 52.52, lon: 13.41 }
const setzen = vi.spyOn(nativ, 'widgetWetterSetzen')
const anheften = vi.spyOn(nativ, 'widgetWetterAnheften')

beforeEach(() => {
  localStorage.clear()
  api.mockReset()
  android.mockReturnValue(true)
  setzen.mockReset().mockResolvedValue(undefined)
  anheften.mockReset().mockResolvedValue(true)
  useEinstellungenStore.setState({ wetterOrt: ORT, wetterEinheit: 'celsius' })
  useSitzung.setState({ stand: 'an' })
})

describe('Wetter-Widget einrichten', () => {
  it('tut auf Nicht-Android nichts und fragt den Server nicht', async () => {
    android.mockReturnValue(false)

    await expect(wetterWidgetAnheften()).resolves.toBe(false)

    expect(api).not.toHaveBeenCalled()
    expect(setzen).not.toHaveBeenCalled()
    expect(anheften).not.toHaveBeenCalled()
  })

  it('tut ohne gewählten Ort nichts und fragt den Server nicht', async () => {
    useEinstellungenStore.setState({ wetterOrt: null })

    await expect(wetterWidgetAnheften()).resolves.toBe(false)

    expect(api).not.toHaveBeenCalled()
    expect(setzen).not.toHaveBeenCalled()
  })

  it('reicht einen Tokenfehler weiter, ohne das Widget zu beschreiben', async () => {
    api.mockRejectedValue(new Error('kein Token'))

    await expect(wetterWidgetAnheften()).rejects.toThrow('kein Token')

    expect(setzen).not.toHaveBeenCalled()
    expect(anheften).not.toHaveBeenCalled()
  })

  it('bricht ab, wenn die native Seite das Token nicht annimmt', async () => {
    api.mockResolvedValue({ token: 'pw_geheim' })
    setzen.mockRejectedValue(new Error('Keystore gesperrt'))

    await expect(wetterWidgetAnheften()).rejects.toThrow('Keystore gesperrt')

    expect(anheften).not.toHaveBeenCalled()
  })

  it('übergibt Token, Ort und Einheit ohne MSM-Adresse an die native Seite', async () => {
    api.mockResolvedValue({ token: 'pw_geheim' })

    await expect(wetterWidgetAnheften()).resolves.toBe(true)

    expect(setzen).toHaveBeenCalledWith({
      ort: 'Berlin',
      land: 'DE',
      lat: 52.52,
      lon: 13.41,
      einheit: 'celsius',
      token: 'pw_geheim',
    })
    expect(JSON.stringify(setzen.mock.calls[0][0])).not.toContain('http')
  })

  it('meldet false, wenn der Startbildschirm das Widget nicht aufnimmt', async () => {
    api.mockResolvedValue({ token: 'pw_geheim' })
    anheften.mockResolvedValue(false)

    await expect(wetterWidgetAnheften()).resolves.toBe(false)
  })
})

describe('Wetter-Widget beim Start', () => {
  it('richtet es ohne Kopplung nicht ein', async () => {
    useSitzung.setState({ stand: 'aus' })

    renderHook(() => useWetterWidget())
    await Promise.resolve()

    expect(api).not.toHaveBeenCalled()
    expect(setzen).not.toHaveBeenCalled()
  })

  it('richtet es bei gleichem Ort und gleicher Einheit nur einmal ein', async () => {
    api.mockResolvedValue({ token: 'pw_geheim' })

    const { rerender } = renderHook(() => useWetterWidget())
    await waitFor(() => expect(api).toHaveBeenCalledTimes(1))
    rerender()
    await Promise.resolve()

    expect(api).toHaveBeenCalledTimes(1)
  })

  it('richtet es nach einem Einheitenwechsel erneut ein', async () => {
    api.mockResolvedValue({ token: 'pw_geheim' })

    const { rerender } = renderHook(() => useWetterWidget())
    await waitFor(() => expect(api).toHaveBeenCalledTimes(1))

    useEinstellungenStore.setState({ wetterEinheit: 'fahrenheit' })
    rerender()
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2))
  })

  it('versucht es nach einem Fehler bei einer Ortsänderung erneut', async () => {
    api.mockRejectedValueOnce(new Error('Token weg')).mockResolvedValue({ token: 'pw_neu' })

    const { rerender } = renderHook(() => useWetterWidget())
    await waitFor(() => expect(api).toHaveBeenCalledTimes(1))

    useEinstellungenStore.setState({ wetterOrt: { ...ORT, lat: 48.14, lon: 11.58 } })
    rerender()
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2))
  })
})
