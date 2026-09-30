/**
 * Lokale Ablage des Tresors: Umschläge, Warteschlange, Revisionsstand.
 *
 * Bis 09/2026 lag das alles als je ein JSON-Array im localStorage. Mit der
 * Tresor-Cloud hat ein Tresor schnell tausende Einträge zu je gut 5 KB, und
 * localStorage fasst nur wenige Megabyte. Deshalb IndexedDB, eine Datenbank je
 * Konto (`msm_tresor:konto:<id>`, AGENTS.md Punkt 50).
 *
 * Der Sync liest und schreibt in einem Zug ohne `await` dazwischen (Punkt 45).
 * IndexedDB ist asynchron. Also hält diese Datei eine Kopie im Speicher, die
 * synchron gelesen und geschrieben wird; jede Änderung geht sofort als eigene
 * Transaktion an IndexedDB. Transaktionen auf denselben Stores laufen in der
 * Reihenfolge, in der sie angelegt wurden, die Platte sieht also dieselbe
 * Abfolge wie der Speicher.
 *
 * Der Umzug aus dem localStorage passiert erst, wenn die Warteschlange leer
 * ist: fiele die App danach auf eine ältere Fassung zurück, fände die ihren
 * Cache nicht mehr, holt ihn aber vom Server neu. Ungesendete Änderungen
 * dagegen gäbe es nur hier. Die Marke „umgezogen“ steht in derselben
 * Transaktion wie die Daten (Punkt 60), der localStorage wird erst nach deren
 * `oncomplete` geleert.
 *
 * Ohne angemeldetes Konto (und in Umgebungen ohne IndexedDB) bleibt alles im
 * localStorage wie bisher.
 */

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'

export interface StoredEncryptedEntry {
  id: string
  ciphertext: string
  revision: number
  is_deleted: boolean
  /**
   * Der Stand aus dem Umschlag (siehe `standAus` im Store), lokal
   * mitgeschrieben, damit der Sync ihn ohne erneutes Entschlüsseln vergleichen
   * kann. Fehlt bei Einträgen aus der Zeit davor. Geht nie an den Server.
   */
  stand?: number
  /**
   * Die Revision, die der Server dieser Fassung zuletzt gegeben hat. `revision`
   * zählt nach dem Speichern lokal weiter; das endgültige Löschen braucht aber
   * die Zahl des Servers, um eine fremde Änderung zu erkennen.
   */
  serverRev?: number
  /** Nur in der Warteschlange: das Löschen gilt nur auf dieser Serverrevision. */
  expected_revision?: number
  /** Nur in der Warteschlange: der Cache-Eintrag vor dem Löschen, für den Konfliktfall. */
  vorher?: StoredEncryptedEntry
}

export const ALT_BLOBS = 'mss:vault_blobs_'
export const ALT_WARTESCHLANGE = 'mss:vault_pending_'
export const ALT_REVISION = 'mss:vault_rev_'

const DB_PRAEFIX = 'msm_tresor:konto:'
const DB_VERSION = 1
const EINTRAEGE = 'eintraege'
const WARTESCHLANGE = 'warteschlange'
const STAND = 'stand'

interface Zeile extends StoredEncryptedEntry {
  bucket: string
}

interface WarteZeile extends Zeile {
  seq: number
}

interface Fach {
  bucket: string
  db: IDBDatabase | null
  blobs: Map<string, StoredEncryptedEntry>
  queue: StoredEncryptedEntry[]
  seq: Map<string, number>
  naechsteSeq: number
  rev: number
}

let fach: Fach | null = null

function dbName(konto: number): string {
  return `${DB_PRAEFIX}${konto}`
}

function altLesen<T>(schluessel: string): T[] {
  if (typeof localStorage === 'undefined') return []
  const roh = localStorage.getItem(schluessel)
  if (!roh) return []
  try {
    const wert = JSON.parse(roh)
    return Array.isArray(wert) ? wert : []
  } catch (err) {
    console.warn('Beschädigter lokaler Tresor-Cache konnte nicht geparst werden:', err)
    return []
  }
}

function altRevision(bucket: string): number {
  const roh = typeof localStorage !== 'undefined' ? localStorage.getItem(`${ALT_REVISION}${bucket}`) : null
  const wert = roh ? parseInt(roh, 10) : 0
  return Number.isFinite(wert) ? wert : 0
}

function altEntfernen(bucket: string): void {
  if (typeof localStorage === 'undefined') return
  localStorage.removeItem(`${ALT_BLOBS}${bucket}`)
  localStorage.removeItem(`${ALT_WARTESCHLANGE}${bucket}`)
  localStorage.removeItem(`${ALT_REVISION}${bucket}`)
}

function anfrage<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

