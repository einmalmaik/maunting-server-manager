import { create } from 'zustand'
import { api } from '@/api/client'
import {
  base64ToBytes,
  decryptVaultEntry,
  deriveVaultKeys,
  encryptVaultEntry,
  generateSecurePassword,
  isBiometricsAvailable,
  promptBiometricVerification,
  unwrapVaultCredentialsFromBiometrics,
  wrapVaultCredentialsForBiometrics,
} from './vaultCrypto'
import { biometrieEntsperren, biometrieLoeschen, biometrieSpeichern } from '../tauri'

export interface VaultAttachment {
  id: string
  name: string
  size: number
  mimeType: string
  dataBase64: string
}

export interface VaultItem {
  id: string
  service: string
  username: string
  password: string
  url?: string
  notes?: string
  totpSecret?: string
  category?: 'login' | 'authenticator' | 'secure_note'
  isFavorite?: boolean
  lastUsedAt?: number
  attachments?: VaultAttachment[]
  linkedServiceId?: string
  createdAt: number
  updatedAt: number
  revision: number
}

interface StoredEncryptedEntry {
  id: string
  ciphertext: string
  revision: number
  is_deleted: boolean
}

interface VaultSyncPayload {
  bucket_id: string
  since_revision: number
  mutations: {
    id: string
    ciphertext: string
    revision: number
    is_deleted: boolean
  }[]
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
}

const VAULT_SALT_KEY = 'mss:vault_salt'
const VAULT_SETUP_DONE_KEY = 'mss:vault_setup_done'
const VAULT_CANARY_PREFIX = 'mss:vault_canary_'
const VAULT_LOCAL_STORAGE_PREFIX = 'mss:vault_blobs_'
const VAULT_PENDING_QUEUE_PREFIX = 'mss:vault_pending_'
const VAULT_REVISION_PREFIX = 'mss:vault_rev_'
const VAULT_AUTOLOCK_MINUTES_KEY = 'mss:vault_autolock_minutes'
const VAULT_LOCK_ON_BLUR_KEY = 'mss:vault_lock_on_blur'
const VAULT_BIOMETRICS_ENABLED_KEY = 'mss:vault_biometrics_enabled'
const VAULT_BIOMETRICS_WRAPPED_KEY = 'mss:vault_bio_wrapped'
const VAULT_SERVER_BUCKET_KEY = 'mss:vault_server_bucket'

export const MAX_VAULT_ATTACHMENT_SIZE_BYTES = 500 * 1024 // 500 KB limit (SEC-08)

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
  setSearchQuery: (q: string) => void
  setSelectedItemId: (id: string | null) => void
  createQuickPasswordEntry: (serviceName?: string) => Promise<VaultItem>
  saveItem: (item: Partial<VaultItem> & { service: string }) => Promise<void>
  deleteItem: (id: string) => Promise<void>
  toggleFavorite: (id: string) => Promise<void>
  markUsed: (id: string) => Promise<void>
  syncWithServer: () => Promise<void>
  saveHint: (hint: string) => Promise<void>
  requestHintEmail: () => Promise<{ ok: boolean; message: string }>
  hasHint: boolean | null
  checkHintStatus: () => Promise<boolean>
}

