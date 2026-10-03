/**
 * Dateien im Tresor: hochladen, lesen, löschen.
 *
 * Hochladen geht in zwei Schritten. Erst wird die Datei Chunk für Chunk
 * verschlüsselt und das Chiffrat in IndexedDB abgelegt (`dateiVorbereiten`);
 * die Datei liegt dabei nie ganz im Speicher. Dann geht das Chiffrat hoch
 * (`uploadsFortsetzen`), auch nach einem Neustart der App und nach dem
 * Sperren des Tresors: dafür braucht es keinen Schlüssel mehr. Offline
 * wartet es, bis das Netz wieder da ist.
 *
 * Gelöscht werden Blobs nur über einen ausdrücklichen Verweis: den Tombstone
 * einer Datei, den der Server angenommen hat (`loeschungenAbarbeiten`). Einen
 * Suchlauf nach verwaisten Blobs gibt es nicht; ein Gerät mit veraltetem Stand
 * könnte sonst Dateien löschen, die ein anderes gerade hochlädt.
 */

import { create } from 'zustand'
import { SanitizedApiError } from '@/api/client'
import { beimLeeren } from '@/services/klartextSpeicher'
import {
  ablageDb,
  anfrage,
  fertig,
  ablageGeladen,
  BLOB_CACHE,
  blobsLesen,
  OFFLINE,
  blobsSchreiben,
  UPLOAD_CHUNKS,
  UPLOADS,
  warteschlangeLesen,
} from './tresorAblage'
import {
  blobAnlegen,
  blobSchluessel,
  chiffratGroesse,
  chunkAnzahl,
  chunkEntschluesseln,
  CHUNK_KLARTEXT,
  CHUNK_UEBERHANG,
  chunkLaenge,
  chunkVerschluesseln,
  istBlobKopf,
  loeschPruefwert,
  MINIATUR_GROESSE,
  VORSCHAU_GROESSE,
  type BlobKopf,
} from './tresorDatei'
import {
  blobFertig,
  blobLoeschen,
  blobReservieren,
  blobStand,
  chunkHochladen,
  chunkLaden,
  kleineLaden,
} from './tresorBlobApi'

/** Was der Tresor-Eintrag einer Datei über sie weiß. Nur verschlüsselt auf dem Server. */
export interface DateiAngaben {
  /** MIME-Typ, wie das Gerät ihn meldet; leer, wenn unbekannt. */
  typ: string
  /** Letzte Änderung der Datei auf dem Gerät, von dem sie kam (ms). */
  geaendert?: number
  /**
   * Aufnahmezeit aus EXIF. Kameras speichern Ortszeit ohne Zone; sie steht
   * hier als UTC und wird auch als UTC angezeigt, damit 14:05 14:05 bleibt.
   */
  aufgenommen?: number
  kamera?: string
  breite?: number
  hoehe?: number
  /** Videos: Länge in Sekunden. */
  dauer?: number
  original: BlobKopf
  /** Bei Bildern und Videos ein Vorschaubild, sonst leer, aber immer gleich groß. */
  vorschau: BlobKopf
  miniatur: BlobKopf
  /**
   * Frühere Fassungen nach einer Bearbeitung, neueste zuerst, höchstens
   * `VERSIONEN`. Sie bleiben, damit ein Gerät mit altem Stand, das den
   * Eintrag noch einmal speichert, auf Blobs zeigt, die es noch gibt.
   */
  frueher?: DateiVersion[]
  /**
   * Kamera-Sicherung: welche Aufnahme dieses Geräts das ist und welche Bytes
   * hochgingen. „Speicher freigeben“ löscht nur, was hier gleich ist.
   */
  quelle?: DateiQuelle
}

export interface DateiQuelle {
  /** Zufällige Kennung der Installation, je Tresor (`kameraSicherung.ts`). */
  geraet: string
  /** Kennung der Aufnahme im MediaStore dieses Geräts. */
  medienId: number
  art: 'bild' | 'video'
  sha256: string
}

export function istQuelle(wert: unknown): wert is DateiQuelle {
  if (!wert || typeof wert !== 'object') return false
  const q = wert as Record<string, unknown>
  return (
    typeof q.geraet === 'string' &&
    typeof q.medienId === 'number' &&
    Number.isSafeInteger(q.medienId) &&
    (q.art === 'bild' || q.art === 'video') &&
    typeof q.sha256 === 'string' &&
    /^[0-9a-f]{64}$/.test(q.sha256)
  )
}

export interface DateiVersion {
  typ: string
  /** Wann diese Fassung ersetzt wurde (ms). */
  ersetzt: number
  original: BlobKopf
  vorschau: BlobKopf
  miniatur: BlobKopf
}

export const VERSIONEN = 5

function istVersion(wert: unknown): wert is DateiVersion {
  if (!wert || typeof wert !== 'object') return false
  const v = wert as Record<string, unknown>
  return (
    typeof v.typ === 'string' &&
    typeof v.ersetzt === 'number' &&
    istBlobKopf(v.original) &&
    istBlobKopf(v.vorschau) &&
    istBlobKopf(v.miniatur)
  )
}

export function istDateiAngaben(wert: unknown): wert is DateiAngaben {
  if (!wert || typeof wert !== 'object') return false
  const d = wert as Record<string, unknown>
  const zahl = (w: unknown) => w === undefined || (typeof w === 'number' && Number.isFinite(w))
  return (
    typeof d.typ === 'string' &&
    zahl(d.geaendert) &&
    zahl(d.aufgenommen) &&
    zahl(d.breite) &&
    zahl(d.hoehe) &&
    zahl(d.dauer) &&
    (d.kamera === undefined || typeof d.kamera === 'string') &&
    istBlobKopf(d.original) &&
    istBlobKopf(d.vorschau) &&
    istBlobKopf(d.miniatur) &&
    (d.frueher === undefined || (Array.isArray(d.frueher) && d.frueher.length <= 50 && d.frueher.every(istVersion))) &&
    (d.quelle === undefined || istQuelle(d.quelle))
  )
}

