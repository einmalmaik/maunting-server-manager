/**
 * Version Service
 *
 * Fetches the latest GitHub release tag with caching and robust fallbacks.
 * Completely decoupled from UI components.
 */

const GITHUB_OWNER = 'einmalmaik'
const GITHUB_REPO = 'maunting-server-manager'
const GITHUB_API_URL = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/releases/latest`

const CACHE_KEY = 'msm_version_cache'
const CACHE_TTL_MS = 60 * 60 * 1000 // 1 hour

/** Static fallback when GitHub is unreachable and no cache exists. */
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
    // Ignore malformed cache
  }
  return null
}

function writeCache(version: string): void {
  try {
    const entry: VersionCacheEntry = { version, fetchedAt: Date.now() }
    localStorage.setItem(CACHE_KEY, JSON.stringify(entry))
  } catch {
    // Ignore localStorage errors (e.g. private mode quota exceeded)
  }
}

function isCacheValid(entry: VersionCacheEntry): boolean {
  return Date.now() - entry.fetchedAt < CACHE_TTL_MS
}

interface GitHubRelease {
  tag_name?: string
}

async function fetchFromGitHub(): Promise<string | null> {
  try {
    const response = await fetch(GITHUB_API_URL, {
      method: 'GET',
      headers: {
        Accept: 'application/vnd.github+json',
      },
    })

    if (response.status === 404) {
      // No releases published yet
      return null
    }

    if (response.status === 403 || response.status === 429) {
      // Rate limited — let caller fall back to cache/default
      return null
    }

    if (!response.ok) {
      return null
    }

    const data = (await response.json()) as GitHubRelease
    const tag = data.tag_name?.trim()
    if (tag && tag.length > 0) {
      return tag
    }
  } catch {
    // Network error, offline, CORS issue, etc.
  }
  return null
}

/**
 * Returns the latest version string.
 *
 * Strategy:
 * 1. Return cached value if still within TTL.
 * 2. Try fetching from GitHub API.
 * 3. On API failure, return stale cache if available.
 * 4. If nothing else works, return {@link DEFAULT_VERSION}.
 */
export async function getVersion(): Promise<string> {
  const cache = readCache()

  if (cache && isCacheValid(cache)) {
    return cache.version
  }

  const live = await fetchFromGitHub()
  if (live) {
    writeCache(live)
    return live
  }

  // API failed: fall back to stale cache, then default
  if (cache) {
    return cache.version
  }

  return DEFAULT_VERSION
}

/**
 * Synchronous cache-only peek. Useful for initial renders
 * where an async fetch would cause a layout shift.
 */
export function getCachedVersion(): string {
  const cache = readCache()
  if (cache) {
    return cache.version
  }
  return DEFAULT_VERSION
}
