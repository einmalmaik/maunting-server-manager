// @vitest-environment node
/**
 * Absturz an jeder lokalen Commitgrenze — der Nachweis, nicht die Absicht.
 *
 * Ein echter Stromausfall lässt sich nicht im Test herstellen; was hier
 * stattdessen passiert, ist strenger als ein gescheitertes `put`: Ab der
 * N-ten Schreibtransaktion verweigert die Datenbank **jede weitere
 * Transaktion**, so wie ein beendeter Prozess nichts mehr schreibt. Danach
 * werden alle Verbindungen geschlossen und die Modulmerker geleert — der
 * nächste Aufruf öffnet dieselbe Ablage neu, wie nach einem Neustart.
 *
 * Geprüft werden die Grenzen, die der Plan (§6) verlangt:
 *
 * Empfang: vor/nach Klartext, vor/nach Ratchet-Zustand, vor/nach
 * Nachrichtenzeile, vor/nach Dedupe-Marke. Senden: vor/nach Ratchet-Zustand
 * (samt Versandabsicht), vor/nach Nachrichtenzeile.
 *
 * Ein einzelner IndexedDB-Transaktionsraum über beide Datenbanken ist
 * technisch unmöglich. Deshalb liegt die Beweiskraft hier auf der Heilung:
 * kein Ratchet-Schritt doppelt, kein Klartext ohne Zustand und umgekehrt.
 */

import { IDBFactory } from 'fake-indexeddb'
import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { bytesToBase64, utf8ToBytes } from '@msdis/shield/core'
import {
  encryptMessage,
  generateRatchetKeyPair,
  initReceiverState,
  initSenderState,
  serializeRatchetMessage,
  type RatchetState,
} from '@msdis/shield/messaging'
import { randomBytes } from '@msdis/shield/random'

vi.mock('../e2eeGeraet', () => ({
  eigenesGeraet: async () => ({ kennung: 'bob-device' }),
}))

import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import {
  leseUmschlagKlartext,
  schliesseLokaleAblageFuerTest,
  speichereUmschlagKlartext,
} from '../messengerLocalStore'
import {
  ausstehendeZustellungen,
  hatZustellung,
  merkeZustellstand,
  schritt,
  setzeAblageFuerTest,
  sitzungsId,
  type AusstehendeZustellung,
} from '../ratchetSpeicher'
import { baueUmschlag, gebundeneDaten, type DrKontext } from './format'
import { liesDrUmschlag, type KlartextAblage } from './lesen'

const KONTO = 2
const KONTEXT: DrKontext = { eigeneId: KONTO, peerId: 1 }
const MEIN_GERAET = 'bob-device'
const IHR_GERAET = 'alice-device'
const SITZUNG = sitzungsId(MEIN_GERAET, 1, IHR_GERAET)
const MAILBOX = 'mailbox-1'

const echt = {
  transaction: IDBDatabase.prototype.transaction,
  open: IDBFactory.prototype.open,
}

const offene = new Set<IDBDatabase>()
let erlaubteSchreibvorgaenge = Number.POSITIVE_INFINITY
let injektionAktiv = false

/**
 * Merkt sich jede geöffnete Verbindung, damit `neustart` sie schliessen kann.
 * Einmal installiert; die Injektion tauscht nur `transaction`.
 */
function verfolgeVerbindungen(): void {
  IDBFactory.prototype.open = function (
    this: IDBFactory,
    ...argumente: unknown[]
  ): IDBOpenDBRequest {
    const anfrage = echt.open.apply(this, argumente as never)
    anfrage.addEventListener('success', () => offene.add(anfrage.result))
    return anfrage
  } as typeof IDBFactory.prototype.open
}

/** Erlaubt noch `n` Schreibtransaktionen; danach ist der Prozess „tot". */
function starteAbsturz(n: number): void {
  erlaubteSchreibvorgaenge = n
  injektionAktiv = true
  IDBDatabase.prototype.transaction = function (
    this: IDBDatabase,
    ...argumente: unknown[]
  ): IDBTransaction {
    const modus = argumente[1]
    if (modus === 'readwrite') {
      if (erlaubteSchreibvorgaenge <= 0) {
        throw new DOMException('Stromausfall', 'InvalidStateError')
      }
      erlaubteSchreibvorgaenge -= 1
    }
    return echt.transaction.apply(this, argumente as never)
  } as typeof IDBDatabase.prototype.transaction
}

/** Beendet die Injektion und schliesst jede Verbindung — der „Neustart". */
function neustart(): void {
  if (injektionAktiv) {
    IDBDatabase.prototype.transaction = echt.transaction
    injektionAktiv = false
  }
  for (const db of offene) {
    try {
      db.close()
    } catch {
      // Schon zu.
    }
  }
  offene.clear()
  erlaubteSchreibvorgaenge = Number.POSITIVE_INFINITY
  // Beide Modulmerker: die Ratchet-Ablage öffnet je Aufruf neu, der
  // Messenger hält seine Verbindung.
  setzeAblageFuerTest(null)
  schliesseLokaleAblageFuerTest()
}

