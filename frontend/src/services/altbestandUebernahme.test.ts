/**
 * Die einmalige Übernahme des Altverlaufs.
 *
 * Der wichtigste Fall steht nicht am Anfang, sondern in der Mitte: schlägt eine
 * Mailbox fehl, **bleibt der Kontoschlüssel liegen**. Wer ihn vorschnell
 * löscht, verliert genau den Verlauf, den diese Datei retten soll — und zwar
 * endgültig, denn die Umschläge auf dem Server sind ohne ihn nichts.
 *
 * Node-Umgebung: DIS prüft Eingaben mit `instanceof Uint8Array`, und unter
 * jsdom liefert `TextEncoder` ein Array aus der falschen Realm.
 */

// @vitest-environment node

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { encryptE2eeHybrid, generateLocalE2eeKeyPair } from './e2eeCrypto'

const { umschlaege, schluesselAblage, lokal } = vi.hoisted(() => ({
  umschlaege: new Map<string, any[]>(),
  schluesselAblage: new Map<number, { publicKeyJwk: string; privateKeyJwk: string } | null>(),
  lokal: new Map<string, any[]>(),
}))

vi.mock('@/api/social', () => ({
  fetchE2eeEnvelopes: vi.fn(async (mid: string) => {
    const liste = umschlaege.get(mid)
    if (!liste) throw new Error('Mailbox nicht erreichbar')
    return liste
  }),
}))

vi.mock('./e2eeCrypto', async () => {
  const echt = await vi.importActual<typeof import('./e2eeCrypto')>('./e2eeCrypto')
  return {
    ...echt,
    getLocalKeyPair: vi.fn(async (userId: number) => schluesselAblage.get(userId) ?? null),
    deleteLocalKeyPair: vi.fn(async (userId: number) => {
      schluesselAblage.delete(userId)
    }),
  }
})

vi.mock('./messengerLocalStore', async () => {
  const echt = await vi.importActual<typeof import('./messengerLocalStore')>('./messengerLocalStore')
  return {
    ...echt,
    loadLocalMessages: vi.fn(async (mid: string) => lokal.get(mid) ?? []),
    saveLocalMessages: vi.fn(async (mid: string, msgs: any[]) => {
      lokal.set(mid, msgs)
    }),
  }
})

const { uebernimmAltbestand, setzeUebernahmeZurueckFuerTest } = await import('./altbestandUebernahme')

const ICH = 42
const DU = 99
const MAILBOX = 'mailbox-alt-1'

/** Ein Umschlag, wie ihn der Messenger vor der Umstellung geschrieben hat. */
async function altUmschlag(
  id: number,
  nutzlast: Record<string, unknown>,
  empfaengerPub: string,
  extra: Record<string, unknown> = {},
) {
  return {
    id,
    blind_mailbox_id: MAILBOX,
    ciphertext_envelope: await encryptE2eeHybrid(JSON.stringify(nutzlast), empfaengerPub),
    client_uuid: `uuid-${id}`,
    created_at: new Date(2026, 0, 1, 12, id).toISOString(),
    ...extra,
  }
}

// localStorage gibt es unter Node nicht; der Merker braucht einen.
const speicher = new Map<string, string>()
;(globalThis as any).localStorage = {
  getItem: (k: string) => speicher.get(k) ?? null,
  setItem: (k: string, v: string) => speicher.set(k, v),
  removeItem: (k: string) => speicher.delete(k),
}

