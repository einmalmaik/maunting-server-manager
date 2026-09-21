// @vitest-environment node
/**
 * Die Absenderbeglaubigung an der Nutzlast, mit echten Schlüsseln.
 *
 * Node-Umgebung, nicht jsdom: DIS prüft Eingaben mit `instanceof Uint8Array`,
 * und ECDSA P-256 kommt aus der WebCrypto von Node. Nachgebildet ist nur das
 * Geräteverzeichnis — es ist eine Serverauskunft, keine Kryptographie.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { erzeugeSignaturPaar, type SignaturPaar } from './absenderSignatur'

interface TestGeraet {
  kennung: string
  konto: number
  paar: SignaturPaar
}

let ich: TestGeraet
/** Das Verzeichnis, wie der Server es herausgibt: `konto:geraet` → JWK. */
const verzeichnis = new Map<string, string>()

vi.mock('./e2eeGeraet', () => ({
  eigenesGeraet: async () => ({
    kennung: ich.kennung,
    paar: { publicKeyJwk: '{}', privateKeyJwk: '{}' },
    signaturPaar: ich.paar,
  }),
  signaturSchluesselVon: async (konto: number, geraet: string) =>
    verzeichnis.get(`${konto}:${geraet}`) ?? null,
}))

import { pruefeNutzlast, signiereNutzlast } from './nutzlastSignatur'

const MAILBOX = 'm'.repeat(32)
const ANNA = 101
const BERT = 102

async function neuesGeraet(konto: number, kennung: string): Promise<TestGeraet> {
  const paar = await erzeugeSignaturPaar()
  verzeichnis.set(`${konto}:${kennung}`, paar.publicKeyJwk)
  return { kennung, konto, paar }
}

describe('nutzlastSignatur', () => {
  beforeEach(async () => {
    verzeichnis.clear()
    ich = await neuesGeraet(ANNA, 'annas-laptop')
  })

  it('erkennt die eigene Unterschrift wieder', async () => {
    const nutzlast = await signiereNutzlast(MAILBOX, ANNA, {
      sender_id: ANNA,
      text: 'Hallo',
      client_uuid: 'uuid-1',
    })

    await expect(pruefeNutzlast(MAILBOX, nutzlast)).resolves.toEqual({
      art: 'geprueft',
      vonKonto: ANNA,
    })
  })

  /**
   * Der eigentliche Zweck. Bert ist Mitglied derselben Gruppe, hat also
   * denselben Schlüssel und kann jede Nutzlast erzeugen — aber nicht Annas
   * Unterschrift.
   */
  it('weist eine fremde Nutzlast unter Annas Namen ab', async () => {
    const bert = await neuesGeraet(BERT, 'berts-telefon')
    ich = bert

    const gefaelscht = await signiereNutzlast(MAILBOX, ANNA, {
      sender_id: ANNA,
      text: 'Passt, überweis die 500 Euro',
      client_uuid: 'uuid-2',
    })
    // Bert kann `von_konto` behaupten, den Beleg dazu aber nicht bilden: der
    // hängt an seinem eigenen Gerät.
    const verbogen = { ...gefaelscht, von_konto: ANNA, von_geraet: 'annas-laptop' }

    await expect(pruefeNutzlast(MAILBOX, verbogen)).resolves.toEqual({
      art: 'gefaelscht',
      behauptet: ANNA,
    })
  })

  it('merkt, wenn am Text gedreht wurde', async () => {
    const nutzlast = await signiereNutzlast(MAILBOX, ANNA, {
      sender_id: ANNA,
      text: 'Original',
      client_uuid: 'uuid-3',
    })

    await expect(
      pruefeNutzlast(MAILBOX, { ...nutzlast, text: 'Umgeschrieben' }),
    ).resolves.toEqual({ art: 'gefaelscht', behauptet: ANNA })
  })

  /**
   * Die Mailbox gehört in die Unterschrift. Ohne sie liesse sich dieselbe
   * beglaubigte Nutzlast in ein anderes Gespräch umhängen, in dem derselbe
   * Mensch auch Mitglied ist.
   */
  it('gilt nur in der Mailbox, für die sie gebildet wurde', async () => {
    const nutzlast = await signiereNutzlast(MAILBOX, ANNA, {
      sender_id: ANNA,
      text: 'Nur hier',
      client_uuid: 'uuid-4',
    })

    await expect(pruefeNutzlast('x'.repeat(32), nutzlast)).resolves.toEqual({
      art: 'gefaelscht',
      behauptet: ANNA,
    })
  })

  /**
   * Die Reihenfolge der Felder darf nichts entscheiden: `JSON.parse` behält
   * sie zwar bei, aber darauf soll sich die Prüfung nicht verlassen müssen.
   */
  it('hängt nicht an der Reihenfolge der Felder', async () => {
    const nutzlast = await signiereNutzlast(MAILBOX, ANNA, {
      sender_id: ANNA,
      text: 'Reihenfolge',
      client_uuid: 'uuid-5',
    })

    const umgedreht: Record<string, unknown> = {}
    for (const name of Object.keys(nutzlast).reverse()) umgedreht[name] = nutzlast[name]

    await expect(pruefeNutzlast(MAILBOX, umgedreht)).resolves.toEqual({
      art: 'geprueft',
      vonKonto: ANNA,
    })
  })

  it('meldet eine Nutzlast ohne Beleg als unsigniert, nicht als gefälscht', async () => {
    await expect(
      pruefeNutzlast(MAILBOX, { sender_id: ANNA, text: 'Altbestand' }),
    ).resolves.toEqual({ art: 'unsigniert' })
  })

  /**
   * Ein Gerät, das im Verzeichnis keinen Signaturschlüssel führt, ist keine
   * fehlende Auskunft: die Nutzlast nennt ein Gerät, das es unter diesem Konto
   * nicht gibt.
   */
  it('wertet einen Beleg ohne Schlüssel im Verzeichnis als Fälschung', async () => {
    const nutzlast = await signiereNutzlast(MAILBOX, ANNA, {
      sender_id: ANNA,
      text: 'Unbekanntes Gerät',
    })
    verzeichnis.clear()

    await expect(pruefeNutzlast(MAILBOX, nutzlast)).resolves.toEqual({
      art: 'gefaelscht',
      behauptet: ANNA,
    })
  })

  /**
   * Ein Feld, das erst nach dem Unterschreiben dazukommt, verändert die
   * Zeichenkette — sonst liesse sich einer beglaubigten Nachricht nachträglich
   * ein Anhang unterschieben.
   */
  it('lässt kein Feld nachträglich hinzufügen', async () => {
    const nutzlast = await signiereNutzlast(MAILBOX, ANNA, {
      sender_id: ANNA,
      text: 'Ohne Anhang',
    })

    await expect(
      pruefeNutzlast(MAILBOX, { ...nutzlast, image_attachment: { mediaId: 'fremd' } }),
    ).resolves.toEqual({ art: 'gefaelscht', behauptet: ANNA })
  })
})