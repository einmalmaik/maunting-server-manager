// @vitest-environment node
/**
 * Die Zustelladresse für den geschlossenen Tab, je Mailbox.
 *
 * Still an drei Stellen, und jede davon merkt man erst, wenn es zu spät ist:
 * meldet diese Datei zu wenig, bleibt das Telefon stumm, während der offene
 * Tab alles bekommt — niemand sucht den Fehler dann hier. Meldet sie zu viel,
 * bleibt eine verlassene Gruppe als Zustellziel stehen. Und fehlt der eigene
 * Abdruck, bekommt man die Meldung über die eigene Nachricht.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const rufe: { pfad: string; methode?: string; koerper: any }[] = []
let apiKaputt = false

vi.mock('@/api/client', () => ({
  api: async (pfad: string, optionen?: { body?: string; method?: string }) => {
    if (apiKaputt) throw new Error('kein Netz')
    rufe.push({
      pfad,
      methode: optionen?.method,
      koerper: optionen?.body ? JSON.parse(optionen.body) : null,
    })
    return { ok: true }
  },
}))

import {
  eigenerPushAbdruck,
  kuendigeMailboxPush,
  leereMailboxPush,
  meldeMailboxPush,
} from './mailboxPush'

const A = 'a'.repeat(64)
const B = 'b'.repeat(64)
const TOKEN = 'c'.repeat(64)
const ADRESSE = 'https://fcm.googleapis.com/fcm/send/geraet-eins'

/** sha256(ADRESSE), im Test gegengerechnet statt abgeschrieben. */
async function abdruckVon(text: string): Promise<string> {
  const roh = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(roh))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

/**
 * Stellt einen Browser mit vorhandenem Push-Abonnement.
 *
 * `null` heißt: kein Abonnement — der Normalfall im Tauri-Fenster und bei
 * abgeschalteten Benachrichtigungen.
 */
function browserMit(abo: { endpoint: string } | null, registrierung: unknown = {}): void {
  const schluessel = (name: string) => {
    const bytes = new Uint8Array(name === 'p256dh' ? 65 : 16).fill(7)
    return bytes.buffer
  }
  ;(globalThis as any).window = globalThis
  ;(globalThis as any).PushManager = class {}
  ;(globalThis as any).btoa = (s: string) => Buffer.from(s, 'binary').toString('base64')
  // `navigator` ist in Node ein Getter ohne Setter — eine Zuweisung darauf
  // wirft. Deshalb die Eigenschaft ersetzen statt sie zu beschreiben.
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    writable: true,
    value: {
      serviceWorker: {
        getRegistration: async () => registrierung,
        ready: Promise.resolve({
          pushManager: {
            getSubscription: async () =>
              abo ? { endpoint: abo.endpoint, getKey: schluessel } : null,
          },
        }),
      },
    },
  })
}

