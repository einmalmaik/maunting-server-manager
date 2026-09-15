/**
 * Bootstrap der Desktop-App — bewusst zweistufig.
 *
 * Die API-Adresse ist hier keine Build-Zeit-Entscheidung (`VITE_API_URL`),
 * sondern steht in der Gerätekonfiguration des Benutzers. `config/api.ts`
 * berechnet seine Konstanten aber beim Laden des Moduls. Deshalb lädt diese
 * Datei **zuerst** die Konfiguration über Rust, setzt den Laufzeit-Override —
 * und importiert erst **danach** dynamisch den Rest der App. Kein statischer
 * Import hier darf transitiv `config/api.ts` erreichen.
 *
 * Kein Service Worker: eine installierte App braucht keine PWA-Schicht,
 * und ein Cache zwischen App und Panel-API wäre nur eine zweite Wahrheit.
 */
import { invoke } from '@tauri-apps/api/core'

interface AppKonfig {
  backend_url: string | null
}

const BOOTSTRAP_TIMEOUT_MS = 4000

async function konfigLadenMitFrist(): Promise<AppKonfig> {
  const timeout = new Promise<never>((_, reject) => {
    window.setTimeout(
      () => reject(new Error('Tauri-Konfiguration beim Start nicht erreichbar')),
      BOOTSTRAP_TIMEOUT_MS,
    )
  })
  return await Promise.race([invoke<AppKonfig>('konfig_laden'), timeout])
}

async function hochfahren(): Promise<void> {
  try {
    // Beim Windows-Autostart kann WebView2 sichtbar werden, bevor Tauri seine
    // Setup-Phase abgeschlossen hat. Ohne Frist bliebe der Root dann leer,
    // weil React erst nach diesem Aufruf importiert und gerendert wird.
    const konfig = await konfigLadenMitFrist()
    if (konfig.backend_url) {
      ;(globalThis as { __MSM_API_URL?: string }).__MSM_API_URL = konfig.backend_url
    }
  } catch (fehler) {
    // Die DesktopRoot lädt die Konfiguration nach dem ersten Render erneut.
    // Das ist insbesondere beim Autostart wichtig: eine verspätete Tauri-
    // Initialisierung darf nicht als weiße, unbedienbare Seite erscheinen.
    console.warn('Startkonfiguration noch nicht verfügbar:', fehler)
  }
  await import('./start')
}

void hochfahren()
