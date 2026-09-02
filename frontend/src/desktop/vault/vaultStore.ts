import { create } from 'zustand'
import { api } from '@/api/client'
import {
  decryptVaultEntry,
  deriveVaultKeys,
  encryptVaultEntry,
  generateSecurePassword,
} from './vaultCrypto'

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

function getOrCreateVaultSalt(): Uint8Array {
  const existing = localStorage.getItem(VAULT_SALT_KEY)
  if (existing) {
    const bytes = new Uint8Array(existing.length / 2)
    for (let i = 0; i < bytes.length; i++) {
      bytes[i] = parseInt(existing.substr(i * 2, 2), 16)
    }
    return bytes
  }
  const newSalt = new Uint8Array(16)
  window.crypto.getRandomValues(newSalt)
  const hex = Array.from(newSalt)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
  localStorage.setItem(VAULT_SALT_KEY, hex)
  return newSalt
}

export type SyncStatus = 'synced' | 'syncing' | 'offline' | 'error'

interface VaultState {
  isInitialized: boolean
  isUnlocked: boolean
  isUnlocking: boolean
  unlockError: string | null
  userKey: CryptoKey | null
  bucketId: string | null
  items: VaultItem[]
  selectedItemId: string | null
  searchQuery: string
  syncStatus: SyncStatus
  lastSyncTime: number | null

  // Aktionen
  initializeVault: (masterPassword: string) => Promise<boolean>
  unlock: (masterPassword: string) => Promise<boolean>
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
}

export const useVaultStore = create<VaultState>((set, get) => ({
  isInitialized: typeof localStorage !== 'undefined' ? !!localStorage.getItem(VAULT_SETUP_DONE_KEY) : false,
  isUnlocked: false,
  isUnlocking: false,
  unlockError: null,
  userKey: null,
  bucketId: null,
  items: [],
  selectedItemId: null,
  searchQuery: '',
  syncStatus: 'synced',
  lastSyncTime: null,

  setSearchQuery: (searchQuery) => set({ searchQuery }),
  setSelectedItemId: (selectedItemId) => set({ selectedItemId }),

  lock: () => {
    set({
      isUnlocked: false,
      userKey: null,
      bucketId: null,
      items: [],
      selectedItemId: null,
      unlockError: null,
    })
  },

  resetLocalVaultState: () => {
    localStorage.removeItem(VAULT_SETUP_DONE_KEY)
    set({
      isInitialized: false,
      isUnlocked: false,
      userKey: null,
      bucketId: null,
      items: [],
      selectedItemId: null,
      unlockError: null,
    })
  },

  initializeVault: async (masterPassword: string) => {
    set({ isUnlocking: true, unlockError: null })
    try {
      const salt = getOrCreateVaultSalt()
      const { userKey, bucketId } = await deriveVaultKeys(masterPassword, salt)

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
    set({ isUnlocking: true, unlockError: null })
    try {
      const salt = getOrCreateVaultSalt()
      const { userKey, bucketId } = await deriveVaultKeys(masterPassword, salt)

      // 1. Canary prüfen falls vorhanden
      const canaryCiphertext = localStorage.getItem(`${VAULT_CANARY_PREFIX}${bucketId}`)
      if (canaryCiphertext) {
        try {
          await decryptVaultEntry(canaryCiphertext, userKey, 'vault-canary')
        } catch {
          throw new Error('Falsches Master-Passwort. Bitte überprüfe deine Eingabe.')
        }
      }

      // 2. Lokale verschlüsselte Blobs aus dem Cache laden
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

      set({
        isInitialized: true,
        isUnlocked: true,
        isUnlocking: false,
        userKey,
        bucketId,
        items: decryptedItems,
        selectedItemId: decryptedItems.length > 0 ? decryptedItems[0].id : null,
      })

      // Hintergrund-Sync anstoßen
      void get().syncWithServer()

      return true
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Entsperren fehlgeschlagen'
      set({ isUnlocking: false, unlockError: msg })
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

      // Server-Antwort verarbeiten
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
}))
