/**
 * Die lokale Ablage eines Kontos: Notizen, Termine und die Warteschlange, dazu
 * die Hinweise des Messengers und der Zwischenspeicher der Kontaktliste.
 *
 * Bis 27.09.2026 lagen die Einträge unter festen Schlüsseln im localStorage,
 * ohne Konto. Endete eine Sitzung ohne Abmelden und meldete sich im selben
 * Browser ein anderes Konto an, sah es die Termine des ersten im Klartext, und
 * dessen ungesendete Änderungen hätten mit der neuen Anmeldung hinausgehen
 * können. Beim Messenger blieb es bis 29.09.2026 so: das Verzeichnis der
 * Mailboxen mit den Namen der Kontakte, die Namen gesperrter Profile,
 * Stummschaltungen und Anheftungen gingen an das nächste Konto über.
 *
 * Deshalb steht neben der Ablage, wem sie gehört. Gehört sie einem anderen
 * Konto als dem angemeldeten, wird sie geleert, bei der Anmeldung
 * (`saveCachedUser`) und spätestens vor dem ersten Zugriff. Ohne Anmeldung gibt
 * es keinen Zugriff. Bewusstes Abmelden leert sie (`authStore.logout`); eine
 * bloß abgelaufene Sitzung lässt sie an ihr Konto gebunden liegen.
 *
 * Diese Datei importiert nur `angemeldetesKonto`: `authStore` braucht
 * `leereOfflineAblage`, und `offlineSync` importiert seinerseits `authStore`.
 */

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'

export const STORAGE_KEYS = {
  NOTES: 'msm_offline_notes',
  CALENDAR: 'msm_offline_calendar',
  OUTBOX: 'msm_offline_outbox',
  LAST_SYNC: 'msm_offline_last_sync',
} as const

/** Die Hinweise des Messengers (`stores/messengerNotificationStore.ts`). */
export const MESSENGER_KEYS = {
  MUTES: 'msm:chat_mutes',
  BLOCKS: 'msm:chat_blocks',
  BLOCKED_PROFILES: 'msm:chat_blocked_profiles',
  UNREAD: 'msm:chat_unread',
  MAILBOX_DIR: 'msm:chat_mailbox_dir',
  PINS: 'msm:chat_pins',
  ARCHIVE: 'msm:chat_archive',
  MENTIONS: 'msm:chat_mentions',
} as const

/** Zwischenspeicher der Kontaktliste (`hooks/useKontaktdaten.ts`). Er kommt vom Server wieder. */
export const KONTAKTE_CACHE_KEY = 'msm:chat_contacts_cache'

const INHABER = 'msm_offline_konto'
const ALTE_SCHLUESSEL = ['msm_outbox_replay_lease']
const ALLE_SCHLUESSEL = [
  ...Object.values(STORAGE_KEYS),
  ...Object.values(MESSENGER_KEYS),
  KONTAKTE_CACHE_KEY,
  ...ALTE_SCHLUESSEL,
  INHABER,
]

// Ersatz, falls localStorage fehlt oder gesperrt ist.
let ersatz: Record<string, string> = {}

function speicher(): Storage | null {
  try {
    if (typeof window !== 'undefined' && window.localStorage) return window.localStorage
  } catch {
    // gesperrt (Datenschutzmodus): Ersatz nehmen
  }
  return null
}

function lies(schluessel: string): string | null {
  const s = speicher()
  if (s) {
    try {
      return s.getItem(schluessel)
    } catch {
      // Ersatz
    }
  }
  return ersatz[schluessel] ?? null
}

function schreib(schluessel: string, wert: string): void {
  const s = speicher()
  if (s) {
    try {
      s.setItem(schluessel, wert)
      return
    } catch {
      // Ersatz
    }
  }
  ersatz[schluessel] = wert
}

/** Leert die Ablage samt Inhabervermerk. */
export function leereOfflineAblage(): void {
  ersatz = {}
  const s = speicher()
  if (!s) return
  try {
    for (const schluessel of ALLE_SCHLUESSEL) {
      s.removeItem(schluessel)
    }
  } catch {
    // nichts zu tun
  }
}

/** Gehört die Ablage dem angemeldeten Konto? Wenn nicht, wird sie ihm geleert übergeben. */
export function gehoertDemKonto(): boolean {
  const konto = angemeldetesKonto()
  if (konto === null) return false
  if (lies(INHABER) !== String(konto)) {
    leereOfflineAblage()
    schreib(INHABER, String(konto))
  }
  return true
}

export function getStorageItem(schluessel: string): string | null {
  return gehoertDemKonto() ? lies(schluessel) : null
}

export function setStorageItem(schluessel: string, wert: string): void {
  if (gehoertDemKonto()) schreib(schluessel, wert)
}
