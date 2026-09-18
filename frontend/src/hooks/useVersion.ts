import { useEffect, useState } from 'react'
import { getCachedVersion, getVersion } from '@/services/versionService'

/** Anzeige im Footer: installierte Version (git describe / .version). */
export function formatInstalledVersion(raw: string): string {
  const trimmed = raw.trim()
  if (!trimmed || trimmed === 'unknown') return ''
  const match = trimmed.match(/v?(\d+\.\d+\.\d+)/)
  if (match) return `v${match[1]}`
  return trimmed.startsWith('v') ? trimmed : `v${trimmed}`
}

export function useVersion() {
  const [version, setVersion] = useState<string>(() => {
    const cached = getCachedVersion()
    return cached && cached !== 'v1.0.0' ? formatInstalledVersion(cached) : ''
  })

  useEffect(() => {
    let cancelled = false

    getVersion()
      .then((raw) => {
        if (cancelled || !raw) return
        const label = formatInstalledVersion(raw)
        if (label) {
          setVersion(label)
        }
      })
      .catch(() => {
        // Bei Backend-Fehlern den aktuellen/gecacheten Stand beibehalten
      })

    return () => {
      cancelled = true
    }
  }, [])

  return version
}