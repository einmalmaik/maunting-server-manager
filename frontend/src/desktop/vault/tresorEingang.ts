/**
 * Posteingang des Tresors: so kommt die Kamera-Sicherung in den Tresor.
 *
 * Der Hintergrund-Job des Telefons (`KameraArbeit.kt`) hat kein
 * Master-Passwort, also keinen `userKey` und keinen Eintrag. Er lädt die
 * Dateien mit rohen Blob-Schlüsseln hoch und legt dazu einen Datensatz ab:
 * Name, Aufnahmedaten und die Köpfe der Blobs samt Schlüsseln, hybrid
 * verschlüsselt mit dem öffentlichen Schlüssel des Tresors (ML-KEM-768 +
 * RSA-4096, in Rust wie DIS: `kamera_krypto.rs`) und vom Gerät unterschrieben
 * (ECDSA P-256 im Android Keystore). Eine App mit offenem Tresor macht daraus
 * einen normalen Eintrag (`eingangAnstossen`), sobald alle drei Blobs fertig
 * beim Server liegen: Schlüssel mit dem `userKey` wickeln, Eintrag speichern,
 * und erst wenn der beim Server angekommen ist, den Datensatz löschen.
 *
 * Schlüsselpaar und Geräte stehen als Systemeinträge (`SYSTEM_KATEGORIE`) im
 * Tresor, verschlüsselt wie jeder Eintrag. Das Gerät kennt bei gesperrtem
 * Tresor nur den öffentlichen Teil: es kann verschlüsseln, aber nichts lesen.
 * Einen Datensatz übernimmt nur, wer ihn mit der Unterschrift eines
 * eingetragenen Geräts vorfindet; den öffentlichen Schlüssel kann jeder
 * kennen, der dieses Telefon ausliest (AGENTS.md Punkt 41).
 */

import { base64ToBytes, utf8ToBytes } from '@msdis/shield/core'
import { generateHybridKeyPair, hybridDecrypt } from '@msdis/shield/post-quantum'
import { importEcdsaP256PublicKeySpki, verifyEcdsaP256 } from '@msdis/shield/signing'

import { SanitizedApiError } from '@/api/client'
import { blobLoeschen, blobStand, eingangListe, eingangLoeschen, type EingangDatensatz } from './tresorBlobApi'
import { blobSchluesselWickeln, istBlobKopf, type BlobKopf } from './tresorDatei'
import { einzeln, istQuelle, type DateiAngaben, type DateiQuelle } from './tresorDateien'
import { SYSTEM_KATEGORIE, type SicherungAngaben, type VaultItem } from './vaultEintrag'
import { getPendingQueue, getStoredBlobs, useVaultStore } from './vaultStore'

const FORMAT = 1
const DOMAENE = 'msm-tresor-eingang-v1'

/**
 * Wie lange ein Datensatz auf sein Gerät oder sein Schlüsselpaar wartet. Das
 * Telefon schreibt keinen Datensatz, bevor beide beim Server liegen
 * (`posteingangEinrichten`); fehlen sie danach noch, kommen sie nicht mehr,
 * und unbekannt heißt fremd (AGENTS.md Punkt 58).
 */
export const WARTEN_HOECHSTENS_MS = 7 * 24 * 60 * 60 * 1000

/** Was ein Gerät bei gesperrtem Tresor vom Schlüsselpaar kennt. */
export interface EingangOeffentlich {
  id: string
  pqPublicKey: string
  rsaPublicKey: string
}

/** Was in einem Datensatz steht. Die Blob-Köpfe tragen rohe Schlüssel. */
export interface EingangInhalt {
  name: string
  typ: string
  geaendert?: number
  aufgenommen?: number
  kamera?: string
  breite?: number
  hoehe?: number
  dauer?: number
  original: BlobKopf
  vorschau: BlobKopf
  miniatur: BlobKopf
  quelle: DateiQuelle
}

/** So liegt ein Datensatz beim Server. Nur `daten` ist verschlüsselt. */
interface Umschlag {
  v: typeof FORMAT
  /** Kennung des Schlüsselpaars. */
  schluessel: string
  /** Kennung des Geräts, das unterschrieben hat. */
  geraet: string
  daten: string
  /** r ‖ s in Base64. */
  signatur: string
}

type Posteingang = Extract<SicherungAngaben, { art: 'posteingang' }>
type Geraet = Extract<SicherungAngaben, { art: 'geraet' }>

function aad(bucket: string, eingangId: string): string {
  return `${DOMAENE}:${bucket}:${eingangId}`
}

/** Was unterschrieben wird: alles am Umschlag außer der Unterschrift, gebunden an Bucket und Kennung. */
function zuUnterschreiben(bucket: string, eingangId: string, u: Omit<Umschlag, 'signatur'>): Uint8Array {
  return utf8ToBytes([DOMAENE, String(u.v), bucket, eingangId, u.schluessel, u.geraet, u.daten].join('\n'))
}

