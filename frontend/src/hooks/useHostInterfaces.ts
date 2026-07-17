import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import type { HostInterface, HostInterfacesResponse } from '@/types'

/**
 * Laedt die verfuegbaren Host-Interfaces (IPv4) fuer das Bind-IP-Dropdown.
 * Nur Owner duerfen den Endpunkt aufrufen — bei 403 bleibt die Liste leer.
 */
export function useHostInterfaces(nodeId?: string | number | null) {
  const [interfaces, setInterfaces] = useState<HostInterface[]>([])
  const [defaultBindIp, setDefaultBindIp] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        const url = nodeId ? `/nodes/${nodeId}/interfaces` : '/system/interfaces'
        const res = await api<HostInterfacesResponse>(url)
        if (cancelled) return
        setInterfaces(res.interfaces || [])
        setDefaultBindIp(res.default_bind_ip || null)
        setError(null)
      } catch (e: any) {
        if (cancelled) return
        setInterfaces([])
        setDefaultBindIp(null)
        setError(e.message || 'unknown')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [nodeId])

  return { interfaces, defaultBindIp, loading, error }
}
