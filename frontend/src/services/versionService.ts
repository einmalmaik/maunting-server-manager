/**
 * Version Service
 *
 * Ruft die installierte bzw. aktuelle Version ausschließlich vom eigenen
 * Backend-Server ab (/api/system/version) und puffert sie im localStorage.
 *
 * Datenschutz-Garantie:
 * Der Browser sendet zu keinem Zeitpunkt eigenständige Anfragen an GitHub oder
 * sonstige Drittanbieter. Die Versionsprüfung und eventuelle GitHub-Release-
 * Abfragen laufen ausschließlich serverseitig und gecacht im MSM-Backend.
 */

import { api } from '@/api/client'
import type { VersionInfo } from '@/types'

const CACHE_KEY = 'msm_version_cache'
const CACHE_TTL_MS = 60 * 60 * 1000 // 1 Stunde

/** Statischer Rückfallwert, falls das Backend offline und kein Cache vorhanden ist. */
export const DEFAULT_VERSION = 'v1.0.0'

interface VersionCacheEntry {
  version: string
  fetchedAt: number
}

function readCache(): VersionCacheEntry | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as VersionCacheEntry
    if (typeof parsed.version === 'string' && typeof parsed.fetchedAt === 'number') {
      return parsed
    }
  } catch {
    // Ungültigen Cache ignorieren
  }
  return null
}

function writeCache(version: string): void {
  try {
    const entry: VersionCacheEntry = { version, fetchedAt: Date.now() }
    localStorage.setItem(CACHE_KEY, JSON.stringify(entry))
  } catch {
    // localStorage-Fehler (z. B. privater Modus / Quota) ignorieren
  }
}

function isCacheValid(entry: VersionCacheEntry): boolean {
  return Date.now() - entry.fetchedAt < CACHE_TTL_MS
}

async function fetchFromBackend(): Promise<string | null> {
  try {
    const info = await api<VersionInfo>('/system/version')
    const raw = info?.current_version || info?.latest_version
    const version = raw?.trim()
    if (version && version !== 'unknown') {
      return version
    }
  } catch {
    // Backend offline oder nicht erreichbar
  }
  return null
}

/**
 * Liefert die Versionsnummer für die Benutzeroberfläche.
 *
 * Strategie:
 * 1. Gültigen Cache zurückgeben, falls vorhanden.
 * 2. Version vom eigenen Backend abfragen und cachen.
 * 3. Bei Backend-Ausfall: Veralteten Cache nutzen.
 * 4. Als letzter Ausweg: statischer DEFAULT_VERSION-Rückfall.
 */
export async function getVersion(): Promise<string> {
  const cache = readCache()

  if (cache && isCacheValid(cache)) {
    return cache.version
  }

  const live = await fetchFromBackend()
  if (live) {
    writeCache(live)
    return live
  }

  if (cache) {
    return cache.version
  }

  return DEFAULT_VERSION
}

/**
 * Synchroner Cache-Blick zur Vermeidung von Layout-Shifts beim ersten Rendern.
 */
export function getCachedVersion(): string {
  const cache = readCache()
  if (cache) {
    return cache.version
  }
  return DEFAULT_VERSION
}
