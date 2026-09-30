/**
 * Verschlüsselung der Dateien im Tresor.
 *
 * Eine Datei besteht aus Blobs (Original, Vorschau, Miniatur). Der Server
 * kennt von einem Blob nur Kennung, Chunkzahl und die gepolsterte Größe.
 * Alles andere steht im Kopf des Blobs (`BlobKopf`), und der liegt im
 * verschlüsselten Tresor-Eintrag der Datei. Damit gelten für Blobs dieselben
 * Zusagen wie für Einträge: der Server kann keinen alten Blob unterschieben,
 * weil der Eintrag die Kennung nennt und der Eintrag gegen Rücksprung
 * geschützt ist.
 *
 * Format der Chunks: das von DIS `encryptChunk` (IV ‖ Chiffrat ‖ Tag,
 * AAD `chunkAad`), aber als Bytes statt Base64. Base64 kostete bei 4 MiB je
 * Chunk spürbar Zeit im Hauptthread und ein Drittel mehr Übertragung.
 */

import {
  chunkAad,
  computeManifestRoot,
  DEFAULT_CHUNK_SIZE,
  fileKeyAad,
  generateFileKeyBytes,
  importFileKey,
  type AttachmentContext,
} from '@msdis/shield/file-encryption'
import { aesGcmDecrypt, aesGcmEncrypt, decryptBytes, encryptBytes } from '@msdis/shield/aead'
import { bytesToHex } from './vaultCrypto'

/**
 * Fester Besitzer im Bindungskontext, nicht der Bucket: wechselt das
 * Master-Passwort, müssen nur die Schlüssel neu gewickelt werden, nicht alle
 * Chunks neu verschlüsselt.
 */
export const TRESOR_BLOB_BESITZER = 'msm-tresor-v1'
export const CHUNK_KLARTEXT = DEFAULT_CHUNK_SIZE
const IV_LAENGE = 12
export const CHUNK_UEBERHANG = IV_LAENGE + 16
/** Jede Datei bekommt ihre Blobs in genau diesen Größen, damit ihre Art nicht auffällt. */
export const MINIATUR_GROESSE = 32 * 1024
export const VORSCHAU_GROESSE = 512 * 1024
const KLEINSTE_KLASSE = 64 * 1024
const DATEI_REVISION = 1

export interface BlobKopf {
  /** 32 Hexzeichen, vom Client gewürfelt. */
  id: string
  /** Gepolsterte Größe in Bytes, so wie der Server sie (plus Überhang) sieht. */
  groesse: number
  /** Echte Größe. Steht nur hier. */
  echt: number
  /** Der Blob-Schlüssel, gewickelt mit dem Tresor-Schlüssel. */
  schluessel: string
  /** Löschnachweis (32 Bytes hex); der Server kennt nur seinen Hash. */
  loeschen: string
}

/**
 * Größenklasse eines Blobs. Unter 4 MiB die nächste Zweierpotenz (mindestens
 * 64 KiB), darüber Padmé: höchstens rund 12 % Aufschlag, und aus der Größe
 * bleibt nur noch ein Bruchteil der Bits ablesbar.
 */
export function gepolsterteGroesse(echt: number): number {
  if (!Number.isSafeInteger(echt) || echt < 0) throw new Error('Ungültige Dateigröße')
  if (echt <= KLEINSTE_KLASSE) return KLEINSTE_KLASSE
  if (echt < CHUNK_KLARTEXT) return 2 ** Math.ceil(Math.log2(echt))
  const e = Math.floor(Math.log2(echt))
  const s = Math.floor(Math.log2(e)) + 1
  const schritt = 2 ** (e - s)
  return Math.ceil(echt / schritt) * schritt
}

/** Ob diese Kombination aus gepolsterter und echter Größe erlaubt ist. */
export function groessePasst(groesse: number, echt: number): boolean {
  if (!Number.isSafeInteger(groesse) || !Number.isSafeInteger(echt) || echt < 0 || echt > groesse) return false
  return groesse === gepolsterteGroesse(echt) || groesse === MINIATUR_GROESSE || groesse === VORSCHAU_GROESSE
}

