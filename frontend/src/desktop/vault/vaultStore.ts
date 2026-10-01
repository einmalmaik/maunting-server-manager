import { create } from 'zustand'
import i18n from '@/i18n'
import { api, apiUrl } from '@/api/client'
import {
  type AutoSperrQuelle,
  fristAbgelaufen,
  liesFensterwechsel,
  liesSperrfrist,
  schreibeFensterwechsel,
  schreibeSperrfrist,
} from '@/services/autoSperre'
import {
  base64ToBytes,
  decryptVaultEntry,
  deriveVaultKeys,
  encryptVaultEntry,
  generateSecurePassword,
  isBiometricsAvailable,
  promptBiometricVerification,
} from './vaultCrypto'
import {
  FACH_TRESOR,
  biometrieEntsperren,
  biometrieLoeschen,
  biometrieSpeichern,
  biometrieSpeicherFragtSelbst,
} from '../tauri'
import { meldeErrungenschaft } from '@/lib/errungenschaft'
import {
  ablageLaden,
  ablageLoeschen,
  ablageUmziehen,
  blobsLesen,
  blobsSchreiben,
  revisionLesen,
  revisionSchreiben,
  warteschlangeLesen,
  warteschlangeSchreiben,
  type StoredEncryptedEntry,
} from './tresorAblage'
import {
  angeheftet,
  ansichtenSchliessen,
  blobLesen,
  dateiBlobs,
  offlineAnheften,
  offlineLoesen,
  dateiVorbereiten,
  istDateiAngaben,
  loeschungenAbarbeiten,
  uploadsFortsetzen,
  vorbereitungAbschliessen,
  VERSIONEN,
  type DateiAngaben,
  type DateiVersion,
} from './tresorDateien'
import { bildAngaben } from './tresorBilder'
import { bucketMelderSetzen } from './tresorBlobApi'

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
const PAPIERKORB_MS = PAPIERKORB_TAGE * 24 * 60 * 60 * 1000

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

const BEKANNTE_FELDER = new Set([
  'service',
  'username',
  'password',
  'url',
  'notes',
  'totpSecret',
  'category',
  'isFavorite',
  'lastUsedAt',
  'linkedServiceId',
  'archivedAt',
  'trashedAt',
  'datei',
  'ordner',
  'album',
  'createdAt',
  'updatedAt',
  'format',
])

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
  }
}

/** Die Nutzlast eines Eintrags: erst die unbekannten Felder, darüber die bekannten. */
export function umschlagAusItem(item: VaultItem): Record<string, unknown> {
  return {
    ...item.extra,
    format: VAULT_EINTRAG_FORMAT,
    service: item.service,
    username: item.username,
    password: item.password,
    url: item.url,
    notes: item.notes,
    totpSecret: item.totpSecret,
    category: item.category,
    isFavorite: item.isFavorite,
    lastUsedAt: item.lastUsedAt,
    linkedServiceId: item.linkedServiceId,
    archivedAt: item.archivedAt,
    trashedAt: item.trashedAt,
    ...(item.datei ? { datei: item.datei } : {}),
    ordner: item.ordner,
    ...(item.album ? { album: item.album } : {}),
    createdAt: item.createdAt,
    updatedAt: item.updatedAt,
  }
}

interface VaultSyncMutation {
  id: string
  ciphertext: string
  revision: number
  is_deleted: boolean
  expected_revision?: number
}

interface VaultSyncPayload {
  bucket_id: string
  since_revision: number
  mutations: VaultSyncMutation[]
  client_format?: number
  min_client_format?: number
}

interface VaultSyncResponse {
  server_revision: number
  entries: {
    id: string
    ciphertext: string
    revision: number
    is_deleted: boolean
    updated_at: string
  }[]
  /**
   * Mutationen, die der Server nicht geschrieben hat, weil der Eintrag dort
   * nicht mehr auf `expected_revision` stand. Ältere Server kennen das Feld nicht.
   */
  conflicts?: string[]
  /** Es liegen weitere Einträge bereit; der Sync holt sie in der nächsten Runde. */
  has_more?: boolean
}

export interface VaultBlindSyncPayload {
  bucket_id: string
  auth_token: string
  since_revision: number
  mutations: VaultSyncMutation[]
  client_format?: number
  min_client_format?: number
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
 * Obergrenzen eines Sync-Aufrufs — deckungsgleich mit `backend/schemas/vault.py`
 * (`max_length=100`, `MAX_MUTATION_PAYLOAD_BYTES`). Bis 09/2026 schickte der
 * Client die ganze Warteschlange auf einmal: ab der 101. Änderung lehnte der
 * Server jeden Sync mit 422 ab, und die Warteschlange wurde nie wieder leer.
 */
const SYNC_MAX_MUTATIONEN = 100
const SYNC_MAX_BYTES = 8 * 1024 * 1024
/** Mehr Runden als das (5.000 Änderungen) übernimmt der nächste Anstoß. */
const SYNC_MAX_RUNDEN = 50

/** Der vordere Teil der Warteschlange, der in einen Sync-Aufruf passt. */
export function naechstesSyncPaket(queue: StoredEncryptedEntry[]): StoredEncryptedEntry[] {
  const paket: StoredEncryptedEntry[] = []
  let bytes = 0
  for (const m of queue) {
    if (paket.length >= SYNC_MAX_MUTATIONEN) break
    if (paket.length > 0 && bytes + m.ciphertext.length > SYNC_MAX_BYTES) break
    paket.push(m)
    bytes += m.ciphertext.length
  }
  return paket
}

/**
 * Der Stand eines Umschlags: sein `updatedAt`, bei Tombstones aus der Zeit vor
 * 09/2026 das `deletedAt`.
 *
 * Die `revision` neben dem Umschlag vergibt der Server, und er kann sie frei
 * wählen: ein alter, gültig verschlüsselter Eintrag mit Revision 999 ging am
 * Rücksprung-Schutz vorbei, der nur die Revision verglich. Der Stand steht
 * **im** Umschlag — ihn kann nur ändern, wer den UserKey hat.
 */
function standAus(payload: Record<string, unknown> | null | undefined): number {
  const wert = payload?.updatedAt ?? payload?.deletedAt
  return typeof wert === 'number' && Number.isFinite(wert) ? wert : 0
}

/**
 * Der Stand einer neuen Fassung: die Uhrzeit, aber nie kleiner als der
 * bisherige Stand plus eins. Ein Gerät mit nachgehender Uhr schriebe sonst
 * Fassungen, die andere Geräte als Rücksprung verwerfen.
 */
function naechsterStand(bisher: number | undefined): number {
  return Math.max(Date.now(), (bisher ?? 0) + 1)
}

type SyncBefund =
  | { art: 'canary'; ciphertext: string }
  | { art: 'grab'; entry: VaultSyncResponse['entries'][number]; stand: number }
  | { art: 'eintrag'; entry: VaultSyncResponse['entries'][number]; stand: number; item: VaultItem }

/**
 * Entschlüsselt und prüft eine Sync-Antwort, ohne etwas zu schreiben.
 *
 * Das Schreiben übernimmt der Aufrufer danach in einem Zug ohne `await`. Hier
 * wird gewartet, und währenddessen kann der Benutzer speichern oder sperren —
 * wer hier schon schriebe, überschriebe dessen Stand mit einer Momentaufnahme.
 *
 * `vorab` liefert den Stand der bekannten Blobs, die noch keinen `stand`
 * mitführen; neue Blobs tragen ihn immer.
 */
async function pruefeSyncAntwort(
  entries: VaultSyncResponse['entries'],
  userKey: CryptoKey,
  bekannteBlobs: StoredEncryptedEntry[],
): Promise<{ befunde: SyncBefund[]; vorab: Map<string, number> }> {
  const befunde: SyncBefund[] = []
  const vorab = new Map<string, number>()

  for (const entry of entries) {
    if (entry.id === 'vault-canary') {
      // Ein Canary, der sich nicht mit dem eigenen Schlüssel öffnen lässt,
      // wird nicht übernommen. Sonst genügte ein ausgetauschter Prüfblock,
      // um den Besitzer beim nächsten Entsperren mit „falsches
      // Master-Passwort" aus seinem eigenen Tresor auszusperren.
      try {
        await decryptVaultEntry(entry.ciphertext, userKey, 'vault-canary')
      } catch {
        console.warn('Tresor-Sync: fremder Canary verworfen.')
        continue
      }
      befunde.push({ art: 'canary', ciphertext: entry.ciphertext })
      continue
    }

    const bekannt = bekannteBlobs.find((b) => b.id === entry.id)
    if (bekannt && bekannt.stand === undefined && !vorab.has(entry.id)) {
      let stand = 0
      try {
        stand = standAus(await decryptVaultEntry(bekannt.ciphertext, userKey, bekannt.id))
      } catch {}
      vorab.set(entry.id, stand)
    }

    let dec: Record<string, unknown>
    try {
      dec = await decryptVaultEntry(entry.ciphertext, userKey, entry.id)
    } catch {
      if (entry.is_deleted) {
        // Gelöscht wird nur auf Vorlage eines belegten Tombstones. Ein
        // `is_deleted` ohne passenden Umschlag stammt nicht vom Besitzer des
        // Schlüssels und bleibt folgenlos.
        //
        // Der Preis, offen benannt: Tombstones aus der Zeit vor dem Audit
        // tragen einen leeren Ciphertext. Eine solche Alt-Löschung erreicht
        // ein frisch eingerichtetes Zweitgerät nicht mehr — der Eintrag
        // steht dort und kann erneut gelöscht werden. Ein stehengebliebener
        // Eintrag ist reparierbar, ein leergeräumter Tresor nicht.
        console.warn(`Tresor-Sync: unbelegte Löschung für ${entry.id} ignoriert.`)
      }
      continue
    }

    // Ein Umschlag mit Tombstone-Inhalt ist nie ein Eintrag — auch dann
    // nicht, wenn die Antwort ihn als lebendig ausgibt.
    if (dec?.[VAULT_TOMBSTONE_MARKER] === true) {
      befunde.push({ art: 'grab', entry, stand: standAus(dec) })
      continue
    }
    if (entry.is_deleted) {
      console.warn(`Tresor-Sync: unbelegte Löschung für ${entry.id} ignoriert.`)
      continue
    }

    befunde.push({
      art: 'eintrag',
      entry,
      stand: standAus(dec),
      item: itemAusUmschlag(entry.id, entry.revision, dec),
    })
  }

  return { befunde, vorab }
}

/**
 * Führt einen anonymen Tresor-Sync über credentials: 'omit' ohne Session-Cookies oder User-Header durch.
 */
export async function blindVaultSync(
  payload: VaultBlindSyncPayload,
): Promise<VaultSyncResponse> {
  const url = apiUrl('/api/vault/blind-sync')
  const res = await fetch(url, {
    method: 'POST',
    credentials: 'omit',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })

  if (!res.ok) {
    throw new Error(`Blind vault sync failed with status ${res.status}`)
  }

  return (await res.json()) as VaultSyncResponse
}

/**
 * Fragt den Server, ob Bucket und Besitznachweis zusammenpassen. Legt anders
 * als `blindVaultSync` nichts an. `false` heißt: dieses Passwort gehört zu
 * keinem Tresor. Wirft, wenn der Server nicht antwortet.
 */
export async function blindVaultCheck(bucketId: string, authToken: string): Promise<boolean> {
  const res = await fetch(apiUrl('/api/vault/blind-check'), {
    method: 'POST',
    credentials: 'omit',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ bucket_id: bucketId, auth_token: authToken }),
  })
  if (res.status === 401) return false
  if (!res.ok) {
    throw new Error(`Blind vault check failed with status ${res.status}`)
  }
  return true
}