/** Alle Blobs einer Datei, frühere Fassungen eingeschlossen. */
export function dateiBlobs(angaben: DateiAngaben): BlobKopf[] {
  return [angaben, ...(angaben.frueher ?? [])].flatMap((f) => [f.original, f.vorschau, f.miniatur])
}

interface UploadZeile {
  blobId: string
  bucket: string
  eintragId: string
  chunkAnzahl: number
  bytes: number
  pruefwert: string
  /** Erst wenn alle Chunks verschlüsselt abgelegt sind. */
  bereit: boolean
  /** Bleibt nach dem Hochladen im Cache, fürs Offline-Anzeigen. */
  behalten: boolean
  /** Fällt nach `ZULETZT_GRENZE` wieder heraus (Vorschau). Sonst bleibt es. */
  offline?: 'zuletzt'
  fehler?: UploadFehler
  /**
   * Posteingang der Kamera-Sicherung: der Eintrag entsteht erst bei der
   * Übernahme. Bis dahin lebt der Upload ohne ihn.
   */
  eingang?: boolean
}

/**
 * Was nach dem Hochladen auf dem Gerät bleibt: nichts, die Miniatur und das
 * Original einer angehefteten Datei für immer, die Vorschau als zuletzt
 * gesehen, bis `ZULETZT_GRENZE` sie verdrängt.
 */
export type Behalten = 'nein' | 'immer' | 'zuletzt'

/**
 * Ein Upload von diesem Gerät bleibt als „zuletzt“ im Cache und ist ohne
 * Download zu öffnen. Große Dateien nicht: sie verdrängten den ganzen Rest.
 */
function originalBehalten(groesse: number): Behalten {
  return groesse <= ZULETZT_GRENZE / 4 ? 'zuletzt' : 'nein'
}

interface ChunkZeile {
  blobId: string
  index: number
  daten: Uint8Array
}

/** `abgelehnt`: der Server wies den Blob ab (etwa 413, 422); der nächste Lauf versucht es wieder. */
export type UploadFehler = 'speicherVoll' | 'platteVoll' | 'zuVieleDateien' | 'abgelehnt'

export interface UploadFortschritt {
  gesendet: number
  gesamt: number
  fehler?: UploadFehler
}

/** Fortschritt der Uploads je Tresor-Eintrag, für die Oberfläche. */
export const useTresorUploads = create<{ je: Record<string, UploadFortschritt> }>(() => ({ je: {} }))

/**
 * `alt` fehlt, wenn der Eintrag nicht (mehr) im Fortschritt steht: beim
 * Sperren fällt alles heraus, und ein Lauf, der danach weiterläuft, legt es
 * nicht wieder an. Der nächste Lauf zählt neu.
 */
function fortschrittSetzen(eintragId: string, aenderung: (alt: UploadFortschritt | undefined) => UploadFortschritt | null) {
  useTresorUploads.setState((zustand) => {
    const neu = aenderung(zustand.je[eintragId])
    const je = { ...zustand.je }
    if (neu) je[eintragId] = neu
    else delete je[eintragId]
    return { je }
  })
}

async function schreiben(db: IDBDatabase, store: string, arbeit: (s: IDBObjectStore) => void): Promise<void> {
  const tx = db.transaction(store, 'readwrite')
  arbeit(tx.objectStore(store))
  await fertig(tx)
}

/** Alle Chunks eines Blobs: `[id, 0]` bis `[id, []]`. */
function chunkBereich(blobId: string): IDBKeyRange {
  return IDBKeyRange.bound([blobId, 0], [blobId, []])
}

async function uploadEntfernen(db: IDBDatabase, blobId: string): Promise<void> {
  const tx = db.transaction([UPLOADS, UPLOAD_CHUNKS], 'readwrite')
  tx.objectStore(UPLOADS).delete(blobId)
  tx.objectStore(UPLOAD_CHUNKS).delete(chunkBereich(blobId))
  await fertig(tx)
}

/**
 * Nimmt den Fortschritt eines Eintrags weg, sobald nichts mehr von ihm in der
 * Ablage wartet: hochgeladen oder verworfen. Ein stehengebliebener Eintrag
 * hielt die Speicheranzeige an (sie fragt erst, wenn kein Upload mehr läuft).
 */
async function fortschrittPruefen(db: IDBDatabase, eintragId: string): Promise<void> {
  const offen = ((await anfrage(db.transaction(UPLOADS).objectStore(UPLOADS).getAll())) as UploadZeile[]).some(
    (z) => z.eintragId === eintragId,
  )
  if (!offen) fortschrittSetzen(eintragId, () => null)
}

/** Verwirft einen Upload samt seinem Fortschritt. */
async function uploadVerwerfen(db: IDBDatabase, zeile: UploadZeile): Promise<void> {
  await uploadEntfernen(db, zeile.blobId)
  await fortschrittPruefen(db, zeile.eintragId)
}

async function ablageOderFehler(): Promise<IDBDatabase> {
  const db = await ablageDb()
  if (!db) throw new Error('Dateien brauchen eine lokale Ablage (IndexedDB) und ein angemeldetes Konto.')
  return db
}

/** Blobs, deren Upload gerade vorbereitet wird: der Uploader lässt sie in Ruhe. */
const inVorbereitung = new Set<string>()

