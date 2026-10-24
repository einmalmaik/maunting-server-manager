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
 *
 * **Ein Weg, und er sagt, welcher.** Bis 22.10.2026 gab diese Datei `false`
 * zurück, wenn nichts ging — und jeder Aufrufer verwarf es. In Pontis sah das
 * so aus: der Befehl `benachrichtigung_senden` fehlte (er war nur in MSS
 * registriert), der Plugin-Aufruf scheiterte, der Service-Worker-Weg gibt es
 * im WebView nicht, und am Ende stand ein stilles `false`. Jetzt kommt ein
 * Ergebnis mit Weg und Grund zurück; wer will, kann es melden.
 */
import { useAuthStore } from '@/stores/authStore'

export interface BenachrichtigungOptionen {
  titel: string
  text: string
  icon?: string
  erzwingen?: boolean // Für Test-Buttons
}

/** Welcher Weg die Meldung tatsächlich gezeigt hat. */
export type BenachrichtigungsWeg = 'tauri' | 'plugin' | 'service-worker' | 'notification' | 'keiner'

export interface BenachrichtigungsErgebnis {
  weg: BenachrichtigungsWeg
  ok: boolean
  /** Warum es nicht ging. Nur bei `ok: false`. */
  fehler?: string
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

function fehlerText(fehler: unknown): string {
  if (fehler instanceof Error) return fehler.message
  return String(fehler ?? 'unbekannt')
}

export async function sendeGeraeteBenachrichtigung({
  titel,
  text,
  erzwingen = false,
}: BenachrichtigungOptionen): Promise<BenachrichtigungsErgebnis> {
  const user = useAuthStore.getState().user
  if (!erzwingen && user && user.device_notifications === false) {
    return { weg: 'keiner', ok: false, fehler: 'device_notifications aus' }
  }

  // Warum jeder Weg gescheitert ist — nicht nur der letzte. Wer in Pontis
  // nachsieht, soll „Befehl fehlt, Plugin abgelehnt, kein WebView-Notification"
  // lesen und nicht bloß das Ende der Kette. Ein stilles `false` war der
  // Grund, warum das wochenlang niemand sah.
  const gruende: string[] = []

  // 1. Tauri-Umgebung (Windows Desktop / Android App / Pontis)
  if (typeof window !== 'undefined' && ('__TAURI_INTERNALS__' in window || '__TAURI__' in window)) {
    try {
      const { invoke } = await import('@tauri-apps/api/core')

      // A. Unser native Rust-Befehl. In MSS und Pontis registriert; fehlt er,
      //    geht es mit dem Plugin weiter.
      try {
        await invoke('benachrichtigung_senden', { titel, text })
        return { weg: 'tauri', ok: true }
      } catch (fehler) {
        gruende.push(`benachrichtigung_senden: ${fehlerText(fehler)}`)
      }

      // B. tauri-plugin-notification. `isPermissionGranted` meldet auf Android
      //    einen echten Zustand, auf Windows immer „gewährt".
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
              icon: 'ic_launcher',
              largeIcon: 'ic_launcher',
              channelId: 'mss_alerts',
              channel_id: 'mss_alerts',
            },
          })
          return { weg: 'plugin', ok: true }
        }
        gruende.push('plugin:notification nicht erlaubt')
      } catch (fehler) {
        gruende.push(`plugin:notification: ${fehlerText(fehler)}`)
      }
    } catch (fehler) {
      gruende.push(`tauri: ${fehlerText(fehler)}`)
    }
  }

  // 2. Web-Browser Umgebung (ServiceWorker / HTML5 Notification API)
  if (typeof window !== 'undefined') {
    // A. ServiceWorker Benachrichtigung (für Android Chrome & PWA-Browser zwingend erforderlich)
    if ('serviceWorker' in navigator && 'Notification' in window) {
      try {
        let perm = Notification.permission
        if (perm !== 'granted' && perm !== 'denied') {
          perm = await Notification.requestPermission()
        }
        if (perm === 'granted') {
          try {
            const reg = (await navigator.serviceWorker.getRegistration()) || (await navigator.serviceWorker.ready)
            if (reg && typeof reg.showNotification === 'function') {
              // Höchste Priorität für WhatsApp-ähnliche Heads-up-Banner auf Android
              await reg.showNotification(titel, {
                body: text,
                icon: '/favicon.ico',
                badge: '/favicon.ico',
                vibrate: [200, 100, 200, 100, 200],
                tag: 'msm-high-priority-alert',
                renotify: true,
                requireInteraction: true,
                silent: false,
                urgency: 'high',
                timestamp: Date.now(),
              } as NotificationOptions & { urgency?: string })
              return { weg: 'service-worker', ok: true }
            }
            gruende.push('keine Service-Worker-Registrierung')
          } catch (fehler) {
            gruende.push(`serviceWorker.showNotification: ${fehlerText(fehler)}`)
          }
        } else {
          gruende.push(`Notification.permission: ${perm}`)
        }
      } catch (fehler) {
        gruende.push(`serviceWorker: ${fehlerText(fehler)}`)
      }
    }

    // B. Standard HTML5 Notification (Desktop Browser: Chrome, Firefox, Safari, Edge)
    if ('Notification' in window) {
      try {
        if (Notification.permission === 'granted') {
          new Notification(titel, {
            body: text,
            icon: '/favicon.ico',
            requireInteraction: true,
            tag: 'msm-high-priority-alert',
          })
          return { weg: 'notification', ok: true }
        } else if (Notification.permission !== 'denied') {
          const permission = await Notification.requestPermission()
          if (permission === 'granted') {
            new Notification(titel, {
              body: text,
              icon: '/favicon.ico',
              requireInteraction: true,
              tag: 'msm-high-priority-alert',
            })
            return { weg: 'notification', ok: true }
          }
        }
        gruende.push('Notification nicht erlaubt')
      } catch (fehler) {
        gruende.push(`Notification: ${fehlerText(fehler)}`)
      }
    }
  }

  return { weg: 'keiner', ok: false, fehler: gruende.join('; ') || 'kein Weg verfügbar' }
}
