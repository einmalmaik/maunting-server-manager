/**
 * Ein Verfahren für alle Anhänge: Bilder, Dateien, Sprach- und Videonotizen.
 *
 * Vorher waren es drei. Bilder und Dateien liefen über
 * `encryptE2eeAttachmentBlob` und damit über einen Kanalschlüssel, der sich aus
 * den Benutzerkennungen ableiten ließ — der Server konnte sie öffnen.
 * Sprachnotizen reisten als data-URL **im Nachrichten-JSON** und blähten jeden
 * Umschlag auf. Videonotizen hatten mit `videoNoteCrypto.ts` ihr eigenes
 * AES-GCM von Hand, mit einem Schlüssel, zu dem es nie einen hochgeladenen Blob
 * gab: die Aufnahme wurde verschlüsselt, der Umschlag weggeworfen und eine
 * lokale Blob-URL verschickt, die beim Empfänger ins Leere zeigte.
 *
 * **Der Aufbau.** Je Anhang ein zufälliger 32-Byte-Paketschlüssel. DIS erzeugt
 * darunter einen eigenen Dateischlüssel, verschlüsselt die Datei stückweise und
 * legt den Dateischlüssel verpackt ins Manifest. Nur der Paketschlüssel reist —
 * im Nachrichten-Payload, also innerhalb des Double Ratchets beziehungsweise des
 * Gruppenschlüssels. Der Server sieht den Blob und sonst nichts.
 *
 * **Die Bindung.** `AttachmentContext` ist
 * `{ ownerId: Absender, vaultItemId: Mailbox, fileId: Anhangkennung }`. DIS
 * bindet das je Stück in die AAD, zusammen mit Stücknummer, Stückzahl und einer
 * Prüfsumme über den geplanten Aufbau. Ein Stück lässt sich damit nicht
 * umordnen, nicht einschleusen und nicht in einen anderen Anhang, eine andere
 * Mailbox oder unter einen anderen Absender umhängen.
 *
 * `absenderId` und `blindMailboxId` reisen **nicht** mit — sie kommen beim Lesen
 * aus dem Gespräch. Das ist der Sinn: wer den Blob woanders einhängt, hat die
 * falschen gebundenen Daten und scheitert am Tag.
 *
 * **Das Manifest wird versiegelt.** Es trägt den echten Dateinamen und den
 * MIME-Typ. Unverschlüsselt wäre der Blob zwar unlesbar, aber der Server wüsste
 * trotzdem, dass da „Gehaltsabrechnung.pdf" liegt. Also verschlüsselt, mit
 * demselben Paketschlüssel unter `manifestAad`.
 *
 * **Das Format.**
 *
 * ```
 * sv-msm-anhang-v1:<base64(JSON{ v, manifest, chunks: [...] })>
 * ```
 *
 * Base64 um das JSON herum, weil `validate_encrypted_blob_payload` im Backend
 * eine Nutzlast abweist, die mit `{` beginnt: für die Klartext-Erkennung ist das
 * Markup. Die Hülle kostet ein Drittel der Blobgröße, siehe `maxKlartextBytes`.
 */

import i18n from '@/i18n'
import {
  decryptBytes,
  decryptString,
  encryptBytes,
  encryptString,
  importAesGcmRawKey,
} from '@msdis/shield/aead'
import {
  DisDecryptionError,
  base64ToBytes,
  bytesToBase64,
  bytesToUtf8,
  utf8ToBytes,
} from '@msdis/shield/core'
import {
  decryptAttachment,
  encryptAttachment,
  manifestAad,
  type AttachmentContext,
  type FileManifestV1,
} from '@msdis/shield/file-encryption'
import { randomBytes } from '@msdis/shield/random'

export const ANHANG_PREFIX = 'sv-msm-anhang-v1:'

const PAKET_BYTES = 32
/** Der Deckel des Backends (`MAX_MEDIA_BYTES`) gilt für den fertigen Blob. */
const BLOB_GRENZE = 25 * 1024 * 1024

/**
 * Was ein Anhang an seinem Platz festmacht. Nur `fileId` reist mit; die
 * anderen beiden stehen beim Lesen aus dem Gespräch fest.
 */
export interface MedienBindung {
  absenderId: number
  blindMailboxId: string
  fileId: string
}

/** Was im Nachrichten-Payload steht, um an einen Anhang zu kommen. */
export interface MedienZeiger {
  mediaId: string
  /** Base64 der 32 Paketschlüssel-Bytes. */
  paketSchluessel: string
  fileId: string
}

interface Paket {
  v: number
  manifest: string
  chunks: string[]
}

function kontext(bindung: MedienBindung): AttachmentContext {
  return {
    ownerId: String(bindung.absenderId),
    vaultItemId: bindung.blindMailboxId,
    fileId: bindung.fileId,
  }
}

async function paketSchluesselOeffnen(base64: string): Promise<CryptoKey> {
  const bytes = base64ToBytes(base64)
  try {
    if (bytes.length !== PAKET_BYTES) {
      throw new DisDecryptionError('Paketschlüssel hat die falsche Länge')
    }
    return await importAesGcmRawKey(bytes, ['encrypt', 'decrypt'])
  } finally {
    bytes.fill(0)
  }
}