async function blobAblegen(
  db: IDBDatabase,
  bucket: string,
  eintragId: string,
  kopf: BlobKopf,
  schluessel: CryptoKey,
  behalten: Behalten,
  eingang: boolean,
  lesen: (von: number, bis: number) => Promise<Uint8Array>,
  abgebrochen: () => boolean,
): Promise<UploadZeile> {
  const anzahl = chunkAnzahl(kopf.groesse)
  const zeile: UploadZeile = {
    blobId: kopf.id,
    bucket,
    eintragId,
    chunkAnzahl: anzahl,
    bytes: chiffratGroesse(kopf.groesse),
    pruefwert: await loeschPruefwert(kopf),
    bereit: false,
    behalten: behalten !== 'nein',
    offline: behalten === 'zuletzt' ? behalten : undefined,
    eingang: eingang || undefined,
  }
  await schreiben(db, UPLOADS, (s) => s.put(zeile))
  for (let index = 0; index < anzahl; index++) {
    if (abgebrochen()) throw new Error('abgebrochen')
    const von = Math.min(index * CHUNK_KLARTEXT, kopf.echt)
    const bis = Math.min((index + 1) * CHUNK_KLARTEXT, kopf.echt)
    const daten = await chunkVerschluesseln(await lesen(von, bis), kopf, index, schluessel, eintragId)
    // Einzeln ablegen und abwarten: so liegt nie mehr als ein Chunk im Speicher.
    await schreiben(db, UPLOAD_CHUNKS, (s) => s.put({ blobId: kopf.id, index, daten } satisfies ChunkZeile))
  }
  return zeile
}

/**
 * Verschlüsselt eine Datei in die Upload-Ablage und liefert die Angaben für
 * ihren Tresor-Eintrag. Der Eintrag muss danach gespeichert und
 * `vorbereitungAbschliessen` aufgerufen werden; erst dann geht etwas hoch.
 *
 * `abgebrochen` wird zwischen den Chunks gefragt (Sperren des Tresors).
 *
 * Ohne `userKey` ist es der Posteingang der Kamera-Sicherung: die Köpfe tragen
 * rohe Schlüssel (`blobAnlegen`), und statt eines Eintrags wird ein
 * Datensatz abgelegt; geht das nicht, verwirft `vorbereitungVerwerfen`.
 */
export async function dateiVorbereiten(
  datei: Blob & { name?: string; lastModified?: number },
  userKey: CryptoKey | null,
  bucket: string,
  eintragId: string,
  abgebrochen: () => boolean,
  bilder: { vorschau?: Uint8Array; miniatur?: Uint8Array; original?: Behalten } = {},
): Promise<DateiAngaben> {
  const db = await ablageOderFehler()
  const vorschauDaten = bilder.vorschau ?? new Uint8Array(0)
  const miniaturDaten = bilder.miniatur ?? new Uint8Array(0)
  const original = await blobAnlegen(userKey, eintragId, datei.size)
  const vorschau = await blobAnlegen(userKey, eintragId, vorschauDaten.length, VORSCHAU_GROESSE)
  const miniatur = await blobAnlegen(userKey, eintragId, miniaturDaten.length, MINIATUR_GROESSE)
  const blobs = [original, vorschau, miniatur]
  for (const { kopf } of blobs) inVorbereitung.add(kopf.id)
  try {
    // Eine leere Vorschau oder Miniatur (keine Bilddatei) ist nur Polster und bleibt nicht liegen.
    const eingang = userKey === null
    const zeilen = [
      await blobAblegen(db, bucket, eintragId, original.kopf, original.schluessel, bilder.original ?? originalBehalten(datei.size), eingang, async (von, bis) => {
        return new Uint8Array(await datei.slice(von, bis).arrayBuffer())
      }, abgebrochen),
      await blobAblegen(db, bucket, eintragId, vorschau.kopf, vorschau.schluessel, vorschauDaten.length > 0 ? 'zuletzt' : 'nein', eingang, async (von, bis) => vorschauDaten.slice(von, bis), abgebrochen),
      await blobAblegen(db, bucket, eintragId, miniatur.kopf, miniatur.schluessel, miniaturDaten.length > 0 ? 'immer' : 'nein', eingang, async (von, bis) => miniaturDaten.slice(von, bis), abgebrochen),
    ]
    await schreiben(db, UPLOADS, (s) => {
      for (const zeile of zeilen) s.put({ ...zeile, bereit: true })
    })
  } catch (err) {
    for (const { kopf } of blobs) {
      inVorbereitung.delete(kopf.id)
      await uploadEntfernen(db, kopf.id).catch(() => {})
    }
    throw err
  }
  fortschrittSetzen(eintragId, () => ({ gesendet: 0, gesamt: blobs.reduce((n, b) => n + chiffratGroesse(b.kopf.groesse), 0) }))
  return {
    typ: datei.type || '',
    geaendert: datei.lastModified,
    original: original.kopf,
    vorschau: vorschau.kopf,
    miniatur: miniatur.kopf,
  }
}

/**
 * Der Eintrag ist gespeichert (oder wurde nicht gespeichert): ab jetzt
 * entscheidet der Uploader, ob die Blobs hochgehen.
 */
export function vorbereitungAbschliessen(angaben: DateiAngaben): void {
  for (const kopf of dateiBlobs(angaben)) inVorbereitung.delete(kopf.id)
}

/**
 * Nimmt vorbereitete Blobs wieder aus der Ablage. Für den Posteingang, wenn
 * der Datensatz nicht ankam: ohne ihn kennt niemand ihre Schlüssel, und sie
 * belegten nur Speicher.
 */
export async function vorbereitungVerwerfen(angaben: DateiAngaben): Promise<void> {
  const db = await ablageDb()
  for (const kopf of dateiBlobs(angaben)) {
    if (db) await uploadEntfernen(db, kopf.id).catch(() => {})
    inVorbereitung.delete(kopf.id)
  }
}