export function chunkAnzahl(groesse: number): number {
  return Math.max(1, Math.ceil(groesse / CHUNK_KLARTEXT))
}

/** Klartextlänge von Chunk `index` in der gepolsterten Datei. */
export function chunkLaenge(groesse: number, index: number): number {
  return Math.min(CHUNK_KLARTEXT, groesse - index * CHUNK_KLARTEXT)
}

/** Was der Server an Bytes speichert. */
export function chiffratGroesse(groesse: number): number {
  return groesse + chunkAnzahl(groesse) * CHUNK_UEBERHANG
}

function kontext(eintragId: string, blobId: string): AttachmentContext {
  return { ownerId: TRESOR_BLOB_BESITZER, vaultItemId: eintragId, fileId: blobId }
}

function zufallHex(bytes: number): string {
  const wert = new Uint8Array(bytes)
  crypto.getRandomValues(wert)
  return bytesToHex(wert)
}

async function sha256Hex(daten: Uint8Array): Promise<string> {
  return bytesToHex(new Uint8Array(await crypto.subtle.digest('SHA-256', daten as BufferSource)))
}

/** Was der Server beim Anlegen erfährt. */
export async function loeschPruefwert(kopf: BlobKopf): Promise<string> {
  return sha256Hex(hexZuBytes(kopf.loeschen))
}

function hexZuBytes(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2)
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16)
  return bytes
}

/**
 * Legt einen Blob an: Kennung, Schlüssel, Löschnachweis. `groesse` für die
 * festen Klassen (Miniatur, Vorschau), sonst die Größenklasse von `echt`.
 */
export async function blobAnlegen(
  userKey: CryptoKey,
  eintragId: string,
  echt: number,
  groesse = gepolsterteGroesse(echt),
): Promise<{ kopf: BlobKopf; schluessel: CryptoKey }> {
  if (!groessePasst(groesse, echt)) throw new Error('Ungültige Blob-Größe')
  const id = zufallHex(16)
  const roh = generateFileKeyBytes()
  try {
    const gewickelt = await encryptBytes(roh, userKey, fileKeyAad(kontext(eintragId, id)))
    const schluessel = await importFileKey(roh)
    return { kopf: { id, groesse, echt, schluessel: gewickelt, loeschen: zufallHex(32) }, schluessel }
  } finally {
    roh.fill(0)
  }
}

/** Prüft einen Kopf aus einem entschlüsselten Eintrag, bevor er benutzt wird. */
export function istBlobKopf(wert: unknown): wert is BlobKopf {
  if (!wert || typeof wert !== 'object') return false
  const k = wert as Record<string, unknown>
  return (
    typeof k.id === 'string' &&
    /^[0-9a-f]{32}$/.test(k.id) &&
    typeof k.groesse === 'number' &&
    typeof k.echt === 'number' &&
    groessePasst(k.groesse, k.echt) &&
    typeof k.schluessel === 'string' &&
    typeof k.loeschen === 'string' &&
    /^[0-9a-f]{64}$/.test(k.loeschen)
  )
}

/** Wickelt den Blob-Schlüssel aus. Gebunden an Eintrag und Blob. */
export async function blobSchluessel(kopf: BlobKopf, userKey: CryptoKey, eintragId: string): Promise<CryptoKey> {
  const roh = await decryptBytes(kopf.schluessel, userKey, fileKeyAad(kontext(eintragId, kopf.id)))
  try {
    return await importFileKey(roh)
  } finally {
    roh.fill(0)
  }
}

const wurzeln = new Map<string, Promise<string>>()