export function neueFileId(): string {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `anhang-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

/**
 * Wie viel Klartext in einen Anhang passt.
 *
 * Aus dem Blob-Deckel zurückgerechnet: Base64 der Hülle, Base64 der Stücke und
 * je Stück zwölf Byte Nonce plus sechzehn Byte Tag. Der Aufrufer prüft damit
 * **vor** dem Verschlüsseln, statt den Upload in einen 413 laufen zu lassen.
 */
export function maxKlartextBytes(): number {
  const nutzlast = (BLOB_GRENZE - ANHANG_PREFIX.length) * 0.75 // äußere Base64-Hülle
  const stuecke = nutzlast * 0.75 // Base64 je Stück
  return Math.floor(stuecke * 0.97) // JSON-Gerüst, Manifest und AEAD-Aufschlag
}

/**
 * Verschlüsselt einen Anhang und liefert Blob und Paketschlüssel.
 *
 * Der Klartext ist eine Zeichenkette, heute immer eine data-URL: so liegt der
 * Anhang schon vor dem Aufruf vor, und die Anzeige bekommt beim Lesen wieder
 * genau das, was sie erwartet.
 */
export async function verschluesselePaket(
  klartext: string,
  bindung: MedienBindung,
  metadaten: { name: string; mimeType: string | null },
): Promise<{ blob: string; paketSchluessel: string }> {
  const roh = randomBytes(PAKET_BYTES)
  const paketSchluessel = bytesToBase64(roh)
  roh.fill(0)

  const schluessel = await paketSchluesselOeffnen(paketSchluessel)
  const ctx = kontext(bindung)
  const inhalt = utf8ToBytes(klartext)
  const chunks: string[] = []

  try {
    const { manifest } = await encryptAttachment({
      context: ctx,
      totalSize: inhalt.length,
      // `slice` kopiert. DIS nullt den übergebenen Klartext nach dem Stück; ein
      // `subarray` würde dabei die eigene Quelle löschen.
      readChunk: async (start, end) => inhalt.slice(start, end),
      writeChunk: async (index, ciphertextBase64) => {
        chunks[index] = ciphertextBase64
        return ciphertextBase64.length
      },
      wrapFileKey: (bytes, aad) => encryptBytes(bytes, schluessel, aad),
      metadata: {
        original_name: metadaten.name,
        mime_type: metadaten.mimeType,
        last_modified: null,
      },
    })

    const versiegeltesManifest = await encryptString(
      JSON.stringify(manifest),
      schluessel,
      manifestAad(ctx),
    )
    const paket: Paket = { v: 1, manifest: versiegeltesManifest, chunks }
    const huelle = utf8ToBytes(JSON.stringify(paket))
    try {
      return { blob: ANHANG_PREFIX + bytesToBase64(huelle), paketSchluessel }
    } finally {
      huelle.fill(0)
    }
  } finally {
    inhalt.fill(0)
  }
}

/** Öffnet einen Anhang. Wirft, wenn Schlüssel, Bindung oder Blob nicht passen. */
export async function entschluesselePaket(
  blob: string,
  paketSchluesselBase64: string,
  bindung: MedienBindung,
): Promise<string> {
  if (!blob.startsWith(ANHANG_PREFIX)) {
    throw new DisDecryptionError('Kein Anhang dieses Verfahrens')
  }

  let paket: Paket
  try {
    paket = JSON.parse(bytesToUtf8(base64ToBytes(blob.slice(ANHANG_PREFIX.length))))
    if (!paket || !Array.isArray(paket.chunks) || typeof paket.manifest !== 'string') {
      throw new Error('Aufbau')
    }
  } catch {
    throw new DisDecryptionError('Anhang ist beschädigt')
  }

  const schluessel = await paketSchluesselOeffnen(paketSchluesselBase64)
  const ctx = kontext(bindung)

  try {
    const manifest = JSON.parse(
      await decryptString(paket.manifest, schluessel, manifestAad(ctx)),
    ) as FileManifestV1

    const teile: Uint8Array[] = []
    await decryptAttachment({
      context: ctx,
      manifest,
      readChunk: async (index) => {
        const stueck = paket.chunks[index]
        if (typeof stueck !== 'string') throw new Error(i18n.t('chat.errors.chunkMissing', { index }))
        return stueck
      },
      // Kopieren: DIS gibt den Puffer nach dem Aufruf wieder frei.
      writeChunk: async (index, klartext) => {
        teile[index] = klartext.slice()
      },
      unwrapFileKey: (verpackt, aad) => decryptBytes(verpackt, schluessel, aad),
    })

    const gesamt = new Uint8Array(teile.reduce((summe, t) => summe + t.length, 0))
    let versatz = 0
    for (const teil of teile) {
      gesamt.set(teil, versatz)
      versatz += teil.length
      teil.fill(0)
    }
    try {
      return bytesToUtf8(gesamt)
    } finally {
      gesamt.fill(0)
    }
  } catch (fehler) {
    if (fehler instanceof DisDecryptionError) throw fehler
    throw new DisDecryptionError('Anhang konnte nicht geöffnet werden oder wurde verändert')
  }
}