// ---- Aufbau ---------------------------------------------------------------

interface Paar {
  alice: RatchetState
  bob: RatchetState
  umschlag: string
  /** Der Senderzustand nach der ersten Nachricht — für die zweite. */
  aliceDanach: RatchetState
}

async function bauePaar(text = 'erste'): Promise<Paar> {
  const paar = await generateRatchetKeyPair()
  const geheimnis = randomBytes(32)
  // Beide Seiten bilden dieselbe Zeichenkette: `gebundeneDaten` sortiert
  // Konten und Geräte, die Perspektive ist also egal.
  const ad = gebundeneDaten({ eigeneId: 1, peerId: 2 }, IHR_GERAET, MEIN_GERAET)
  const alice = await initSenderState({
    sharedSecret: geheimnis.slice(),
    remotePublicKey: paar.publicKey,
    associatedData: ad,
  })
  const bob = await initReceiverState({
    sharedSecret: geheimnis.slice(),
    dhKeyPair: paar,
    associatedData: ad,
  })
  const { nextState, message } = await encryptMessage(alice, utf8ToBytes(text))
  return {
    alice,
    bob,
    umschlag: baueUmschlag({
      vonKonto: 1,
      vonGeraet: IHR_GERAET,
      fuerGeraet: MEIN_GERAET,
      rumpf: bytesToBase64(utf8ToBytes(serializeRatchetMessage(message))),
    }),
    aliceDanach: nextState,
  }
}

async function legeEmpfaengerAn(bob: RatchetState): Promise<void> {
  await schritt(SITZUNG, async () => ({ naechster: bob, ergebnis: null }))
}

function fach(umschlagId: number): KlartextAblage {
  return {
    lies: () => leseUmschlagKlartext(MAILBOX, umschlagId),
    lege: (text) => speichereUmschlagKlartext(MAILBOX, umschlagId, text),
  }
}

/** Eine zweite Nachricht vom selben Senderzustand aus. */
async function zweiteNachricht(p: Paar): Promise<string> {
  const { message } = await encryptMessage(p.aliceDanach, utf8ToBytes('zweite'))
  return baueUmschlag({
    vonKonto: 1,
    vonGeraet: IHR_GERAET,
    fuerGeraet: MEIN_GERAET,
    rumpf: bytesToBase64(utf8ToBytes(serializeRatchetMessage(message))),
  })
}