/** Ob ein Eintrag lokal lebt: im Cache oder in der Warteschlange, und kein Tombstone. */
function eintragLebt(bucket: string, eintragId: string): boolean {
  const inWarteschlange = warteschlangeLesen(bucket).find((e) => e.id === eintragId)
  if (inWarteschlange) return !inWarteschlange.is_deleted
  const imCache = blobsLesen(bucket).find((e) => e.id === eintragId)
  return !!imCache && !imCache.is_deleted
}

/** Ob ein Eintrag lokal als gelöscht bekannt ist. Fehlt er ganz, ist er es nicht. */
function eintragGeloescht(bucket: string, eintragId: string): boolean {
  const eintrag = warteschlangeLesen(bucket).find((e) => e.id === eintragId) ?? blobsLesen(bucket).find((e) => e.id === eintragId)
  return !!eintrag?.is_deleted
}

function status(err: unknown): number | null {
  return err instanceof SanitizedApiError ? err.status : null
}

/**
 * Ein Lauf zur Zeit. Wer während eines Laufs anstößt, bekommt einen weiteren
 * danach: der laufende hat seine Liste schon gelesen und sähe neu Abgelegtes
 * sonst erst beim nächsten Sync. Das Versprechen gilt für diesen weiteren
 * Lauf, nicht für den, der schon lief.
 */
export function einzeln(arbeit: (bucket: string) => Promise<void>) {
  let lauf: Promise<void> | null = null
  let folge: Promise<void> | null = null
  let nochmal = ''
  const starten = (bucket: string): Promise<void> => {
    if (lauf) {
      nochmal = bucket
      const danach = () => {
        folge = null
        return starten(nochmal)
      }
      folge ??= lauf.then(danach, danach)
      return folge
    }
    lauf = arbeit(bucket).finally(() => {
      lauf = null
    })
    return lauf
  }
  return starten
}

const VOLL: Record<string, UploadFehler> = { VAULT_PLATTE_VOLL: 'platteVoll', VAULT_ZU_VIELE_DATEIEN: 'zuVieleDateien' }

/**
 * Was nur diesen Blob trifft (zu groß, falsche Länge) oder vollen Speicher
 * meldet; alles andere: `null`, warten. Voll hat drei Gründe, und nur beim
 * Kontingent hilft Löschen (bis 02.10.2026 hieß alles „Tresorspeicher voll“).
 */
function uploadFehler(err: unknown): UploadFehler | null {
  const s = status(err)
  if (s === 507) return (err instanceof SanitizedApiError && err.code && VOLL[err.code]) || 'speicherVoll'
  return s === 413 || s === 422 ? 'abgelehnt' : null
}

/**
 * Lädt hoch, was in der Ablage wartet. Bricht beim ersten Fehler ab, der alle
 * träfe (Netz, Sitzung, Server); der nächste Sync stößt wieder an.
 */
export const uploadsFortsetzen = einzeln(hochladen)

async function hochladen(bucket: string): Promise<void> {
  // Ohne geladene Ablage wirkt jeder Eintrag tot, und wartende Uploads würden
  // verworfen.
  // Vor jedem Blob und Chunk gefragt: die Anfragen gehen mit der Sitzung, die
  // gerade angemeldet ist, und nach einem Kontowechsel landeten Dateien sonst
  // im Bucket des anderen Kontos.
  const weiter = () => ablageGeladen(bucket)
  if (!weiter()) return
  const db = await ablageDb()
  if (!db) return
  const zeilen = ((await anfrage(db.transaction(UPLOADS).objectStore(UPLOADS).getAll())) as UploadZeile[]).filter(
    (z) => z.bucket === bucket && !inVorbereitung.has(z.blobId),
  )
  // Jeder Lauf zählt neu: was fertig ist, steht nicht mehr in der Ablage, und
  // schon angekommene Chunks eines Blobs zählen im Lauf wieder mit.
  const gesamt = new Map<string, number>()
  for (const zeile of zeilen) {
    if (zeile.bereit) gesamt.set(zeile.eintragId, (gesamt.get(zeile.eintragId) ?? 0) + zeile.bytes)
  }
  for (const [eintragId, bytes] of gesamt) {
    const fehler = zeilen.find((z) => z.eintragId === eintragId && z.fehler)?.fehler
    fortschrittSetzen(eintragId, () => ({ gesendet: 0, gesamt: bytes, fehler }))
  }
  for (const zeile of zeilen) {
    if (!weiter()) return
    // Ein Posteingang-Upload hat seinen Eintrag erst nach der Übernahme.
    const verwaist = zeile.eingang ? eintragGeloescht(bucket, zeile.eintragId) : !eintragLebt(bucket, zeile.eintragId)
    if (!zeile.bereit || verwaist) {
      // Abgebrochene Vorbereitung oder inzwischen gelöschter Eintrag.
      await uploadVerwerfen(db, zeile)
      continue
    }
    try {
      await blobHochladen(db, zeile, weiter)
    } catch (err) {
      // Ein abgewiesener Blob hält die übrigen nicht auf. Voller Speicher,
      // Netz weg, abgelaufene Sitzung oder gestörter Server träfen jeden
      // weiteren auch: dann bis zum nächsten Anstoß warten.
      const fehler = uploadFehler(err)
      if (fehler && zeile.fehler !== fehler) {
        await schreiben(db, UPLOADS, (s) => s.put({ ...zeile, fehler }))
        fortschrittSetzen(zeile.eintragId, (alt) => (alt ? { ...alt, fehler } : null))
      }
      if (fehler !== 'abgelehnt') return
    }
  }
}