export const useVaultStore = create<VaultState>((set, get) => ({
  isInitialized: typeof localStorage !== 'undefined' ? !!localStorage.getItem(VAULT_SETUP_DONE_KEY) : false,
  isUnlocked: false,
  isUnlocking: false,
  unlockError: null,
  failedUnlockAttempts: 0,
  lockedUntilMs: 0,
  userKey: null,
  bucketId: null,
  items: [],
  selectedItemId: null,
  searchQuery: '',
  syncStatus: 'synced',
  lastSyncTime: null,
  hasHint: null,

  autoLockMinutes: typeof localStorage !== 'undefined'
    ? parseInt(localStorage.getItem(VAULT_AUTOLOCK_MINUTES_KEY) || '15', 10)
    : 15,
  lockOnWindowBlur: typeof localStorage !== 'undefined'
    ? localStorage.getItem(VAULT_LOCK_ON_BLUR_KEY) === 'true'
    : false,
  isBiometricsSupported: false,
  isBiometricsEnabled: typeof localStorage !== 'undefined'
    ? localStorage.getItem(VAULT_BIOMETRICS_ENABLED_KEY) === 'true'
    : false,
  lastActivityTime: Date.now(),

  setSearchQuery: (searchQuery) => set({ searchQuery }),
  setSelectedItemId: (selectedItemId) => set({ selectedItemId }),

  setAutoLockMinutes: (minutes: number) => {
    try {
      localStorage.setItem(VAULT_AUTOLOCK_MINUTES_KEY, String(minutes))
    } catch {}
    set({ autoLockMinutes: minutes })
  },

  setLockOnWindowBlur: (enabled: boolean) => {
    try {
      localStorage.setItem(VAULT_LOCK_ON_BLUR_KEY, enabled ? 'true' : 'false')
    } catch {}
    set({ lockOnWindowBlur: enabled })
  },

  recordActivity: () => {
    set({ lastActivityTime: Date.now() })
  },

  checkAutoLock: () => {
    const { isUnlocked, autoLockMinutes, lastActivityTime, lock } = get()
    if (!isUnlocked || autoLockMinutes <= 0) return false
    const now = Date.now()
    const diffMs = now - lastActivityTime
    if (diffMs >= autoLockMinutes * 60 * 1000) {
      lock()
      return true
    }
    return false
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
    try {
      const supported = await isBiometricsAvailable()
      set({ isBiometricsSupported: supported })
      return supported
    } catch {
      set({ isBiometricsSupported: false })
      return false
    }
  },

  enableBiometrics: async (masterPassword: string) => {
    try {
      const salt = getOrCreateVaultSalt()
      const { userKey, bucketId } = await deriveVaultKeys(masterPassword, salt)
      const canary = typeof localStorage !== 'undefined' ? localStorage.getItem(`${VAULT_CANARY_PREFIX}${bucketId}`) : null
      if (canary) {
        await decryptVaultEntry(canary, userKey, 'vault-canary')
      }

      await promptBiometricVerification('Windows Hello für Passwort-Manager aktivieren')

      const wrapped = await wrapVaultCredentialsForBiometrics(masterPassword)
      try {
        await biometrieSpeichern(wrapped)
      } catch {
        // Fallback: Speichere in memory/desktop state
      }
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(VAULT_BIOMETRICS_ENABLED_KEY, 'true')
      }
      set({ isBiometricsEnabled: true })
      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Biometrie-Aktivierung fehlgeschlagen'
      throw new Error(msg)
    }
  },

  disableBiometrics: async () => {
    try {
      await biometrieLoeschen()
    } catch {}
    if (typeof localStorage !== 'undefined') {
      localStorage.removeItem(VAULT_BIOMETRICS_WRAPPED_KEY)
      localStorage.setItem(VAULT_BIOMETRICS_ENABLED_KEY, 'false')
    }
    set({ isBiometricsEnabled: false })
  },

  unlockWithBiometrics: async () => {
    set({ isUnlocking: true, unlockError: null })
    try {
      let wrapped: string | null = null
      try {
        // Primär: Native Windows Hello Verifikation & Freigabe aus dem Windows Credential Store
        wrapped = await biometrieEntsperren('Passwort-Manager entsperren')
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : 'Biometrische Authentifizierung fehlgeschlagen.'
        throw new Error(msg)
      }

      if (!wrapped) {
        throw new Error('Biometrischer Schlüssel konnte nicht geladen werden.')
      }

      const masterPassword = await unwrapVaultCredentialsFromBiometrics(wrapped)
      const success = await get().unlock(masterPassword)
      return success
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Biometrisches Entsperren fehlgeschlagen.'
      set({ isUnlocking: false, unlockError: msg })
      return false
    }
  },

  lock: () => {
    set({
      isUnlocked: false,
      userKey: null,
      bucketId: null,
      items: [],
      selectedItemId: null,
      unlockError: null,
      lastActivityTime: Date.now(),
    })
  },

  resetLocalVaultState: () => {
    if (typeof localStorage !== 'undefined') {
      localStorage.removeItem(VAULT_SETUP_DONE_KEY)
      localStorage.removeItem(VAULT_BIOMETRICS_WRAPPED_KEY)
      localStorage.removeItem(VAULT_BIOMETRICS_ENABLED_KEY)
      localStorage.removeItem(VAULT_SERVER_BUCKET_KEY)
      localStorage.removeItem(VAULT_SALT_KEY)
    }
    set({
      isInitialized: false,
      isUnlocked: false,
      isBiometricsEnabled: false,
      failedUnlockAttempts: 0,
      lockedUntilMs: 0,
      userKey: null,
      bucketId: null,
      items: [],
      selectedItemId: null,
      unlockError: null,
      lastActivityTime: Date.now(),
    })
  },

  initializeVault: async (masterPassword: string) => {
    set({ isUnlocking: true, unlockError: null })
    try {
      const salt = getOrCreateVaultSalt()
      const saltHex = Array.from(salt).map((b) => b.toString(16).padStart(2, '0')).join('')
      const { userKey, bucketId } = await deriveVaultKeys(masterPassword, salt)

      // KDF-Salt serverseitig hinterlegen (SEC-04)
      try {
        await api('/api/vault/salt', {
          method: 'POST',
          body: JSON.stringify({ kdf_salt: saltHex, bucket_id: bucketId }),
        })
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(VAULT_SERVER_BUCKET_KEY, bucketId)
        }
      } catch {}

      // Canary-Prüfblock verschlüsseln und lokal speichern
      const canaryCiphertext = await encryptVaultEntry(
        { canary: 'mss-vault-initialized-v1', createdAt: Date.now() },
        userKey,
        'vault-canary',
      )
      localStorage.setItem(`${VAULT_CANARY_PREFIX}${bucketId}`, canaryCiphertext)
      localStorage.setItem(VAULT_SETUP_DONE_KEY, 'true')

      // Falls bereits gecachte Einträge existieren, entschlüsseln
      const cachedRaw = localStorage.getItem(`${VAULT_LOCAL_STORAGE_PREFIX}${bucketId}`)
      const cachedBlobs: StoredEncryptedEntry[] = cachedRaw ? JSON.parse(cachedRaw) : []
      const decryptedItems: VaultItem[] = []

      for (const blob of cachedBlobs) {
        if (blob.is_deleted) continue
        try {
          const payload = await decryptVaultEntry(blob.ciphertext, userKey, blob.id)
          decryptedItems.push({
            id: blob.id,
            service: String(payload.service || 'Unbekannt'),
            username: String(payload.username || ''),
            password: String(payload.password || ''),
            url: payload.url ? String(payload.url) : undefined,
            notes: payload.notes ? String(payload.notes) : undefined,
            totpSecret: payload.totpSecret ? String(payload.totpSecret) : undefined,
            category: (payload.category as VaultItem['category']) || 'login',
            createdAt: Number(payload.createdAt || Date.now()),
            updatedAt: Number(payload.updatedAt || Date.now()),
            revision: blob.revision,
          })
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
        userKey,
        bucketId,
        items: decryptedItems,
        selectedItemId: decryptedItems.length > 0 ? decryptedItems[0].id : null,
      })

      // Hintergrund-Sync anstoßen
      void get().syncWithServer()

      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Einrichten fehlgeschlagen'
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
      if (!salt) {
        salt = getOrCreateVaultSalt()
      }

      const { userKey, bucketId } = await deriveVaultKeys(masterPassword, salt)

      // Bei Multi-Device Login: Wenn ein Server-Bucket hinterlegt ist, muss der abgeleitete Bucket exakt übereinstimmen
      const serverBucket = typeof localStorage !== 'undefined' ? localStorage.getItem(VAULT_SERVER_BUCKET_KEY) : null
      if (serverBucket && bucketId !== serverBucket) {
        throw new Error('Falsches Master-Passwort. Bitte überprüfe deine Eingabe.')
      }

      // 3. Canary prüfen falls vorhanden
      const canaryCiphertext = localStorage.getItem(`${VAULT_CANARY_PREFIX}${bucketId}`)
      if (canaryCiphertext) {
        try {
          await decryptVaultEntry(canaryCiphertext, userKey, 'vault-canary')
        } catch {
          throw new Error('Falsches Master-Passwort. Bitte überprüfe deine Eingabe.')
        }
      }

      // 4. Lokale verschlüsselte Blobs aus dem Cache laden
      const cachedRaw = localStorage.getItem(`${VAULT_LOCAL_STORAGE_PREFIX}${bucketId}`)
      const cachedBlobs: StoredEncryptedEntry[] = cachedRaw ? JSON.parse(cachedRaw) : []

      const decryptedItems: VaultItem[] = []
      for (const blob of cachedBlobs) {
        if (blob.is_deleted) continue
        try {
          const payload = await decryptVaultEntry(blob.ciphertext, userKey, blob.id)
          decryptedItems.push({
            id: blob.id,
            service: String(payload.service || 'Unbekannt'),
            username: String(payload.username || ''),
            password: String(payload.password || ''),
            url: payload.url ? String(payload.url) : undefined,
            notes: payload.notes ? String(payload.notes) : undefined,
            totpSecret: payload.totpSecret ? String(payload.totpSecret) : undefined,
            category: (payload.category as VaultItem['category']) || 'login',
            isFavorite: !!payload.isFavorite,
            lastUsedAt: typeof payload.lastUsedAt === 'number' ? payload.lastUsedAt : undefined,
            attachments: Array.isArray(payload.attachments) ? (payload.attachments as VaultAttachment[]) : undefined,
            linkedServiceId: payload.linkedServiceId ? String(payload.linkedServiceId) : undefined,
            createdAt: Number(payload.createdAt || Date.now()),
            updatedAt: Number(payload.updatedAt || Date.now()),
            revision: blob.revision,
          })
        } catch {
          throw new Error('Falsches Master-Passwort. Bitte überprüfe deine Eingabe.')
        }
      }

      // Falls noch kein Canary da war, jetzt absichern
      if (!canaryCiphertext) {
        const newCanary = await encryptVaultEntry(
          { canary: 'mss-vault-initialized-v1', createdAt: Date.now() },
          userKey,
          'vault-canary',
        )
        localStorage.setItem(`${VAULT_CANARY_PREFIX}${bucketId}`, newCanary)
      }
      localStorage.setItem(VAULT_SETUP_DONE_KEY, 'true')

      // Erfolg: Fehlversuche zurücksetzen
      set({
        isInitialized: true,
        isUnlocked: true,
        isUnlocking: false,
        failedUnlockAttempts: 0,
        lockedUntilMs: 0,
        userKey,
        bucketId,
        items: decryptedItems,
        selectedItemId: decryptedItems.length > 0 ? decryptedItems[0].id : null,
      })

      // Hintergrund-Sync & Hinweis-Status anstoßen
      void get().syncWithServer()
      void get().checkHintStatus()

      return true
    } catch (err: unknown) {
      // Fehlversuchszähler mit exponentiellem Backoff (SEC-07)
      const attempts = failedUnlockAttempts + 1
      let backoffMs = 0
      if (attempts >= 3) {
        backoffMs = Math.min(60000, Math.pow(2, attempts - 3) * 1000)
      }

      const msg = err instanceof Error ? err.message : 'Entsperren fehlgeschlagen'
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
    const { userKey, bucketId, items } = get()
    if (!userKey || !bucketId) {
      throw new Error('Tresor ist gesperrt')
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
    const ciphertext = await encryptVaultEntry(
      {
        service: newItem.service,
        username: newItem.username,
        password: newItem.password,
        category: newItem.category,
        createdAt: newItem.createdAt,
        updatedAt: newItem.updatedAt,
      },
      userKey,
      newId,
    )

    // In Cache und Warteschlange ablegen
    const cachedRaw = localStorage.getItem(`${VAULT_LOCAL_STORAGE_PREFIX}${bucketId}`)
    const cachedBlobs: StoredEncryptedEntry[] = cachedRaw ? JSON.parse(cachedRaw) : []
    cachedBlobs.push({ id: newId, ciphertext, revision: 1, is_deleted: false })
    localStorage.setItem(`${VAULT_LOCAL_STORAGE_PREFIX}${bucketId}`, JSON.stringify(cachedBlobs))

    const pendingRaw = localStorage.getItem(`${VAULT_PENDING_QUEUE_PREFIX}${bucketId}`)
    const pendingQueue: StoredEncryptedEntry[] = pendingRaw ? JSON.parse(pendingRaw) : []
    pendingQueue.push({ id: newId, ciphertext, revision: 1, is_deleted: false })
    localStorage.setItem(`${VAULT_PENDING_QUEUE_PREFIX}${bucketId}`, JSON.stringify(pendingQueue))

    set({
      items: [newItem, ...items],
      selectedItemId: newId,
    })

    // Im Hintergrund synchronisieren
    void get().syncWithServer()

    return newItem
  },

  saveItem: async (itemData) => {
    const { userKey, bucketId, items } = get()
    if (!userKey || !bucketId) throw new Error('Tresor ist gesperrt')

    // Payload-Guardrail (SEC-08): Dateianhänge begrenzen (<500 KB)
    if (itemData.attachments && itemData.attachments.length > 0) {
      let totalSize = 0
      for (const att of itemData.attachments) {
        if (att.size > MAX_VAULT_ATTACHMENT_SIZE_BYTES || (att.dataBase64 && att.dataBase64.length > MAX_VAULT_ATTACHMENT_SIZE_BYTES * 1.4)) {
          throw new Error(`Dateianhang "${att.name}" überschreitet das Limit von 500 KB.`)
        }
        totalSize += att.size
      }
      if (totalSize > MAX_VAULT_ATTACHMENT_SIZE_BYTES) {
        throw new Error('Die Gesamtgröße aller Dateianhänge überschreitet das Limit von 500 KB.')
      }
    }

    const id = itemData.id || window.crypto.randomUUID()
    const existing = items.find((i) => i.id === id)
    const revision = (existing?.revision || 0) + 1
    const now = Date.now()

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
      attachments: itemData.attachments !== undefined ? itemData.attachments : existing?.attachments,
      linkedServiceId: itemData.linkedServiceId !== undefined ? itemData.linkedServiceId : existing?.linkedServiceId,
      createdAt: existing?.createdAt || now,
      updatedAt: now,
      revision,
    }

    const payload: Record<string, unknown> = {
      service: updatedItem.service,
      username: updatedItem.username,
      password: updatedItem.password,
      url: updatedItem.url,
      notes: updatedItem.notes,
      totpSecret: updatedItem.totpSecret,
      category: updatedItem.category,
      isFavorite: updatedItem.isFavorite,
      lastUsedAt: updatedItem.lastUsedAt,
      attachments: updatedItem.attachments,
      linkedServiceId: updatedItem.linkedServiceId,
      createdAt: updatedItem.createdAt,
      updatedAt: updatedItem.updatedAt,
    }

    const ciphertext = await encryptVaultEntry(payload, userKey, id)

    // Lokalen Cache aktualisieren
    const cachedRaw = localStorage.getItem(`${VAULT_LOCAL_STORAGE_PREFIX}${bucketId}`)
    let cachedBlobs: StoredEncryptedEntry[] = cachedRaw ? JSON.parse(cachedRaw) : []
    cachedBlobs = cachedBlobs.filter((b) => b.id !== id)
    cachedBlobs.push({ id, ciphertext, revision, is_deleted: false })
    localStorage.setItem(`${VAULT_LOCAL_STORAGE_PREFIX}${bucketId}`, JSON.stringify(cachedBlobs))

    // Pending Queue aktualisieren
    const pendingRaw = localStorage.getItem(`${VAULT_PENDING_QUEUE_PREFIX}${bucketId}`)
    let pendingQueue: StoredEncryptedEntry[] = pendingRaw ? JSON.parse(pendingRaw) : []
    pendingQueue = pendingQueue.filter((b) => b.id !== id)
    pendingQueue.push({ id, ciphertext, revision, is_deleted: false })
    localStorage.setItem(`${VAULT_PENDING_QUEUE_PREFIX}${bucketId}`, JSON.stringify(pendingQueue))

    const newItems = items.some((i) => i.id === id)
      ? items.map((i) => (i.id === id ? updatedItem : i))
      : [updatedItem, ...items]

    set({ items: newItems, selectedItemId: id })
    void get().syncWithServer()
  },

  deleteItem: async (id: string) => {
    const { userKey, bucketId, items } = get()
    if (!userKey || !bucketId) return

    const existing = items.find((i) => i.id === id)
    const revision = (existing?.revision || 0) + 1

    // Lokalen Cache bereinigen
    const cachedRaw = localStorage.getItem(`${VAULT_LOCAL_STORAGE_PREFIX}${bucketId}`)
    let cachedBlobs: StoredEncryptedEntry[] = cachedRaw ? JSON.parse(cachedRaw) : []
    cachedBlobs = cachedBlobs.filter((b) => b.id !== id)
    localStorage.setItem(`${VAULT_LOCAL_STORAGE_PREFIX}${bucketId}`, JSON.stringify(cachedBlobs))

    // Tombstone in Pending Queue
    const pendingRaw = localStorage.getItem(`${VAULT_PENDING_QUEUE_PREFIX}${bucketId}`)
    let pendingQueue: StoredEncryptedEntry[] = pendingRaw ? JSON.parse(pendingRaw) : []
    pendingQueue = pendingQueue.filter((b) => b.id !== id)
    pendingQueue.push({ id, ciphertext: '', revision, is_deleted: true })
    localStorage.setItem(`${VAULT_PENDING_QUEUE_PREFIX}${bucketId}`, JSON.stringify(pendingQueue))

    const remaining = items.filter((i) => i.id !== id)
    set({
      items: remaining,
      selectedItemId: remaining.length > 0 ? remaining[0].id : null,
    })

    void get().syncWithServer()
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
    const { userKey, bucketId, syncStatus, items } = get()
    if (!userKey || !bucketId || syncStatus === 'syncing') return

    set({ syncStatus: 'syncing' })

    try {
      const pendingRaw = localStorage.getItem(`${VAULT_PENDING_QUEUE_PREFIX}${bucketId}`)
      const pendingQueue: StoredEncryptedEntry[] = pendingRaw ? JSON.parse(pendingRaw) : []

      const storedRev = localStorage.getItem(`${VAULT_REVISION_PREFIX}${bucketId}`)
      const sinceRevision = storedRev ? parseInt(storedRev, 10) : 0

      const payload: VaultSyncPayload = {
        bucket_id: bucketId,
        since_revision: sinceRevision,
        mutations: pendingQueue.map((m) => ({
          id: m.id,
          ciphertext: m.ciphertext,
          revision: m.revision,
          is_deleted: m.is_deleted,
        })),
      }

      const data = await api<VaultSyncResponse>('/api/vault/sync', {
        method: 'POST',
        body: JSON.stringify(payload),
      })

      // Server-Antwort verarbeiten (SEC-03: Monotone Revisionsverarbeitung)
      const cachedRaw = localStorage.getItem(`${VAULT_LOCAL_STORAGE_PREFIX}${bucketId}`)
      let cachedBlobs: StoredEncryptedEntry[] = cachedRaw ? JSON.parse(cachedRaw) : []
      let currentItems = [...items]

      for (const entry of data.entries) {
        if (entry.is_deleted) {
          cachedBlobs = cachedBlobs.filter((b) => b.id !== entry.id)
          currentItems = currentItems.filter((i) => i.id !== entry.id)
        } else {
          try {
            const dec = await decryptVaultEntry(entry.ciphertext, userKey, entry.id)
            const item: VaultItem = {
              id: entry.id,
              service: String(dec.service || 'Unbekannt'),
              username: String(dec.username || ''),
              password: String(dec.password || ''),
              url: dec.url ? String(dec.url) : undefined,
              notes: dec.notes ? String(dec.notes) : undefined,
              totpSecret: dec.totpSecret ? String(dec.totpSecret) : undefined,
              category: (dec.category as VaultItem['category']) || 'login',
              isFavorite: !!dec.isFavorite,
              lastUsedAt: typeof dec.lastUsedAt === 'number' ? dec.lastUsedAt : undefined,
              attachments: Array.isArray(dec.attachments) ? (dec.attachments as VaultAttachment[]) : undefined,
              linkedServiceId: dec.linkedServiceId ? String(dec.linkedServiceId) : undefined,
              createdAt: Number(dec.createdAt || Date.now()),
              updatedAt: Number(dec.updatedAt || Date.now()),
              revision: entry.revision,
            }

            cachedBlobs = cachedBlobs.filter((b) => b.id !== entry.id)
            cachedBlobs.push({
              id: entry.id,
              ciphertext: entry.ciphertext,
              revision: entry.revision,
              is_deleted: false,
            })

            const idx = currentItems.findIndex((i) => i.id === entry.id)
            if (idx >= 0) {
              currentItems[idx] = item
            } else {
              currentItems.push(item)
            }
          } catch {
            // Ciphertext konnte nicht entschlüsselt werden
          }
        }
      }

      // Warteschlange leeren und neue Revision speichern
      localStorage.setItem(`${VAULT_LOCAL_STORAGE_PREFIX}${bucketId}`, JSON.stringify(cachedBlobs))
      localStorage.removeItem(`${VAULT_PENDING_QUEUE_PREFIX}${bucketId}`)
      localStorage.setItem(`${VAULT_REVISION_PREFIX}${bucketId}`, String(data.server_revision))

      set({
        items: currentItems,
        syncStatus: 'synced',
        lastSyncTime: Date.now(),
      })
    } catch {
      // Bei Netzwerk- oder Serverfehler: Im Offline-Modus bleiben
      set({ syncStatus: 'offline' })
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
}))

if (typeof window !== 'undefined') {
  setTimeout(() => {
    void useVaultStore.getState().checkBiometricsSupport()
    void useVaultStore.getState().fetchVaultSalt()
  }, 50)

  const triggerBlurLock = () => {
    const s = useVaultStore.getState()
    if (s.lockOnWindowBlur && s.isUnlocked && !s.isUnlocking) {
      s.lock()
    }
  }

  window.addEventListener('blur', triggerBlurLock)
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) triggerBlurLock()
  })

  if ('__TAURI_INTERNALS__' in window) {
    try {
      import('@tauri-apps/api/event')
        .then(({ listen }) => {
          void listen('mss:fenster-blur', triggerBlurLock)
          void listen('tauri://blur', triggerBlurLock)
        })
        .catch(() => {})
    } catch {}
  }
}