/** Was beim Server über die Blobs eines Datensatzes steht. */
async function blobLage(bucket: string, id: string): Promise<'fertig' | 'offen' | 'weg'> {
  try {
    const stand = await blobStand(bucket, id)
    if (stand.state === 'fertig') return 'fertig'
    return stand.state === 'offen' ? 'offen' : 'weg'
  } catch (err) {
    if (err instanceof SanitizedApiError && err.status === 404) return 'weg'
    throw err
  }
}

function zahl(wert: unknown): boolean {
  return wert === undefined || (typeof wert === 'number' && Number.isFinite(wert))
}

function roherKopf(wert: unknown): wert is BlobKopf {
  return istBlobKopf(wert) && /^[0-9a-f]{64}$/.test(wert.schluessel)
}

function istInhalt(wert: unknown, geraet: string): wert is EingangInhalt {
  if (!wert || typeof wert !== 'object') return false
  const i = wert as Record<string, unknown>
  return (
    typeof i.name === 'string' &&
    i.name.length > 0 &&
    i.name.length <= 1024 &&
    typeof i.typ === 'string' &&
    i.typ.length <= 255 &&
    zahl(i.geaendert) &&
    zahl(i.aufgenommen) &&
    zahl(i.breite) &&
    zahl(i.hoehe) &&
    zahl(i.dauer) &&
    (i.kamera === undefined || (typeof i.kamera === 'string' && i.kamera.length <= 255)) &&
    roherKopf(i.original) &&
    roherKopf(i.vorschau) &&
    roherKopf(i.miniatur) &&
    istQuelle(i.quelle) &&
    // Ein Gerät spricht nur für seine eigenen Aufnahmen.
    i.quelle.geraet === geraet
  )
}

function istUmschlag(wert: unknown): wert is Umschlag {
  if (!wert || typeof wert !== 'object') return false
  const u = wert as Record<string, unknown>
  return (
    u.v === FORMAT &&
    typeof u.schluessel === 'string' &&
    typeof u.geraet === 'string' &&
    typeof u.daten === 'string' &&
    typeof u.signatur === 'string'
  )
}

export type Geoeffnet =
  | { ergebnis: 'ok'; inhalt: EingangInhalt }
  /** Gefälscht, fremd unterschrieben oder kaputt: wird nie gültig. */
  | { ergebnis: 'verwerfen' }
  /** Gerät oder Schlüssel noch unbekannt; der Abgleich kann hinterherhinken. */
  | { ergebnis: 'warten' }

export async function eingangOeffnen(
  text: string,
  bucket: string,
  eingangId: string,
  schluessel: Posteingang[],
  geraete: Geraet[],
): Promise<Geoeffnet> {
  let umschlag: unknown
  try {
    umschlag = JSON.parse(text)
  } catch {
    return { ergebnis: 'verwerfen' }
  }
  if (!istUmschlag(umschlag)) return { ergebnis: 'verwerfen' }
  const kandidaten = geraete.filter((g) => g.geraet === umschlag.geraet)
  if (kandidaten.length === 0) return { ergebnis: 'warten' }
  const signiert = zuUnterschreiben(bucket, eingangId, umschlag)
  let echt = false
  try {
    const signatur = base64ToBytes(umschlag.signatur)
    for (const g of kandidaten) {
      if (await verifyEcdsaP256(await importEcdsaP256PublicKeySpki(base64ToBytes(g.spki)), signatur, signiert)) echt = true
    }
  } catch {
    echt = false
  }
  if (!echt) return { ergebnis: 'verwerfen' }

  const paar = schluessel.find((s) => s.id === umschlag.schluessel)
  if (!paar) return { ergebnis: 'warten' }
  try {
    const inhalt: unknown = JSON.parse(await hybridDecrypt(umschlag.daten, paar.pqSecretKey, paar.rsaPrivateKey, aad(bucket, eingangId)))
    return istInhalt(inhalt, umschlag.geraet) ? { ergebnis: 'ok', inhalt } : { ergebnis: 'verwerfen' }
  } catch {
    return { ergebnis: 'verwerfen' }
  }
}

/**
 * Die Blobs eines Datensatzes, soweit sich sein Inhalt öffnen lässt. Die
 * Unterschrift zählt hier nicht: es geht nur darum, was beim Verwerfen
 * mitgeht. Ohne Schlüsselpaar bleiben die Blobs liegen, bis das Aufräumen
 * des Servers offene Uploads entfernt oder der Tresor zurückgesetzt wird.
 */