/** Der Manifest-Root von DIS über die Aufteilung; bindet Chunkzahl und -längen in jede AAD. */
function wurzel(kopf: BlobKopf): Promise<string> {
  const schluessel = `${kopf.id}:${kopf.groesse}`
  let wert = wurzeln.get(schluessel)
  if (!wert) {
    const anzahl = chunkAnzahl(kopf.groesse)
    wert = computeManifestRoot({
      fileId: kopf.id,
      fileRevision: DATEI_REVISION,
      chunkSize: CHUNK_KLARTEXT,
      chunkCount: anzahl,
      chunks: Array.from({ length: anzahl }, (_, index) => ({ index, plaintext_size: chunkLaenge(kopf.groesse, index) })),
    })
    wurzeln.set(schluessel, wert)
    if (wurzeln.size > 1000) wurzeln.delete(wurzeln.keys().next().value!)
  }
  return wert
}

async function aad(kopf: BlobKopf, eintragId: string, index: number): Promise<Uint8Array> {
  const text = chunkAad(kontext(eintragId, kopf.id), DATEI_REVISION, await wurzel(kopf), index, chunkAnzahl(kopf.groesse))
  return new TextEncoder().encode(text)
}

function indexPruefen(kopf: BlobKopf, index: number): void {
  if (!Number.isInteger(index) || index < 0 || index >= chunkAnzahl(kopf.groesse)) throw new Error('Chunk außerhalb des Blobs')
}

/**
 * Verschlüsselt Chunk `index`. `daten` sind die echten Bytes dieses Abschnitts
 * (beim letzten Chunk mit Inhalt weniger, danach keine); der Rest bis zur
 * gepolsterten Länge wird mit Nullen aufgefüllt. `daten` wird danach genullt.
 */
export async function chunkVerschluesseln(
  daten: Uint8Array,
  kopf: BlobKopf,
  index: number,
  schluessel: CryptoKey,
  eintragId: string,
): Promise<Uint8Array> {
  indexPruefen(kopf, index)
  const laenge = chunkLaenge(kopf.groesse, index)
  const erwartet = Math.max(0, Math.min(laenge, kopf.echt - index * CHUNK_KLARTEXT))
  if (daten.byteLength !== erwartet) throw new Error('Chunk hat die falsche Länge')
  const klartext = new Uint8Array(laenge)
  klartext.set(daten)
  daten.fill(0)
  const iv = new Uint8Array(IV_LAENGE)
  crypto.getRandomValues(iv)
  try {
    const chiffrat = await aesGcmEncrypt(schluessel, iv, klartext, await aad(kopf, eintragId, index))
    const ergebnis = new Uint8Array(IV_LAENGE + chiffrat.byteLength)
    ergebnis.set(iv)
    ergebnis.set(chiffrat, IV_LAENGE)
    return ergebnis
  } finally {
    klartext.fill(0)
  }
}

/**
 * Entschlüsselt Chunk `index` und gibt nur die echten Bytes zurück, ohne
 * Polsterung. Wirft bei falscher Länge, fremdem Blob, vertauschtem Index oder
 * verändertem Inhalt.
 */
export async function chunkEntschluesseln(
  chiffrat: Uint8Array,
  kopf: BlobKopf,
  index: number,
  schluessel: CryptoKey,
  eintragId: string,
): Promise<Uint8Array> {
  indexPruefen(kopf, index)
  const laenge = chunkLaenge(kopf.groesse, index)
  if (chiffrat.byteLength !== laenge + CHUNK_UEBERHANG) throw new Error('Chunk hat die falsche Länge')
  const klartext = await aesGcmDecrypt(
    schluessel,
    chiffrat.subarray(0, IV_LAENGE),
    chiffrat.subarray(IV_LAENGE),
    await aad(kopf, eintragId, index),
  )
  const echt = Math.max(0, Math.min(laenge, kopf.echt - index * CHUNK_KLARTEXT))
  if (echt === laenge) return klartext
  const ergebnis = klartext.slice(0, echt)
  klartext.fill(0)
  return ergebnis
}