const VAULT_SALT_KEY = 'mss:vault_salt'
const VAULT_SETUP_DONE_KEY = 'mss:vault_setup_done'
const VAULT_CANARY_KEY = 'mss:vault_canary'
const VAULT_CANARY_PREFIX = 'mss:vault_canary_'
/**
 * Der Namensraum der Sperrfrist-Einstellungen. Die daraus gebauten Schlüssel
 * heißen weiter `mss:vault_autolock_minutes` und `mss:vault_lock_on_blur` —
 * ein umbenannter Schlüssel wäre für jede bestehende Installation eine
 * zurückgesetzte Sicherheitseinstellung.
 */
const VAULT_SPERR_PRAEFIX = 'mss:vault'
const VAULT_BIOMETRICS_ENABLED_KEY = 'mss:vault_biometrics_enabled'
const VAULT_SERVER_BUCKET_KEY = 'mss:vault_server_bucket'
const VAULT_BIO_MIGRATED_KEY = 'mss:vault_bio_migrated_v2'

/**
 * Aktive Migration & Bereinigung vulnerabler Altdaten (SEC-CRIT-01):
 * Entfernt bedingungslos unsichere Schlüssel und Salts aus dem localStorage
 * und reinigt alte Schlüssel aus dem OS-Keyring.
 */
export async function cleanseVulnerableBiometricData(): Promise<void> {
  if (typeof localStorage !== 'undefined') {
    localStorage.removeItem('mss:vault_bio_wrapped')
    localStorage.removeItem('mss:vault_device_salt')
  }
  try {
    await biometrieLoeschen(FACH_TRESOR)
  } catch {}
}

/**
 * Führt die aktive Migration bei Store-Initialisierung durch (SEC-CRIT-01):
 * - Entfernt bedingungslos veraltete mss:vault_bio_wrapped und mss:vault_device_salt
 * - Bereinigt bestehende vulnerable Keyring-Schlüssel via biometrieLoeschen()
 * - Setzt biometrics_enabled zurück falls Altdaten vorhanden waren
 */
export function runBiometricsMigration(): void {
  if (typeof localStorage === 'undefined') {
    void cleanseVulnerableBiometricData()
    return
  }

  const hadLegacyWrapped = !!localStorage.getItem('mss:vault_bio_wrapped')
  const hadLegacySalt = !!localStorage.getItem('mss:vault_device_salt')
  const notMigrated = !localStorage.getItem(VAULT_BIO_MIGRATED_KEY)

  // Bedingungsloses Entfernen vulnerabler Schlüssel aus dem localStorage
  localStorage.removeItem('mss:vault_bio_wrapped')
  localStorage.removeItem('mss:vault_device_salt')

  if (notMigrated || hadLegacyWrapped || hadLegacySalt) {
    void cleanseVulnerableBiometricData()
    localStorage.removeItem(VAULT_BIOMETRICS_ENABLED_KEY)
    localStorage.setItem(VAULT_BIO_MIGRATED_KEY, 'true')
  }
}

// Aktive Migration/Bereinigung bei Modul-Initialisierung (SEC-CRIT-01)
runBiometricsMigration()

export function getLocalVaultSalt(): Uint8Array | null {
  const existing = typeof localStorage !== 'undefined' ? localStorage.getItem(VAULT_SALT_KEY) : null
  if (!existing) return null

  if (/^[0-9a-fA-F]+$/.test(existing) && existing.length % 2 === 0) {
    const bytes = new Uint8Array(existing.length / 2)
    for (let i = 0; i < bytes.length; i++) {
      bytes[i] = parseInt(existing.substr(i * 2, 2), 16)
    }
    return bytes
  }

  try {
    return base64ToBytes(existing)
  } catch {
    return null
  }
}

export function getStoredBlobs(bucketId: string): StoredEncryptedEntry[] {
  return blobsLesen(bucketId)
}

export function getPendingQueue(bucketId: string): StoredEncryptedEntry[] {
  return warteschlangeLesen(bucketId)
}

export function getOrCreateVaultSalt(): Uint8Array {
  const local = getLocalVaultSalt()
  if (local) return local

  const newSalt = new Uint8Array(32)
  window.crypto.getRandomValues(newSalt)
  const hex = Array.from(newSalt)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(VAULT_SALT_KEY, hex)
  }
  return newSalt
}

export type SyncStatus = 'synced' | 'syncing' | 'offline' | 'error'

interface VaultState {
  isInitialized: boolean
  isUnlocked: boolean
  isUnlocking: boolean
  unlockError: string | null
  failedUnlockAttempts: number
  lockedUntilMs: number
  userKey: CryptoKey | null
  bucketId: string | null
  bucketAuthToken: string | null
  items: VaultItem[]
  selectedItemId: string | null
  searchQuery: string
  syncStatus: SyncStatus
  lastSyncTime: number | null

  // Auto-Lock & Biometrie
  autoLockMinutes: number
  lockOnWindowBlur: boolean
  isBiometricsSupported: boolean
  isBiometricsEnabled: boolean
  lastActivityTime: number