async function blobHochladen(db: IDBDatabase, zeile: UploadZeile, weiter: () => boolean): Promise<void> {
  // Erst reservieren; nur wenn es den Blob schon gibt (ein früherer Lauf kam
  // an), nach dem Stand fragen.
  let vorhanden: number[] = []
  try {
    await blobReservieren(zeile.bucket, zeile.blobId, zeile.chunkAnzahl, zeile.bytes, zeile.pruefwert)
  } catch (err) {
    if (status(err) !== 409) throw err
    const stand = await blobStand(zeile.bucket, zeile.blobId)
    if (stand.state === 'geloescht') {
      await uploadVerwerfen(db, zeile)
      return
    }
    if (stand.state === 'fertig') {
      await uploadAbschliessen(db, zeile)
      return
    }
    vorhanden = stand.vorhanden
  }
  const schon = new Set(vorhanden)
  for (let index = 0; index < zeile.chunkAnzahl; index++) {
    if (!weiter()) throw new Error('Tresor oder Konto gewechselt')
    const chunk = (await anfrage(
      db.transaction(UPLOAD_CHUNKS).objectStore(UPLOAD_CHUNKS).get([zeile.blobId, index]),
    )) as ChunkZeile | undefined
    if (!chunk) {
      // Die Ablage ist unvollständig (vom Browser geräumt): so kommt die Datei nie an.
      await uploadVerwerfen(db, zeile)
      return
    }
    if (!schon.has(index)) await chunkHochladen(zeile.bucket, zeile.blobId, index, chunk.daten)
    const laenge = chunk.daten.byteLength
    fortschrittSetzen(zeile.eintragId, (alt) => (alt ? { ...alt, gesendet: alt.gesendet + laenge } : null))
  }
  if (!weiter()) throw new Error('Tresor oder Konto gewechselt')
  await blobFertig(zeile.bucket, zeile.blobId)
  await uploadAbschliessen(db, zeile)
}

async function uploadAbschliessen(db: IDBDatabase, zeile: UploadZeile): Promise<void> {
  const chunks = zeile.behalten
    ? ((await anfrage(db.transaction(UPLOAD_CHUNKS).objectStore(UPLOAD_CHUNKS).getAll(chunkBereich(zeile.blobId)))) as ChunkZeile[])
    : []
  const tx = db.transaction([UPLOADS, UPLOAD_CHUNKS, BLOB_CACHE], 'readwrite')
  for (const chunk of chunks) tx.objectStore(BLOB_CACHE).put(chunk)
  tx.objectStore(UPLOADS).delete(zeile.blobId)
  tx.objectStore(UPLOAD_CHUNKS).delete(chunkBereich(zeile.blobId))
  await fertig(tx)
  if (zeile.offline === 'zuletzt') await zuletztMerken(db, zeile.blobId, zeile.bytes).catch(() => {})
  await fortschrittPruefen(db, zeile.eintragId)
}

/** Welche dieser Blobs noch nicht vollständig beim Server liegen. */
export async function uploadsOffen(blobIds: string[]): Promise<Set<string>> {
  const db = await ablageDb()
  if (!db) return new Set(blobIds)
  const store = db.transaction(UPLOADS).objectStore(UPLOADS)
  const offen = new Set<string>()
  for (const id of blobIds) {
    if ((await anfrage(store.getKey(id))) !== undefined || inVorbereitung.has(id)) offen.add(id)
  }
  return offen
}

/** Chiffrat eines Chunks, falls es auf dem Gerät liegt: im Cache oder noch in der Upload-Ablage. */
async function lokalesChiffrat(db: IDBDatabase, blobId: string, index: number): Promise<Uint8Array | null> {
  const tx = db.transaction([BLOB_CACHE, UPLOAD_CHUNKS])
  const ausCache = (await anfrage(tx.objectStore(BLOB_CACHE).get([blobId, index]))) as ChunkZeile | undefined
  if (ausCache) return ausCache.daten
  const ausUpload = (await anfrage(tx.objectStore(UPLOAD_CHUNKS).get([blobId, index]))) as ChunkZeile | undefined
  return ausUpload?.daten ?? null
}

interface LeseOptionen {
  /** Als „zuletzt geöffnet“ offline behalten, solange Platz ist (`ZULETZT_GRENZE`). */
  zuletzt?: boolean
  signal?: AbortSignal
  fortschritt?: (anteil: number) => void
}

/**
 * Entschlüsselt einen Blob Chunk für Chunk. Wer einen Teil bekommt, muss ihn
 * vor dem nächsten verbraucht haben: danach wird er genullt.
 */
export async function* klartextTeile(
  kopf: BlobKopf,
  eintragId: string,
  userKey: CryptoKey,
  optionen: LeseOptionen = {},
): AsyncGenerator<Uint8Array> {
  const db = await ablageDb()
  const zuletzt = !!optionen.zuletzt && chiffratGroesse(kopf.groesse) <= ZULETZT_HOECHSTENS
  const schluessel = await blobSchluessel(kopf, userKey, eintragId)
  const anzahl = chunkAnzahl(kopf.groesse)
  // Die Zeile kommt vor dem ersten Chunk in den Cache: bricht das Lesen ab
  // (Lichtbox weitergeblättert, Netz weg), zählt das schon Geschriebene gegen
  // `ZULETZT_GRENZE`, statt ohne Grenze liegenzubleiben. Erst dann, sonst
  // verdrängte offline jeder Blick auf eine fehlende Datei echte Einträge.
  let gemerkt = false
  for (let index = 0; index < anzahl; index++) {
    optionen.signal?.throwIfAborted()
    let klartext: Uint8Array | null = null
    const lokal = db ? await lokalesChiffrat(db, kopf.id, index) : null
    if (db && lokal) {
      try {
        klartext = await chunkEntschluesseln(lokal, kopf, index, schluessel, eintragId)
      } catch {
        // Kaputt im Cache: weg damit und frisch vom Server, sonst bliebe die Datei hier für immer unlesbar.
        await schreiben(db, BLOB_CACHE, (s) => s.delete([kopf.id, index])).catch(() => {})
      }
    }
    if (!klartext) {
      const chiffrat = await chunkLaden(kopf.id, index, optionen.signal)
      klartext = await chunkEntschluesseln(chiffrat, kopf, index, schluessel, eintragId)
      // In den Cache kommt nur, was sich entschlüsseln ließ.
      if (db && zuletzt) {
        if (!gemerkt) await zuletztMerken(db, kopf.id, chiffratGroesse(kopf.groesse))
        gemerkt = true
        await schreiben(db, BLOB_CACHE, (s) => s.put({ blobId: kopf.id, index, daten: chiffrat } satisfies ChunkZeile))
      }
    }
    try {
      yield klartext
    } finally {
      klartext.fill(0)
    }
    optionen.fortschritt?.((index + 1) / anzahl)
  }
  if (db && zuletzt && !gemerkt) await zuletztMerken(db, kopf.id, chiffratGroesse(kopf.groesse)).catch(() => {})
}

