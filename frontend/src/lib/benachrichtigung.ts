/**
 * Plattformübergreifender Helfer für Geräte- & Push-Benachrichtigungen.
 * 
 * Unterstützt:
 * - Tauri v2 (Windows Desktop & Android Mobile) via @tauri-apps/api/core invoke
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
  if (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window) {
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      await invoke('plugin:notification|notify', {
        options: {
          title: titel,
          body: text,
        },
      })
      return true
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