  // Aktionen
  fetchVaultSalt: () => Promise<string | null>
  initializeVault: (masterPassword: string) => Promise<boolean>
  unlock: (masterPassword: string) => Promise<boolean>
  unlockWithBiometrics: () => Promise<boolean>
  enableBiometrics: (masterPassword: string) => Promise<boolean>
  disableBiometrics: () => Promise<void>
  checkBiometricsSupport: () => Promise<boolean>
  setAutoLockMinutes: (minutes: number) => void
  setLockOnWindowBlur: (enabled: boolean) => void
  recordActivity: () => void
  checkAutoLock: () => boolean
  lock: () => void
  resetLocalVaultState: () => void
  /**
   * Tresor zurücksetzen, wenn das Master-Passwort vergessen ist: der Server
   * löst Salz und Hinweis vom Konto, danach verschwindet die lokale Kopie.
   * `nachweis` ist, was das Konto hat: Passwort oder zweiter Faktor.
   */
  resetVault: (nachweis: Record<string, unknown>, bestaetigung: string) => Promise<void>
  setSearchQuery: (q: string) => void
  setSelectedItemId: (id: string | null) => void
  createQuickPasswordEntry: (serviceName?: string) => Promise<VaultItem>
  saveItem: (item: Partial<VaultItem> & { service: string }, optionen?: { loeschBlobs?: { id: string; loeschen: string }[] }) => Promise<void>
  /** Legt einen Eintrag in den Papierkorb. Nach {@link PAPIERKORB_TAGE} Tagen wird er endgültig gelöscht. */
  trashItem: (id: string) => Promise<void>
  restoreItem: (id: string) => Promise<void>
  setArchived: (id: string, archiviert: boolean) => Promise<void>
  /** Löscht endgültig: ein Tombstone ersetzt den Eintrag, auch auf allen anderen Geräten. */
  deleteItem: (id: string) => Promise<void>
  emptyTrash: () => Promise<void>
  /**
   * Legt eine Datei im Tresor ab: verschlüsselt sie auf dem Gerät, speichert
   * den Eintrag und lädt im Hintergrund hoch. Liefert die Kennung des Eintrags.
   */
  dateiHinzufuegen: (datei: File, ordner?: string) => Promise<string>
  ordnerAnlegen: (name: string, ordner?: string) => Promise<string>
  albumAnlegen: (name: string, eintraege: string[]) => Promise<string>
  /**
   * Ersetzt den Inhalt einer Datei (Editor). Die bisherige Fassung rückt nach
   * `frueher`; was dort über `VERSIONEN` hinausgeht, wird gelöscht, sobald
   * der Server die neue Fassung hat.
   */
  dateiErsetzen: (id: string, inhalt: Blob) => Promise<void>
  /**
   * Holt eine frühere Fassung zurück (erkannt am Original-Blob). Sie wird als
   * neue Fassung gespeichert, die aktuelle rückt dabei selbst nach `frueher`.
   * So geht beim Zurückholen nichts verloren.
   */
  fassungZurueckholen: (id: string, originalId: string) => Promise<void>
  /** Nimmt Einträge in ein Album auf oder heraus; doppelte zählen einmal. */
  albumAendern: (id: string, aenderung: { hinzu?: string[]; weg?: string[] }) => Promise<void>
  toggleFavorite: (id: string) => Promise<void>
  markUsed: (id: string) => Promise<void>
  syncWithServer: () => Promise<void>
  saveHint: (hint: string) => Promise<void>
  requestHintEmail: () => Promise<{ ok: boolean; message: string }>
  hasHint: boolean | null
  checkHintStatus: () => Promise<boolean>
}

/**
 * Hinterlegt Salz und Bucket des offenen Tresors erneut beim Konto. Die
 * Datei-Routen brauchen die Zuordnung; beim Einrichten wird ein Fehler hier
 * verschluckt (`initializeVault`), und dann fragen sie über
 * `VAULT_BUCKET_UNBEKANNT` nach.
 */
async function bucketNeuMelden(): Promise<void> {
  const { bucketId, bucketAuthToken } = useVaultStore.getState()
  const salz = typeof localStorage !== 'undefined' ? localStorage.getItem(VAULT_SALT_KEY) : null
  if (!bucketId || !salz) throw new Error(i18n.t('errors.vault_bucket_unbekannt'))
  await api('/api/vault/salt', {
    method: 'POST',
    body: JSON.stringify({ kdf_salt: salz, bucket_id: bucketId, auth_token: bucketAuthToken }),
  })
}