/**
 * Entschlüsselt einen Blob in ein `Blob`. Jeder Chunk wird einzeln zu einem
 * `Blob`; die Laufzeit darf die Teile auslagern, und im JavaScript-Speicher
 * liegt nie mehr als ein Chunk Klartext.
 */
export async function blobLesen(
  kopf: BlobKopf,
  eintragId: string,
  userKey: CryptoKey,
  typ: string,
  optionen: LeseOptionen = {},
): Promise<Blob> {
  const teile: Blob[] = []
  for await (const klartext of klartextTeile(kopf, eintragId, userKey, optionen)) {
    teile.push(new Blob([klartext as BlobPart]))
  }
  return new Blob(teile, { type: typ })
}

/*
 * Offline verfügbar. Miniaturen liegen immer auf dem Gerät. Originale nur,
 * wenn sie angeheftet sind („Offline verfügbar“) oder zuletzt geöffnet
 * wurden; die zuletzt geöffneten teilen sich `ZULETZT_GRENZE`, die ältesten
 * fallen zuerst heraus. Angeheftete zählen nicht mit und fallen nie heraus.
 * Das gilt nur für dieses Gerät und wird nicht synchronisiert.
 */

interface OfflineZeile {
  blobId: string
  angeheftet: boolean
  zuletzt: number
  bytes: number
}

export const ZULETZT_GRENZE = 512 * 1024 * 1024
/** Größere Dateien werden nur angeheftet offline gehalten, nicht nebenbei. */
const ZULETZT_HOECHSTENS = 128 * 1024 * 1024

async function zuletztMerken(db: IDBDatabase, blobId: string, bytes: number): Promise<void> {
  const alt = (await anfrage(db.transaction(OFFLINE).objectStore(OFFLINE).get(blobId))) as OfflineZeile | undefined
  const zeile: OfflineZeile = { blobId, angeheftet: alt?.angeheftet ?? false, zuletzt: Date.now(), bytes }
  await schreiben(db, OFFLINE, (s) => s.put(zeile))
  const alle = ((await anfrage(db.transaction(OFFLINE).objectStore(OFFLINE).getAll())) as OfflineZeile[])
    .filter((z) => !z.angeheftet)
    .sort((a, b) => b.zuletzt - a.zuletzt)
  let summe = 0
  for (const z of alle) {
    summe += z.bytes
    if (summe > ZULETZT_GRENZE) await offlineZeileEntfernen(db, z.blobId)
  }
}

async function offlineZeileEntfernen(db: IDBDatabase, blobId: string): Promise<void> {
  const tx = db.transaction([OFFLINE, BLOB_CACHE], 'readwrite')
  tx.objectStore(OFFLINE).delete(blobId)
  tx.objectStore(BLOB_CACHE).delete(chunkBereich(blobId))
  await fertig(tx)
}

/** Welche dieser Blobs angeheftet sind. */
export async function angeheftet(blobIds: string[]): Promise<Set<string>> {
  const db = await ablageDb()
  if (!db) return new Set()
  const zeilen = (await anfrage(db.transaction(OFFLINE).objectStore(OFFLINE).getAll())) as OfflineZeile[]
  const gesucht = new Set(blobIds)
  return new Set(zeilen.filter((z) => z.angeheftet && gesucht.has(z.blobId)).map((z) => z.blobId))
}

/**
 * Ob Chiffrat die Länge hat, die der Kopf für diesen Chunk vorgibt. Was der
 * Server sonst liefert, ließe sich nie öffnen und kommt nicht in den Cache.
 */
function laengePasst(kopf: BlobKopf, index: number, daten: Uint8Array): boolean {
  return daten.byteLength === chunkLaenge(kopf.groesse, index) + CHUNK_UEBERHANG
}

/**
 * Heftet ein Original an und lädt sein Chiffrat auf das Gerät. Jeder Chunk
 * wird vorher probeweise entschlüsselt und der Klartext gleich genullt: bis
 * 02.10.2026 zählte nur die Länge, und ein kaputter oder fremder Chunk
 * derselben Größe galt als angeheftet, ließ sich offline aber nie öffnen.
 *
 * Scheitert es (Netz weg, abgebrochen, nicht zu öffnen), gilt das Original
 * nicht als angeheftet, und der Fehler geht an den Aufrufer. Was schon geholt
 * war, fällt wieder heraus; war das Original vorher zuletzt geöffnet, bleibt
 * es das, und die Chunks zählen gegen `ZULETZT_GRENZE`.
 */
