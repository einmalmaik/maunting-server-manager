/**
 * Zugangsdaten speichern, während der Tresor gesperrt ist.
 *
 * Der Browser kennt dann keinen Schlüssel des Tresors. Er hat aber, seit er
 * den Tresor einmal offen hatte, den öffentlichen Schlüssel des Posteingangs
 * und einen eigenen Geräteschlüssel (ECDSA P-256, nicht exportierbar, nur in
 * IndexedDB). Damit verschlüsselt und unterschreibt er einen Datensatz und legt
 * ihn in den Posteingang (`eingangFormat.ts`); lesen kann er ihn nicht. Beim
 * nächsten Entsperren, hier oder in MSS, wird daraus ein Eintrag
 * (`eingangZugang.ts`).
 *
 * Die Ablage gehört einem Konto (AGENTS.md Punkt 50): ein anderes Konto oder
 * das Abmelden leert sie.
 */
import { bytesToBase64 } from '@msdis/shield/core'
import { generateEcdsaP256KeyPair, signEcdsaP256 } from '@msdis/shield/signing'

import { angemeldetesKonto, beiKontowechsel } from '@/lib/angemeldetesKonto'
import { eingangVerpacken, type EingangOeffentlich } from '@/desktop/vault/eingangFormat'
import type { EingangZugang } from '@/desktop/vault/eingangZugang'
import { eingangAblegen } from '@/desktop/vault/tresorBlobApi'
import { posteingangEinrichten } from '@/desktop/vault/tresorEingang'
import { useVaultStore } from '@/desktop/vault/vaultStore'

const DB = 'msb_tresor_gesperrt'
const FACH = 'geraet'

interface Geraet {
  konto: number
  geraet: string
  privat: CryptoKey
  spki: string
  /** Gesetzt, sobald Schlüsselpaar und Gerät beim Server im Tresor liegen. */
  bucket?: string
  oeffentlich?: EingangOeffentlich
}

function oeffnen(): Promise<IDBDatabase> {
  return new Promise((fertig, fehler) => {
    const anfrage = indexedDB.open(DB, 1)
    anfrage.onupgradeneeded = () => anfrage.result.createObjectStore(FACH)
    anfrage.onsuccess = () => fertig(anfrage.result)
    anfrage.onerror = () => fehler(anfrage.error)
  })
}

async function fach<T>(modus: IDBTransactionMode, tun: (s: IDBObjectStore) => IDBRequest | void): Promise<T | undefined> {
  const db = await oeffnen()
  try {
    return await new Promise<T | undefined>((fertig, fehler) => {
      const tx = db.transaction(FACH, modus)
      const anfrage = tun(tx.objectStore(FACH))
      tx.oncomplete = () => fertig(anfrage ? (anfrage.result as T) : undefined)
      tx.onerror = () => fehler(tx.error)
    })
  } finally {
    db.close()
  }
}

/** Das Gerät des angemeldeten Kontos; ein fremdes fällt vorher weg. */
async function lesen(): Promise<Geraet | null> {
  const konto = angemeldetesKonto()
  const geraet = await fach<Geraet>('readonly', (s) => s.get(FACH))
  if (geraet && geraet.konto !== konto) {
    await vergessen()
    return null
  }
  return konto === null ? null : (geraet ?? null)
}

export async function vergessen(): Promise<void> {
  await fach('readwrite', (s) => s.delete(FACH))
}

/**
 * Bei offenem Tresor: Gerät anlegen und beim Tresor eintragen. Danach kann
 * dieser Browser bei gesperrtem Tresor speichern.
 */
export async function einrichten(): Promise<boolean> {
  const konto = angemeldetesKonto()
  const { bucketId, isUnlocked } = useVaultStore.getState()
  if (konto === null || !bucketId || !isUnlocked) return false
  let geraet = await lesen()
  if (!geraet) {
    const paar = await generateEcdsaP256KeyPair()
    geraet = { konto, geraet: `browser-${crypto.randomUUID()}`, privat: paar.privateKey, spki: bytesToBase64(paar.publicKeySpki) }
  }
  if (geraet.bucket === bucketId && geraet.oeffentlich) return true
  // Erst den Stand des Servers: ein Schlüsselpaar, das ein anderes Gerät schon
  // angelegt hat, soll hier nicht ein zweites bekommen.
  await useVaultStore.getState().syncWithServer()
  const spki = geraet.spki
  const oeffentlich = await posteingangEinrichten(bucketId, geraet.geraet, async () => spki)
  if (!oeffentlich || angemeldetesKonto() !== konto) return false
  const fertig: Geraet = { ...geraet, bucket: bucketId, oeffentlich }
  await fach('readwrite', (s) => s.put(fertig, FACH))
  return true
}

/** Ob dieser Browser bei gesperrtem Tresor speichern kann. */
export async function eingerichtet(): Promise<boolean> {
  return !!(await lesen())?.oeffentlich
}

/** Legt Zugangsdaten verschlüsselt in den Posteingang. Wirft, wenn das Gerät nicht eingerichtet ist. */
export async function speichern(url: string, benutzer: string, passwort: string): Promise<void> {
  const geraet = await lesen()
  if (!geraet?.oeffentlich || !geraet.bucket) throw new Error('nicht_eingerichtet')
  const id = crypto.randomUUID()
  const inhalt: EingangZugang = { art: 'zugang', url, benutzer, passwort, zeit: Date.now() }
  const text = await eingangVerpacken(inhalt, geraet.bucket, id, geraet.oeffentlich, geraet.geraet, (daten) => signEcdsaP256(geraet.privat, daten))
  await eingangAblegen(geraet.bucket, id, text)
}

// Ein anderes Konto oder das Abmelden nimmt das Gerät mit.
beiKontowechsel(() => {
  void lesen().catch(() => null)
})
