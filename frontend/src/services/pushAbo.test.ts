/**
 * Wann dieser Browser abonniert — und vor allem: wann nicht.
 *
 * Die drei Fälle, in denen ein Abonnement Schaden anrichtet statt zu helfen:
 *
 * * **Ohne Erlaubnis.** `pushManager.subscribe` löst in manchen Browsern selbst
 *   den Berechtigungsdialog aus. Ein Dialog, den niemand angefordert hat, ist
 *   die schnellste Art, ein dauerhaftes „blockiert" zu kassieren — danach ist
 *   auch der Vordergrundweg tot.
 * * **Ohne Schlüssel des Panels.** Der Browser bindet sein Abonnement an den
 *   `applicationServerKey`. Gegen einen leeren abonnieren hieße: ein Abo, das
 *   nie bedient werden kann und das auch kein späterer Versuch repariert.
 * * **Beim Abmelden nicht gekündigt.** Dann bekäme das Gerät weiter
 *   Benachrichtigungen für ein Konto, das sich hier abgemeldet hat.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/social', () => ({
  getPushPublicKey: vi.fn(async () => 'BHDV6Ux8kTp7Ag'),
  meldePushAbo: vi.fn(async () => ({ ok: true })),
  loeschePushAbo: vi.fn(async () => ({ ok: true })),
}))

import { getPushPublicKey, loeschePushAbo, meldePushAbo } from '@/api/social'
import { abonniere, kuendige } from './pushAbo'

const ENDPUNKT = 'https://fcm.googleapis.com/fcm/send/beispiel'

function schluesselAls(text: string): ArrayBuffer {
  return new TextEncoder().encode(text).buffer as ArrayBuffer
}

/** Ein Browser, der alles kann. Einzelne Tests nehmen ihm etwas weg. */
function browserAufsetzen(optionen: { erlaubnis?: NotificationPermission; abo?: unknown } = {}) {
  const subscribe = vi.fn(async () => optionen.abo ?? abonnementAttrappe())
  const getSubscription = vi.fn(async () => (optionen.abo === undefined ? null : optionen.abo))
  const registration = { pushManager: { subscribe, getSubscription } }

  vi.stubGlobal('navigator', {
    serviceWorker: {
      ready: Promise.resolve(registration),
      getRegistration: vi.fn(async () => registration),
    },
  })
  vi.stubGlobal('PushManager', class {})
  vi.stubGlobal('Notification', { permission: optionen.erlaubnis ?? 'granted' })
  return { subscribe, getSubscription }
}

function abonnementAttrappe(unsubscribe = vi.fn(async () => true)) {
  return {
    endpoint: ENDPUNKT,
    getKey: (name: string) => schluesselAls(name === 'p256dh' ? 'punkt-65-bytes' : 'auth-16'),
    unsubscribe,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
  vi.mocked(getPushPublicKey).mockResolvedValue('BHDV6Ux8kTp7Ag')
})

describe('abonniere', () => {
  it('meldet die Adresse ans Panel, wenn alles vorliegt', async () => {
    browserAufsetzen()

    await expect(abonniere()).resolves.toBe(true)
    expect(meldePushAbo).toHaveBeenCalledWith({
      endpoint: ENDPUNKT,
      p256dh: expect.any(String),
      auth: expect.any(String),
    })
  })

  it('abonniert nicht ohne erteilte Erlaubnis', async () => {
    const { subscribe } = browserAufsetzen({ erlaubnis: 'default' })

    await expect(abonniere()).resolves.toBe(false)
    expect(subscribe).not.toHaveBeenCalled()
    expect(meldePushAbo).not.toHaveBeenCalled()
  })

  it('abonniert nicht nach einer Ablehnung', async () => {
    const { subscribe } = browserAufsetzen({ erlaubnis: 'denied' })

    await expect(abonniere()).resolves.toBe(false)
    expect(subscribe).not.toHaveBeenCalled()
  })

  it('abonniert nicht, wenn das Panel keinen Schlüssel hat', async () => {
    const { subscribe } = browserAufsetzen()
    vi.mocked(getPushPublicKey).mockResolvedValue('')

    await expect(abonniere()).resolves.toBe(false)
    expect(subscribe).not.toHaveBeenCalled()
  })

  it('nimmt ein bestehendes Abonnement, statt ein zweites anzulegen', async () => {
    const { subscribe } = browserAufsetzen({ abo: abonnementAttrappe() })

    await expect(abonniere()).resolves.toBe(true)
    expect(subscribe).not.toHaveBeenCalled()
    // Gemeldet wird trotzdem: der Server kann die Zeile verloren haben.
    expect(meldePushAbo).toHaveBeenCalledTimes(1)
  })

  it('bleibt still, wenn der Browser kein Push kann', async () => {
    vi.stubGlobal('navigator', {})
    vi.stubGlobal('Notification', { permission: 'granted' })

    await expect(abonniere()).resolves.toBe(false)
    expect(getPushPublicKey).not.toHaveBeenCalled()
  })

  it('hängt nicht, wenn gar kein Service Worker registriert ist', async () => {
    // `serviceWorker.ready` löst in diesem Fall nie auf. Im Tauri-Fenster ist
    // das der Normalfall; ein `await` darauf bliebe still für immer stehen.
    vi.stubGlobal('navigator', {
      serviceWorker: {
        ready: new Promise(() => {}),
        getRegistration: vi.fn(async () => undefined),
      },
    })
    vi.stubGlobal('PushManager', class {})
    vi.stubGlobal('Notification', { permission: 'granted' })

    await expect(abonniere()).resolves.toBe(false)
    expect(meldePushAbo).not.toHaveBeenCalled()
  })

  it('wirft nicht, wenn das Panel nicht antwortet', async () => {
    browserAufsetzen()
    vi.mocked(meldePushAbo).mockRejectedValue(new Error('503'))

    // Push ist eine Verbesserung, kein Bestandteil des Messengers. Ein
    // Fehlschlag darf nirgends vor dem Benutzer landen.
    await expect(abonniere()).resolves.toBe(false)
  })
})

describe('kuendige', () => {
  it('trägt erst beim Panel aus, dann beim Browser', async () => {
    const reihenfolge: string[] = []
    const unsubscribe = vi.fn(async () => {
      reihenfolge.push('browser')
      return true
    })
    vi.mocked(loeschePushAbo).mockImplementation(async () => {
      reihenfolge.push('panel')
      return { ok: true }
    })
    browserAufsetzen({ abo: abonnementAttrappe(unsubscribe) })

    await kuendige()

    // Andersherum bliebe bei einem Fehlschlag eine Zeile stehen, deren Adresse
    // danach niemand mehr kennt.
    expect(reihenfolge).toEqual(['panel', 'browser'])
    expect(loeschePushAbo).toHaveBeenCalledWith(ENDPUNKT)
  })

  it('kündigt beim Browser auch dann, wenn das Panel ablehnt', async () => {
    const unsubscribe = vi.fn(async () => true)
    vi.mocked(loeschePushAbo).mockRejectedValue(new Error('401'))
    browserAufsetzen({ abo: abonnementAttrappe(unsubscribe) })

    await kuendige()

    // Die Sitzung kann schon abgelaufen sein. Der Server räumt die Zeile
    // spätestens beim nächsten 410 des Push-Dienstes ab.
    expect(unsubscribe).toHaveBeenCalled()
  })

  it('tut nichts ohne bestehendes Abonnement', async () => {
    browserAufsetzen()

    await kuendige()

    expect(loeschePushAbo).not.toHaveBeenCalled()
  })
})
