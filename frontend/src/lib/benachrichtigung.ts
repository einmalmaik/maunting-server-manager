/**
 * Plattformübergreifender Helfer für Geräte- & Push-Benachrichtigungen.
 * 
 * Unterstützt:
 * - Tauri v2 (Windows Desktop & Android Mobile) via Rust Command `benachrichtigung_senden`
 *   und @tauri-apps/plugin-notification
 * - Web-Browser via Standard HTML5 Notification API
 * 
 * Sicherheitsinvariante:
 * - Wird nur ausgelöst, wenn der Nutzer `device_notifications` aktiv geschaltet hat
 *   (oder für System-Testläufe).
 */
import { useAuthStore } from '@/stores/authStore'

export interface BenachrichtigungOptionen {
  titel: string
  text: string
  icon?: string
  erzwingen?: boolean // Für Test-Buttons
}

/**
 * Fragt bei Bedarf die Systemberechtigung für Benachrichtigungen an (Tauri & Browser).
 */
export async function pruefeUndFrageGeraeteBerechtigung(): Promise<boolean> {
  // 1. Tauri (Android / Windows)
  if (typeof window !== 'undefined' && ('__TAURI_INTERNALS__' in window || '__TAURI__' in window)) {
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      let isGranted = false
      try {
        isGranted = await invoke<boolean>('plugin:notification|isPermissionGranted')
      } catch {
        // Fallback
      }
      if (!isGranted) {
        try {
          const permState = await invoke<string>('plugin:notification|requestPermission')
          isGranted = permState === 'granted'
        } catch {
          // Keine Unterbrechung
        }
      }
      return isGranted
    } catch {
      // Ignorieren
    }
  }

  // 2. Browser HTML5 Notification
  if (typeof window !== 'undefined' && 'Notification' in window) {
    try {
      if (Notification.permission === 'granted') {
        return true
      }
      if (Notification.permission !== 'denied') {
        const perm = await Notification.requestPermission()
        return perm === 'granted'
      }
    } catch {
      // Ignorieren
    }
  }

  return false
}

export async function sendeGeraeteBenachrichtigung({
  titel,
  text,
  erzwingen = false,
}: BenachrichtigungOptionen): Promise<boolean> {
  const user = useAuthStore.getState().user
  if (!erzwingen && user && user.device_notifications === false) {
    return false
  }

  // 1. Tauri-Umgebung (Windows Desktop / Android App)
  if (typeof window !== 'undefined' && ('__TAURI_INTERNALS__' in window || '__TAURI__' in window)) {
    try {
      const { invoke } = await import('@tauri-apps/api/core')

      // A. Vorrang: Unser nativer Rust-Befehl mit channel_id = singra_alerts_v2
      try {
        await invoke('benachrichtigung_senden', {
          titel,
          text,
        })
        return true
      } catch {
        // Fallback zu Plugin-Aufruf
      }

      // B. Fallback: tauri-plugin-notification IPC
      try {
        let isGranted = false
        try {
          isGranted = await invoke<boolean>('plugin:notification|isPermissionGranted')
          if (!isGranted) {
            const permState = await invoke<string>('plugin:notification|requestPermission')
            isGranted = permState === 'granted'
          }
        } catch {
          isGranted = true
        }

        if (isGranted) {
          await invoke('plugin:notification|notify', {
            options: {
              title: titel,
              body: text,
              channelId: 'singra_alerts_v2',
              channel_id: 'singra_alerts_v2',
            },
          })
          return true
        }
      } catch {
        // Stiller Fallback
      }
    } catch {
      // Stiller Fallback zum Browser
    }
  }

  // 2. Web-Browser Umgebung (HTML5 Notification API)
  if (typeof window !== 'undefined' && 'Notification' in window) {
    try {
      if (Notification.permission === 'granted') {
        new Notification(titel, { body: text })
        return true
      } else if (Notification.permission !== 'denied') {
        const permission = await Notification.requestPermission()
        if (permission === 'granted') {
          new Notification(titel, { body: text })
          return true
        }
      }
    } catch {
      // Stiller Fallback
    }
  }

  return false
}
