/**
 * Der Dauerdienst auf Android: Ausweis anlegen, Dienst starten, widerrufen.
 *
 * Was hier schiefgehen kann, ist still: ein falscher Basispfad und der Dienst
 * verbindet sich nie; ein nicht widerrufener Ausweis bleibt nach dem
 * Ausschalten gültig; ein vergessener Mailbox-Stand macht den Dienst stumm.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const aufrufe = vi.hoisted(() => ({
  invoke: [] as { befehl: string; args?: Record<string, unknown> }[],
  api: [] as { pfad: string; methode: string; koerper: unknown }[],
  nachweis: [] as string[],
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: async (befehl: string, args?: Record<string, unknown>) => {
    aufrufe.invoke.push({ befehl, args })
    if (befehl === 'dienst_stand') return { aktiv: true, akkuBefreit: false }
    return undefined
  },
}))

vi.mock('@/api/client', () => ({
  api: async (pfad: string, optionen?: { method?: string; body?: string }) => {
    aufrufe.api.push({
      pfad,
      methode: optionen?.method ?? 'GET',
      koerper: optionen?.body ? JSON.parse(optionen.body) : null,
    })
    if (pfad === '/notifications/channels') return { id: 7, token: 'a'.repeat(64) }
    return undefined
  },
}))

vi.mock('@/services/passkeyService', () => ({
  passkeyNachweis: async (zweck: string) => {
    aufrufe.nachweis.push(zweck)
    return { type: 'browser', vorgang: 'v'.repeat(32) }
  },
}))

vi.mock('@/services/mailboxAbo', () => ({
  offeneMailboxEintraege: () => [{ mailbox_id: 'mb-1' }],
}))

vi.mock('@/config/api', () => ({ getEffectiveApiUrl: () => 'https://panel.test' }))

import { ausschalten, dienstVerfuegbar, einschalten, mailboxenMelden, stand } from './hintergrunddienst'

const ruhe = () => new Promise((r) => setTimeout(r, 0))

describe('hintergrunddienst', () => {
  beforeEach(() => {
    aufrufe.invoke.length = 0
    aufrufe.api.length = 0
    aufrufe.nachweis.length = 0
    localStorage.clear()
    Object.defineProperty(navigator, 'userAgent', {
      value: 'Mozilla/5.0 (Linux; Android 14; Pixel 8)',
      configurable: true,
    })
    ;(window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ = {}
  })

  it('legt den Ausweis mit frischem Nachweis an und startet den Dienst', async () => {
    const ok = await einschalten('Telefon')
    await ruhe()

    expect(ok).toBe(true)
    expect(aufrufe.nachweis).toEqual(['notification_channel'])
    const anlegen = aufrufe.api.find((a) => a.methode === 'POST')
    expect(anlegen?.pfad).toBe('/notifications/channels')
    expect(anlegen?.koerper).toMatchObject({
      label: 'Telefon',
      passkey: { type: 'browser', vorgang: 'v'.repeat(32) },
    })

    const start = aufrufe.invoke.find((a) => a.befehl === 'dienst_starten')
    expect(start?.args).toMatchObject({
      ausweis: 'a'.repeat(64),
      basis: 'https://panel.test',
      mailboxen: JSON.stringify([{ mailbox_id: 'mb-1' }]),
    })
    expect(localStorage.getItem('msm:hintergrundkanal')).toBe('7')
  })

  it('schaltet den Dienst ab und widerruft den Ausweis', async () => {
    await einschalten('Telefon')
    aufrufe.invoke.length = 0
    aufrufe.api.length = 0

    await ausschalten()

    expect(aufrufe.invoke.map((a) => a.befehl)).toEqual(['dienst_stoppen'])
    expect(aufrufe.api).toEqual([
      { pfad: '/notifications/channels/7', methode: 'DELETE', koerper: null },
    ])
    expect(localStorage.getItem('msm:hintergrundkanal')).toBeNull()
  })

  it('meldet die geöffneten Mailboxen nach', async () => {
    await mailboxenMelden()

    const nach = aufrufe.invoke.find((a) => a.befehl === 'dienst_mailboxen')
    expect(nach?.args).toEqual({ mailboxen: JSON.stringify([{ mailbox_id: 'mb-1' }]) })
  })

  it('liest den Stand aus Android, nicht aus dem Fenster', async () => {
    await expect(stand()).resolves.toEqual({ aktiv: true, akkuBefreit: false })
  })

  it('tut auf einem Rechner nichts', async () => {
    Object.defineProperty(navigator, 'userAgent', { value: 'Mozilla/5.0 (Windows NT 10.0)', configurable: true })

    expect(dienstVerfuegbar()).toBe(false)
    await expect(einschalten()).resolves.toBe(false)
    await ausschalten()

    expect(aufrufe.invoke).toEqual([])
    expect(aufrufe.nachweis).toEqual([])
  })
})