export const useVaultStore = create<VaultState>((set, get) => {
  runBiometricsMigration()

  /**
   * Ob die Sitzung, die `userKey` und `bucketId` gelesen hat, noch offen ist.
   *
   * Jedes Entsperren leitet einen neuen `CryptoKey` ab; die Objektgleichheit
   * ist damit die Sitzungskennung. Wer nach einem `await` Klartext in den
   * Store schreibt, fragt vorher hier — sonst stehen nach dem Sperren wieder
   * Passwörter im Speicher, geschrieben von einer verspäteten Antwort.
   */
  const sitzungOffen = (userKey: CryptoKey, bucketId: string) => {
    const jetzt = get()
    return jetzt.userKey === userKey && jetzt.bucketId === bucketId
  }

  return {
  isInitialized: typeof localStorage !== 'undefined' ? !!localStorage.getItem(VAULT_SETUP_DONE_KEY) : false,
  isUnlocked: false,
  isUnlocking: false,
  unlockError: null,
  failedUnlockAttempts: 0,
  lockedUntilMs: 0,
  userKey: null,
  bucketId: null,
  bucketAuthToken: null,
  items: [],
  selectedItemId: null,
  searchQuery: '',
  syncStatus: 'synced',
  lastSyncTime: null,
  hasHint: null,

  autoLockMinutes: liesSperrfrist(VAULT_SPERR_PRAEFIX, 15),
  lockOnWindowBlur: liesFensterwechsel(VAULT_SPERR_PRAEFIX),
  isBiometricsSupported: false,
  isBiometricsEnabled: typeof localStorage !== 'undefined'
    ? localStorage.getItem(VAULT_BIOMETRICS_ENABLED_KEY) === 'true'
    : false,
  lastActivityTime: Date.now(),

  setSearchQuery: (searchQuery) => set({ searchQuery }),
  setSelectedItemId: (selectedItemId) => set({ selectedItemId }),

  setAutoLockMinutes: (minutes: number) => {
    schreibeSperrfrist(VAULT_SPERR_PRAEFIX, minutes)
    if (minutes > 0) meldeErrungenschaft('starter_autolock')
    set({ autoLockMinutes: minutes })
  },

  setLockOnWindowBlur: (enabled: boolean) => {
    schreibeFensterwechsel(VAULT_SPERR_PRAEFIX, enabled)
    if (enabled) meldeErrungenschaft('starter_autolock')
    set({ lockOnWindowBlur: enabled })
  },

  recordActivity: () => {
    const { isUnlocked, autoLockMinutes, lastActivityTime, lock } = get()
    if (isUnlocked && fristAbgelaufen(lastActivityTime, autoLockMinutes)) {
      lock()
      return
    }
    set({ lastActivityTime: Date.now() })
  },

  checkAutoLock: () => {
    const { isUnlocked, autoLockMinutes, lastActivityTime, lock } = get()
    if (!isUnlocked || !fristAbgelaufen(lastActivityTime, autoLockMinutes)) return false
    lock()
    return true
  },

  fetchVaultSalt: async () => {
    try {
      const res = await api<{ kdf_salt: string | null; bucket_id: string | null; has_vault: boolean }>('/api/vault/salt')
      if (res.kdf_salt && typeof localStorage !== 'undefined') {
        localStorage.setItem(VAULT_SALT_KEY, res.kdf_salt)
      }
      if (res.bucket_id && typeof localStorage !== 'undefined') {
        localStorage.setItem(VAULT_SERVER_BUCKET_KEY, res.bucket_id)
      }
      if (res.has_vault) {
        set({ isInitialized: true })
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(VAULT_SETUP_DONE_KEY, 'true')
        }
      } else if (res.has_vault === false) {
        if (typeof localStorage !== 'undefined') {
          localStorage.removeItem(VAULT_SETUP_DONE_KEY)
          localStorage.removeItem(VAULT_CANARY_KEY)
          localStorage.removeItem(VAULT_SERVER_BUCKET_KEY)
          localStorage.removeItem(VAULT_SALT_KEY)
        }
        set({ isInitialized: false })
      }
      return res.kdf_salt
    } catch {
      return null
    }
  },

  checkBiometricsSupport: async () => {
    runBiometricsMigration()
    try {
      const supported = await isBiometricsAvailable()
      set({
        isBiometricsSupported: supported,
        isBiometricsEnabled: supported && (typeof localStorage !== 'undefined' ? localStorage.getItem(VAULT_BIOMETRICS_ENABLED_KEY) === 'true' : false),
      })
      return supported
    } catch {
      set({ isBiometricsSupported: false, isBiometricsEnabled: false })
      return false
    }
  },

  enableBiometrics: async (masterPassword: string) => {
    try {
      const isAvailable = await isBiometricsAvailable()
      if (!isAvailable) {
        throw new Error(i18n.t('mss.vault.errors.biometricsNotSupported'))
      }

      const salt = getOrCreateVaultSalt()
      const { userKey, bucketId } = await deriveVaultKeys(masterPassword, salt)

      const currentBucketId = get().bucketId
      const serverBucket = typeof localStorage !== 'undefined' ? localStorage.getItem(VAULT_SERVER_BUCKET_KEY) : null
      if (currentBucketId && bucketId !== currentBucketId) {
        throw new Error(i18n.t('mss.vault.errors.wrongMasterPassword'))
      }
      if (serverBucket && bucketId !== serverBucket) {
        throw new Error(i18n.t('mss.vault.errors.wrongMasterPassword'))
      }

      const canary = typeof localStorage !== 'undefined'
        ? (localStorage.getItem(VAULT_CANARY_KEY) || localStorage.getItem(`${VAULT_CANARY_PREFIX}${bucketId}`))
        : null
      if (canary) {
        await decryptVaultEntry(canary, userKey, 'vault-canary')
      } else {
        const canaryCiphertext = await encryptVaultEntry(
          { canary: 'mss-vault-initialized-v1', createdAt: Date.now() },
          userKey,
          'vault-canary',
        )
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(VAULT_CANARY_KEY, canaryCiphertext)
          localStorage.setItem(`${VAULT_CANARY_PREFIX}${bucketId}`, canaryCiphertext)
          localStorage.setItem(VAULT_SERVER_BUCKET_KEY, bucketId)
        }
      }

      // Genau eine Bestätigung. Auf Android verlangt der Keystore sie schon
      // beim Verschlüsseln, auf Windows nimmt der Credential Store das Geheimnis
      // wortlos entgegen — dort muss sie also vorher kommen. Ohne diese
      // Unterscheidung fragte Android zweimal hintereinander nach demselben
      // Fingerabdruck.
      if (!(await biometrieSpeicherFragtSelbst())) {
        const verified = await promptBiometricVerification('Biometrischen Schnelleinstieg aktivieren')
        if (!verified) {
          throw new Error(i18n.t('mss.vault.errors.biometricsFailed'))
        }
      }

      // Speichere ausschließlich im hardware-/OS-geschützten Schlüsselspeicher
      // des Betriebssystems (kein localStorage!)
      await biometrieSpeichern(masterPassword, FACH_TRESOR)

      if (typeof localStorage !== 'undefined') {
        localStorage.removeItem('mss:vault_bio_wrapped')
        localStorage.removeItem('mss:vault_device_salt')
        localStorage.setItem(VAULT_BIOMETRICS_ENABLED_KEY, 'true')
      }
      set({ isBiometricsEnabled: true })
      meldeErrungenschaft('starter_biometrics')
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : i18n.t('mss.vault.errors.biometricsActivationFailed')
      throw new Error(msg)
    }
  },

  disableBiometrics: async () => {
    await cleanseVulnerableBiometricData()
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(VAULT_BIOMETRICS_ENABLED_KEY, 'false')
    }
    set({ isBiometricsEnabled: false })
  },

  unlockWithBiometrics: async () => {
    set({ isUnlocking: true, unlockError: null })
    try {
      const isAvailable = await isBiometricsAvailable()
      if (!isAvailable) {
        throw new Error(i18n.t('mss.vault.errors.biometricsNotSupportedShort'))
      }

      // Primär: Native Windows Hello Verifikation & Freigabe aus dem geschützten Credential Store
      const masterPassword = await biometrieEntsperren(i18n.t('mss.vault.entsperrenGrund'), FACH_TRESOR)
      if (!masterPassword) {
        throw new Error(i18n.t('mss.vault.errors.biometricsKeyLoadFailed'))
      }

      const success = await get().unlock(masterPassword)
      return success
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : i18n.t('mss.vault.errors.biometricsUnlockFailed')
      set({ isUnlocking: false, unlockError: msg })
      return false
    }
  },

  lock: () => {
    ansichtenSchliessen()
    set({
      isUnlocked: false,
      userKey: null,
      bucketId: null,
      bucketAuthToken: null,
      items: [],
      selectedItemId: null,
      unlockError: null,
      lastActivityTime: Date.now(),
      // Ein Sync, der gerade unterwegs ist, gehört zur alten Sitzung und
      // verwirft seine Antwort (siehe `syncWithServer`). Bliebe hier
      // „syncing" stehen, liefe nach dem nächsten Entsperren keiner mehr.
      syncStatus: 'synced',
    })
  },

  resetLocalVaultState: () => {
    void cleanseVulnerableBiometricData()
    // Einträge, Warteschlange und Revisionsstand: ohne Salz und Canary lässt
    // sich davon nichts mehr öffnen, und eine liegengebliebene Warteschlange
    // schöbe alte Umschläge in den nächsten Tresor.
    void ablageLoeschen()
    if (typeof localStorage !== 'undefined') {
      localStorage.removeItem(VAULT_SETUP_DONE_KEY)
      localStorage.removeItem(VAULT_CANARY_KEY)
      localStorage.removeItem(VAULT_BIOMETRICS_ENABLED_KEY)
      localStorage.removeItem(VAULT_SERVER_BUCKET_KEY)
      localStorage.removeItem(VAULT_SALT_KEY)

      const toRemove: string[] = []
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i)
        if (k && k.startsWith(VAULT_CANARY_PREFIX)) {
          toRemove.push(k)
        }
      }
      for (const k of toRemove) {
        localStorage.removeItem(k)
      }
    }
    set({
      isInitialized: false,
      isUnlocked: false,
      isBiometricsEnabled: false,
      failedUnlockAttempts: 0,
      lockedUntilMs: 0,
      userKey: null,
      bucketId: null,
      bucketAuthToken: null,
      items: [],
      selectedItemId: null,
      unlockError: null,
      lastActivityTime: Date.now(),
      syncStatus: 'synced',
    })
  },

  resetVault: async (nachweis, bestaetigung) => {
    // Erst der Server: scheitert der Nachweis, bleibt die lokale Kopie, wie sie war.
    await api('/api/vault/reset', {
      method: 'POST',
      body: JSON.stringify({ ...nachweis, confirmation: bestaetigung }),
    })
    get().resetLocalVaultState()
  },

  initializeVault: async (masterPassword: string) => {
    runBiometricsMigration()
    set({ isUnlocking: true, unlockError: null })
    try {
      const salt = getOrCreateVaultSalt()
      const saltHex = Array.from(salt).map((b) => b.toString(16).padStart(2, '0')).join('')
      const { userKey, bucketId, bucketAuthToken } = await deriveVaultKeys(masterPassword, salt)

      await ablageLaden(bucketId)

      // KDF-Salt und Bucket lokal sichern
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(VAULT_SERVER_BUCKET_KEY, bucketId)
        localStorage.setItem(VAULT_SETUP_DONE_KEY, 'true')
      }

      // KDF-Salt serverseitig hinterlegen (SEC-04)
      try {
        await api('/api/vault/salt', {
          method: 'POST',
          // Der Besitznachweis zaehlt nur, wenn der Bucket schon blind
          // registriert ist; sonst ignoriert der Server ihn.
          body: JSON.stringify({ kdf_salt: saltHex, bucket_id: bucketId, auth_token: bucketAuthToken }),
        })
      } catch {}

      // Canary-Prüfblock verschlüsseln und lokal speichern
      const canaryCiphertext = await encryptVaultEntry(
        { canary: 'mss-vault-initialized-v1', createdAt: Date.now() },
        userKey,
        'vault-canary',
      )
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(VAULT_CANARY_KEY, canaryCiphertext)
        localStorage.setItem(`${VAULT_CANARY_PREFIX}${bucketId}`, canaryCiphertext)
        localStorage.setItem(VAULT_SETUP_DONE_KEY, 'true')
        localStorage.setItem(VAULT_SERVER_BUCKET_KEY, bucketId)
        const pendingQueue = getPendingQueue(bucketId)
        if (!pendingQueue.some((p) => p.id === 'vault-canary')) {
          pendingQueue.push({
            id: 'vault-canary',
            ciphertext: canaryCiphertext,
            revision: 1,
            is_deleted: false,
          })
          warteschlangeSchreiben(bucketId, pendingQueue)
        }
      }

      // Falls bereits gecachte Einträge existieren, entschlüsseln
      const cachedBlobs = getStoredBlobs(bucketId)
      const decryptedItems: VaultItem[] = []

      for (const blob of cachedBlobs) {
        if (blob.is_deleted || blob.id === 'vault-canary') continue
        try {
          const payload = await decryptVaultEntry(blob.ciphertext, userKey, blob.id)
          if (payload?.[VAULT_TOMBSTONE_MARKER] === true) continue
          decryptedItems.push(itemAusUmschlag(blob.id, blob.revision, payload))
        } catch {
          // Ignorieren falls nicht entschlüsselbar
        }
      }

      set({
        isInitialized: true,
        isUnlocked: true,
        isUnlocking: false,
        failedUnlockAttempts: 0,
        lockedUntilMs: 0,
        // Entsperren ist Aktivität. Ohne das lief die Frist weiter, während der
        // Tresor zu war: wer ihn aufmacht, eine Viertelstunde woanders
        // hinschaut und dann sein Master-Passwort eingibt, gilt im selben
        // Moment als „seit 15 Minuten untätig" — der Tresor geht auf und beim
        // nächsten Takt wieder zu.
        lastActivityTime: Date.now(),
        userKey,
        bucketId,
        bucketAuthToken,
        items: decryptedItems,
        selectedItemId: decryptedItems.length > 0 ? decryptedItems[0].id : null,
      })

      // Hintergrund-Sync anstoßen
      void get().syncWithServer()

      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : i18n.t('mss.vault.errors.setupFailed')
      set({ isUnlocking: false, unlockError: msg })
      return false
    }
  },

  unlock: async (masterPassword: string) => {
    // 1. Client-seitiger Brute-Force-Schutz (SEC-07): Sperrfrist prüfen
    const now = Date.now()
    const { lockedUntilMs, failedUnlockAttempts } = get()
    if (lockedUntilMs > now) {
      const waitSeconds = Math.ceil((lockedUntilMs - now) / 1000)
      const errorMsg = `Zu viele Fehlversuche. Bitte warte noch ${waitSeconds} Sekunde(n).`
      set({ isUnlocking: false, unlockError: errorMsg })
      return false
    }

    set({ isUnlocking: true, unlockError: null })
    try {
      // 2. Salt ermitteln (lokal oder von Backend abrufen)
      let salt = getLocalVaultSalt()
      if (!salt) {
        await get().fetchVaultSalt()
        salt = getLocalVaultSalt()
      }

      const isSetupDone = typeof localStorage !== 'undefined' && localStorage.getItem(VAULT_SETUP_DONE_KEY) === 'true'
      const hasCanary = typeof localStorage !== 'undefined' && (
        !!localStorage.getItem(VAULT_CANARY_KEY) ||
        Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i)).some((k) => k?.startsWith(VAULT_CANARY_PREFIX))
      )
      const hasServerBucket = typeof localStorage !== 'undefined' && !!localStorage.getItem(VAULT_SERVER_BUCKET_KEY)
      const isConfigured = get().isInitialized || isSetupDone || hasCanary || hasServerBucket

      // Wenn weder ein lokaler noch ein Server-Tresor eingerichtet ist: Keinen Phantom-Tresor anlegen!
      if (!isConfigured) {
        throw new Error(i18n.t('mss.vault.errors.notSetup'))
      }

      if (!salt) {
        throw new Error(i18n.t('mss.vault.errors.keysMissing'))
      }

      const { userKey, bucketId, bucketAuthToken } = await deriveVaultKeys(masterPassword, salt)

      // Bei hinterlegtem Server-Bucket muss der abgeleitete Bucket exakt übereinstimmen
      const serverBucket = typeof localStorage !== 'undefined' ? localStorage.getItem(VAULT_SERVER_BUCKET_KEY) : null
      if (serverBucket && bucketId !== serverBucket) {
        throw new Error(i18n.t('mss.vault.errors.wrongMasterPassword'))
      }

      // 3. Canary prüfen
      let canaryCiphertext = typeof localStorage !== 'undefined' ? localStorage.getItem(VAULT_CANARY_KEY) : null
      let matchedCanary = false

      if (canaryCiphertext) {
        try {
          await decryptVaultEntry(canaryCiphertext, userKey, 'vault-canary')
          matchedCanary = true
        } catch {
          throw new Error(i18n.t('mss.vault.errors.wrongMasterPassword'))
        }
      } else if (typeof localStorage !== 'undefined') {
        const legacyCanaryKeys: string[] = []
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i)
          if (k && k.startsWith(VAULT_CANARY_PREFIX)) {
            legacyCanaryKeys.push(k)
          }
        }

        if (legacyCanaryKeys.length > 0) {
          // Sortieren: serverBucket zuerst, dann Buckets mit Einträgen
          const sortedKeys = legacyCanaryKeys.sort((a, b) => {
            if (serverBucket && a === `${VAULT_CANARY_PREFIX}${serverBucket}`) return -1
            if (serverBucket && b === `${VAULT_CANARY_PREFIX}${serverBucket}`) return 1
            const blobsA = getStoredBlobs(a.replace(VAULT_CANARY_PREFIX, '')).length
            const blobsB = getStoredBlobs(b.replace(VAULT_CANARY_PREFIX, '')).length
            return blobsB - blobsA
          })

          let decryptedSuccessfully = false
          for (const k of sortedKeys) {
            const cipher = localStorage.getItem(k)
            if (!cipher) continue
            try {
              await decryptVaultEntry(cipher, userKey, 'vault-canary')
              canaryCiphertext = cipher
              decryptedSuccessfully = true
              matchedCanary = true
              localStorage.setItem(VAULT_CANARY_KEY, cipher)
              localStorage.setItem(VAULT_SERVER_BUCKET_KEY, bucketId)
              break
            } catch {}
          }

          if (!decryptedSuccessfully) {
            throw new Error(i18n.t('mss.vault.errors.wrongMasterPassword'))
          }
        }
      }

      // Neues Gerät eines blinden Tresors: kein Canary hier, und der Server
      // nennt keinen Bucket, weil der blinde Tresor an keinem Konto hängt.
      // Bis 09/2026 endete das in „falsches Master-Passwort", egal was man
      // eingab. Dann muss der Server den Besitznachweis bestätigen.
      let serverBestaetigt = false
      if (!matchedCanary && !serverBucket) {
        let passt: boolean
        try {
          passt = await blindVaultCheck(bucketId, bucketAuthToken)
        } catch {
          throw new Error(i18n.t('mss.vault.errors.serverCheckFailed'))
        }
        if (!passt) {
          throw new Error(i18n.t('mss.vault.errors.wrongMasterPassword'))
        }
        serverBestaetigt = true
      }

      // Entweder Canary entschlüsselt, über hinterlegten serverBucket
      // verifiziert oder vom Server als blinder Tresor bestätigt
      const isVerified = matchedCanary || serverBestaetigt || (serverBucket !== null && bucketId === serverBucket)
      if (!isVerified) {
        throw new Error(i18n.t('mss.vault.errors.wrongMasterPassword'))
      }

      // 4. Lokale verschlüsselte Blobs aus dem Cache laden
      await ablageLaden(bucketId)
      const cachedBlobs = getStoredBlobs(bucketId)

      const decryptedItems: VaultItem[] = []
      for (const blob of cachedBlobs) {
        if (blob.is_deleted || blob.id === 'vault-canary') continue
        try {
          const payload = await decryptVaultEntry(blob.ciphertext, userKey, blob.id)
          if (payload?.[VAULT_TOMBSTONE_MARKER] === true) continue
          decryptedItems.push(itemAusUmschlag(blob.id, blob.revision, payload))
        } catch (err) {
          console.warn(`Gecachter Tresor-Eintrag ${blob.id} konnte nicht entschlüsselt werden (übersprungen):`, err)
        }
      }

      // Falls noch kein Canary da war (z. B. First-Unlock nach Salt-Injection), jetzt absichern
      if (!canaryCiphertext) {
        const newCanary = await encryptVaultEntry(
          { canary: 'mss-vault-initialized-v1', createdAt: Date.now() },
          userKey,
          'vault-canary',
        )
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(VAULT_CANARY_KEY, newCanary)
          localStorage.setItem(`${VAULT_CANARY_PREFIX}${bucketId}`, newCanary)
          // Auch zum Server, wie beim Einrichten. `/blind-check` bestätigt nur
          // Buckets mit Eintrag; ein Tresor, der nie einen Canary hochlud und
          // leer blieb, hieße auf dem nächsten Gerät sonst wieder „falsches
          // Master-Passwort".
          const pendingQueue = getPendingQueue(bucketId)
          if (!pendingQueue.some((p) => p.id === 'vault-canary')) {
            pendingQueue.push({ id: 'vault-canary', ciphertext: newCanary, revision: 1, is_deleted: false })
            warteschlangeSchreiben(bucketId, pendingQueue)
          }
        }
      }
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(VAULT_SETUP_DONE_KEY, 'true')
        localStorage.setItem(VAULT_SERVER_BUCKET_KEY, bucketId)
      }

      // Erfolg: Fehlversuche zurücksetzen
      set({
        isInitialized: true,
        isUnlocked: true,
        isUnlocking: false,
        failedUnlockAttempts: 0,
        lockedUntilMs: 0,
        // Siehe oben: der Zähler startet beim Entsperren, nicht beim Laden.
        lastActivityTime: Date.now(),
        userKey,
        bucketId,
        bucketAuthToken,
        items: decryptedItems,
        selectedItemId: decryptedItems.length > 0 ? decryptedItems[0].id : null,
      })

      // Hintergrund-Sync anstoßen (kein verräterischer Vorab-Ping an hint-status im fremden Netz)
      void get().syncWithServer()

      return true
    } catch (err: unknown) {
      // Fehlversuchszähler mit exponentiellem Backoff (SEC-07)
      const attempts = failedUnlockAttempts + 1
      let backoffMs = 0
      if (attempts >= 3) {
        backoffMs = Math.min(60000, Math.pow(2, attempts - 3) * 1000)
      }

      const msg = err instanceof Error ? err.message : i18n.t('mss.vault.errors.unlockFailed')
      set({
        isUnlocking: false,
        failedUnlockAttempts: attempts,
        lockedUntilMs: Date.now() + backoffMs,
        unlockError: msg,
      })
      return false
    }
  },

  createQuickPasswordEntry: async (serviceName = 'Neuer Eintrag') => {
    const { userKey, bucketId } = get()
    if (!userKey || !bucketId) {
      throw new Error(i18n.t('mss.vault.errors.locked'))
    }

    const newId = window.crypto.randomUUID()
    const password = generateSecurePassword(20, true)
    const now = Date.now()

    const newItem: VaultItem = {
      id: newId,
      service: serviceName,
      username: '',
      password,
      category: 'login',
      createdAt: now,
      updatedAt: now,
      revision: 1,
    }

    // Verschlüsseln
    const ciphertext = await encryptVaultEntry(umschlagAusItem(newItem), userKey, newId)

    // In Cache und Warteschlange ablegen
    const cachedBlobs = getStoredBlobs(bucketId)
    cachedBlobs.push({ id: newId, ciphertext, revision: 1, is_deleted: false, stand: now })
    blobsSchreiben(bucketId, cachedBlobs)

    const pendingQueue = getPendingQueue(bucketId)
    pendingQueue.push({ id: newId, ciphertext, revision: 1, is_deleted: false })
    warteschlangeSchreiben(bucketId, pendingQueue)

    // Der Umschlag ist abgelegt; in den Speicher kommt der Klartext nur, wenn
    // während des Verschlüsselns niemand gesperrt hat.
    if (sitzungOffen(userKey, bucketId)) {
      set({
        items: [newItem, ...get().items],
        selectedItemId: newId,
      })
    }

    // Im Hintergrund synchronisieren
    void get().syncWithServer()

    return newItem
  },

  saveItem: async (itemData, optionen) => {
    const { userKey, bucketId, items } = get()
    if (!userKey || !bucketId) throw new Error(i18n.t('mss.vault.errors.locked'))

    const id = itemData.id || window.crypto.randomUUID()
    const existing = items.find((i) => i.id === id)
    // Was diese Fassung nicht kennt, schreibt sie nicht: sie würde es nur
    // unvollständig verstehen und beim Speichern verfälschen.
    if (!istBekannteKategorie(existing?.category) || !istBekannteKategorie(itemData.category ?? existing?.category)) {
      throw new Error(i18n.t('mss.vault.errors.unbekannteArt'))
    }
    const revision = (existing?.revision || 0) + 1
    const now = naechsterStand(existing?.updatedAt)
    // Ein ausdrücklich mitgegebenes `undefined` hebt Archiv oder Papierkorb auf;
    // fehlt das Feld ganz (Bearbeiten im Formular), bleibt der bisherige Wert.
    const uebernimm = (feld: 'archivedAt' | 'trashedAt'): number | undefined =>
      feld in itemData ? itemData[feld] : existing?.[feld]

    const updatedItem: VaultItem = {
      id,
      service: itemData.service,
      username: itemData.username || '',
      password: itemData.password || '',
      url: itemData.url,
      notes: itemData.notes,
      totpSecret: itemData.totpSecret,
      category: itemData.category || existing?.category || 'login',
      isFavorite: itemData.isFavorite !== undefined ? itemData.isFavorite : existing?.isFavorite,
      lastUsedAt: itemData.lastUsedAt !== undefined ? itemData.lastUsedAt : existing?.lastUsedAt,
      linkedServiceId: itemData.linkedServiceId !== undefined ? itemData.linkedServiceId : existing?.linkedServiceId,
      archivedAt: uebernimm('archivedAt'),
      trashedAt: uebernimm('trashedAt'),
      datei: itemData.datei ?? existing?.datei,
      ordner: 'ordner' in itemData ? itemData.ordner : existing?.ordner,
      album: itemData.album ?? existing?.album,
      createdAt: existing?.createdAt || now,
      updatedAt: now,
      revision,
      extra: existing?.extra,
    }

    const ciphertext = await encryptVaultEntry(umschlagAusItem(updatedItem), userKey, id)

    // Lokalen Cache aktualisieren
    let cachedBlobs = getStoredBlobs(bucketId)
    const bisher = cachedBlobs.find((b) => b.id === id)
    // Löschaufträge der vorigen Fassung, die noch nicht liefen, gehen mit:
    // auch die neue Fassung zeigt nicht mehr auf diese Blobs.
    const loeschBlobs = [...(bisher?.loeschBlobs ?? []), ...(optionen?.loeschBlobs ?? [])]
    cachedBlobs = cachedBlobs.filter((b) => b.id !== id)
    cachedBlobs.push({
      id,
      ciphertext,
      revision,
      is_deleted: false,
      stand: now,
      serverRev: bisher?.serverRev,
      loeschBlobs: loeschBlobs.length > 0 ? loeschBlobs : undefined,
    })
    blobsSchreiben(bucketId, cachedBlobs)

    // Pending Queue aktualisieren
    const pendingQueue = getPendingQueue(bucketId).filter((b) => b.id !== id)
    pendingQueue.push({ id, ciphertext, revision, is_deleted: false })
    warteschlangeSchreiben(bucketId, pendingQueue)

    // Siehe createQuickPasswordEntry. Die Liste wird frisch gelesen: ein Sync
    // oder ein zweites Speichern kann sie während des Verschlüsselns geändert
    // haben.
    if (!sitzungOffen(userKey, bucketId)) return
    const aktuell = get().items
    const newItems = aktuell.some((i) => i.id === id)
      ? aktuell.map((i) => (i.id === id ? updatedItem : i))
      : [updatedItem, ...aktuell]

    set({ items: newItems, selectedItemId: id })
    void get().syncWithServer()
  },

  deleteItem: async (id: string) => {
    const { userKey, bucketId, items } = get()
    if (!userKey || !bucketId) return

    const existing = items.find((i) => i.id === id)
    const revision = (existing?.revision || 0) + 1
    const stand = naechsterStand(existing?.updatedAt)

    // Der Tombstone wird verschlüsselt und an dieselbe `entryId` gebunden wie
    // der Eintrag, den er beerdigt. Früher stand hier `ciphertext: ''` — eine
    // Löschung ohne Absender, die jeder erfinden konnte. Siehe
    // VAULT_TOMBSTONE_MARKER.
    const tombstone = await encryptVaultEntry(
      { [VAULT_TOMBSTONE_MARKER]: true, deletedAt: Date.now(), updatedAt: stand },
      userKey,
      id,
    )

    // Im Cache bleibt der Tombstone stehen, nicht nichts: ohne ihn hielte der
    // Sync eine zurückgespielte alte Fassung des Eintrags für einen neuen und
    // holte das gelöschte Passwort zurück.
    //
    // Bei einer Datei trägt der Tombstone die Löschaufträge ihrer Blobs. Sie
    // laufen erst, wenn der Server das Löschen angenommen hat.
    let cachedBlobs = getStoredBlobs(bucketId)
    const vorher = cachedBlobs.find((b) => b.id === id)
    cachedBlobs = cachedBlobs.filter((b) => b.id !== id)
    const loeschBlobs = existing?.datei ? dateiBlobs(existing.datei).map((k) => ({ id: k.id, loeschen: k.loeschen })) : undefined
    cachedBlobs.push({ id, ciphertext: tombstone, revision, is_deleted: true, stand, serverRev: vorher?.serverRev, loeschBlobs })
    blobsSchreiben(bucketId, cachedBlobs)

    // Tombstone in Pending Queue. Gelöscht wird nur die Fassung, die dieses
    // Gerät zuletzt vom Server kannte: hat ein anderes Gerät den Eintrag
    // inzwischen wiederhergestellt oder geändert, lehnt der Server ab, und die
    // Änderung des anderen Geräts bleibt.
    const pendingQueue = getPendingQueue(bucketId).filter((b) => b.id !== id)
    pendingQueue.push({
      id,
      ciphertext: tombstone,
      revision,
      is_deleted: true,
      expected_revision: vorher?.serverRev,
      vorher: vorher ? { ...vorher, vorher: undefined } : undefined,
    })
    warteschlangeSchreiben(bucketId, pendingQueue)

    if (!sitzungOffen(userKey, bucketId)) return
    const remaining = get().items.filter((i) => i.id !== id)
    set({
      items: remaining,
      selectedItemId: remaining.length > 0 ? remaining[0].id : null,
    })

    // Was in einem Ordner liegt, geht mit ihm.
    if (existing?.category === 'ordner') {
      for (const kind of get().items.filter((i) => i.ordner === id)) {
        if (!sitzungOffen(userKey, bucketId)) return
        await get().deleteItem(kind.id)
      }
    }

    void get().syncWithServer()
  },

  trashItem: async (id: string) => {
    const { items, saveItem } = get()
    const item = items.find((i) => i.id === id)
    if (!item || item.trashedAt) return
    await saveItem({ ...item, trashedAt: Date.now() })
  },

  restoreItem: async (id: string) => {
    const { items, saveItem } = get()
    const item = items.find((i) => i.id === id)
    if (!item || !item.trashedAt) return
    await saveItem({ ...item, trashedAt: undefined })
  },

  setArchived: async (id: string, archiviert: boolean) => {
    const { items, saveItem } = get()
    const item = items.find((i) => i.id === id)
    if (!item || !!item.archivedAt === archiviert) return
    await saveItem({ ...item, archivedAt: archiviert ? Date.now() : undefined })
  },

  emptyTrash: async () => {
    const imPapierkorb = get().items.filter((i) => i.trashedAt && istBekannteKategorie(i.category))
    for (const item of imPapierkorb) {
      await get().deleteItem(item.id)
    }
  },

  dateiHinzufuegen: async (datei, ordner) => {
    const { userKey, bucketId } = get()
    if (!userKey || !bucketId) throw new Error(i18n.t('mss.vault.errors.locked'))
    const id = window.crypto.randomUUID()
    const { vorschau, miniatur, ...bild } = await bildAngaben(datei)
    const angaben = await dateiVorbereiten(datei, userKey, bucketId, id, () => !sitzungOffen(userKey, bucketId), { vorschau, miniatur })
    try {
      if (!sitzungOffen(userKey, bucketId)) throw new Error(i18n.t('mss.vault.errors.locked'))
      await get().saveItem({ id, service: datei.name, category: 'datei', datei: { ...angaben, ...bild }, ordner })
    } finally {
      // Ohne gespeicherten Eintrag verwirft der Uploader die Blobs beim nächsten Lauf.
      vorbereitungAbschliessen(angaben)
    }
    void uploadsFortsetzen(bucketId)
    return id
  },

  ordnerAnlegen: async (name, ordner) => {
    const id = window.crypto.randomUUID()
    await get().saveItem({ id, service: name, category: 'ordner', ordner })
    return id
  },

  dateiErsetzen: async (id, inhalt) => {
    const { userKey, bucketId } = get()
    if (!userKey || !bucketId) throw new Error(i18n.t('mss.vault.errors.locked'))
    const alt = get().items.find((i) => i.id === id)
    if (!alt?.datei) throw new Error(i18n.t('mss.vault.dateien.oeffnenFehler'))
    const { vorschau, miniatur, ...bild } = await bildAngaben(inhalt)
    // Eine angeheftete Datei bleibt offline verfügbar: das neue Original wird gleich behalten.
    const warAngeheftet = (await angeheftet([alt.datei.original.id])).size > 0
    const neu = await dateiVorbereiten(inhalt, userKey, bucketId, id, () => !sitzungOffen(userKey, bucketId), {
      vorschau,
      miniatur,
      originalBehalten: warAngeheftet,
    })
    try {
      if (!sitzungOffen(userKey, bucketId)) throw new Error(i18n.t('mss.vault.errors.locked'))
      // Frisch lesen: während des Verschlüsselns kann ein Sync die Datei geändert haben.
      const jetzt = get().items.find((i) => i.id === id)
      if (!jetzt?.datei) throw new Error(i18n.t('mss.vault.dateien.oeffnenFehler'))
      const { frueher = [], ...bisher } = jetzt.datei
      const versionen: DateiVersion[] = [
        { typ: bisher.typ, ersetzt: Date.now(), original: bisher.original, vorschau: bisher.vorschau, miniatur: bisher.miniatur },
        ...frueher,
      ]
      const weg = versionen.slice(VERSIONEN).flatMap((v) => [v.original, v.vorschau, v.miniatur])
      await get().saveItem(
        {
          ...jetzt,
          datei: {
            // Aufnahmezeit und Kamera bleiben: ein gedrehtes Foto ist noch dasselbe.
            ...bisher,
            ...bild,
            typ: inhalt.type || bisher.typ,
            geaendert: Date.now(),
            original: neu.original,
            vorschau: neu.vorschau,
            miniatur: neu.miniatur,
            frueher: versionen.slice(0, VERSIONEN),
          },
        },
        { loeschBlobs: weg.map((k) => ({ id: k.id, loeschen: k.loeschen })) },
      )
      if (warAngeheftet) {
        await offlineAnheften(neu.original)
        await offlineLoesen(alt.datei.original.id)
      }
    } finally {
      vorbereitungAbschliessen(neu)
    }
    void uploadsFortsetzen(bucketId)
  },

  fassungZurueckholen: async (id, originalId) => {
    const { userKey } = get()
    if (!userKey) throw new Error(i18n.t('mss.vault.errors.locked'))
    const fassung = get().items.find((i) => i.id === id)?.datei?.frueher?.find((v) => v.original.id === originalId)
    if (!fassung) throw new Error(i18n.t('mss.vault.dateien.oeffnenFehler'))
    const inhalt = await blobLesen(fassung.original, id, userKey, fassung.typ)
    await get().dateiErsetzen(id, inhalt)
  },

  albumAnlegen: async (name, eintraege) => {
    const liste = [...new Set(eintraege)]
    if (liste.length > ALBUM_HOECHSTENS) throw new Error(i18n.t('mss.vault.fotos.albumVoll', { max: ALBUM_HOECHSTENS }))
    const id = window.crypto.randomUUID()
    await get().saveItem({ id, service: name, category: 'album', album: { eintraege: liste } })
    return id
  },

  albumAendern: async (id, { hinzu = [], weg = [] }) => {
    // Frisch lesen: eine zweite Änderung kurz davor ist sonst verloren.
    const album = get().items.find((i) => i.id === id && i.category === 'album')
    if (!album) return
    const raus = new Set(weg)
    const liste = [...new Set([...(album.album?.eintraege ?? []), ...hinzu])].filter((e) => !raus.has(e))
    if (liste.length > ALBUM_HOECHSTENS) throw new Error(i18n.t('mss.vault.fotos.albumVoll', { max: ALBUM_HOECHSTENS }))
    await get().saveItem({ ...album, album: { eintraege: liste } })
  },

  toggleFavorite: async (id: string) => {
    const { items, saveItem } = get()
    const item = items.find((i) => i.id === id)
    if (!item) return
    await saveItem({ ...item, isFavorite: !item.isFavorite })
  },

  markUsed: async (id: string) => {
    const { items, saveItem } = get()
    const item = items.find((i) => i.id === id)
    if (!item) return
    await saveItem({ ...item, lastUsedAt: Date.now() })
  },

  syncWithServer: async () => {
    const { userKey, bucketId, bucketAuthToken, syncStatus } = get()
    if (!userKey || !bucketId || syncStatus === 'syncing') return

    set({ syncStatus: 'syncing' })

    // Hat der Server uns abgewiesen, bleibt das ein Auth-Fehler — auch wenn der
    // anschließende Registrierungsversuch am Netz scheitert. „offline" wäre hier
    // die falsche Auskunft: erreichbar war er ja.
    let blindAbgewiesen = false

    const senden = async (
      mutations: VaultSyncPayload['mutations'],
      sinceRevision: number,
    ): Promise<VaultSyncResponse> => {
      const format = {
        client_format: VAULT_EINTRAG_FORMAT,
        ...(brauchtNeueApp(get().items) ? { min_client_format: VAULT_EINTRAG_FORMAT } : {}),
      }
      if (!bucketAuthToken) {
        const payload: VaultSyncPayload = {
          bucket_id: bucketId,
          since_revision: sinceRevision,
          mutations,
          ...format,
        }
        return api<VaultSyncResponse>('/api/vault/sync', {
          method: 'POST',
          body: JSON.stringify(payload),
        })
      }
      const blind = () =>
        blindVaultSync({
          bucket_id: bucketId,
          auth_token: bucketAuthToken,
          since_revision: sinceRevision,
          mutations,
          ...format,
        })
      try {
        return await blind()
      } catch (err: unknown) {
        // 401 heißt hier: der Bucket trägt schon Daten, aber noch keinen
        // blinden Besitznachweis — ein Tresor aus der Zeit vor dem Audit.
        // Den Nachweis darf nur der angemeldete Besitzer hinterlegen;
        // unauthentifiziert nachzuregistrieren war genau die Lücke, über die
        // sich fremde Tresore übernehmen ließen.
        const istAuthFehler = err instanceof Error && err.message.includes('401')
        if (!istAuthFehler) throw err
        blindAbgewiesen = true
        await api('/api/vault/blind-register', {
          method: 'POST',
          body: JSON.stringify({ bucket_id: bucketId, auth_token: bucketAuthToken }),
        })
        return blind()
      }
    }

    // Erst wenn die Warteschlange leer ist, kennt dieses Gerät den Stand des
    // Servers. Nur dann darf der Papierkorb geleert werden: ein Gerät mit
    // veraltetem Stand löschte sonst, was ein anderes gerade wiederhergestellt hat.
    let abgeglichen = false

    try {
      // Die Warteschlange geht in Paketen, die der Server annimmt. Was
      // während einer Runde dazukommt, geht in der nächsten mit: der
      // Speichern-Aufruf, der es eingereiht hat, fand den Sync schon laufend
      // vor und hat keinen eigenen gestartet.
      for (let runde = 0; runde < SYNC_MAX_RUNDEN; runde++) {
        const paket = naechstesSyncPaket(getPendingQueue(bucketId))
        const sinceRevision = revisionLesen(bucketId)

        const data = await senden(
          paket.map((m) => ({
            id: m.id,
            ciphertext: m.ciphertext,
            revision: m.revision,
            is_deleted: m.is_deleted,
            ...(m.expected_revision !== undefined ? { expected_revision: m.expected_revision } : {}),
          })),
          sinceRevision,
        )
        // Gesperrt, während die Antwort unterwegs war: sie gehört einer
        // Sitzung, die es nicht mehr gibt. Kein Klartext in den Store, kein
        // Schreiben in den Cache — der nächste Sync holt dasselbe noch einmal.
        if (!sitzungOffen(userKey, bucketId)) return

        const { befunde, vorab } = await pruefeSyncAntwort(data.entries, userKey, getStoredBlobs(bucketId))
        if (!sitzungOffen(userKey, bucketId)) return

        // Ab hier kein `await` mehr bis zum Ende der Runde: Cache, Liste und
        // Warteschlange werden frisch gelesen und in einem Zug geschrieben.
        // Vorher kam die Liste aus dem Aufruf von vor der Netzanfrage, und
        // ein zwischendurch gespeicherter Eintrag wurde mit der alten Fassung
        // überschrieben.
        let cachedBlobs = getStoredBlobs(bucketId)
        let currentItems = [...get().items]

        // Abgelehnte Löschungen: ein anderes Gerät hat den Eintrag inzwischen
        // geändert. Der eigene Tombstone kommt aus dem Cache, die Fassung von
        // davor zurück, damit die neuere des anderen Geräts gegen sie
        // verglichen wird. Behaupten kann der Server damit nur, was dieses
        // Gerät selbst vorher hatte.
        for (const konfliktId of data.conflicts ?? []) {
          const gesendet = paket.find((m) => m.id === konfliktId && m.vorher)
          if (!gesendet?.vorher) continue
          const lokal = cachedBlobs.find((b) => b.id === konfliktId)
          if (lokal?.ciphertext !== gesendet.ciphertext) continue
          cachedBlobs = cachedBlobs.filter((b) => b.id !== konfliktId)
          cachedBlobs.push(gesendet.vorher)
          console.warn(`Tresor-Sync: Löschung von ${konfliktId} abgelehnt, ein anderes Gerät hat ihn geändert.`)
        }

        for (const befund of befunde) {
          if (befund.art === 'canary') {
            localStorage.setItem(VAULT_CANARY_KEY, befund.ciphertext)
            localStorage.setItem(`${VAULT_CANARY_PREFIX}${bucketId}`, befund.ciphertext)
            localStorage.setItem(VAULT_SERVER_BUCKET_KEY, bucketId)
            continue
          }

          const { entry, stand } = befund
          // Rollback-Schutz: eine Fassung, die älter ist als die bekannte,
          // ist kein Sync, sondern ein Angebot — etwa das alte, längst
          // ersetzte Passwort eines kompromittierten Dienstes. Der Umschlag
          // daran ist gültig (er war es ja einmal), also fällt es der
          // Entschlüsselung nicht auf. Verglichen wird der Stand im Umschlag,
          // nicht die Revision daneben: die setzt der Server, wie er will.
          //
          // Gleichstand wird übernommen. So kommt das Echo der eigenen
          // Änderung an, und zwei Geräte, die gleichzeitig geändert haben,
          // landen beide beim Stand des Servers.
          const bekannt = cachedBlobs.find((b) => b.id === entry.id)
          const bekannterStand = bekannt ? (bekannt.stand ?? vorab.get(entry.id) ?? 0) : undefined
          if (bekannterStand !== undefined && stand < bekannterStand) {
            console.warn(`Tresor-Sync: Rücksprung für ${entry.id} (${bekannterStand} → ${stand}) verworfen.`)
            continue
          }

          cachedBlobs = cachedBlobs.filter((b) => b.id !== entry.id)
          const serverRev = entry.revision
          // Das Echo der eigenen Fassung behält deren Löschaufträge.
          const loeschBlobs = bekannt?.ciphertext === entry.ciphertext ? bekannt.loeschBlobs : undefined
          if (befund.art === 'grab') {
            cachedBlobs.push({ id: entry.id, ciphertext: entry.ciphertext, revision: entry.revision, is_deleted: true, stand, serverRev, loeschBlobs })
            currentItems = currentItems.filter((i) => i.id !== entry.id)
            continue
          }

          cachedBlobs.push({ id: entry.id, ciphertext: entry.ciphertext, revision: entry.revision, is_deleted: false, stand, serverRev, loeschBlobs })
          const idx = currentItems.findIndex((i) => i.id === entry.id)
          if (idx >= 0) {
            currentItems[idx] = befund.item
          } else {
            currentItems.push(befund.item)
          }
        }

        // Ausgetragen wird nur, was genau so übertragen wurde. Früher reichte
        // die gleiche ID: wer einen Eintrag änderte, während seine vorige
        // Fassung unterwegs war, verlor die neue Fassung aus der
        // Warteschlange, ohne dass sie je den Server erreichte.
        const offen = getPendingQueue(bucketId).filter(
          (m) => !paket.some((g) => g.id === m.id && g.ciphertext === m.ciphertext),
        )
        warteschlangeSchreiben(bucketId, offen)
        blobsSchreiben(bucketId, cachedBlobs)
        revisionSchreiben(bucketId, data.server_revision)
        set({ items: currentItems })

        if (offen.length === 0 && !data.has_more) {
          abgeglichen = true
          break
        }
      }

      // Falls lokal noch kein Canary existiert (z. B. Multi-Device Login), jetzt absichern
      const hasCanary = !!localStorage.getItem(VAULT_CANARY_KEY) || !!localStorage.getItem(`${VAULT_CANARY_PREFIX}${bucketId}`)
      if (!hasCanary) {
        try {
          const canary = await encryptVaultEntry(
            { canary: 'mss-vault-initialized-v1', createdAt: Date.now() },
            userKey,
            'vault-canary',
          )
          if (!sitzungOffen(userKey, bucketId)) return
          localStorage.setItem(VAULT_CANARY_KEY, canary)
          localStorage.setItem(`${VAULT_CANARY_PREFIX}${bucketId}`, canary)
        } catch {}
      }
      if (!localStorage.getItem(VAULT_SERVER_BUCKET_KEY)) {
        localStorage.setItem(VAULT_SERVER_BUCKET_KEY, bucketId)
      }

      set({
        syncStatus: 'synced',
        lastSyncTime: Date.now(),
      })
      void uploadsFortsetzen(bucketId)
      void loeschungenAbarbeiten(bucketId)

      if (abgeglichen) {
        await ablageUmziehen()
        if (!sitzungOffen(userKey, bucketId)) return
        const grenze = Date.now() - PAPIERKORB_MS
        const abgelaufen = get().items.filter(
          (i) => i.trashedAt !== undefined && i.trashedAt < grenze && istBekannteKategorie(i.category),
        )
        for (const item of abgelaufen) {
          if (!sitzungOffen(userKey, bucketId)) return
          await get().deleteItem(item.id)
        }
      }
    } catch (err: unknown) {
      // Der Status gehört der Sitzung, die ihn gesetzt hat.
      if (!sitzungOffen(userKey, bucketId)) return
      // Bei 401 Unauthorized: Auth-Fehler anzeigen, sonst im Offline-Modus bleiben
      // 426: eine neuere App hat den Tresor hochgestuft, diese muss aktualisiert werden.
      const isAuthError =
        blindAbgewiesen || (err instanceof Error && (err.message.includes('401') || err.message.includes('426')))
      set({ syncStatus: isAuthError ? 'error' : 'offline' })
    }
  },

  saveHint: async (hint: string) => {
    if (!hint.trim()) return
    try {
      await api('/api/vault/hint', {
        method: 'POST',
        body: JSON.stringify({ hint: hint.trim() }),
      })
      set({ hasHint: true })
    } catch {
      // Offline / Fehler leise ignorieren oder später syncen
    }
  },

  checkHintStatus: async () => {
    try {
      const res = await api<{ has_hint: boolean }>('/api/vault/hint-status')
      const has = !!res.has_hint
      set({ hasHint: has })
      return has
    } catch {
      return false
    }
  },

  requestHintEmail: async (): Promise<{ ok: boolean; message: string }> => {
    try {
      const res = await api<{ status: string; message: string }>('/api/vault/request-hint', {
        method: 'POST',
      })
      return { ok: true, message: res.message || 'Passwort-Hinweis wurde per E-Mail gesendet.' }
    } catch (err: unknown) {
      const msg =
        err && typeof err === 'object' && 'message' in err && typeof (err as { message: unknown }).message === 'string'
          ? (err as { message: string }).message
          : 'Fehler beim Anfordern des Hinweises.'
      return { ok: false, message: msg }
    }
  },
  }
})