export async function offlineAnheften(
  kopf: BlobKopf,
  eintragId: string,
  userKey: CryptoKey,
  fortschritt?: (anteil: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  const db = await ablageOderFehler()
  const schluessel = await blobSchluessel(kopf, userKey, eintragId)
  const vorher = (await anfrage(db.transaction(OFFLINE).objectStore(OFFLINE).get(kopf.id))) as OfflineZeile | undefined
  await schreiben(db, OFFLINE, (s) => s.put({ blobId: kopf.id, angeheftet: true, zuletzt: Date.now(), bytes: chiffratGroesse(kopf.groesse) } satisfies OfflineZeile))
  const oeffnetSich = async (daten: Uint8Array, index: number) => {
    try {
      ;(await chunkEntschluesseln(daten, kopf, index, schluessel, eintragId)).fill(0)
      return true
    } catch {
      return false
    }
  }
  try {
    const anzahl = chunkAnzahl(kopf.groesse)
    for (let index = 0; index < anzahl; index++) {
      signal?.throwIfAborted()
      const lokal = await lokalesChiffrat(db, kopf.id, index)
      // Auch was schon hier liegt: Miniaturen ohne Schlüssel kamen ungeprüft in den Cache.
      if (!lokal || !(await oeffnetSich(lokal, index))) {
        const daten = await chunkLaden(kopf.id, index, signal)
        if (!(await oeffnetSich(daten, index))) throw new Error('Chunk lässt sich nicht öffnen')
        await schreiben(db, BLOB_CACHE, (s) => s.put({ blobId: kopf.id, index, daten } satisfies ChunkZeile))
      }
      fortschritt?.((index + 1) / anzahl)
    }
  } catch (err) {
    if (vorher) await schreiben(db, OFFLINE, (s) => s.put(vorher)).catch(() => {})
    else await offlineZeileEntfernen(db, kopf.id).catch(() => {})
    throw err
  }
}

/**
 * Nimmt OFFLINE-Zeilen und Cache-Chunks aller Blobs weg, die kein Eintrag
 * mehr nennt (`benutzt`: alle Blobs aller Einträge, frühere Fassungen,
 * Vorschauen und Miniaturen eingeschlossen). So fällt vom Gerät, was ein
 * anderes Gerät gelöscht oder ersetzt hat, auch ein angeheftetes Original.
 *
 * Uploads fasst sie nicht an, und Blobs, die gerade hochgehen oder vorbereitet
 * werden, bleiben auch im Cache: ihr Eintrag kann noch unterwegs sein.
 */
export async function unbenutzteBlobsEntfernen(benutzt: Set<string>): Promise<void> {
  const db = await ablageDb()
  if (!db) return
  const lesen = db.transaction([OFFLINE, BLOB_CACHE, UPLOADS])
  const [offline, cache, uploads] = await Promise.all([
    anfrage(lesen.objectStore(OFFLINE).getAllKeys()) as Promise<string[]>,
    anfrage(lesen.objectStore(BLOB_CACHE).getAllKeys()) as Promise<[string, number][]>,
    anfrage(lesen.objectStore(UPLOADS).getAllKeys()) as Promise<string[]>,
  ])
  const laufend = new Set(uploads)
  const weg = new Set(
    [...offline, ...cache.map(([id]) => id)].filter((id) => !benutzt.has(id) && !laufend.has(id) && !inVorbereitung.has(id)),
  )
  if (weg.size === 0) return
  const tx = db.transaction([OFFLINE, BLOB_CACHE], 'readwrite')
  for (const id of weg) {
    tx.objectStore(OFFLINE).delete(id)
    tx.objectStore(BLOB_CACHE).delete(chunkBereich(id))
  }
  await fertig(tx)
}

/** Nimmt ein Original wieder vom Gerät. */
export async function offlineLoesen(blobId: string): Promise<void> {
  const db = await ablageDb()
  if (db) await offlineZeileEntfernen(db, blobId)
}

const KLEIN_JE_ANFRAGE = 100

/**
 * Liest viele einteilige Blobs (Miniaturen) auf einmal: zuerst vom Gerät, den
 * Rest gebündelt vom Server. Was vom Server kommt, bleibt im Cache, damit die
 * Galerie offline ihre Bilder hat. Was fehlt, offline nicht zu holen ist oder
 * sich nicht entschlüsseln lässt, steht nicht in der Antwort.
 *
 * Ohne `userKey` wird nur der Cache gefüllt (Vorladen für offline).
 */
export async function miniaturenLesen(
  anfragen: { kopf: BlobKopf; eintragId: string }[],
  userKey: CryptoKey | null,
): Promise<Map<string, Uint8Array>> {
  const db = await ablageDb()
  const einteilig = anfragen.filter(({ kopf }) => kopf.echt > 0 && chunkAnzahl(kopf.groesse) === 1)
  const chiffrate = new Map<string, Uint8Array>()
  if (db) {
    if (userKey) {
      for (const { kopf } of einteilig) {
        const lokal = await lokalesChiffrat(db, kopf.id, 0)
        if (lokal) chiffrate.set(kopf.id, lokal)
      }
    } else {
      // Nur vorladen: ein Blick auf die Schlüssel genügt, gelesen wird nichts.
      const tx = db.transaction([BLOB_CACHE, UPLOAD_CHUNKS])
      const schluessel = [
        ...((await anfrage(tx.objectStore(BLOB_CACHE).getAllKeys())) as [string, number][]),
        ...((await anfrage(tx.objectStore(UPLOAD_CHUNKS).getAllKeys())) as [string, number][]),
      ]
      const da = new Set(schluessel.map(([id]) => id))
      for (const { kopf } of einteilig) if (da.has(kopf.id)) chiffrate.set(kopf.id, new Uint8Array(0))
    }
  }
  const fehlend = einteilig.filter(({ kopf }) => !chiffrate.has(kopf.id))
  for (let i = 0; i < fehlend.length; i += KLEIN_JE_ANFRAGE) {
    const gefragt = new Map(fehlend.slice(i, i + KLEIN_JE_ANFRAGE).map(({ kopf }) => [kopf.id, kopf]))
    let geladen: Map<string, Uint8Array>
    try {
      geladen = await kleineLaden([...gefragt.keys()])
    } catch {
      break // offline oder Server nicht erreichbar: was da ist, reicht für jetzt
    }
    // Nur Gefragtes in der Länge, die der Kopf vorgibt; alles andere ließe sich nie öffnen.
    for (const [id, daten] of geladen) {
      const kopf = gefragt.get(id)
      if (!kopf || !laengePasst(kopf, 0, daten)) geladen.delete(id)
    }
    if (db && geladen.size > 0) {
      await schreiben(db, BLOB_CACHE, (s) => {
        for (const [blobId, daten] of geladen) s.put({ blobId, index: 0, daten } satisfies ChunkZeile)
      }).catch(() => {})
    }
    for (const [id, daten] of geladen) chiffrate.set(id, daten)
  }
  const ergebnis = new Map<string, Uint8Array>()
  if (!userKey) return ergebnis
  for (const { kopf, eintragId } of einteilig) {
    const chiffrat = chiffrate.get(kopf.id)
    if (!chiffrat) continue
    try {
      ergebnis.set(kopf.id, await chunkEntschluesseln(chiffrat, kopf, 0, await blobSchluessel(kopf, userKey, eintragId), eintragId))
    } catch {
      // Fremder, vertauschter oder kaputter Blob: kein Bild. Aus dem Cache
      // fällt er, damit der nächste Aufruf ihn frisch holt.
      if (db) await schreiben(db, BLOB_CACHE, (s) => s.delete([kopf.id, 0])).catch(() => {})
    }
  }
  return ergebnis
}

/** Ob alle Chunks eines Blobs auf dem Gerät liegen. */
export async function blobLokal(kopf: BlobKopf): Promise<boolean> {
  const db = await ablageDb()
  if (!db) return false
  for (let index = 0; index < chunkAnzahl(kopf.groesse); index++) {
    if (!(await lokalesChiffrat(db, kopf.id, index))) return false
  }
  return true
}

/**
 * Löscht die Blobs gelöschter Dateien, sobald der Server deren Tombstone
 * angenommen hat: der Tombstone steht im Cache, und in der Warteschlange ist
 * für den Eintrag nichts mehr offen. Hat der Server abgelehnt, steht im Cache
 * wieder die alte Fassung, und es gibt nichts zu tun.
 *
 * Genauso bei einer bearbeiteten Datei: die Fassung, die aus `frueher`
 * herausfällt, verschwindet erst, wenn der Server den neuen Eintrag hat.
 */
export const loeschungenAbarbeiten = einzeln(loeschen)

async function loeschen(bucket: string): Promise<void> {
  if (!ablageGeladen(bucket)) return
  const offen = new Set(warteschlangeLesen(bucket).map((e) => e.id))
  const faellig = blobsLesen(bucket).filter((e) => e.loeschBlobs?.length && !offen.has(e.id))
  const db = await ablageDb()
  for (const grab of faellig) {
    for (const blob of grab.loeschBlobs ?? []) {
      // Nach einem Kontowechsel kämen Löschungen als 404 zurück und gälten als erledigt.
      if (!ablageGeladen(bucket)) return
      try {
        await blobLoeschen(bucket, blob.id, blob.loeschen)
      } catch (err) {
        // 404: nie angekommen oder schon weg. Ein falscher Löschnachweis
        // (fremder Kopf) gilt nie, wiederholen hilft dann nicht. Jedes andere
        // 403 (CSRF, Tresor abgeschaltet) ist vorübergehend: der nächste Lauf
        // versucht es wieder, sonst belegte die Datei den Speicher für immer.
        if (err instanceof SanitizedApiError && err.code === 'VAULT_LOESCHNACHWEIS_FALSCH') {
          console.warn(`Tresor: Löschnachweis für Blob ${blob.id} abgelehnt.`)
        } else if (status(err) !== 404) return
      }
      if (db) {
        await uploadEntfernen(db, blob.id).catch(() => {})
        await offlineZeileEntfernen(db, blob.id).catch(() => {})
      }
    }
    // Nach den Anfragen frisch lesen und nur diesen Eintrag umschreiben, wenn
    // er noch derselbe Tombstone ist.
    const aktuell = blobsLesen(bucket)
    const jetzt = aktuell.find((e) => e.id === grab.id)
    if (jetzt && jetzt.ciphertext === grab.ciphertext) {
      blobsSchreiben(
        bucket,
        aktuell.map((e) => (e.id === grab.id ? { ...e, loeschBlobs: undefined } : e)),
      )
    }
  }
}

/** Offene Ansichten entschlüsselter Dateien (Objekt-URLs). */
const ansichten = new Set<string>()

export function ansichtOeffnen(blob: Blob): string {
  const url = URL.createObjectURL(blob)
  ansichten.add(url)
  return url
}

export function ansichtSchliessen(url: string): void {
  if (ansichten.delete(url)) URL.revokeObjectURL(url)
}

/**
 * Beim Sperren des Tresors und beim Abmelden: keine entschlüsselte Datei
 * bleibt erreichbar. Der Upload-Fortschritt fällt mit; er gehört zur Sitzung,
 * und nach einem Kontowechsel hielte ein stehengebliebener Eintrag die
 * Speicheranzeige des nächsten Kontos an. Der nächste Lauf zählt neu.
 */
export function ansichtenSchliessen(): void {
  for (const url of ansichten) URL.revokeObjectURL(url)
  ansichten.clear()
  useTresorUploads.setState({ je: {} })
}

beimLeeren.add(ansichtenSchliessen)
