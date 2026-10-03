/**
 * Kamera-Sicherung (Android): neue Aufnahmen aus DCIM gehen verschlüsselt in
 * den Tresor, solange MSS offen ist.
 *
 * Gesichert wird das Original Bit für Bit, mit Aufnahmeort. Bei offenem Tresor
 * wie jede andere Datei (`dateiHinzufuegen`). Bei gesperrtem Tresor über den
 * Posteingang (`tresorEingang.ts`): hochgeladen wird gleich, der Eintrag
 * entsteht beim nächsten Entsperren. Das geht erst, wenn Schlüsselpaar und
 * Geräteeintrag beim Server liegen (`eingang` im Stand).
 *
 * Was erledigt ist, steht als Marke: alle Aufnahmen bis `bis` sind gesichert
 * oder bewusst übersprungen. Die Marke ist ab Android 11 die Generation der
 * letzten Änderung, nicht die Kennung: eine Aufnahme, die noch geschrieben
 * wird (laufendes Video), fiel sonst hinter ein später fertiges Foto zurück
 * und wurde nie gesichert. Doppelt gesichert wird trotzdem nichts, denn vor
 * jeder Aufnahme wird gegen den Tresor geprüft (Gerät, Kennung, SHA-256).
 *
 * Der Zustand liegt je Bucket in der kontogebundenen Ablage (AGENTS.md Punkt 50).
 */
import { create } from 'zustand'

import {
  medienAufnahmen,
  medienLesen,
  medienPapierkorb,
  medienPruefsumme,
  medienStand,
  medienZugriff,
  sicherungSchluessel,
  sicherungSignieren,
  type Aufnahme,
  type MedienZugriff,
} from '@/desktop/tauri'
import { SanitizedApiError } from '@/api/client'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { ablageDb, ablageGeladen, ablageLaden, anfrage, fertig, KAMERA } from './tresorAblage'
import { bildAngaben } from './tresorBilder'
import { blobStand, eingangAblegen } from './tresorBlobApi'
import {
  dateiVorbereiten,
  einzeln,
  uploadsFortsetzen,
  vorbereitungAbschliessen,
  vorbereitungVerwerfen,
  type DateiQuelle,
} from './tresorDateien'
import { eingangVerpacken, posteingangEinrichten, type EingangOeffentlich } from './tresorEingang'
import { sichtbareEintraege } from './tresorOrdner'
import { getPendingQueue, useVaultStore } from './vaultStore'

export interface KameraStand {
  bucket: string
  an: boolean
  nurWlan: boolean
  /** Zufällige Kennung dieser Installation, je Tresor. */
  geraet: string
  /** Alle Aufnahmen bis zu dieser Marke sind erledigt. */
  bis: number
  /** Fassung des MediaStore, zu der `bis` gehört. */
  fassung: string
  gesichert: number
  zuletzt?: number
  /** Öffentlicher Teil des Posteingangs; fehlt, solange bei gesperrtem Tresor nichts gesichert wird. */
  eingang?: EingangOeffentlich
}

/** Warum gerade nichts gesichert wird. */
export type KameraWarten = 'zugriff' | 'wlan' | 'fehler' | null

export const useKameraSicherung = create<{ stand: KameraStand | null; laeuft: boolean; warten: KameraWarten }>(() => ({
  stand: null,
  laeuft: false,
  warten: null,
}))

/** Aufnahmen je Abfrage an den MediaStore. */
const JE_RUNDE = 20
/** Gelesen wird in Tresor-Chunks. */
const STUECK = 4 * 1024 * 1024

async function lesen(bucket: string): Promise<KameraStand | null> {
  const db = await ablageDb()
  if (!db) return null
  return ((await anfrage(db.transaction(KAMERA).objectStore(KAMERA).get(bucket))) as KameraStand | undefined) ?? null
}

async function schreiben(stand: KameraStand): Promise<void> {
  const db = await ablageDb()
  if (!db) throw new Error('Keine Ablage')
  const tx = db.transaction(KAMERA, 'readwrite')
  tx.objectStore(KAMERA).put(stand)
  await fertig(tx)
  useKameraSicherung.setState({ stand })
}

/** Liest den Stand des offenen Tresors in die Anzeige. */
export async function kameraStandLaden(bucket: string): Promise<KameraStand | null> {
  const stand = await lesen(bucket)
  useKameraSicherung.setState({ stand })
  return stand
}

function offenerBucket(): string | null {
  const s = useVaultStore.getState()
  return s.isUnlocked && s.userKey && s.bucketId ? s.bucketId : null
}

async function alleStaende(): Promise<KameraStand[]> {
  const db = await ablageDb()
  if (!db) return []
  return (await anfrage(db.transaction(KAMERA).objectStore(KAMERA).getAll())) as KameraStand[]
}