bucketMelderSetzen(bucketNeuMelden)

/**
 * Was `useAutoSperre` braucht, um den Tresor zu bewachen.
 *
 * Die Ereignis-Anmeldungen standen bis 09/2026 dreimal im Baum: hier auf
 * Modulebene, noch einmal in `DesktopApp` und in Teilen ein drittes Mal in
 * `VaultView`. Alle drei taten dasselbe, keine wusste von den anderen. Jetzt
 * meldet `DesktopApp` sie einmal an, mit dieser Quelle.
 */
export const tresorAutoSperrQuelle: AutoSperrQuelle = {
  istEntsperrt: () => useVaultStore.getState().isUnlocked,
  istBeschaeftigt: () => useVaultStore.getState().isUnlocking,
  sperrtBeiFensterwechsel: () => useVaultStore.getState().lockOnWindowBlur,
  merkeAktivitaet: () => useVaultStore.getState().recordActivity(),
  pruefeFrist: () => {
    useVaultStore.getState().checkAutoLock()
  },
  sperre: () => useVaultStore.getState().lock(),
}

if (typeof window !== 'undefined') {
  setTimeout(() => {
    void useVaultStore.getState().checkBiometricsSupport()
    if (!getLocalVaultSalt()) {
      void useVaultStore.getState().fetchVaultSalt()
    }
  }, 50)

  // `mss:fenster-blur` kennt nur der Tresor: das Ereignis kommt aus der App,
  // wenn das Fenster in den Hintergrund geht, und `useAutoSperre` hört
  // ausschließlich auf `tauri://blur`. Deshalb bleibt diese eine Anmeldung
  // hier stehen, während der Rest in den Haken gewandert ist.
  if ('__TAURI_INTERNALS__' in window) {
    try {
      import('@tauri-apps/api/event')
        .then(({ listen }) =>
          listen('mss:fenster-blur', () => {
            const s = useVaultStore.getState()
            if (s.lockOnWindowBlur && s.isUnlocked && !s.isUnlocking) s.lock()
          }),
        )
        .catch(() => {})
    } catch {}
  }
}
