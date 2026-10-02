/**
 * Ein Eintrag des Tresors, wie er entschlüsselt aussieht: Typen, Arten, das
 * Lesen und Schreiben des Umschlags und das Zusammenführen zweier Fassungen.
 * Ohne Store und ohne Netz; der Store (`vaultStore.ts`) verschlüsselt,
 * speichert und gleicht ab.
 */
import { dateiBlobs, istDateiAngaben, VERSIONEN, type DateiAngaben } from './tresorDateien'

/**
 * Die Arten von Einträgen, die diese Fassung der App anzeigen und bearbeiten
 * kann. Ein Eintrag anderer Art stammt von einer neueren App: er bleibt im
 * Speicher, damit der Sync ihn nicht verliert, erscheint aber nirgends und
 * wird nicht geschrieben.
 */
export const BEKANNTE_KATEGORIEN = ['login', 'authenticator', 'secure_note', 'datei', 'ordner', 'album'] as const
export type VaultKategorie = (typeof BEKANNTE_KATEGORIEN)[number]

export function istBekannteKategorie(kategorie: string | undefined): boolean {
  return (BEKANNTE_KATEGORIEN as readonly string[]).includes(kategorie ?? 'login')
}

/** Was in der Passwortliste steht. Dateien und Ordner haben ihren eigenen Bereich. */
export const PASSWORT_KATEGORIEN = ['login', 'authenticator', 'secure_note'] as const

export function istPasswortKategorie(kategorie: string | undefined): boolean {
  return (PASSWORT_KATEGORIEN as readonly string[]).includes(kategorie ?? 'login')
}

/** Aufbau der Nutzlast. Eine neuere App erkennt daran, was eine ältere geschrieben hat. */
export const VAULT_EINTRAG_FORMAT = 1

/** So lange liegt ein Eintrag im Papierkorb, bevor er endgültig gelöscht wird. */
export const PAPIERKORB_TAGE = 30

export interface VaultItem {
  id: string
  service: string
  username: string
  password: string
  url?: string
  notes?: string
  totpSecret?: string
  category?: string
  isFavorite?: boolean
  lastUsedAt?: number
  linkedServiceId?: string
  /** Gesetzt, solange der Eintrag im Archiv liegt. */
  archivedAt?: number
  /** Gesetzt, solange der Eintrag im Papierkorb liegt. */
  trashedAt?: number
  /** Nur bei Dateien: Typ und die Köpfe ihrer Blobs. Der Name steht in `service`. */
  datei?: DateiAngaben
  /** Dateien und Ordner: der Ordner, in dem sie liegen. Fehlt in der obersten Ebene. */
  ordner?: string
  /** Nur bei Alben: die Einträge darin, in dieser Reihenfolge. Die Dateien selbst ändern sich nicht. */
  album?: AlbumAngaben
  createdAt: number
  updatedAt: number
  revision: number
  /**
   * Felder der Nutzlast, die diese Fassung nicht kennt. Sie gehen beim
   * Speichern unverändert mit. Bis 09/2026 baute jede Speicherung die
   * Nutzlast aus einer festen Feldliste neu, und eine ältere App löschte
   * damit still, was eine neuere hineingeschrieben hatte.
   */
  extra?: Record<string, unknown>
}

/**
 * Die Felder, die ein Gerät an einem Eintrag ändern kann: alles außer
 * Kennung, Revision, Zeitstempeln, Inhalt (`datei`) und `extra`. Eine Liste
 * für Lesen, Schreiben und das Zusammenführen im Konflikt. Bis 02.10.2026
 * stand sie an drei Stellen, und ein dort vergessenes Feld ging im Konflikt
 * still verloren. Als `Record` über die Schlüssel von `VaultItem` meldet tsc
 * jedes fehlende oder überzählige Feld.
 */
