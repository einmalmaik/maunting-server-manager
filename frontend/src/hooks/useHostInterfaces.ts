import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import type { HostInterface, HostInterfacesResponse } from '@/types'

/**
 * Laedt die verfuegbaren Host-Interfaces (IPv4) fuer das Bind-IP-Dropdown.
 * Nur Owner duerfen den Endpunkt aufrufen — bei 403 bleibt die Liste leer.
 *
 * `enabled` erlaubt Aufrufern, die die Liste nur in einem Dialog brauchen, das
 * Laden bis zum Öffnen aufzuschieben. Ohne das kostet jeder Seitenaufruf eine
 * Anfrage für Daten, die niemand sieht — und für Benutzer ohne `system.view`
 * zusätzlich einen 403-Eintrag im Log.
 */
export function useHostInterfaces(nodeId?: string | number | null, enabled = true) {
  const [interfaces, setInterfaces] = useState<HostInterface[]>([])
  const [defaultBindIp, setDefaultBindIp] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setInterfaces([])
      setDefaultBindIp(null)
      setError(null)
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
  }, [nodeId, enabled])

  return { interfaces, defaultBindIp, loading, error }
}