describe('mailboxPush', () => {
  beforeEach(() => {
    rufe.length = 0
    apiKaputt = false
    leereMailboxPush()
    browserMit({ endpoint: ADRESSE })
  })

  it('meldet Adresse und Liste zusammen', async () => {
    await meldeMailboxPush([{ mailbox_id: A, mailbox_token: TOKEN }])

    expect(rufe).toHaveLength(1)
    expect(rufe[0].pfad).toBe('/social/e2ee/mailbox-push')
    expect(rufe[0].koerper.endpoint).toBe(ADRESSE)
    expect(rufe[0].koerper.eintraege).toEqual([{ mailbox_id: A, mailbox_token: TOKEN }])
    // Die Schlüssel des Browsers gehören dazu — ohne sie kann der Server
    // nichts verschlüsseln und die Zeile wäre wertlos.
    expect(rufe[0].koerper.p256dh).toBeTruthy()
    expect(rufe[0].koerper.auth).toBeTruthy()
  })

  it('rechnet den eigenen Abdruck und merkt ihn sich', async () => {
    expect(eigenerPushAbdruck()).toBe('')

    await meldeMailboxPush([{ mailbox_id: A }])

    expect(eigenerPushAbdruck()).toBe(await abdruckVon(ADRESSE))
  })

  it('meldet nichts ohne Abonnement im Browser', async () => {
    // Der Schalter „Gerätebenachrichtigungen" steht auf aus, oder es ist das
    // Tauri-Fenster ohne Service Worker. Beides darf hier nichts anlegen:
    // sonst hinge eine Zustelladresse am Konto, die niemand bedienen kann.
    browserMit(null)

    await meldeMailboxPush([{ mailbox_id: A }])

    expect(rufe).toEqual([])
  })

  it('meldet nichts ohne Registrierung', async () => {
    // `serviceWorker.ready` löst nie auf, wenn die Registrierung fehlt. Ein
    // `await` darauf bliebe still für immer stehen.
    //
    // `null` und nicht `undefined`: ein ausdrückliches `undefined` greift auf
    // den Vorgabewert des Parameters zurück, und der Test prüfte dann das
    // Gegenteil von dem, was hier steht.
    browserMit({ endpoint: ADRESSE }, null)

    await meldeMailboxPush([{ mailbox_id: A }])

    expect(rufe).toEqual([])
  })

  it('schickt dieselbe Liste nicht zweimal', async () => {
    await meldeMailboxPush([{ mailbox_id: A }])
    await meldeMailboxPush([{ mailbox_id: A }])

    expect(rufe).toHaveLength(1)
  })

  it('meldet erneut, wenn sich die Liste ändert', async () => {
    await meldeMailboxPush([{ mailbox_id: A }])
    await meldeMailboxPush([{ mailbox_id: A }, { mailbox_id: B }])

    expect(rufe).toHaveLength(2)
    expect(rufe[1].koerper.eintraege).toHaveLength(2)
  })

  it('meldet auch eine leere Liste', async () => {
    /*
     * Der Fall „letzte Gruppe verlassen". Das Backend räumt ab, was nicht mehr
     * genannt wird — würde hier bei leerer Liste gar nichts gehen, bliebe die
     * alte Mailbox als Zustellziel stehen und das Gerät bekäme weiter
     * Meldungen über Nachrichten, die es nicht mehr lesen kann.
     */
    await meldeMailboxPush([{ mailbox_id: A }])
    rufe.length = 0

    await meldeMailboxPush([])

    expect(rufe).toHaveLength(1)
    expect(rufe[0].koerper.eintraege).toEqual([])
  })

  it('merkt sich nichts, was nicht angekommen ist', async () => {
    apiKaputt = true
    await meldeMailboxPush([{ mailbox_id: A }])

    apiKaputt = false
    await meldeMailboxPush([{ mailbox_id: A }])

    expect(rufe).toHaveLength(1)
  })

  it('meldet erneut nach einem Adresswechsel', async () => {
    // Ein Browser kann sein Abonnement erneuern. Dann ist die alte Zeile tot
    // und die neue muss angelegt werden — auch wenn die Liste dieselbe ist.
    await meldeMailboxPush([{ mailbox_id: A }])
    browserMit({ endpoint: 'https://fcm.googleapis.com/fcm/send/geraet-neu' })

    await meldeMailboxPush([{ mailbox_id: A }])

    expect(rufe).toHaveLength(2)
    expect(rufe[1].koerper.endpoint).toContain('geraet-neu')
  })

  it('deckelt die Liste bei 200', async () => {
    // Dieselbe Grenze wie im Backend. Ohne sie fiele die ganze Meldung am
    // Schema durch, und der Browser stünde mit **keiner** Zustelladresse da.
    const viele = Array.from({ length: 250 }, (_, i) => ({ mailbox_id: String(i).padStart(64, '0') }))

    await meldeMailboxPush(viele)

    expect(rufe[0].koerper.eintraege).toHaveLength(200)
  })

  it('trägt beim Kündigen die eigene Adresse aus', async () => {
    await kuendigeMailboxPush()

    expect(rufe).toHaveLength(1)
    expect(rufe[0].methode).toBe('DELETE')
    expect(rufe[0].pfad).toContain(encodeURIComponent(ADRESSE))
  })

  it('meldet nach dem Kündigen wieder von vorn', async () => {
    await meldeMailboxPush([{ mailbox_id: A }])
    await kuendigeMailboxPush()
    rufe.length = 0

    await meldeMailboxPush([{ mailbox_id: A }])

    expect(rufe).toHaveLength(1)
  })

  it('hält eine Störung beim Kündigen aus', async () => {
    // Die Sitzung ist womöglich schon abgelaufen. Das Abmelden darf daran
    // nicht scheitern.
    apiKaputt = true
    await expect(kuendigeMailboxPush()).resolves.toBeUndefined()
  })
})
