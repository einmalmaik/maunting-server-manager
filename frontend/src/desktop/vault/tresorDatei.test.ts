/**
 * Verschlüsselung der Tresor-Dateien: Format wie DIS, Polsterung auf
 * Größenklassen, und jeder Chunk gehört genau an seine Stelle.
 */

import { beforeAll, describe, expect, it } from 'vitest'
import { chunkAad, decryptChunk, encryptChunk } from '@msdis/shield/file-encryption'
import { base64ToBytes, bytesToBase64 } from './vaultCrypto'
import {
  blobAnlegen,
  blobSchluessel,
  CHUNK_KLARTEXT,
  CHUNK_UEBERHANG,
  chiffratGroesse,
  chunkAnzahl,
  chunkEntschluesseln,
  chunkVerschluesseln,
  gepolsterteGroesse,
  groessePasst,
  istBlobKopf,
  loeschPruefwert,
  MINIATUR_GROESSE,
  TRESOR_BLOB_BESITZER,
  type BlobKopf,
} from './tresorDatei'

const KIB = 1024
const MIB = 1024 * KIB

let userKey: CryptoKey

beforeAll(async () => {
  userKey = await crypto.subtle.importKey('raw', new Uint8Array(32).fill(3), { name: 'AES-GCM', length: 256 }, false, [
    'encrypt',
    'decrypt',
  ])
})

function muster(laenge: number, start = 0): Uint8Array {
  const daten = new Uint8Array(laenge)
  for (let i = 0; i < laenge; i++) daten[i] = (start + i * 7) & 0xff
  return daten
}

async function allesVerschluesseln(kopf: BlobKopf, schluessel: CryptoKey, eintrag: string, inhalt: Uint8Array) {
  const chunks: Uint8Array[] = []
  for (let i = 0; i < chunkAnzahl(kopf.groesse); i++) {
    const von = Math.min(i * CHUNK_KLARTEXT, inhalt.length)
    const bis = Math.min((i + 1) * CHUNK_KLARTEXT, inhalt.length)
    chunks.push(await chunkVerschluesseln(inhalt.slice(von, bis), kopf, i, schluessel, eintrag))
  }
  return chunks
}

describe('Größenklassen', () => {
  it('polstert klein auf Zweierpotenzen ab 64 KiB und groß nach Padmé', () => {
    expect(gepolsterteGroesse(0)).toBe(64 * KIB)
    expect(gepolsterteGroesse(1)).toBe(64 * KIB)
    expect(gepolsterteGroesse(64 * KIB + 1)).toBe(128 * KIB)
    expect(gepolsterteGroesse(3 * MIB)).toBe(4 * MIB)
    expect(gepolsterteGroesse(4 * MIB)).toBe(4 * MIB)
    // Padmé: 4 MiB + 1 Byte landet auf der nächsten Stufe zu 128 KiB.
    expect(gepolsterteGroesse(4 * MIB + 1)).toBe(4 * MIB + 128 * KIB)
    for (const echt of [5 * MIB + 3, 123_456_789, 3_000_000_000]) {
      const g = gepolsterteGroesse(echt)
      expect(g).toBeGreaterThanOrEqual(echt)
      expect(g / echt).toBeLessThan(1.12)
    }
  })

  it('lässt nur echte Klassen zu, und nie mehr Inhalt als Platz', () => {
    expect(groessePasst(64 * KIB, 10)).toBe(true)
    expect(groessePasst(MINIATUR_GROESSE, 10)).toBe(true)
    expect(groessePasst(100 * KIB, 10)).toBe(false)
    expect(groessePasst(64 * KIB, 64 * KIB + 1)).toBe(false)
    expect(chiffratGroesse(8 * MIB)).toBe(8 * MIB + 2 * CHUNK_UEBERHANG)
  })
})

