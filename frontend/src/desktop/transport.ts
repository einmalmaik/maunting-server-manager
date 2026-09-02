/**
 * Die native Sitzung der Desktop-App.
 *
 * Das Access-Token lebt ausschließlich in diesem Modul im Speicher; das
 * Refresh-Token liegt im OS-Tresor (Rust: `refresh_token_*`). Kein Token
 * erreicht je localStorage, Logs oder Fehlermeldungen. Der Panel-API-Client
 * (`@/api/client`) bekommt beides über `registriereNativeSitzung` — danach
 * laufen alle Panel-Komponenten unverändert über Bearer statt Cookies.
 */
import { invoke } from '@tauri-apps/api/core'

import { registriereNativeSitzung } from '@/api/client'
import { apiUrl } from '@/config/api'

let accessToken: string | null = null

export function setzeAccessToken(token: string | null): void {
  accessToken = token
}

export function istAngemeldet(): boolean {
  return accessToken !== null
}

/** Beim Abmelden: alles vergessen — Speicher und Tresor. */
export async function sitzungVerwerfen(): Promise<void> {
  accessToken = null
  await invoke('refresh_token_loeschen')
}

/**
 * Holt über das Tresor-Refresh-Token neue Tokens. `false`, wenn keines
 * hinterlegt ist oder das Backend die Rotation ablehnt — der Aufrufer schickt
 * den Benutzer dann zur Kopplung.
 *
 * Bewusst `fetch` statt `api()`: dieses Modul ist der Refresh-Weg des
 * Clients, und ein Refresh, der bei 401 selbst einen Refresh anstößt, wäre
 * eine Schleife.
 */
export async function stillAnmelden(): Promise<boolean> {
  const refresh = await invoke<string | null>('refresh_token_laden')
  if (!refresh) {
    return false
  }
  const antwort = await fetch(apiUrl('/auth/refresh'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refresh }),
  })
  if (!antwort.ok) {
    // Abgelehnte Rotation heißt: das Token ist verbrannt (Widerruf oder
    // Wiederverwendungserkennung). Aufheben wäre sinnlos und riskant.
    await invoke('refresh_token_loeschen')
    return false
  }
  let tokens: { access_token: string; refresh_token: string }
  try {
    tokens = (await antwort.json()) as { access_token: string; refresh_token: string }
  } catch {
    // Keine Daten, sondern eine Webseite (falsche Adresse). Das Token im
    // Tresor ist deswegen nicht verbrannt — es bleibt liegen, damit die
    // Anmeldung nach korrigierter Adresse noch still gelingen kann.
    return false
  }
  accessToken = tokens.access_token
  await invoke('refresh_token_speichern', { token: tokens.refresh_token })
  return true
}

/** Einmal beim Start: den Panel-API-Client auf Bearer umstellen. */
export function transportEinrichten(): void {
  registriereNativeSitzung({
    token: () => accessToken,
    erneuern: stillAnmelden,
  })
}
