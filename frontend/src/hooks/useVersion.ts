import { useEffect, useState } from 'react'
import { getCachedVersion, getVersion } from '@/services/versionService'

export function useVersion() {
  const [version, setVersion] = useState<string>(getCachedVersion)

  useEffect(() => {
    let cancelled = false

    getVersion().then((v) => {
      if (!cancelled) {
        setVersion(v)
      }
    })

    return () => {
      cancelled = true
    }
  }, [])

  return version
}