describe('Blob-Verschlüsselung', () => {
  it('entschlüsselt, was sie verschlüsselt hat, ohne Polsterung, über Chunkgrenzen hinweg', async () => {
    const inhalt = muster(4 * MIB + 5000)
    const { kopf, schluessel } = await blobAnlegen(userKey, 'eintrag', inhalt.length)
    expect(kopf.groesse).toBe(4 * MIB + 128 * KIB)
    const chunks = await allesVerschluesseln(kopf, schluessel, 'eintrag', inhalt)
    expect(chunks.map((c) => c.length)).toEqual([CHUNK_KLARTEXT + CHUNK_UEBERHANG, 128 * KIB + CHUNK_UEBERHANG])

    const schluessel2 = await blobSchluessel(kopf, userKey, 'eintrag')
    const teile = [
      await chunkEntschluesseln(chunks[0], kopf, 0, schluessel2, 'eintrag'),
      await chunkEntschluesseln(chunks[1], kopf, 1, schluessel2, 'eintrag'),
    ]
    const zurueck = new Uint8Array(teile[0].length + teile[1].length)
    zurueck.set(teile[0])
    zurueck.set(teile[1], teile[0].length)
    // Byteweise vergleichen: `toEqual` auf 4 MiB dauert Sekunden.
    expect(zurueck.length).toBe(inhalt.length)
    expect(zurueck.every((b, i) => b === inhalt[i])).toBe(true)
  })

  it('spricht das Chunkformat von DIS, in beide Richtungen', async () => {
    const inhalt = muster(1000)
    const { kopf, schluessel } = await blobAnlegen(userKey, 'e', inhalt.length)
    const ctx = { ownerId: TRESOR_BLOB_BESITZER, vaultItemId: 'e', fileId: kopf.id }
    const chiffrat = await chunkVerschluesseln(inhalt.slice(), kopf, 0, schluessel, 'e')

    // Die AAD hängt am Manifest-Root; den holt sich der Test aus einer
    // gültigen Entschlüsselung nicht, er rechnet ihn wie DIS.
    const { computeManifestRoot } = await import('@msdis/shield/file-encryption')
    const root = await computeManifestRoot({
      fileId: kopf.id,
      fileRevision: 1,
      chunkSize: CHUNK_KLARTEXT,
      chunkCount: 1,
      chunks: [{ index: 0, plaintext_size: kopf.groesse }],
    })
    const aad = chunkAad(ctx, 1, root, 0, 1)
    const vonDis = await decryptChunk(bytesToBase64(chiffrat), schluessel, aad)
    expect(vonDis.slice(0, 1000)).toEqual(inhalt)
    expect(vonDis.length).toBe(kopf.groesse)

    const gepolstert = new Uint8Array(kopf.groesse)
    gepolstert.set(inhalt)
    const zuDis = base64ToBytes(await encryptChunk(gepolstert, schluessel, aad))
    expect(await chunkEntschluesseln(zuDis, kopf, 0, schluessel, 'e')).toEqual(inhalt)
  })

  it('lehnt vertauschte, fremde, gekürzte und veränderte Chunks ab', async () => {
    const inhalt = muster(4 * MIB + 5000)
    const { kopf, schluessel } = await blobAnlegen(userKey, 'e', inhalt.length)
    const [erster, zweiter] = await allesVerschluesseln(kopf, schluessel, 'e', inhalt)

    // Anderer Index (gleiche Länge erzwingen: ein zweiter Blob mit zwei vollen Chunks).
    const gross = await blobAnlegen(userKey, 'e', 8 * MIB)
    const vollerInhalt = muster(8 * MIB)
    const [g0, g1] = await allesVerschluesseln(gross.kopf, gross.schluessel, 'e', vollerInhalt)
    await expect(chunkEntschluesseln(g1, gross.kopf, 0, gross.schluessel, 'e')).rejects.toThrow()
    await expect(chunkEntschluesseln(g0, gross.kopf, 0, gross.schluessel, 'e')).resolves.toBeDefined()

    // Chunk eines anderen Blobs, selbst mit dessen Schlüssel an falscher Stelle.
    await expect(chunkEntschluesseln(g0, kopf, 0, gross.schluessel, 'e')).rejects.toThrow()
    // Anderer Eintrag.
    await expect(chunkEntschluesseln(erster, kopf, 0, schluessel, 'anderer')).rejects.toThrow()
    // Gekürzt.
    await expect(chunkEntschluesseln(zweiter.slice(0, -1), kopf, 1, schluessel, 'e')).rejects.toThrow()
    // Verändert.
    const kaputt = erster.slice()
    kaputt[100] ^= 1
    await expect(chunkEntschluesseln(kaputt, kopf, 0, schluessel, 'e')).rejects.toThrow()
    // Ein Kopf, der eine kleinere Datei behauptet: andere Aufteilung, andere AAD.
    const kleiner = { ...kopf, echt: 4 * MIB, groesse: 4 * MIB }
    await expect(chunkEntschluesseln(erster, kleiner, 0, schluessel, 'e')).rejects.toThrow()
  })

  it('bindet den gewickelten Schlüssel an Eintrag und Blob', async () => {
    const { kopf } = await blobAnlegen(userKey, 'e', 10)
    await expect(blobSchluessel(kopf, userKey, 'anderer')).rejects.toThrow()
    const zweiter = await blobAnlegen(userKey, 'e', 10)
    await expect(blobSchluessel({ ...zweiter.kopf, schluessel: kopf.schluessel }, userKey, 'e')).rejects.toThrow()
  })

  it('gibt dem Server nur den Hash des Löschnachweises', async () => {
    const { kopf } = await blobAnlegen(userKey, 'e', 10)
    const pruefwert = await loeschPruefwert(kopf)
    expect(pruefwert).toMatch(/^[0-9a-f]{64}$/)
    expect(pruefwert).not.toBe(kopf.loeschen)
    expect(istBlobKopf(kopf)).toBe(true)
    expect(istBlobKopf({ ...kopf, groesse: 70_000 })).toBe(false)
    expect(istBlobKopf({ ...kopf, id: '../x' })).toBe(false)
  })

  it('verrät bei Miniaturen die echte Größe nicht', async () => {
    const a = await blobAnlegen(userKey, 'e', 3_000, MINIATUR_GROESSE)
    const b = await blobAnlegen(userKey, 'e', 30_000, MINIATUR_GROESSE)
    const [ca] = await allesVerschluesseln(a.kopf, a.schluessel, 'e', muster(3_000))
    const [cb] = await allesVerschluesseln(b.kopf, b.schluessel, 'e', muster(30_000))
    expect(ca.length).toBe(cb.length)
    expect(ca.length).toBe(MINIATUR_GROESSE + CHUNK_UEBERHANG)
  })
})