describe('Übernahme des Altverlaufs', () => {
  let paar: { publicKeyJwk: string; privateKeyJwk: string }

  beforeEach(async () => {
    umschlaege.clear()
    lokal.clear()
    schluesselAblage.clear()
    speicher.clear()
    setzeUebernahmeZurueckFuerTest(ICH)
    if (!paar) paar = await generateLocalE2eeKeyPair()
    schluesselAblage.set(ICH, paar)
  }, 30_000)

  it('holt den alten Verlauf herüber und löscht danach den Kontoschlüssel', async () => {
    umschlaege.set(MAILBOX, [
      await altUmschlag(1, { client_uuid: 'a', sender_id: DU, text: 'Hallo von früher' }, paar.publicKeyJwk),
      await altUmschlag(2, { client_uuid: 'b', sender_id: ICH, text: 'Meine Antwort' }, paar.publicKeyJwk),
    ])

    const ergebnis = await uebernimmAltbestand(ICH, [MAILBOX])

    expect(ergebnis).toEqual({ abgeschlossen: true, uebernommen: 2, offen: 0 })
    const verlauf = lokal.get(MAILBOX)!
    expect(verlauf.map((m) => m.text)).toEqual(['Hallo von früher', 'Meine Antwort'])
    // Die eigene Zeile muss als eigene erkannt werden, sonst steht sie links.
    expect(verlauf.find((m) => m.text === 'Meine Antwort')!.isSelf).toBe(true)
    expect(verlauf.find((m) => m.text === 'Hallo von früher')!.isSelf).toBe(false)
    // Der Schlüssel ist weg.
    expect(schluesselAblage.has(ICH)).toBe(false)
  }, 30_000)

  it('lässt den Kontoschlüssel liegen, wenn eine Mailbox nicht erreichbar war', async () => {
    // Genau hier entscheidet sich, ob ein abgebrochener Lauf Verlauf kostet.
    umschlaege.set(MAILBOX, [
      await altUmschlag(1, { client_uuid: 'a', sender_id: DU, text: 'Gerettet' }, paar.publicKeyJwk),
    ])

    const ergebnis = await uebernimmAltbestand(ICH, [MAILBOX, 'mailbox-offline'])

    expect(ergebnis.abgeschlossen).toBe(false)
    expect(ergebnis.offen).toBe(1)
    expect(ergebnis.uebernommen).toBe(1)
    expect(schluesselAblage.has(ICH)).toBe(true)
    expect(speicher.has('msm_altbestand_v1_' + ICH)).toBe(false)
  }, 30_000)

  it('nimmt beim nächsten Start den Faden wieder auf', async () => {
    umschlaege.set(MAILBOX, [
      await altUmschlag(1, { client_uuid: 'a', sender_id: DU, text: 'Gerettet' }, paar.publicKeyJwk),
    ])
    await uebernimmAltbestand(ICH, [MAILBOX, 'mailbox-offline'])
    expect(schluesselAblage.has(ICH)).toBe(true)

    // Zweiter Anlauf, diesmal ist alles da.
    umschlaege.set('mailbox-offline', [])
    const zweiter = await uebernimmAltbestand(ICH, [MAILBOX, 'mailbox-offline'])

    expect(zweiter.abgeschlossen).toBe(true)
    expect(schluesselAblage.has(ICH)).toBe(false)
    // Kein doppelter Verlauf: dieselbe Umschlagkennung, dieselbe Zeile.
    expect(lokal.get(MAILBOX)!.filter((m) => m.text === 'Gerettet')).toHaveLength(1)
  }, 30_000)

  it('läuft nur ein einziges Mal', async () => {
    umschlaege.set(MAILBOX, [
      await altUmschlag(1, { client_uuid: 'a', sender_id: DU, text: 'Einmalig' }, paar.publicKeyJwk),
    ])
    await uebernimmAltbestand(ICH, [MAILBOX])
    expect(speicher.get('msm_altbestand_v1_' + ICH)).toBe('1')

    // Der Schlüssel käme nie zurück, aber selbst wenn: der Merker hält.
    schluesselAblage.set(ICH, paar)
    const zweiter = await uebernimmAltbestand(ICH, [MAILBOX])
    expect(zweiter.uebernommen).toBe(0)
    expect(schluesselAblage.has(ICH)).toBe(true)
  }, 30_000)

  it('tut nichts, wenn dieses Gerät nie einen Kontoschlüssel hatte', async () => {
    schluesselAblage.delete(ICH)
    umschlaege.set(MAILBOX, [])

    const ergebnis = await uebernimmAltbestand(ICH, [MAILBOX])

    expect(ergebnis).toEqual({ abgeschlossen: true, uebernommen: 0, offen: 0 })
    expect(speicher.get('msm_altbestand_v1_' + ICH)).toBe('1')
  })

  it('lässt Quittungen und fremde Umschläge draußen', async () => {
    const fremd = await generateLocalE2eeKeyPair()
    umschlaege.set(MAILBOX, [
      await altUmschlag(1, { client_uuid: 'a', sender_id: DU, text: 'Echte Nachricht' }, paar.publicKeyJwk),
      // Ein Steuerpaket, das als Nachricht ausgegeben würde, wäre eine leere Zeile.
      await altUmschlag(2, { type: 'read_receipt', read_up_to_id: 1, reader_id: DU }, paar.publicKeyJwk),
      // Als Steuerumschlag gekennzeichnet.
      await altUmschlag(3, { type: 'delivery_receipt' }, paar.publicKeyJwk, { is_control: true }),
      // Für ein anderes Konto versiegelt: lässt sich gar nicht öffnen.
      await altUmschlag(4, { client_uuid: 'd', sender_id: DU, text: 'Nicht für mich' }, fremd.publicKeyJwk),
    ])

    const ergebnis = await uebernimmAltbestand(ICH, [MAILBOX])

    expect(ergebnis.uebernommen).toBe(1)
    expect(lokal.get(MAILBOX)!.map((m) => m.text)).toEqual(['Echte Nachricht'])
  }, 45_000)

  it('mischt den Altbestand unter den neuen Verlauf, statt ihn zu ersetzen', async () => {
    // Auf demselben Gerät liegt längst Verlauf aus der Ratchet-Zeit. Der ist
    // der aktuellere Stand und darf nicht weggewischt werden.
    lokal.set(MAILBOX, [
      {
        id: 50,
        clientUuid: 'neu',
        senderId: DU,
        text: 'Aus der Ratchet-Zeit',
        createdAt: new Date(2026, 5, 1).toISOString(),
        isSelf: false,
      },
    ])
    umschlaege.set(MAILBOX, [
      await altUmschlag(1, { client_uuid: 'alt', sender_id: DU, text: 'Von früher' }, paar.publicKeyJwk),
    ])

    await uebernimmAltbestand(ICH, [MAILBOX])

    const texte = lokal.get(MAILBOX)!.map((m) => m.text)
    expect(texte).toContain('Aus der Ratchet-Zeit')
    expect(texte).toContain('Von früher')
  }, 30_000)

  it('liest auch den reinen Text ohne JSON-Hülle', async () => {
    // Von Hand gebaut, weil der Inhalt hier bewusst kein JSON ist.
    umschlaege.set(MAILBOX, [
      {
        id: 1,
        blind_mailbox_id: MAILBOX,
        ciphertext_envelope: await encryptE2eeHybrid('[ME]:Ganz alter Text', paar.publicKeyJwk),
        client_uuid: 'uuid-1',
        created_at: new Date(2026, 0, 1).toISOString(),
      },
    ])

    await uebernimmAltbestand(ICH, [MAILBOX])

    const zeile = lokal.get(MAILBOX)![0]
    expect(zeile.text).toBe('Ganz alter Text')
    expect(zeile.isSelf).toBe(true)
  }, 30_000)
})
