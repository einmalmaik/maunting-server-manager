// @vitest-environment node
/**
 * Was der Echtzeitstrom hören soll.
 *
 * Klein, aber an einer Stelle, an der ein Fehler still ist: meldet diese Datei
 * zu wenig, bleibt der Messenger stumm und niemand sieht warum — die
 * Nachrichten kommen ja an, nur eben erst beim nächsten Abruf. Meldet sie zu
 * oft, hängt an jeder ankommenden Nachricht ein Aufruf.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const rufe: { pfad: string; koerper: any }[] = []
let apiKaputt = false

vi.mock('@/api/client', () => ({
  api: async (pfad: string, optionen?: { body?: string }) => {
    if (apiKaputt) throw new Error('kein Netz')
    rufe.push({ pfad, koerper: optionen?.body ? JSON.parse(optionen.body) : null })
    return { ok: true }
  },
}))

const anPush: { mailbox_id: string; mailbox_token?: string }[][] = []

vi.mock('@/services/mailboxPush', () => ({
  meldeMailboxPush: async (eintraege: { mailbox_id: string }[]) => {
    anPush.push(eintraege as never)
  },
}))

import {
  abonniereMailbox,
  kuendigeMailbox,
  leereMailboxAbos,
  merkeStromKennung,
  offeneMailboxAbos,
  vergissStromKennung,
} from './mailboxAbo'

const A = 'a'.repeat(64)
const B = 'b'.repeat(64)
const TOKEN = 'c'.repeat(64)

/** Lässt die angehängten Meldungen durchlaufen. */
const ruhe = () => new Promise((r) => setTimeout(r, 0))