describe('Absturz an den lokalen Commitgrenzen', () => {
  beforeEach(() => {
    // Jeder Test bekommt eine leere Ablage. Die Module greifen auf das globale
    // `indexedDB` zu, deshalb genügt der Austausch hier.
    ;(globalThis as { indexedDB: IDBFactory }).indexedDB = new IDBFactory()
    setzeAngemeldetesKonto(KONTO)
    setzeAblageFuerTest(null)
    schliesseLokaleAblageFuerTest()
    IDBDatabase.prototype.transaction = echt.transaction
    injektionAktiv = false
    offene.clear()
    verfolgeVerbindungen()
  })

  it('verliert nichts, wenn der Absturz vor dem Klartext kommt', async () => {
    const p = await bauePaar()
    await legeEmpfaengerAn(p.bob)
    neustart()

    starteAbsturz(0)
    const abgestuerzt = await liesDrUmschlag(KONTEXT, p.umschlag, fach(7))
    expect(abgestuerzt.art).toBe('fehler')
    neustart()

    // Der Zustand ist unangetastet, der Umschlag geht beim nächsten Lauf auf.
    expect(await liesDrUmschlag(KONTEXT, p.umschlag, fach(7))).toMatchObject({
      art: 'klartext',
      text: 'erste',
    })
    expect(await liesDrUmschlag(KONTEXT, await zweiteNachricht(p), fach(8))).toMatchObject({
      art: 'klartext',
      text: 'zweite',
    })
  })

  it('holt den Klartext nach, wenn der Absturz zwischen Klartext und Zustand liegt', async () => {
    const p = await bauePaar()
    await legeEmpfaengerAn(p.bob)
    neustart()

    // Genau eine Schreibtransaktion: der Klartext. Der Zustand scheitert.
    starteAbsturz(1)
    const abgestuerzt = await liesDrUmschlag(KONTEXT, p.umschlag, fach(9))
    expect(abgestuerzt.art).toBe('fehler')
    neustart()

    // Der Klartext liegt, der Zustand nicht. Beim nächsten Lauf wird aus dem
    // gespeicherten Zustand erneut entschlüsselt; derselbe Umschlag ergibt
    // denselben Nachfolger, kein Schritt geht verloren.
    expect(await liesDrUmschlag(KONTEXT, p.umschlag, fach(9))).toMatchObject({
      art: 'klartext',
      text: 'erste',
    })
    // Genau ein Schritt: die zweite Nachricht derselben Kette geht noch auf.
    expect(await liesDrUmschlag(KONTEXT, await zweiteNachricht(p), fach(10))).toMatchObject({
      art: 'klartext',
      text: 'zweite',
    })
  })

  it('trägt den Klartext aus der Ablage nach, wenn die Nachrichtenzeile fehlt', async () => {
    const p = await bauePaar()
    await legeEmpfaengerAn(p.bob)
    neustart()

    // Klartext und Zustand gehen durch, die Nachrichtenzeile scheitert.
    starteAbsturz(2)
    expect(await liesDrUmschlag(KONTEXT, p.umschlag, fach(11))).toMatchObject({
      art: 'klartext',
      text: 'erste',
    })
    const { saveLocalMessagesStrict } = await import('../messengerLocalStore')
    await expect(
      saveLocalMessagesStrict(MAILBOX, [
        { id: -11, clientUuid: 'u-1', senderId: 1, text: 'erste', createdAt: new Date().toISOString(), isSelf: false },
      ]),
    ).rejects.toBeTruthy()
    neustart()

    // Der Umschlag ist beim Server noch offen (kein ACK), der Klartext liegt.
    // Er wird ohne zweiten Ratchet-Schritt erneut geliefert und diesmal
    // gespeichert.
    expect(await liesDrUmschlag(KONTEXT, p.umschlag, fach(11))).toMatchObject({
      art: 'klartext',
      text: 'erste',
    })
    await saveLocalMessagesStrict(MAILBOX, [
      { id: -11, clientUuid: 'u-1', senderId: 1, text: 'erste', createdAt: new Date().toISOString(), isSelf: false },
    ])
    const { loadLocalMessagesStrict } = await import('../messengerLocalStore')
    expect((await loadLocalMessagesStrict(MAILBOX)).map((m) => m.text)).toContain('erste')
  })

  it('schreibt Zustand und Versandabsicht nur zusammen', async () => {
    const p = await bauePaar('ausgehend')
    const absicht: AusstehendeZustellung = {
      clientUuid: 'basis#geraet',
      eigeneId: KONTO,
      peerId: 1,
      mailbox: MAILBOX,
      ciphertext: 'umschlag-bytes',
      angelegtAm: '2026-10-24T10:00:00.000Z',
      reihenfolge: 1,
    }

    starteAbsturz(0)
    await expect(
      schritt(SITZUNG, async () => ({ naechster: p.alice, versand: [absicht], ergebnis: null })),
    ).rejects.toBeTruthy()
    neustart()
    expect(await hatZustellung(absicht.clientUuid)).toBe(false)
    expect(await ausstehendeZustellungen(KONTO)).toEqual([])
  })

  it('behält den Umschlag, wenn der Absturz nach dem Ratchet-Schritt kommt', async () => {
    const p = await bauePaar('ausgehend')
    const absicht: AusstehendeZustellung = {
      clientUuid: 'basis#geraet',
      eigeneId: KONTO,
      peerId: 1,
      mailbox: MAILBOX,
      ciphertext: 'umschlag-bytes',
      zielInbox: 'i'.repeat(64),
      angelegtAm: '2026-10-24T10:00:00.000Z',
      reihenfolge: 1,
    }

    starteAbsturz(1)
    await schritt(SITZUNG, async () => ({ naechster: p.alice, versand: [absicht], ergebnis: null }))
    // Die Kennungen der Geräte-Inbox kommen vor dem HTTP — der Absturz trifft
    // genau diese Marke.
    await expect(
      merkeZustellstand(absicht.clientUuid, {
        zustellung: { payloadId: 'p-1', deliveryId: 'd-1' },
      }),
    ).rejects.toBeTruthy()
    neustart()

    const offen = await ausstehendeZustellungen(KONTO)
    expect(offen).toHaveLength(1)
    expect(offen[0]).toMatchObject({
      clientUuid: 'basis#geraet',
      mailbox: MAILBOX,
      ciphertext: 'umschlag-bytes',
      zielInbox: 'i'.repeat(64),
    })
    // Die Marke fehlt, der nächste Lauf vergibt sie vor dem HTTP.
    expect(offen[0].zustellung).toBeUndefined()
    await merkeZustellstand(absicht.clientUuid, {
      zustellung: { payloadId: 'p-1', deliveryId: 'd-1' },
    })
    expect((await ausstehendeZustellungen(KONTO))[0].zustellung).toEqual({
      payloadId: 'p-1',
      deliveryId: 'd-1',
    })
  })
})
