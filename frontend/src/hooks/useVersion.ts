import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import type { VersionInfo } from '@/types'
import { getCachedVersion, getVersion } from '@/services/versionService'

/** Anzeige im Footer: installierte Version (git describe / .version), nicht GitHub-latest. */
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
    return cached !== 'v1.0.0' ? cached : ''
  })

  useEffect(() => {
    let cancelled = false

    api<VersionInfo>('/system/version')
      .then((info) => {
        if (cancelled) return
        const label = formatInstalledVersion(info.current_version)
        if (label) {
          setVersion(label)
          return
        }
        return getVersion()
      })
      .then((fallback) => {
        if (cancelled || fallback === undefined) return
        if (typeof fallback === 'string' && fallback) {
          setVersion(fallback.startsWith('v') ? fallback : `v${fallback}`)
        }
      })
      .catch(() => {
        if (cancelled) return
        getVersion().then((v) => {
          if (!cancelled && v) {
            setVersion(v.startsWith('v') ? v : `v${v}`)
          }
        })
      })

    return () => {
      cancelled = true
    }
  }, [])

  return version
}