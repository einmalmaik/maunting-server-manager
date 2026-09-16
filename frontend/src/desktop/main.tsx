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

async function hochfahren(): Promise<void> {
  try {
    const konfig = await invoke<AppKonfig>('konfig_laden')
    if (konfig.backend_url) {
      ;(globalThis as { __MSM_API_URL?: string }).__MSM_API_URL = konfig.backend_url
    }
  } catch {
    // Keine Konfiguration heißt: der Assistent fragt gleich nach der Adresse.
  }
  await import('./start')
}

void hochfahren()
