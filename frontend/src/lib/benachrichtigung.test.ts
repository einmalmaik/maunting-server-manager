import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const lage = vi.hoisted(() => ({ user: { device_notifications: true } as { device_notifications?: boolean } | null }))
const tauri = vi.hoisted(() => ({ invoke: vi.fn() }))

vi.mock('@tauri-apps/api/core', () => ({ invoke: tauri.invoke }))
vi.mock('@/stores/authStore', () => ({ useAuthStore: { getState: () => ({ user: lage.user }) } }))

import { sendeGeraeteBenachrichtigung } from './benachrichtigung'

function imTauriFenster(): void {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
}

function ausTauriFenster(): void {
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__
}

function ohneNotificationApi(): void {
  // jsdom kennt die Notification-API nicht; ein Test, der sie braucht, setzt
  // sie selbst (hier auf `undefined` für den Gegenfall).
  delete (globalThis as Record<string, unknown>).Notification
}

describe('sendeGeraeteBenachrichtigung', () => {
  beforeEach(() => {
    tauri.invoke.mockReset()
    lage.user = { device_notifications: true }
    ausTauriFenster()
    ohneNotificationApi()
  })

  afterEach(() => {
    ausTauriFenster()
    ohneNotificationApi()
  })

  it('sagt, dass es keinen Weg gibt, statt still false zurückzugeben', async () => {
    // Genau das war der Pontis-Fall: kein Befehl, kein Plugin, kein WebView-
    // Notification — und am Ende ein `false`, das niemand ansah.
    const ergebnis = await sendeGeraeteBenachrichtigung({ titel: 'T', text: 'X' })
    expect(ergebnis.weg).toBe('keiner')
    expect(ergebnis.ok).toBe(false)
    expect(ergebnis.fehler).toBeTruthy()
  })

  it('nimmt in der App den nativen Befehl', async () => {
    imTauriFenster()
    tauri.invoke.mockResolvedValue(undefined)
    const ergebnis = await sendeGeraeteBenachrichtigung({ titel: 'T', text: 'X' })
    expect(ergebnis).toEqual({ weg: 'tauri', ok: true })
    expect(tauri.invoke).toHaveBeenCalledWith('benachrichtigung_senden', { titel: 'T', text: 'X' })
  })

  it('fällt auf das Plugin zurück, wenn der Befehl fehlt', async () => {
    imTauriFenster()
    tauri.invoke.mockImplementation(async (befehl: string) => {
      if (befehl === 'benachrichtigung_senden') throw new Error('Command not found')
      if (befehl === 'plugin:notification|isPermissionGranted') return true
      return undefined
    })
    const ergebnis = await sendeGeraeteBenachrichtigung({ titel: 'T', text: 'X' })
    expect(ergebnis).toEqual({ weg: 'plugin', ok: true })
    expect(tauri.invoke).toHaveBeenCalledWith('plugin:notification|notify', expect.anything())
  })

  it('nennt den ersten Grund, wenn kein Weg greift', async () => {
    imTauriFenster()
    tauri.invoke.mockRejectedValue(new Error('nicht da'))
    const ergebnis = await sendeGeraeteBenachrichtigung({ titel: 'T', text: 'X' })
    expect(ergebnis.ok).toBe(false)
    expect(ergebnis.fehler).toContain('benachrichtigung_senden')
  })

  it('schweigt vollständig, wenn der Schalter aus ist — und ruft nichts auf', async () => {
    // Negativtest: kein Befehl, keine Systemmeldung, nur ein benannter Grund.
    lage.user = { device_notifications: false }
    imTauriFenster()
    tauri.invoke.mockResolvedValue(undefined)
    const ergebnis = await sendeGeraeteBenachrichtigung({ titel: 'T', text: 'X' })
    expect(ergebnis).toEqual({ weg: 'keiner', ok: false, fehler: 'device_notifications aus' })
    expect(tauri.invoke).not.toHaveBeenCalled()
  })

  it('nimmt im Browser die Benachrichtigung des Systems', async () => {
    const anzeigen = vi.fn()
    class FakeNotification {
      static permission = 'granted'
      static requestPermission = async () => 'granted'
      constructor(titel: string) {
        anzeigen(titel)
      }
    }
    ;(globalThis as Record<string, unknown>).Notification = FakeNotification

    const ergebnis = await sendeGeraeteBenachrichtigung({ titel: 'T', text: 'X' })
    expect(ergebnis).toEqual({ weg: 'notification', ok: true })
    expect(anzeigen).toHaveBeenCalledWith('T')
  })
})