function fertig(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error ?? new Error('Transaktion abgebrochen'))
  })
}

function oeffnen(konto: number): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(dbName(konto), DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(EINTRAEGE)) {
        db.createObjectStore(EINTRAEGE, { keyPath: ['bucket', 'id'] })
      }
      if (!db.objectStoreNames.contains(WARTESCHLANGE)) {
        db.createObjectStore(WARTESCHLANGE, { keyPath: ['bucket', 'id'] })
      }
      if (!db.objectStoreNames.contains(STAND)) {
        db.createObjectStore(STAND, { keyPath: 'bucket' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

function ohneBucket(zeile: Zeile | WarteZeile): StoredEncryptedEntry {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { bucket, seq, ...rest } = zeile as WarteZeile
  return rest
}

/** Alle Schlüssel eines Buckets in einem Store: `[bucket]` < `[bucket, id]` < `[bucket, []]`. */
function bucketBereich(bucket: string): IDBKeyRange {
  return IDBKeyRange.bound([bucket], [bucket, []])
}

function altVorhanden(bucket: string): boolean {
  if (typeof localStorage === 'undefined') return false
  return [ALT_BLOBS, ALT_WARTESCHLANGE, ALT_REVISION].some((p) => localStorage.getItem(`${p}${bucket}`) !== null)
}

/**
 * Lädt die Ablage eines Buckets in den Speicher. Wird beim Entsperren
 * aufgerufen, bevor Einträge entschlüsselt werden.
 */
export async function ablageLaden(bucket: string): Promise<void> {
  ablageSchliessen()
  const konto = angemeldetesKonto()
  const alt: Fach = {
    bucket,
    db: null,
    blobs: new Map(altLesen<StoredEncryptedEntry>(`${ALT_BLOBS}${bucket}`).map((e) => [e.id, e])),
    queue: altLesen<StoredEncryptedEntry>(`${ALT_WARTESCHLANGE}${bucket}`),
    seq: new Map(),
    naechsteSeq: 0,
    rev: altRevision(bucket),
  }
  if (konto === null || typeof indexedDB === 'undefined') {
    fach = alt
    return
  }

  let db: IDBDatabase
  let stand: { rev: number; umgezogen: boolean } | undefined
  let eintraege: Zeile[]
  let warte: WarteZeile[]
  try {
    db = await oeffnen(konto)
    const tx = db.transaction([EINTRAEGE, WARTESCHLANGE, STAND], 'readonly')
    ;[stand, eintraege, warte] = await Promise.all([
      anfrage(tx.objectStore(STAND).get(bucket)) as Promise<{ rev: number; umgezogen: boolean } | undefined>,
      anfrage(tx.objectStore(EINTRAEGE).getAll(bucketBereich(bucket))) as Promise<Zeile[]>,
      anfrage(tx.objectStore(WARTESCHLANGE).getAll(bucketBereich(bucket))) as Promise<WarteZeile[]>,
    ])
  } catch (err) {
    console.warn('Tresor-Ablage: IndexedDB nicht nutzbar, bleibe beim localStorage.', err)
    fach = alt
    return
  }

  if (!stand?.umgezogen) {
    // Erster Umzug. Mit ungesendeten Änderungen wird gewartet, bis ein
    // vollständiger Abgleich die Warteschlange geleert hat (`ablageUmziehen`).
    fach = alt
    if (alt.queue.length === 0) await umziehen(db)
    else db.close()
    return
  }

  warte.sort((a, b) => a.seq - b.seq)
  const neu: Fach = {
    bucket,
    db: null,
    blobs: new Map(eintraege.map((z) => [z.id, ohneBucket(z)])),
    queue: warte.map(ohneBucket),
    seq: new Map(warte.map((z) => [z.id, z.seq])),
    naechsteSeq: warte.reduce((m, z) => Math.max(m, z.seq + 1), 0),
    rev: stand.rev,
  }
  fach = neu

  if (!altVorhanden(bucket)) {
    neu.db = db
    return
  }
  // Nach dem Umzug hat jemand wieder in den localStorage geschrieben: eine
  // ältere App-Version oder ein Speichern, das nach dem Wechsel des Tresors
  // noch fertig wurde. Deren Einträge und Änderungen gewinnen je ID, und der
  // Revisionsstand fällt auf den kleineren, damit der nächste Sync alles
  // nachholt, was dazwischen lag.
  for (const eintrag of alt.blobs.values()) neu.blobs.set(eintrag.id, eintrag)
  for (const eintrag of alt.queue) {
    neu.queue = neu.queue.filter((m) => m.id !== eintrag.id)
    neu.queue.push(eintrag)
  }
  neu.rev = Math.min(neu.rev, alt.rev)
  await umziehen(db, true)
}

/**
 * Schreibt den Speicherstand samt Marke in einer Transaktion nach IndexedDB
 * und leert danach den localStorage. Der erste Umzug nur mit leerer
 * Warteschlange: fiele die App danach auf eine ältere Fassung zurück, gingen
 * ungesendete Änderungen sonst verloren.
 */
async function umziehen(db: IDBDatabase, bereitsUmgezogen = false): Promise<void> {
  const aktuell = fach
  if (!aktuell || (!bereitsUmgezogen && aktuell.queue.length > 0)) {
    db.close()
    return
  }
  const bucket = aktuell.bucket
  let tx: IDBTransaction | null = null
  try {
    tx = db.transaction([EINTRAEGE, WARTESCHLANGE, STAND], 'readwrite')
    const eintraege = tx.objectStore(EINTRAEGE)
    const warte = tx.objectStore(WARTESCHLANGE)
    // Reste eines früher abgebrochenen Umzugs zuerst weg.
    eintraege.delete(bucketBereich(bucket))
    warte.delete(bucketBereich(bucket))
    for (const eintrag of aktuell.blobs.values()) eintraege.put({ ...eintrag, bucket })
    const seq = new Map<string, number>()
    for (const eintrag of aktuell.queue) {
      const nr = seq.size
      seq.set(eintrag.id, nr)
      warte.put({ ...eintrag, bucket, seq: nr })
    }
    tx.objectStore(STAND).put({ bucket, rev: aktuell.rev, umgezogen: true })
    // Ab jetzt schreibt der Speicher nach IndexedDB. Spätere Transaktionen
    // laufen erst nach dieser.
    aktuell.seq = seq
    aktuell.naechsteSeq = seq.size
    aktuell.db = db
    await fertig(tx)
  } catch (err) {
    console.warn('Tresor-Ablage: Umzug nach IndexedDB gescheitert, bleibe beim localStorage.', err)
    try {
      tx?.abort()
    } catch {
      // Schon abgebrochen oder beendet.
    }
    if (fach === aktuell) {
      aktuell.db = null
      altSchreibenAlles(aktuell)
    }
    db.close()
    return
  }
  if (fach === aktuell && aktuell.db === db) altEntfernen(bucket)
  // Ohne diese Bitte darf der Browser die Ablage bei Platzmangel räumen, samt
  // ungesendeter Änderungen. Scheitert sie, bleibt es beim Versuch.
  try {
    if (typeof navigator !== 'undefined' && navigator.storage?.persist && !(await navigator.storage.persisted())) {
      await navigator.storage.persist()
    }
  } catch {
    // Ältere WebViews ohne Speicher-API.
  }
}

/** Nach einem vollständigen Abgleich: zieht um, falls noch nicht geschehen. */
export async function ablageUmziehen(): Promise<void> {
  const aktuell = fach
  const konto = angemeldetesKonto()
  if (!aktuell || aktuell.db || konto === null || typeof indexedDB === 'undefined') return
  if (aktuell.queue.length > 0) return
  try {
    await umziehen(await oeffnen(konto))
  } catch (err) {
    console.warn('Tresor-Ablage: IndexedDB nicht nutzbar.', err)
  }
}

export function ablageSchliessen(): void {
  fach?.db?.close()
  fach = null
}

/** Ob die Ablage dieses Buckets in IndexedDB liegt (für Tests und Diagnose). */
export function ablageInIndexedDb(bucket: string): boolean {
  return fach?.bucket === bucket && fach.db !== null
}

function altSchreibenAlles(f: Fach): void {
  if (typeof localStorage === 'undefined') return
  localStorage.setItem(`${ALT_BLOBS}${f.bucket}`, JSON.stringify([...f.blobs.values()]))
  if (f.queue.length > 0) localStorage.setItem(`${ALT_WARTESCHLANGE}${f.bucket}`, JSON.stringify(f.queue))
  else localStorage.removeItem(`${ALT_WARTESCHLANGE}${f.bucket}`)
  localStorage.setItem(`${ALT_REVISION}${f.bucket}`, String(f.rev))
}

function gleich(a: StoredEncryptedEntry | undefined, b: StoredEncryptedEntry): boolean {
  return (
    !!a &&
    a.ciphertext === b.ciphertext &&
    a.revision === b.revision &&
    a.is_deleted === b.is_deleted &&
    a.stand === b.stand &&
    a.serverRev === b.serverRev &&
    a.expected_revision === b.expected_revision
  )
}

function schreibfehler(err: unknown): void {
  console.warn('Tresor-Ablage: Schreiben nach IndexedDB gescheitert.', err)
}

export function blobsLesen(bucket: string): StoredEncryptedEntry[] {
  if (fach?.bucket === bucket) return [...fach.blobs.values()]
  return altLesen(`${ALT_BLOBS}${bucket}`)
}

export function blobsSchreiben(bucket: string, liste: StoredEncryptedEntry[]): void {
  if (fach?.bucket !== bucket) {
    if (typeof localStorage !== 'undefined') localStorage.setItem(`${ALT_BLOBS}${bucket}`, JSON.stringify(liste))
    return
  }
  const alt = fach.blobs
  const neu = new Map(liste.map((e) => [e.id, e]))
  fach.blobs = neu
  if (!fach.db) {
    localStorage.setItem(`${ALT_BLOBS}${bucket}`, JSON.stringify(liste))
    return
  }
  const tx = fach.db.transaction(EINTRAEGE, 'readwrite')
  const store = tx.objectStore(EINTRAEGE)
  for (const eintrag of neu.values()) {
    if (!gleich(alt.get(eintrag.id), eintrag)) store.put({ ...eintrag, bucket })
  }
  for (const id of alt.keys()) {
    if (!neu.has(id)) store.delete([bucket, id])
  }
  fertig(tx).catch(schreibfehler)
}

export function warteschlangeLesen(bucket: string): StoredEncryptedEntry[] {
  if (fach?.bucket === bucket) return [...fach.queue]
  return altLesen(`${ALT_WARTESCHLANGE}${bucket}`)
}

export function warteschlangeSchreiben(bucket: string, liste: StoredEncryptedEntry[]): void {
  if (fach?.bucket !== bucket) {
    if (typeof localStorage === 'undefined') return
    if (liste.length > 0) localStorage.setItem(`${ALT_WARTESCHLANGE}${bucket}`, JSON.stringify(liste))
    else localStorage.removeItem(`${ALT_WARTESCHLANGE}${bucket}`)
    return
  }
  const alt = new Map(fach.queue.map((e) => [e.id, e]))
  fach.queue = [...liste]
  if (!fach.db) {
    if (liste.length > 0) localStorage.setItem(`${ALT_WARTESCHLANGE}${bucket}`, JSON.stringify(liste))
    else localStorage.removeItem(`${ALT_WARTESCHLANGE}${bucket}`)
    return
  }
  // Die Reihenfolge ist die des Einreihens: neue und geänderte Einträge
  // bekommen die nächste Nummer, unveränderte behalten ihre.
  const tx = fach.db.transaction(WARTESCHLANGE, 'readwrite')
  const store = tx.objectStore(WARTESCHLANGE)
  const bleibend = new Set<string>()
  for (const eintrag of liste) {
    bleibend.add(eintrag.id)
    if (gleich(alt.get(eintrag.id), eintrag) && fach.seq.has(eintrag.id)) continue
    const seq = fach.naechsteSeq++
    fach.seq.set(eintrag.id, seq)
    store.put({ ...eintrag, bucket, seq })
  }
  for (const id of alt.keys()) {
    if (!bleibend.has(id)) {
      store.delete([bucket, id])
      fach.seq.delete(id)
    }
  }
  fertig(tx).catch(schreibfehler)
}

export function revisionLesen(bucket: string): number {
  if (fach?.bucket === bucket) return fach.rev
  return altRevision(bucket)
}

export function revisionSchreiben(bucket: string, rev: number): void {
  if (fach?.bucket !== bucket) {
    if (typeof localStorage !== 'undefined') localStorage.setItem(`${ALT_REVISION}${bucket}`, String(rev))
    return
  }
  fach.rev = rev
  if (!fach.db) {
    localStorage.setItem(`${ALT_REVISION}${bucket}`, String(rev))
    return
  }
  const tx = fach.db.transaction(STAND, 'readwrite')
  tx.objectStore(STAND).put({ bucket, rev, umgezogen: true })
  fertig(tx).catch(schreibfehler)
}

/**
 * Löscht die ganze Ablage dieses Geräts für das angemeldete Konto: Speicher,
 * IndexedDB und die alten localStorage-Schlüssel. Beim Zurücksetzen des Tresors.
 */
export async function ablageLoeschen(): Promise<void> {
  ablageSchliessen()
  if (typeof localStorage !== 'undefined') {
    const weg: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k && (k.startsWith(ALT_BLOBS) || k.startsWith(ALT_WARTESCHLANGE) || k.startsWith(ALT_REVISION))) weg.push(k)
    }
    for (const k of weg) localStorage.removeItem(k)
  }
  const konto = angemeldetesKonto()
  if (konto === null || typeof indexedDB === 'undefined') return
  await new Promise<void>((resolve) => {
    const req = indexedDB.deleteDatabase(dbName(konto))
    req.onsuccess = () => resolve()
    req.onerror = () => resolve()
    req.onblocked = () => resolve()
  })
}