/**
 * Der Bucket, für den gesichert wird: der offene Tresor, sonst der, für den
 * der Posteingang eingerichtet ist. Die Ablage gehört dem angemeldeten Konto,
 * fremde Stände gibt es hier nicht (Punkt 50).
 */
async function sicherungsBucket(): Promise<string | null> {
  const offen = offenerBucket()
  if (offen) return offen
  return (await alleStaende()).find((s) => s.an && s.eingang)?.bucket ?? null
}

/**
 * Richtet bei offenem Tresor den Posteingang ein, falls er fehlt, und räumt
 * Stände früherer Tresore desselben Kontos ab (nach einem Zurücksetzen).
 */
async function eingangBereitmachen(bucket: string): Promise<void> {
  const db = await ablageDb()
  if (db) {
    const alt = (await alleStaende()).filter((s) => s.bucket !== bucket)
    if (alt.length > 0) {
      const tx = db.transaction(KAMERA, 'readwrite')
      for (const s of alt) tx.objectStore(KAMERA).delete(s.bucket)
      await fertig(tx)
    }
  }
  const stand = await lesen(bucket)
  if (!stand?.an || stand.eingang) return
  const eingang = await posteingangEinrichten(bucket, stand.geraet, sicherungSchluessel).catch(() => null)
  if (!eingang) return
  const frisch = await lesen(bucket)
  if (!frisch?.an || frisch.geraet !== stand.geraet) return
  await schreiben({ ...frisch, eingang })
}

/**
 * Schaltet ein: fragt nach Zugriff und merkt sich, ab welcher Aufnahme neu
 * zählt. Gesichert wird erst, was danach aufgenommen wird.
 */
export async function kameraEinschalten(bucket: string): Promise<MedienZugriff> {
  const zugriff = await medienZugriff(true)
  if (zugriff.stand !== 'voll') return zugriff
  const alt = await lesen(bucket)
  const jetzt = await medienStand()
  const weiter = alt?.an && alt.fassung === jetzt.fassung
  await schreiben({
    bucket,
    an: true,
    nurWlan: alt?.nurWlan ?? false,
    geraet: alt?.geraet ?? crypto.randomUUID(),
    bis: weiter ? alt.bis : jetzt.marke,
    fassung: jetzt.fassung,
    gesichert: alt?.gesichert ?? 0,
    zuletzt: alt?.zuletzt,
    eingang: weiter ? alt.eingang : undefined,
  })
  void kameraAnstossen(bucket)
  void eingangBereitmachen(bucket)
  return zugriff
}

export async function kameraAusschalten(bucket: string): Promise<void> {
  const alt = await lesen(bucket)
  if (alt) await schreiben({ ...alt, an: false })
  useKameraSicherung.setState({ warten: null })
}

export async function kameraNurWlan(bucket: string, nurWlan: boolean): Promise<void> {
  const alt = await lesen(bucket)
  if (!alt) return
  await schreiben({ ...alt, nurWlan })
  void kameraAnstossen(bucket)
}

/** Sichert auch, was vor dem Einschalten aufgenommen wurde. */
export async function kameraVorhandeneSichern(bucket: string): Promise<void> {
  const alt = await lesen(bucket)
  if (!alt?.an) return
  await schreiben({ ...alt, bis: 0 })
  void kameraAnstossen(bucket)
}

/** Ohne Auskunft über das Netz gilt es nicht als WLAN (Punkt 58). */
function imWlan(): boolean {
  const verbindung = (navigator as Navigator & { connection?: { type?: string } }).connection
  return verbindung?.type === 'wifi' || verbindung?.type === 'ethernet'
}

function quellen(geraet: string): Set<string> {
  const schluessel = new Set<string>()
  for (const item of useVaultStore.getState().items) {
    const q = item.datei?.quelle
    if (q && q.geraet === geraet) schluessel.add(`${q.medienId}:${q.sha256}`)
  }
  return schluessel
}

type Ergebnis = 'gesichert' | 'uebersprungen' | 'warten'

/**
 * Bei gesperrtem Tresor: Blobs mit rohen Schlüsseln ablegen, den Datensatz
 * verschlüsselt und unterschrieben in den Posteingang, dann hochladen. Kommt
 * der Datensatz nicht an, fallen die Blobs wieder aus der Ablage: ohne ihn
 * kennt niemand ihre Schlüssel.
 */
