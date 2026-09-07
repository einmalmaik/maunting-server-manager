import { useEffect, useRef, useState, useCallback } from 'react'
import { useLocation } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'
import { updatePresence, recordActivityTime } from '@/api/social'

export type PresenceStatus = 'online' | 'away' | 'invisible'
export type DeviceType = 'web' | 'desktop' | 'mobile'

export function detectDeviceType(): DeviceType {
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
  if (pathname.startsWith('/chat') || pathname.startsWith('/social')) {
    return { label: 'Im Chat', detail: 'Messenger', category: 'general' }
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

export function usePresenceAndActivity(
  socialEnabled: boolean = true,
  enableActivityTracking: boolean = true
) {
  const { user } = useAuthStore()
  const location = useLocation()
  const [status, setStatus] = useState<PresenceStatus>('online')
  const deviceTypeRef = useRef<DeviceType>(detectDeviceType())
  const lastReportedLabelRef = useRef<string>('')
  const activeSecondsRef = useRef<number>(0)
  const activeCategoryRef = useRef<string>('general')
  const lastInteractionTimeRef = useRef<number>(Date.now())

  const manualStatusRef = useRef<PresenceStatus>('online')
  const isAutoAwayRef = useRef<boolean>(false)
  const pathnameRef = useRef<string>(location.pathname)

  useEffect(() => {
    pathnameRef.current = location.pathname
  }, [location.pathname])

  // Presence status updater
  const changeStatus = useCallback(
    async (newStatus: PresenceStatus) => {
      manualStatusRef.current = newStatus
      isAutoAwayRef.current = false
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

  // Active interaction tracker & Automatic Inactivity (Away) Detection
  useEffect(() => {
    if (!socialEnabled || !enableActivityTracking || !user) return

    const INACTIVITY_TIMEOUT_MS = 300000 // 5 minutes idle

    const registerActivity = () => {
      const now = Date.now()
      lastInteractionTimeRef.current = now

      // If user was set to 'away' due to automatic inactivity, restore to 'online'
      if (isAutoAwayRef.current && manualStatusRef.current === 'online') {
        isAutoAwayRef.current = false
        setStatus('online')
        const routeInfo = getPresenceForRoute(pathnameRef.current)
        updatePresence({
          status: 'online',
          device_type: deviceTypeRef.current,
          activity_label: routeInfo.label,
          activity_detail: routeInfo.detail,
        }).catch(() => {})
      }
    }

    let lastMoveTime = 0
    const handlePointerMove = () => {
      const now = Date.now()
      if (now - lastMoveTime > 3000) {
        lastMoveTime = now
        registerActivity()
      }
    }

    window.addEventListener('pointerdown', registerActivity, { passive: true })
    window.addEventListener('keydown', registerActivity, { passive: true })
    window.addEventListener('wheel', registerActivity, { passive: true })
    window.addEventListener('touchstart', registerActivity, { passive: true })
    window.addEventListener('pointermove', handlePointerMove, { passive: true })

    const handleVisibilityChange = () => {
      if (document.hidden) {
        // Tab / window hidden: trigger away if user was online
        if (manualStatusRef.current === 'online' && !isAutoAwayRef.current) {
          isAutoAwayRef.current = true
          setStatus('away')
          const routeInfo = getPresenceForRoute(pathnameRef.current)
          updatePresence({
            status: 'away',
            device_type: deviceTypeRef.current,
            activity_label: routeInfo.label,
            activity_detail: routeInfo.detail,
          }).catch(() => {})
        }
      } else {
        registerActivity()
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)

    // Ticker to check inactivity and accumulate active seconds
    const ticker = setInterval(() => {
      const now = Date.now()
      const idleTime = now - lastInteractionTimeRef.current

      // Check auto-away condition (5 minutes of no user input)
      if (
        idleTime >= INACTIVITY_TIMEOUT_MS &&
        manualStatusRef.current === 'online' &&
        !isAutoAwayRef.current
      ) {
        isAutoAwayRef.current = true
        setStatus('away')
        const routeInfo = getPresenceForRoute(pathnameRef.current)
        updatePresence({
          status: 'away',
          device_type: deviceTypeRef.current,
          activity_label: routeInfo.label,
          activity_detail: routeInfo.detail,
        }).catch(() => {})
      }

      if (document.hidden) return
      const isRecentlyActive = idleTime < 45000

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
      window.removeEventListener('touchstart', registerActivity)
      window.removeEventListener('pointermove', handlePointerMove)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      clearInterval(ticker)
      // Flush remaining active seconds
      if (activeSecondsRef.current >= 10) {
        recordActivityTime(activeCategoryRef.current, activeSecondsRef.current).catch(() => {})
        activeSecondsRef.current = 0
      }
    }
  }, [socialEnabled, enableActivityTracking, user])

  return {
    status,
    deviceType: deviceTypeRef.current,
    changeStatus,
  }
}
