/**
 * Einstieg des Browsers, zweistufig wie MSS (`desktop/main.tsx`): erst die
 * Panel-Adresse aus der Gerätekonfiguration holen und setzen, dann den Rest
 * laden. `config/api.ts` liest die Adresse beim Laden des Moduls; kein
 * statischer Import hier darf es erreichen.
 */
import { invoke } from '@tauri-apps/api/core'

import { geraeteArtSetzen } from '@/desktop/geraeteArt'
import { gestenleisteUebernehmen } from '@/lib/gestenleiste'
import { randomUuidNachruesten } from '@/lib/uuidNachruesten'

async function hochfahren(): Promise<void> {
  try {
    const konfig = await invoke<{ backend_url: string | null }>('konfig_laden')
    if (konfig.backend_url) {
      ;(globalThis as { __MSM_API_URL?: string }).__MSM_API_URL = konfig.backend_url
    }
  } catch {
    // Ohne Konfiguration startet der Browser ungekoppelt.
  }
  await import('./start')
}

randomUuidNachruesten()
// Android: die Gestenleiste unten, die ältere WebViews nicht melden (Punkt 87).
gestenleisteUebernehmen()
// Die Kopplung meldet dem Panel, dass hier der Browser koppelt, nicht MSS.
geraeteArtSetzen('browser')
void hochfahren()
