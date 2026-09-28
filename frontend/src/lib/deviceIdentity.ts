/**
 * Eindeutige Kennung und Gerätetyp für diese Clientsitzung.
 *
 * Ermöglicht die Unterscheidung mehrerer offener Tabs, des Desktop-Clients und der APK
 * für geräteübergreifendes Anruf-Handoff.
 */

let instanceDeviceId: string | null = null

export function getDeviceId(): string {
  if (instanceDeviceId) return instanceDeviceId
  if (typeof window === 'undefined') return 'server'
  try {
    const runId = Math.random().toString(36).slice(2, 8)
    instanceDeviceId = `dev-${runId}-${crypto.randomUUID().slice(0, 8)}`
    return instanceDeviceId
  } catch {
    return 'fallback-device'
  }
}

export type DeviceType = 'web' | 'desktop' | 'mobile'

export function getDeviceType(): DeviceType {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return 'web'

  const ua = navigator.userAgent || ''
  const isAndroid = /Android/i.test(ua)
  const isIOS = /iPhone|iPad|iPod/i.test(ua)
  const isTauri =
    (window as unknown as { __TAURI__?: unknown }).__TAURI__ !== undefined ||
    ua.includes('Tauri')

  if (isTauri && !isAndroid && !isIOS) return 'desktop'
  if (isAndroid || isIOS || /webOS|BlackBerry|IEMobile|Opera Mini/i.test(ua)) return 'mobile'
  if (isTauri) return 'desktop'

  return 'web'
}

/**
 * Der Schlüssel zum Gerätetyp. Die drei Namen standen früher an drei Stellen
 * in drei Fassungen — „Smartphone / APK" hier, „Mobile App" im Abzeichen,
 * „Mobile" in dessen Beschriftung. Sie kommen jetzt alle aus `social.device.*`.
 */
export function deviceLabelKey(deviceType?: string | null): string {
  switch (deviceType) {
    case 'mobile':
      return 'social.device.mobile'
    case 'desktop':
      return 'social.device.desktop'
    case 'web':
    default:
      return 'social.device.web'
  }
}