async function blobsVon(text: string, bucket: string, eingangId: string, schluessel: Posteingang[]): Promise<BlobKopf[]> {
  try {
    const umschlag: unknown = JSON.parse(text)
    if (!istUmschlag(umschlag)) return []
    const paar = schluessel.find((s) => s.id === umschlag.schluessel)
    if (!paar) return []
    const inhalt = JSON.parse(await hybridDecrypt(umschlag.daten, paar.pqSecretKey, paar.rsaPrivateKey, aad(bucket, eingangId))) as Record<string, unknown>
    return [inhalt?.original, inhalt?.vorschau, inhalt?.miniatur].filter(roherKopf)
  } catch {
    return []
  }
}

function sicherungen<A extends SicherungAngaben['art']>(items: VaultItem[], art: A): Extract<SicherungAngaben, { art: A }>[] {
  return items
    .filter((i) => i.category === SYSTEM_KATEGORIE && i.sicherung?.art === art && !i.trashedAt)
    .sort((a, b) => a.createdAt - b.createdAt || a.id.localeCompare(b.id))
    .map((i) => i.sicherung as Extract<SicherungAngaben, { art: A }>)
}

/** Ob der Server diese Einträge hat: im Cache und nicht mehr in der Warteschlange. */
function beimServer(bucket: string, ids: string[]): boolean {
  const wartend = new Set(getPendingQueue(bucket).map((e) => e.id))
  const da = new Set(getStoredBlobs(bucket).filter((b) => !b.is_deleted).map((b) => b.id))
  return ids.every((id) => da.has(id) && !wartend.has(id))
}

/** Gleicht ab und wartet, bis kein Abgleich mehr läuft. */
async function abgleichen(): Promise<void> {
  await useVaultStore.getState().syncWithServer()
  if (useVaultStore.getState().syncStatus !== 'syncing') return
  await new Promise<void>((fertig) => {
    const ende = useVaultStore.subscribe((s) => {
      if (s.syncStatus !== 'syncing') {
        ende()
        fertig()
      }
    })
  })
}

/**
 * Bei offenem Tresor: sorgt für Schlüsselpaar und Geräteeintrag und gibt den
 * öffentlichen Teil zurück, sobald beides beim Server liegt. Vorher `null`:
 * ein Datensatz zu einem Schlüssel, den der Server nie bekam, ließe sich auf
 * keinem anderen Gerät öffnen.
 *
 * Das Paar entsteht einmal je Tresor; haben zwei Geräte zugleich eines
 * erzeugt, bleiben beide gültig (die Übernahme sucht nach Kennung).
 */
export async function posteingangEinrichten(
  bucket: string,
  geraet: string,
  oeffentlicherSchluessel: (geraet: string) => Promise<string>,
): Promise<EingangOeffentlich | null> {
  const store = () => useVaultStore.getState()
  if (store().bucketId !== bucket || !store().userKey) return null

  let paar = sicherungen(store().items, 'posteingang')[0]
  if (!paar) {
    const neu = await generateHybridKeyPair()
    paar = { art: 'posteingang', id: crypto.randomUUID(), ...neu }
    await store().saveItem({ service: 'Posteingang', category: SYSTEM_KATEGORIE, sicherung: paar })
  }
  const spki = await oeffentlicherSchluessel(geraet)
  if (!sicherungen(store().items, 'geraet').some((g) => g.geraet === geraet && g.spki === spki)) {
    await store().saveItem({ service: 'Sicherungsgerät', category: SYSTEM_KATEGORIE, sicherung: { art: 'geraet', geraet, spki } })
  }

  const ids = () =>
    store()
      .items.filter((i) => i.category === SYSTEM_KATEGORIE && ((i.sicherung?.art === 'posteingang' && i.sicherung.id === paar.id) || (i.sicherung?.art === 'geraet' && i.sicherung.geraet === geraet)))
      .map((i) => i.id)
  for (let versuch = 0; versuch < 3 && !beimServer(bucket, ids()); versuch++) await abgleichen()
  if (store().bucketId !== bucket || !beimServer(bucket, ids())) return null
  return { id: paar.id, pqPublicKey: paar.pqPublicKey, rsaPublicKey: paar.rsaPublicKey }
}

function gleicheQuelle(a: DateiQuelle | undefined, b: DateiQuelle): boolean {
  return !!a && a.geraet === b.geraet && a.medienId === b.medienId && a.sha256 === b.sha256
}