async function inDenPosteingang(datei: File, quelle: DateiQuelle, stand: KameraStand, eingang: EingangOeffentlich): Promise<void> {
  const bucket = stand.bucket
  const eintragId = crypto.randomUUID()
  const { vorschau, miniatur, ...bild } = await bildAngaben(datei)
  const angaben = await dateiVorbereiten(datei, null, bucket, eintragId, () => !ablageGeladen(bucket), {
    vorschau,
    miniatur,
    original: 'nein',
  })
  try {
    const { original, vorschau: vorschauKopf, miniatur: miniaturKopf } = angaben
    const umschlag = await eingangVerpacken(
      { name: datei.name, typ: angaben.typ, geaendert: angaben.geaendert, ...bild, original, vorschau: vorschauKopf, miniatur: miniaturKopf, quelle },
      bucket,
      eintragId,
      eingang,
      stand.geraet,
      (daten) => sicherungSignieren(stand.geraet, daten),
    )
    await eingangAblegen(bucket, eintragId, umschlag)
  } catch (err) {
    await vorbereitungVerwerfen(angaben)
    throw err
  }
  vorbereitungAbschliessen(angaben)
  void uploadsFortsetzen(bucket)
}

async function eineSichern(
  aufnahme: Aufnahme,
  stand: KameraStand,
  bekannt: Set<string>,
  weiter: () => boolean,
): Promise<Ergebnis> {
  let vorher: { sha256: string; groesse: number }
  try {
    vorher = await medienPruefsumme(aufnahme.id, aufnahme.art)
  } catch {
    // Inzwischen gelöscht oder nicht lesbar: bleibt liegen, wie es ist.
    return 'uebersprungen'
  }
  const schluessel = `${aufnahme.id}:${vorher.sha256}`
  if (bekannt.has(schluessel)) return 'uebersprungen'

  const teile: Uint8Array[] = []
  for (let von = 0; von < vorher.groesse; von += STUECK) {
    const teil = await medienLesen(aufnahme.id, aufnahme.art, von, Math.min(STUECK, vorher.groesse - von))
    if (teil.length === 0) return 'warten'
    teile.push(teil)
    if (!weiter()) return 'warten'
  }
  // Wird die Datei gerade geschrieben, stimmen die Bytes nicht mit der Prüfsumme
  // überein. Dann beim nächsten Lauf noch einmal.
  const nachher = await medienPruefsumme(aufnahme.id, aufnahme.art).catch(() => null)
  if (!nachher || nachher.sha256 !== vorher.sha256 || teile.reduce((n, t) => n + t.length, 0) !== vorher.groesse) {
    return 'warten'
  }

  const name = aufnahme.name || `${aufnahme.art === 'video' ? 'VID' : 'IMG'}_${aufnahme.id}`
  const datei = new File(teile as BlobPart[], name, { type: aufnahme.typ, lastModified: aufnahme.aufgenommen })
  const quelle: DateiQuelle = { geraet: stand.geraet, medienId: aufnahme.id, art: aufnahme.art, sha256: vorher.sha256 }
  if (offenerBucket() === stand.bucket) {
    // Das Original liegt schon in der Galerie, eine zweite Kopie im Cache wäre doppelt.
    await useVaultStore.getState().dateiHinzufuegen(datei, undefined, { quelle, original: 'nein' })
  } else if (stand.eingang) {
    await inDenPosteingang(datei, quelle, stand, stand.eingang)
  } else {
    return 'warten'
  }
  bekannt.add(schluessel)
  return 'gesichert'
}

async function sichern(bucket: string): Promise<void> {
  const offen = offenerBucket() === bucket
  const konto = angemeldetesKonto()
  let stand = await lesen(bucket)
  if (offen) useKameraSicherung.setState({ stand })
  if (!stand?.an) return
  if (!offen) {
    if (!stand.eingang || konto === null) return
    if (!ablageGeladen(bucket)) await ablageLaden(bucket)
  }
  // Offen: solange dieser Tresor offen ist. Gesperrt: solange dasselbe Konto angemeldet ist.
  const weiter = offen ? () => offenerBucket() === bucket : () => angemeldetesKonto() === konto && ablageGeladen(bucket)
  if (stand.nurWlan && !imWlan()) {
    useKameraSicherung.setState({ warten: 'wlan' })
    return
  }
  const zugriff = await medienZugriff(false).catch(() => null)
  if (zugriff?.stand !== 'voll') {
    useKameraSicherung.setState({ warten: 'zugriff' })
    return
  }
  useKameraSicherung.setState({ laeuft: true, warten: null })
  try {
    // Ein neu aufgebauter MediaStore zählt von vorn; mit der alten Marke fände
    // die Sicherung nie wieder etwas. Weiter ab dem, was es jetzt gibt, wie beim Einschalten.
    const jetzt = await medienStand()
    if (jetzt.fassung !== stand.fassung) {
      stand = { ...stand, bis: jetzt.marke, fassung: jetzt.fassung }
      await schreiben(stand)
    }
    const bekannt = quellen(stand.geraet)
    for (;;) {
      const aufnahmen = await medienAufnahmen(stand.bis, JE_RUNDE)
      if (aufnahmen.length === 0) return
      for (const aufnahme of aufnahmen) {
        if (!weiter()) return
        const ergebnis = await eineSichern(aufnahme, stand, bekannt, weiter)
        if (ergebnis === 'warten') return
        // Frisch lesen: Schalter und Zähler können sich während des Lesens geändert haben (Punkt 45).
        const frisch = await lesen(bucket)
        if (!frisch?.an || frisch.geraet !== stand.geraet || frisch.fassung !== stand.fassung) return
        stand = {
          ...frisch,
          bis: Math.max(frisch.bis, aufnahme.marke),
          gesichert: frisch.gesichert + (ergebnis === 'gesichert' ? 1 : 0),
          zuletzt: ergebnis === 'gesichert' ? Date.now() : frisch.zuletzt,
        }
        await schreiben(stand)
      }
    }
  } catch (err) {
    // Zurückgesetzt: dieser Tresor nimmt nichts mehr an, auch kein späterer Lauf.
    if (err instanceof SanitizedApiError && err.code === 'VAULT_ZURUECKGESETZT') {
      const db = await ablageDb()
      if (db) {
        const tx = db.transaction(KAMERA, 'readwrite')
        tx.objectStore(KAMERA).delete(bucket)
        await fertig(tx).catch(() => {})
      }
      return
    }
    // Gesperrt oder gewechselt ist kein Fehler, nur ein Ende.
    if (weiter()) useKameraSicherung.setState({ warten: 'fehler' })
  } finally {
    useKameraSicherung.setState({ laeuft: false })
  }
}

