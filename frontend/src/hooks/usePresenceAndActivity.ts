import { useEffect, useRef, useState, useCallback } from 'react'
import { useLocation } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'
import { updatePresence, recordActivityTime } from '@/api/social'

export type PresenceStatus = 'online' | 'away' | 'invisible'
export type DeviceType = 'web' | 'desktop' | 'mobile'

function detectDeviceType(): DeviceType {
  if (typeof window === 'undefined') return 'web'
  const isDesktop =
    '__TAURI__' in window ||
    '__TAURI_INTERNALS__' in window ||
    navigator.userAgent.includes('MSM-Desktop') ||
    navigator.userAgent.includes('Tauri')
  if (isDesktop) return 'desktop'

  const isMobile =
    window.innerWidth < 768 ||
    /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)
  if (isMobile) return 'mobile'

  return 'web'
}

function getPresenceForRoute(pathname: string): { label: string; detail: string; category: string } {
  if (pathname.startsWith('/ai')) {
    return { label: 'Im KI-Chat', detail: 'Singra Assistent', category: 'ai_chat' }
  }
  if (pathname.startsWith('/servers/') && pathname !== '/servers') {
    return { label: 'Auf Server', detail: 'Server-Administration', category: 'server_admin' }
  }
  if (pathname.startsWith('/servers')) {
    return { label: 'Server-Übersicht', detail: 'Infrastruktur', category: 'server_admin' }
  }
  if (pathname.startsWith('/social')) {
    return { label: 'Im Social Hub', detail: 'Errungenschaften & Freunde', category: 'general' }
  }
  if (pathname.startsWith('/calendar')) {
    return { label: 'Im Kalender', detail: 'Termine & Aufgaben', category: 'general' }
  }
  if (pathname.startsWith('/notes')) {
    return { label: 'In den Notizen', detail: 'Dokumentation', category: 'general' }
  }
  if (pathname.startsWith('/settings')) {
    return { label: 'In den Einstellungen', detail: 'Systemkonfiguration', category: 'general' }
  }
  return { label: 'Im Panel', detail: 'Control Center', category: 'general' }
}

export function usePresenceAndActivity(socialEnabled: boolean = true) {
  const { user } = useAuthStore()
  const location = useLocation()
  const [status, setStatus] = useState<PresenceStatus>('online')
  const deviceTypeRef = useRef<DeviceType>(detectDeviceType())
  const lastReportedLabelRef = useRef<string>('')
  const activeSecondsRef = useRef<number>(0)
  const activeCategoryRef = useRef<string>('general')
  const lastInteractionTimeRef = useRef<number>(Date.now())

  // Presence status updater
  const changeStatus = useCallback(
    async (newStatus: PresenceStatus) => {
      setStatus(newStatus)
      if (!socialEnabled || !user) return
      try {
        const routeInfo = getPresenceForRoute(location.pathname)
        await updatePresence({
          status: newStatus,
          device_type: deviceTypeRef.current,
          activity_label: routeInfo.label,
          activity_detail: routeInfo.detail,
        })
      } catch {
        // Non-blocking
      }
    },
    [socialEnabled, user, location.pathname]
  )

  // Rich Presence sync on route changes
  useEffect(() => {
    if (!socialEnabled || !user) return

    const routeInfo = getPresenceForRoute(location.pathname)
    activeCategoryRef.current = routeInfo.category

    if (lastReportedLabelRef.current !== routeInfo.label) {
      lastReportedLabelRef.current = routeInfo.label
      updatePresence({
        status,
        device_type: deviceTypeRef.current,
        activity_label: routeInfo.label,
        activity_detail: routeInfo.detail,
      }).catch(() => {})
    }
  }, [location.pathname, socialEnabled, user, status])

  // Active interaction tracker (Playtime)
  useEffect(() => {
    if (!socialEnabled || !user) return

    const registerActivity = () => {
      lastInteractionTimeRef.current = Date.now()
    }

    window.addEventListener('pointerdown', registerActivity, { passive: true })
    window.addEventListener('keydown', registerActivity, { passive: true })
    window.addEventListener('wheel', registerActivity, { passive: true })

    // Accumulate seconds when user was active within last 45 seconds
    const ticker = setInterval(() => {
      if (document.hidden) return
      const now = Date.now()
      const isRecentlyActive = now - lastInteractionTimeRef.current < 45000

      if (isRecentlyActive) {
        activeSecondsRef.current += 1

        // Every 30 active seconds, flush to backend
        if (activeSecondsRef.current >= 30) {
          const secs = activeSecondsRef.current
          const cat = activeCategoryRef.current
          activeSecondsRef.current = 0
          recordActivityTime(cat, secs).catch(() => {})
        }
      }
    }, 1000)

    return () => {
      window.removeEventListener('pointerdown', registerActivity)
      window.removeEventListener('keydown', registerActivity)
      window.removeEventListener('wheel', registerActivity)
      clearInterval(ticker)
      // Flush remaining active seconds
      if (activeSecondsRef.current >= 10) {
        recordActivityTime(activeCategoryRef.current, activeSecondsRef.current).catch(() => {})
        activeSecondsRef.current = 0
      }
    }
  }, [socialEnabled, user])

  return {
    status,
    deviceType: deviceTypeRef.current,
    changeStatus,
  }
}