describe('mailboxAbo', () => {
  beforeEach(() => {
    rufe.length = 0
    anPush.length = 0
    apiKaputt = false
    leereMailboxAbos()
  })

  it('meldet nichts, solange der Strom nicht steht', async () => {
    // Die Reihenfolge ist nicht vorhersagbar: der Messenger kennt seine
    // Mailbox oft, bevor der Strom sich vorgestellt hat.
    abonniereMailbox(A)
    await ruhe()

    expect(rufe).toEqual([])
    expect(offeneMailboxAbos()).toEqual([A])
  })

  it('meldet nach, sobald der Strom steht', async () => {
    abonniereMailbox(A)
    merkeStromKennung('conn-1')
    await ruhe()

    expect(rufe).toHaveLength(1)
    expect(rufe[0].pfad).toBe('/events/mailboxes')
    expect(rufe[0].koerper).toEqual({
      conn_id: 'conn-1',
      eintraege: [{ mailbox_id: A }],
    })
  })

  it('schickt das Token mit, wo es eines gibt', async () => {
    merkeStromKennung('conn-1')
    await ruhe()
    rufe.length = 0

    abonniereMailbox(A, TOKEN)
    await ruhe()

    expect(rufe[0].koerper.eintraege).toEqual([{ mailbox_id: A, mailbox_token: TOKEN }])
  })

  it('schickt dieselbe Liste nicht zweimal', async () => {
    // Der Nachweis wird bei jeder ankommenden Schlüsselzustellung neu gesetzt.
    // Ohne diese Bremse hinge an jeder ein Aufruf.
    merkeStromKennung('conn-1')
    abonniereMailbox(A, TOKEN)
    await ruhe()
    const nachErstem = rufe.length

    abonniereMailbox(A, TOKEN)
    await ruhe()

    expect(rufe.length).toBe(nachErstem)
  })

  it('meldet erneut, wenn sich das Token ändert', async () => {
    merkeStromKennung('conn-1')
    abonniereMailbox(A, TOKEN)
    await ruhe()
    rufe.length = 0

    abonniereMailbox(A, 'd'.repeat(64))
    await ruhe()

    expect(rufe).toHaveLength(1)
  })

  it('ersetzt die Liste, statt anzuhängen', async () => {
    merkeStromKennung('conn-1')
    abonniereMailbox(A)
    abonniereMailbox(B)
    await ruhe()
    rufe.length = 0

    kuendigeMailbox(A)
    await ruhe()

    expect(rufe).toHaveLength(1)
    expect(rufe[0].koerper.eintraege).toEqual([{ mailbox_id: B }])
  })

  it('meldet die ganze Liste erneut nach einem Neuverbinden', async () => {
    /*
     * Der Fall, der den Messenger sonst stumm zurücklässt: nach einem
     * Netzwechsel hat der Strom eine neue Kennung, und der Server weiss von
     * den Abos der alten Verbindung nichts mehr. Ohne das Zurücksetzen des
     * Abdrucks hielte diese Datei die Liste für längst gemeldet.
     */
    merkeStromKennung('conn-1')
    abonniereMailbox(A, TOKEN)
    await ruhe()
    rufe.length = 0

    vergissStromKennung()
    merkeStromKennung('conn-2')
    await ruhe()

    expect(rufe).toHaveLength(1)
    expect(rufe[0].koerper.conn_id).toBe('conn-2')
    expect(rufe[0].koerper.eintraege).toEqual([{ mailbox_id: A, mailbox_token: TOKEN }])
  })

  it('merkt sich nichts, was nicht angekommen ist', async () => {
    // Ein gescheiterter Aufruf darf nicht als gemeldet gelten, sonst bliebe
    // die Mailbox bis zur nächsten Änderung unabonniert.
    merkeStromKennung('conn-1')
    apiKaputt = true
    abonniereMailbox(A)
    await ruhe()

    apiKaputt = false
    abonniereMailbox(B)
    await ruhe()

    expect(rufe).toHaveLength(1)
    expect(rufe[0].koerper.eintraege).toEqual([{ mailbox_id: A }, { mailbox_id: B }])
  })

  it('liest Kennungen schreibungsunabhängig', () => {
    abonniereMailbox(A.toUpperCase())
    expect(offeneMailboxAbos()).toEqual([A])
  })

  it('übergeht eine leere Kennung', async () => {
    merkeStromKennung('conn-1')
    abonniereMailbox('')
    abonniereMailbox('   ')
    await ruhe()

    expect(offeneMailboxAbos()).toEqual([])
    expect(rufe).toEqual([])
  })

  it('gibt dieselbe Liste an die Push-Adresse weiter', async () => {
    /*
     * Die Kopplung, die sonst still bricht. Der Strom und Push haben
     * verschiedene Lebensdauern, aber **eine** Liste — führte jede Seite ihre
     * eigene, erführe der Benutzer je nach Tab-Zustand etwas anderes, und
     * niemand suchte den Fehler an dieser Stelle.
     *
     * Ohne `conn_id`, mit Absicht: die Zustelladresse soll gerade dann noch
     * stehen, wenn keine Verbindung da ist.
     */
    abonniereMailbox(A, TOKEN)
    await ruhe()

    expect(anPush).toEqual([[{ mailbox_id: A, mailbox_token: TOKEN }]])
    expect(rufe).toEqual([]) // der Strom steht ja noch nicht
  })

  it('meldet der Push-Adresse auch das Kündigen', async () => {
    // Der Fall „Gruppe verlassen". Bliebe die Zeile stehen, bekäme das Gerät
    // weiter Meldungen über Nachrichten, die es nicht mehr lesen kann.
    abonniereMailbox(A)
    abonniereMailbox(B)
    await ruhe()
    anPush.length = 0

    kuendigeMailbox(A)
    await ruhe()

    expect(anPush).toEqual([[{ mailbox_id: B }]])
  })

  it('vergisst beim Abmelden alles', async () => {
    merkeStromKennung('conn-1')
    abonniereMailbox(A)
    await ruhe()

    leereMailboxAbos()

    expect(offeneMailboxAbos()).toEqual([])
    // Und ohne Strom meldet auch nichts mehr.
    rufe.length = 0
    abonniereMailbox(B)
    await ruhe()
    expect(rufe).toEqual([])
  })
})
