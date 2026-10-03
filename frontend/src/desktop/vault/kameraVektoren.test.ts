// @vitest-environment node
/**
 * Die Kamera-Sicherung verschlüsselt bei geschlossener App in Rust
 * (`smart-system/src-tauri/src/kamera_krypto.rs`), geöffnet wird im WebView mit
 * DIS. Beide Seiten müssen Byte für Byte dasselbe Format sprechen.
 *
 * `__vektoren__/kamera_ts.json` hat DIS erzeugt; die Rust-Tests öffnen es.
 * `__vektoren__/kamera_rust.json` hat Rust erzeugt; dieser Test öffnet es mit
 * DIS. Die Schlüssel darin sind Wegwerfschlüssel nur für diese Tests.
 *
 * Neu schreiben: `KAMERA_VEKTOREN_SCHREIBEN=1 npx vitest run kameraVektoren`,
 * danach `KAMERA_VEKTOREN_SCHREIBEN=1 cargo test kamera_krypto` in
 * `smart-system/src-tauri`.
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { generateHybridKeyPair, hybridDecrypt, hybridEncrypt } from '@msdis/shield/post-quantum'
import { chunkAad, computeManifestRoot, importFileKey } from '@msdis/shield/file-encryption'
import {
  CHUNK_KLARTEXT,
  MINIATUR_GROESSE,
  TRESOR_BLOB_BESITZER,
  chunkAnzahl,
  chunkEntschluesseln,
  chunkLaenge,
  chunkVerschluesseln,
  gepolsterteGroesse,
  type BlobKopf,
} from './tresorDatei'
import { bytesToHex } from './vaultCrypto'

const ORDNER = fileURLToPath(new URL('./__vektoren__/', import.meta.url))
const SCHREIBEN = process.env.KAMERA_VEKTOREN_SCHREIBEN === '1'

const GROESSEN = [0, 1, 65_536, 65_537, 100_000, 1_048_576, 4_194_303, 4_194_304, 4_194_305, 10_000_000, 123_456_789, 3_000_000_000]

function b64(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString('base64')
}

function ausB64(text: string): Uint8Array {
  return new Uint8Array(Buffer.from(text, 'base64'))
}

function ausHex(text: string): Uint8Array {
  return new Uint8Array(Buffer.from(text, 'hex'))
}

function muster(laenge: number): Uint8Array {
  return Uint8Array.from({ length: laenge }, (_, i) => i % 251)
}

function kopf(id: string, groesse: number, echt: number): BlobKopf {
  return { id, groesse, echt, schluessel: '', loeschen: '0'.repeat(64) }
}

async function wurzel(id: string, groesse: number): Promise<string> {
  const anzahl = chunkAnzahl(groesse)
  return computeManifestRoot({
    fileId: id,
    fileRevision: 1,
    chunkSize: CHUNK_KLARTEXT,
    chunkCount: anzahl,
    chunks: Array.from({ length: anzahl }, (_, index) => ({ index, plaintext_size: chunkLaenge(groesse, index) })),
  })
}

interface TsVektoren {
  pqPublicKey: string
  pqSecretKey: string
  rsaPublicKey: string
  rsaPrivateKey: string
  rsaSpki: string
  rsaPkcs8: string
  groessen: [number, number][]
  hybrid: { klartext: string; aad: string; chiffrat: string }
  blob: { id: string; eintragId: string; schluessel: string; groesse: number; echt: number; chunk0: string }
  gross: { id: string; eintragId: string; schluessel: string; groesse: number; echt: number; wurzel: string; aad1: string }
}

interface RustVektoren {
  hybrid: { klartext: string; aad: string; chiffrat: string }
  chunk0: string
  chunk1: string
}

async function tsVektorenErzeugen(): Promise<TsVektoren> {
  const paar = await generateHybridKeyPair()
  const algo = { name: 'RSA-OAEP', hash: 'SHA-256' }
  const oeffentlich = await crypto.subtle.importKey('jwk', JSON.parse(paar.rsaPublicKey), algo, true, ['encrypt'])
  const privat = await crypto.subtle.importKey('jwk', JSON.parse(paar.rsaPrivateKey), algo, true, ['decrypt'])
  const klartext = '{"name":"IMG_0001.jpg"} Grüße aus dem WebView'
  const aad = 'msm-tresor-eingang-v1:ts'

  const blobSchluessel = crypto.getRandomValues(new Uint8Array(32))
  const blob = { id: bytesToHex(crypto.getRandomValues(new Uint8Array(16))), eintragId: crypto.randomUUID(), schluessel: bytesToHex(blobSchluessel), groesse: MINIATUR_GROESSE, echt: 1000 }
  const chunk0 = await chunkVerschluesseln(muster(blob.echt), kopf(blob.id, blob.groesse, blob.echt), 0, await importFileKey(blobSchluessel.slice()), blob.eintragId)

  const echt = CHUNK_KLARTEXT + 1
  const gross = { id: bytesToHex(crypto.getRandomValues(new Uint8Array(16))), eintragId: crypto.randomUUID(), schluessel: bytesToHex(crypto.getRandomValues(new Uint8Array(32))), groesse: gepolsterteGroesse(echt), echt }
  const grossWurzel = await wurzel(gross.id, gross.groesse)

  return {
    pqPublicKey: paar.pqPublicKey,
    pqSecretKey: paar.pqSecretKey,
    rsaPublicKey: paar.rsaPublicKey,
    rsaPrivateKey: paar.rsaPrivateKey,
    rsaSpki: b64(new Uint8Array(await crypto.subtle.exportKey('spki', oeffentlich))),
    rsaPkcs8: b64(new Uint8Array(await crypto.subtle.exportKey('pkcs8', privat))),
    groessen: GROESSEN.map((g) => [g, gepolsterteGroesse(g)]),
    hybrid: { klartext, aad, chiffrat: await hybridEncrypt(klartext, paar.pqPublicKey, paar.rsaPublicKey, aad) },
    blob: { ...blob, chunk0: b64(chunk0) },
    gross: {
      ...gross,
      wurzel: grossWurzel,
      aad1: chunkAad({ ownerId: TRESOR_BLOB_BESITZER, vaultItemId: gross.eintragId, fileId: gross.id }, 1, grossWurzel, 1, chunkAnzahl(gross.groesse)),
    },
  }
}

describe('Kamera-Sicherung: Rust und DIS sprechen dasselbe Format', () => {
  it.runIf(SCHREIBEN)('schreibt die Vektoren aus DIS', async () => {
    const v = await tsVektorenErzeugen()
    writeFileSync(`${ORDNER}kamera_ts.json`, JSON.stringify(v, null, 2) + '\n')
  }, 60_000)

  it('die Vektoren aus DIS stimmen mit dem heutigen Code überein', async () => {
    const v = JSON.parse(readFileSync(`${ORDNER}kamera_ts.json`, 'utf8')) as TsVektoren
    for (const [echt, groesse] of v.groessen) expect(gepolsterteGroesse(echt)).toBe(groesse)
    expect(await hybridDecrypt(v.hybrid.chiffrat, v.pqSecretKey, v.rsaPrivateKey, v.hybrid.aad)).toBe(v.hybrid.klartext)
    const klar = await chunkEntschluesseln(ausB64(v.blob.chunk0), kopf(v.blob.id, v.blob.groesse, v.blob.echt), 0, await importFileKey(ausHex(v.blob.schluessel)), v.blob.eintragId)
    expect(klar).toEqual(muster(v.blob.echt))
    expect(await wurzel(v.gross.id, v.gross.groesse)).toBe(v.gross.wurzel)
  })

  it('öffnet mit DIS, was Rust verschlüsselt hat', async () => {
    const v = JSON.parse(readFileSync(`${ORDNER}kamera_ts.json`, 'utf8')) as TsVektoren
    const r = JSON.parse(readFileSync(`${ORDNER}kamera_rust.json`, 'utf8')) as RustVektoren

    expect(await hybridDecrypt(r.hybrid.chiffrat, v.pqSecretKey, v.rsaPrivateKey, r.hybrid.aad)).toBe(r.hybrid.klartext)
    // Eine andere AAD darf nicht passen: sonst wäre der Umschlag an nichts gebunden.
    await expect(hybridDecrypt(r.hybrid.chiffrat, v.pqSecretKey, v.rsaPrivateKey, 'msm-tresor-eingang-v1:anders')).rejects.toThrow()

    const klein = await chunkEntschluesseln(ausB64(r.chunk0), kopf(v.blob.id, v.blob.groesse, v.blob.echt), 0, await importFileKey(ausHex(v.blob.schluessel)), v.blob.eintragId)
    expect(klein).toEqual(muster(v.blob.echt))

    const gross = kopf(v.gross.id, v.gross.groesse, v.gross.echt)
    const schluessel = await importFileKey(ausHex(v.gross.schluessel))
    expect(await chunkEntschluesseln(ausB64(r.chunk1), gross, 1, schluessel, v.gross.eintragId)).toEqual(muster(v.gross.echt - CHUNK_KLARTEXT))
    // Als Chunk 0 gelesen passt die AAD nicht.
    await expect(chunkEntschluesseln(ausB64(r.chunk1), gross, 0, schluessel, v.gross.eintragId)).rejects.toThrow()
  })
})
