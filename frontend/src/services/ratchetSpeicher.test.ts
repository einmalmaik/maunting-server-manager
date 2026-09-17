// @vitest-environment node
/**
 * Der Speicher trägt zwei Zusagen, und beide scheitern lautlos, wenn sie
 * brechen: ein weitergelebter Kettenschlüssel fällt gar nicht auf, und zwei
 * Nachrichten auf derselben Kettenposition erst beim Empfänger.
 *
 * Node-Umgebung, nicht jsdom — siehe `disMessaging.test.ts`.
 */

import { beforeEach, describe, expect, it } from 'vitest'
import {
  generateRatchetKeyPair,
  initSenderState,
  initReceiverState,
  encryptMessage,
  decryptMessage,
  deserializeRatchetState,
  type RatchetMessage,
} from '@msdis/shield/messaging'
import { randomBytes } from '@msdis/shield/random'

import {
  schritt,
  hatSitzung,
  verwirfSitzung,
  sitzungsId,
  setzeAblageFuerTest,
  type RatchetAblage,
} from './ratchetSpeicher'

const text = new TextEncoder()
const zurueck = new TextDecoder()

/** Merkt sich zusätzlich, in welcher Reihenfolge geschrieben wurde. */
function speicherAblage() {
  const daten = new Map<string, string>()
  const schreibfolge: string[] = []
  const ablage: RatchetAblage = {
    async lies(id) {
      return daten.get(id) ?? null
    },
    async schreibe(id, zustand) {
      daten.set(id, zustand)
      schreibfolge.push(id)
    },
    async loesche(id) {
      daten.delete(id)
    },
  }
  return { ablage, daten, schreibfolge }
}

async function paarAufbauen() {
  const paar = await generateRatchetKeyPair()
  const geheimnis = randomBytes(32)
  const alice = await initSenderState({
    sharedSecret: geheimnis.slice(),
    remotePublicKey: paar.publicKey,
  })
  const bob = await initReceiverState({ sharedSecret: geheimnis.slice(), dhKeyPair: paar })
  return { alice, bob }
}