async function einenUebernehmen(
  bucket: string,
  userKey: CryptoKey,
  satz: EingangDatensatz,
  schluessel: Posteingang[],
  geraete: Geraet[],
): Promise<void> {
  const store = () => useVaultStore.getState()
  const imCache = getStoredBlobs(bucket).find((b) => b.id === satz.id)
  if (imCache?.is_deleted) {
    // Übernommen und schon wieder gelöscht: die Blobs nahm der Tombstone mit.
    await eingangLoeschen(bucket, satz.id)
    return
  }
  if (store().items.some((i) => i.id === satz.id)) {
    // Schon übernommen. Weg ist der Datensatz erst, wenn der Eintrag beim Server liegt.
    if (beimServer(bucket, [satz.id])) await eingangLoeschen(bucket, satz.id)
    return
  }

  const geoeffnet = await eingangOeffnen(satz.ciphertext, bucket, satz.id, schluessel, geraete)
  if (geoeffnet.ergebnis === 'warten') {
    // Ohne Frist läge der Datensatz eines verschwundenen Geräts für immer im
    // Posteingang, und seine Blobs belegten den Speicher.
    if (Date.now() - Date.parse(satz.created_at) > WARTEN_HOECHSTENS_MS) {
      for (const k of await blobsVon(satz.ciphertext, bucket, satz.id, schluessel)) await blobLoeschen(bucket, k.id, k.loeschen).catch(() => {})
      await eingangLoeschen(bucket, satz.id)
    }
    return
  }
  if (geoeffnet.ergebnis === 'verwerfen') {
    await eingangLoeschen(bucket, satz.id)
    return
  }
  const { inhalt } = geoeffnet
  const koepfe = [inhalt.original, inhalt.vorschau, inhalt.miniatur]

  // Das Telefon lädt noch hoch: erst übernehmen, wenn alle drei Blobs fertig
  // sind. Das Original meldet es erst fertig, wenn es noch dieselben Bytes hat.
  // Ist einer weg (abgebrochen und aufgeräumt), fängt das Telefon neu an.
  const lagen = await Promise.all(koepfe.map((k) => blobLage(bucket, k.id)))
  if (lagen.includes('weg')) {
    for (const k of koepfe) await blobLoeschen(bucket, k.id, k.loeschen).catch(() => {})
    await eingangLoeschen(bucket, satz.id)
    return
  }
  if (lagen.some((l) => l !== 'fertig')) return

  // Dieselbe Aufnahme liegt schon im Tresor (bei gesperrtem Tresor kennt das
  // Gerät seine Einträge nicht). Die zweite Kopie belegte nur Speicher.
  if (store().items.some((i) => gleicheQuelle(i.datei?.quelle, inhalt.quelle))) {
    for (const k of koepfe) await blobLoeschen(bucket, k.id, k.loeschen).catch(() => {})
    await eingangLoeschen(bucket, satz.id)
    return
  }

  const [original, vorschau, miniatur] = await Promise.all(koepfe.map((k) => blobSchluesselWickeln(k, userKey, satz.id)))
  const datei: DateiAngaben = {
    typ: inhalt.typ,
    geaendert: inhalt.geaendert,
    aufgenommen: inhalt.aufgenommen,
    kamera: inhalt.kamera,
    breite: inhalt.breite,
    hoehe: inhalt.hoehe,
    dauer: inhalt.dauer,
    original,
    vorschau,
    miniatur,
    quelle: inhalt.quelle,
  }
  if (store().userKey !== userKey || store().bucketId !== bucket) return
  await store().saveItem({ id: satz.id, service: inhalt.name, category: 'datei', datei })
}

async function uebernehmen(bucket: string): Promise<void> {
  const offen = () => {
    const s = useVaultStore.getState()
    return s.isUnlocked && s.bucketId === bucket ? s.userKey : null
  }
  if (!offen()) return
  const { items } = useVaultStore.getState()
  const schluessel = sicherungen(items, 'posteingang')
  const geraete = sicherungen(items, 'geraet')
  let nach: string | undefined
  do {
    const seite = await eingangListe(bucket, nach)
    for (const satz of seite.eintraege) {
      const userKey = offen()
      if (!userKey) return
      await einenUebernehmen(bucket, userKey, satz, schluessel, geraete)
    }
    nach = seite.weiter ?? undefined
  } while (nach)
}

/** Ein Lauf zur Zeit (Punkt 64). Fehler warten auf den nächsten Abgleich. */
export const eingangAnstossen = einzeln((bucket) => uebernehmen(bucket).catch(() => {}))

/**
 * Übernimmt nach jedem Abgleich bei offenem Tresor, auf jedem Gerät: auch der
 * Rechner holt ab, was das Telefon bei gesperrtem Tresor gesichert hat. Der
 * Abgleich danach löscht die Datensätze, deren Einträge angekommen sind.
 */
export function eingangBeobachten(): () => void {
  return useVaultStore.subscribe((s, vorher) => {
    if (s.isUnlocked && s.bucketId && vorher.syncStatus === 'syncing' && s.syncStatus === 'synced') {
      void eingangAnstossen(s.bucketId)
    }
  })
}