const AENDERBAR: Record<Exclude<keyof VaultItem, 'id' | 'revision' | 'createdAt' | 'updatedAt' | 'datei' | 'extra'>, true> = {
  service: true,
  username: true,
  password: true,
  url: true,
  notes: true,
  totpSecret: true,
  category: true,
  isFavorite: true,
  lastUsedAt: true,
  linkedServiceId: true,
  archivedAt: true,
  trashedAt: true,
  ordner: true,
  album: true,
}
const AENDERBARE_FELDER = Object.keys(AENDERBAR) as (keyof typeof AENDERBAR)[]

const BEKANNTE_FELDER = new Set<string>([...AENDERBARE_FELDER, 'datei', 'createdAt', 'updatedAt', 'format'])

export interface AlbumAngaben {
  eintraege: string[]
}

/**
 * Ein Umschlag darf höchstens 1 MiB Chiffrat haben. 10.000 Kennungen sind
 * rund 400 KB und lassen Luft.
 */
export const ALBUM_HOECHSTENS = 10_000

function istAlbumAngaben(wert: unknown): wert is AlbumAngaben {
  if (!wert || typeof wert !== 'object') return false
  const eintraege = (wert as { eintraege?: unknown }).eintraege
  return (
    Array.isArray(eintraege) &&
    eintraege.length <= ALBUM_HOECHSTENS &&
    eintraege.every((id) => typeof id === 'string' && id.length > 0 && id.length <= 64)
  )
}

function zahlOderNichts(wert: unknown): number | undefined {
  return typeof wert === 'number' && Number.isFinite(wert) ? wert : undefined
}

/** Liest einen entschlüsselten Umschlag. Unbekannte Felder landen in `extra`. */
export function itemAusUmschlag(id: string, revision: number, payload: Record<string, unknown>): VaultItem {
  const extra: Record<string, unknown> = {}
  for (const [schluessel, wert] of Object.entries(payload)) {
    if (!BEKANNTE_FELDER.has(schluessel)) extra[schluessel] = wert
  }
  // Angaben, die diese Fassung nicht lesen kann, gehen unverändert mit, statt
  // beim nächsten Speichern zu verschwinden.
  const datei = istDateiAngaben(payload.datei) ? payload.datei : undefined
  if (payload.datei !== undefined && !datei) extra.datei = payload.datei
  const album = istAlbumAngaben(payload.album) ? payload.album : undefined
  if (payload.album !== undefined && !album) extra.album = payload.album
  return {
    id,
    service: String(payload.service || 'Unbekannt'),
    username: String(payload.username || ''),
    password: String(payload.password || ''),
    url: payload.url ? String(payload.url) : undefined,
    notes: payload.notes ? String(payload.notes) : undefined,
    totpSecret: payload.totpSecret ? String(payload.totpSecret) : undefined,
    category: typeof payload.category === 'string' && payload.category ? payload.category : 'login',
    isFavorite: !!payload.isFavorite,
    lastUsedAt: zahlOderNichts(payload.lastUsedAt),
    linkedServiceId: payload.linkedServiceId ? String(payload.linkedServiceId) : undefined,
    archivedAt: zahlOderNichts(payload.archivedAt),
    trashedAt: zahlOderNichts(payload.trashedAt),
    datei,
    ordner: typeof payload.ordner === 'string' && payload.ordner ? payload.ordner : undefined,
    album,
    createdAt: Number(payload.createdAt || Date.now()),
    updatedAt: Number(payload.updatedAt || Date.now()),
    revision,
    extra: Object.keys(extra).length > 0 ? extra : undefined,
  } satisfies Record<keyof VaultItem, unknown>
}

/**
 * Die Nutzlast eines Eintrags: erst die unbekannten Felder, darüber die
 * bekannten. Nur gesetzte Felder: `datei` oder `album`, die diese Fassung
 * nicht lesen konnte, liegen in `extra` und gehen sonst verloren.
 */
export function umschlagAusItem(item: VaultItem): Record<string, unknown> {
  const nutzlast: Record<string, unknown> = { ...item.extra, format: VAULT_EINTRAG_FORMAT }
  for (const feld of [...AENDERBARE_FELDER, 'datei', 'createdAt', 'updatedAt'] as const) {
    if (item[feld] !== undefined) nutzlast[feld] = item[feld]
  }
  return nutzlast
}