describe('ratchetSpeicher', () => {
  let umgebung: ReturnType<typeof speicherAblage>

  beforeEach(() => {
    umgebung = speicherAblage()
    setzeAblageFuerTest(umgebung.ablage)
  })

  it('bildet die Sitzungskennung aus Konto und Gerät', () => {
    // Dieselbe Person an zwei Geräten sind zwei Fäden.
    expect(sitzungsId(7, 'aaaa1111')).toBe('7:aaaa1111')
    expect(sitzungsId(7, 'aaaa1111')).not.toBe(sitzungsId(7, 'bbbb2222'))
  })

  it('legt einen brauchbaren Zustand ab', async () => {
    const { alice } = await paarAufbauen()
    const id = 'a:1'
    await schritt(id, async () => ({ naechster: alice, ergebnis: null }))

    const zurueckgelesen = deserializeRatchetState(umgebung.daten.get(id)!)
    expect(zurueckgelesen.sendingChainKey).not.toBeNull()
    expect(zurueckgelesen.sendingChainKey!.some((b) => b !== 0)).toBe(true)
    expect(zurueckgelesen.dhSelf.publicKey.some((b) => b !== 0)).toBe(true)
  })

  it('überlebt einen gescheiterten Schreibvorgang mit intakter Sitzung', async () => {
    const { alice, bob } = await paarAufbauen()
    const idA = 'bob:geraet1'
    const idB = 'alice:geraet1'
    await schritt(idA, async () => ({ naechster: alice, ergebnis: null }))
    await schritt(idB, async () => ({ naechster: bob, ergebnis: null }))

    const senden = (inhalt: string) =>
      schritt<RatchetMessage>(idA, async (zustand) => {
        const { nextState, message } = await encryptMessage(zustand!, text.encode(inhalt))
        return { naechster: nextState, ergebnis: message }
      })

    // Die Platte ist voll, das Kontingent erschöpft, der private Modus
    // verweigert die Datenbank — irgendwann trifft es jeden.
    const echt = umgebung.ablage.schreibe
    umgebung.ablage.schreibe = async () => {
      throw new Error('QuotaExceededError')
    }
    await expect(senden('geht verloren')).rejects.toThrow('QuotaExceededError')
    umgebung.ablage.schreibe = echt

    // Die abgelegte Fassung muss den Fehlschlag überstanden haben. Eine volle
    // Platte darf einen Gesprächsfaden nicht beenden — sie darf den Versuch
    // kosten, mehr nicht.
    const danach = await senden('kommt an')
    const gelesen = await schritt<string>(idB, async (zustand) => {
      const { nextState, plaintext } = await decryptMessage(zustand!, danach)
      return { naechster: nextState, ergebnis: zurueck.decode(plaintext) }
    })
    expect(gelesen).toBe('kommt an')
  })

  it('überlebt einen App-Neustart und führt das Gespräch fort', async () => {
    const { alice, bob } = await paarAufbauen()
    const idA = 'bob:geraet1'
    const idB = 'alice:geraet1'
    await schritt(idA, async () => ({ naechster: alice, ergebnis: null }))
    await schritt(idB, async () => ({ naechster: bob, ergebnis: null }))

    const senden = (id: string, inhalt: string) =>
      schritt<RatchetMessage>(id, async (zustand) => {
        const { nextState, message } = await encryptMessage(zustand!, text.encode(inhalt))
        return { naechster: nextState, ergebnis: message }
      })

    const empfangen = (id: string, nachricht: RatchetMessage) =>
      schritt<string>(id, async (zustand) => {
        const { nextState, plaintext } = await decryptMessage(zustand!, nachricht)
        return { naechster: nextState, ergebnis: zurueck.decode(plaintext) }
      })

    const eins = await senden(idA, 'erste')
    expect(await empfangen(idB, eins)).toBe('erste')

    // App-Neustart. Der Speicher hält zwischen zwei Aufrufen ohnehin nichts im
    // RAM — jeder `schritt` liest neu —, also ist die serialisierte Form die
    // einzige Brücke über den Neustart, und dieser Test läuft über sie. Das
    // Zurücksetzen leert zusätzlich die Warteschlange des Ersatzschlosses.
    setzeAblageFuerTest(umgebung.ablage)

    const zwei = await senden(idA, 'zweite')
    expect(await empfangen(idB, zwei)).toBe('zweite')

    // Und die Gegenrichtung, die Bob erst nach seiner ersten empfangenen
    // Nachricht überhaupt kann.
    const antwort = await empfangen(idA, await senden(idB, 'Antwort'))
    expect(antwort).toBe('Antwort')
  })

  it('serialisiert zwei gleichzeitige Sendevorgänge derselben Sitzung', async () => {
    const { alice, bob } = await paarAufbauen()
    const idA = 'bob:geraet1'
    const idB = 'alice:geraet1'
    await schritt(idA, async () => ({ naechster: alice, ergebnis: null }))
    await schritt(idB, async () => ({ naechster: bob, ergebnis: null }))

    const senden = (inhalt: string) =>
      schritt<RatchetMessage>(idA, async (zustand) => {
        const { nextState, message } = await encryptMessage(zustand!, text.encode(inhalt))
        return { naechster: nextState, ergebnis: message }
      })

    // Ohne Schloss lesen beide denselben Zustand und erzeugen zwei Nachrichten
    // auf Kettenposition 0. Die zweite wäre beim Empfänger nicht zu öffnen —
    // und zwar erst dort, lange nach der Ursache.
    const [erste, zweite] = await Promise.all([senden('A'), senden('B')])
    expect(erste.header.n).not.toBe(zweite.header.n)

    const empfangen = (nachricht: RatchetMessage) =>
      schritt<string>(idB, async (zustand) => {
        const { nextState, plaintext } = await decryptMessage(zustand!, nachricht)
        return { naechster: nextState, ergebnis: zurueck.decode(plaintext) }
      })

    const gelesen = [await empfangen(erste), await empfangen(zweite)]
    expect(gelesen.sort()).toEqual(['A', 'B'])
  })

  it('lässt den abgelegten Zustand unangetastet, wenn der Schritt scheitert', async () => {
    const { alice } = await paarAufbauen()
    const id = 'bob:geraet1'
    await schritt(id, async () => ({ naechster: alice, ergebnis: null }))
    const vorher = umgebung.daten.get(id)

    await expect(
      schritt(id, async () => {
        throw new Error('Entschlüsselung fehlgeschlagen')
      })
    ).rejects.toThrow('Entschlüsselung fehlgeschlagen')

    // DIS sagt zu, dass eine gefälschte Nachricht den Zustand byteweise
    // unverändert lässt. Diese Zusage endet hier nicht, sondern wird
    // weitergereicht: eine Fälschung darf eine Sitzung weder vorspulen noch
    // verklemmen.
    expect(umgebung.daten.get(id)).toBe(vorher)
  })

  it('meldet und entfernt Sitzungen', async () => {
    const { alice } = await paarAufbauen()
    const id = 'bob:geraet1'
    expect(await hatSitzung(id)).toBe(false)

    await schritt(id, async () => ({ naechster: alice, ergebnis: null }))
    expect(await hatSitzung(id)).toBe(true)

    await verwirfSitzung(id)
    expect(await hatSitzung(id)).toBe(false)
  })

  it('reicht einen fehlenden Zustand als null durch', async () => {
    // Der Bootstrap-Fall: es gibt noch nichts, und das ist kein Fehler.
    const gesehen = await schritt<'leer' | 'da'>('unbekannt:xy', async (zustand) => ({
      ergebnis: zustand === null ? 'leer' : 'da',
    }))
    expect(gesehen).toBe('leer')
    expect(umgebung.schreibfolge).toEqual([])
  })
})
