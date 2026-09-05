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

export type AnmeldeErgebnis = {
  status: 'erfolg' | 'abgelehnt' | 'offline'
}

/**
 * Holt über das Tresor-Refresh-Token neue Tokens mit kurzem Timeout (z. B. 2.5s).
 *
 * Unterscheidet sauber zwischen:
 * - 'erfolg': Neue Tokens erhalten und im Tresor hinterlegt.
 * - 'abgelehnt': Backend lehnte das Token explizit ab (HTTP 401/403) -> Token verbrannt,
 *   wird gelöscht, Benutzer muss zur Kopplung.
 * - 'offline': Server nicht erreichbar, Timeout, Flugmodus oder Verbindungsabbruch.
 *   Das Token im Tresor bleibt erhalten, damit die App offline starten und bei
 *   Wiederverbindung nahtlos synchronisieren kann.
 */
let refreshPromise: Promise<AnmeldeErgebnis> | null = null

export function stillAnmeldenDetail(timeoutMs = 5000): Promise<AnmeldeErgebnis> {
  if (refreshPromise) {
    return refreshPromise
  }
  refreshPromise = (async () => {
    try {
      return await _stillAnmeldenDetailIntern(timeoutMs)
    } finally {
      refreshPromise = null
    }
  })()
  return refreshPromise
}

async function _stillAnmeldenDetailIntern(timeoutMs: number): Promise<AnmeldeErgebnis> {
  let refresh: string | null = null
  try {
    refresh = await invoke<string | null>('refresh_token_laden')
  } catch {
    return { status: 'abgelehnt' }
  }

  if (!refresh) {
    return { status: 'abgelehnt' }
  }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const antwort = await fetch(apiUrl('/auth/refresh'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
      signal: controller.signal,
    })
    clearTimeout(timer)

    if (antwort.status === 401 || antwort.status === 403) {
      // Abgelehnte Rotation heißt: das Token ist verbrannt (Widerruf oder
      // Wiederverwendungserkennung). Aufheben wäre sinnlos und riskant.
      await invoke('refresh_token_loeschen').catch(() => {})
      return { status: 'abgelehnt' }
    }

    if (!antwort.ok) {
      // 502/503/504 Proxy-Fehler oder Backend temporär down: Token im Tresor lassen!
      return { status: 'offline' }
    }

    let tokens: { access_token: string; refresh_token: string }
    try {
      tokens = (await antwort.json()) as { access_token: string; refresh_token: string }
    } catch {
      // Falsche Antwort oder Captive Portal -> offline werten
      return { status: 'offline' }
    }

    accessToken = tokens.access_token
    await invoke('refresh_token_speichern', { token: tokens.refresh_token })
    return { status: 'erfolg' }
  } catch {
    clearTimeout(timer)
    // Netzwerkfehler, DNS-Fehler oder Timeout -> offline
    return { status: 'offline' }
  }
}

/**
 * Standard-Signatur für den Panel-API-Client: Gibt true bei erfolgreicher
 * Rotation zurück.
 */
export async function stillAnmelden(): Promise<boolean> {
  const res = await stillAnmeldenDetail(5000)
  return res.status === 'erfolg'
}

/** Einmal beim Start: den Panel-API-Client auf Bearer umstellen. */
export function transportEinrichten(): void {
  registriereNativeSitzung({
    token: () => accessToken,
    erneuern: stillAnmelden,
  })
}