/** Ein Lauf zur Zeit; ein Anstoß währenddessen wird danach nachgeholt (Punkt 64). */
export const kameraAnstossen = einzeln(sichern)

/** Wie oft bei offenem Tresor nach neuen Aufnahmen gesehen wird. */
const TAKT_MS = 60_000

/**
 * Stößt die Sicherung beim Entsperren, bei Rückkehr in den Vordergrund, wenn
 * das Netz zurückkommt, und jede Minute an. Einmal beim Start der App (nur Android).
 */
export function kameraBeobachten(): () => void {
  const anstossen = () => {
    if (document.visibilityState !== 'visible') return
    void sicherungsBucket().then((bucket) => {
      if (bucket) void kameraAnstossen(bucket)
    })
  }
  const abmelden = useVaultStore.subscribe((s, vorher) => {
    if (s.isUnlocked && s.bucketId && (!vorher.isUnlocked || vorher.bucketId !== s.bucketId)) {
      const bucket = s.bucketId
      void kameraStandLaden(bucket).then(anstossen)
      void eingangBereitmachen(bucket)
    }
    if (!s.isUnlocked && vorher.isUnlocked) useKameraSicherung.setState({ stand: null, warten: null })
  })
  const takt = window.setInterval(anstossen, TAKT_MS)
  window.addEventListener('online', anstossen)
  document.addEventListener('visibilitychange', anstossen)
  return () => {
    abmelden()
    window.clearInterval(takt)
    window.removeEventListener('online', anstossen)
    document.removeEventListener('visibilitychange', anstossen)
  }
}

export interface Freigabe {
  bilder: number[]
  videos: number[]
  bytes: number
}

/**
 * Was sich aus der Galerie entfernen lässt: nur Aufnahmen dieses Geräts, deren
 * Eintrag beim Server angekommen ist, deren Original dort fertig liegt und
 * deren Datei in DCIM noch genau die gesicherten Bytes hat.
 */
export async function freigebbar(bucket: string): Promise<Freigabe> {
  const stand = await lesen(bucket)
  const ergebnis: Freigabe = { bilder: [], videos: [], bytes: 0 }
  if (!stand || offenerBucket() !== bucket) return ergebnis
  const wartend = new Set(getPendingQueue(bucket).map((e) => e.id))
  for (const item of sichtbareEintraege(useVaultStore.getState().items)) {
    const q = item.datei?.quelle
    if (!q || q.geraet !== stand.geraet || wartend.has(item.id)) continue
    const original = item.datei!.original
    const amServer = await blobStand(bucket, original.id).catch(() => null)
    if (amServer?.state !== 'fertig') continue
    const jetzt = await medienPruefsumme(q.medienId, q.art).catch(() => null)
    if (!jetzt || jetzt.sha256 !== q.sha256) continue
    if (offenerBucket() !== bucket) return { bilder: [], videos: [], bytes: 0 }
    ;(q.art === 'video' ? ergebnis.videos : ergebnis.bilder).push(q.medienId)
    ergebnis.bytes += jetzt.groesse
  }
  return ergebnis
}

/** Legt die Aufnahmen in den Papierkorb der Galerie. Android fragt selbst nach. */
export async function speicherFreigeben(freigabe: Freigabe): Promise<boolean> {
  if (freigabe.bilder.length + freigabe.videos.length === 0) return false
  return medienPapierkorb(freigabe.bilder, freigabe.videos)
}