/**
 * Ob eine App vor 09/2026 diesen Tresor beschädigen würde. Sie kennt weder
 * Papierkorb noch Archiv noch fremde Felder und schriebe jeden Eintrag ohne
 * sie zurück. Trägt der Tresor so etwas, stuft der Sync den Bucket hoch, und
 * der Server nimmt von alten Apps nichts mehr an.
 */
export function brauchtNeueApp(items: VaultItem[]): boolean {
  return items.some(
    (i) => i.trashedAt !== undefined || i.archivedAt !== undefined || i.extra !== undefined || !istPasswortKategorie(i.category),
  )
}

/**
 * Nutzlast eines kryptographisch belegten Tombstones.
 *
 * `is_deleted` ist ein unverschlüsseltes Feld neben dem Umschlag — der Server
 * setzt es, der Client befolgte es. Damit konnte jeder, der die Antwort formt
 * (der Betreiber, ein übernommener Bucket, ein MitM mit eigenem Zertifikat),
 * einen Tresor leerräumen: der Client warf die betroffenen Blobs aus seinem
 * lokalen Cache, und der ist bei einem Zero-Knowledge-Tresor die einzige
 * lesbare Kopie. Verschwiegen werden konnte nichts, zerstört alles.
 *
 * Seit dem Audit vom 22.09.2026 trägt jede Löschung ihren Beweis im Umschlag:
 * nur wer den UserKey hat, kann einen Tombstone erzeugen, der an dieselbe
 * `entryId` gebunden ist.
 */
export const VAULT_TOMBSTONE_MARKER = 'mss-vault-tombstone-v1'

/**
 * Setzt die Änderung dieses Geräts (`eigen`, ausgehend von `basis`) auf die
 * Fassung des Servers. Jedes Feld, das dieses Gerät geändert hat, gilt; hat es
 * den Inhalt ersetzt, wird der des Servers zur früheren Fassung. `weg` sind die
 * Blobs, auf die danach keine Fassung mehr zeigt.
 */
export function fassungenZusammenfuehren(
  basis: VaultItem,
  eigen: VaultItem,
  server: VaultItem,
): { item: VaultItem; weg: { id: string; loeschen: string }[] } {
  const item: VaultItem = { ...server, datei: server.datei ?? eigen.datei }
  for (const feld of AENDERBARE_FELDER) {
    if (JSON.stringify(eigen[feld]) !== JSON.stringify(basis[feld])) Object.assign(item, { [feld]: eigen[feld] })
  }
  const neuerInhalt = eigen.datei?.original.id
  if (eigen.datei && server.datei && neuerInhalt !== basis.datei?.original.id && neuerInhalt !== server.datei.original.id) {
    const { frueher: eigeneFrueher = [], ...kopf } = eigen.datei
    const { frueher: serverFrueher = [], ...serverKopf } = server.datei
    const gesehen = new Set([kopf.original.id])
    const versionen = [
      { typ: serverKopf.typ, ersetzt: Date.now(), original: serverKopf.original, vorschau: serverKopf.vorschau, miniatur: serverKopf.miniatur },
      ...serverFrueher,
      ...eigeneFrueher,
    ]
      .filter((v) => !gesehen.has(v.original.id) && gesehen.add(v.original.id))
      .sort((a, b) => b.ersetzt - a.ersetzt)
    item.datei = { ...serverKopf, ...kopf, frueher: versionen.slice(0, VERSIONEN) }
  }
  const bleibt = new Set(item.datei ? dateiBlobs(item.datei).map((k) => k.id) : [])
  const weg = new Map<string, { id: string; loeschen: string }>()
  for (const fassung of [basis, eigen, server]) {
    for (const k of fassung.datei ? dateiBlobs(fassung.datei) : []) {
      if (!bleibt.has(k.id)) weg.set(k.id, { id: k.id, loeschen: k.loeschen })
    }
  }
  return { item, weg: [...weg.values()] }
}
