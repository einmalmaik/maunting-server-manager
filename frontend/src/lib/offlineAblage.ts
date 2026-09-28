/**
 * Die Offline-Ablage für Notizen, Termine und die Warteschlange — gebunden an
 * das angemeldete Konto.
 *
 * Bis 27.09.2026 lagen die Einträge unter festen Schlüsseln im localStorage,
 * ohne Konto. Endete eine Sitzung ohne Abmelden und meldete sich im selben
 * Browser ein anderes Konto an, sah es die Termine des ersten im Klartext, und
 * dessen ungesendete Änderungen hätten mit der neuen Anmeldung hinausgehen
 * können.
 *
 * Deshalb steht neben der Ablage, wem sie gehört. Gehört sie einem anderen
 * Konto als dem angemeldeten, wird sie vor dem ersten Zugriff geleert. Ohne
 * Anmeldung gibt es keinen Zugriff. `clearSession` leert sie außerdem beim
 * Ende jeder Sitzung.
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

const INHABER = 'msm_offline_konto'
const ALTE_SCHLUESSEL = ['msm_outbox_replay_lease']

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
    for (const schluessel of [...Object.values(STORAGE_KEYS), ...ALTE_SCHLUESSEL, INHABER]) {
      s.removeItem(schluessel)
    }
  } catch {
    // nichts zu tun
  }
}

/** Gehört die Ablage dem angemeldeten Konto? Wenn nicht, wird sie ihm geleert übergeben. */
function gehoertDemKonto(): boolean {
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
