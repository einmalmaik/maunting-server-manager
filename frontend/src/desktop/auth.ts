/**
 * Der Weg hinein — und es ist genau einer: die Kopplung.
 *
 * Passwort, 2FA und Captcha bleiben im Browser. Im Panel entsteht unter
 * Profil → KI ein Code, hier wird er eingelöst; die Sitzung trägt im Token,
 * dass sie von einem Gerät kommt (daran hängt die Werkzeugmenge der KI).
 * Warum es keinen Passwort-Weg gibt, steht in `backend/routers/auth.py`
 * bei `/devices/redeem` — kurz: Turnstile-Schlüssel hängen an Domains, und
 * `tauri.localhost` ist keine.
 */
import { invoke } from '@tauri-apps/api/core'

import { api } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { konfigLaden, konfigSpeichern } from './tauri'
import { setzeAccessToken, sitzungVerwerfen } from './transport'

interface TokenAntwort {
  access_token: string
  refresh_token: string
  expires_in: number
}

/**
 * Erreichbarkeits-Test beim Einrichten — gegen eine **ausdrückliche** Adresse,
 * denn beim ersten Schritt steht sie noch nicht in der Konfiguration.
 *
 * Geprüft wird mehr als der Statuscode: die Antwort muss JSON sein. Eine
 * Oberfläche antwortet auf jeden Pfad mit ihrer HTML-Seite (SPA-Fallback) —
 * Status 200, aber die falsche Adresse. Der Aufrufer übersetzt den Fehler
 * in einen Satz, der das sagt (`mss.wizard.antwortIstWebseite`).
 */
export async function erreichbar(adresse: string): Promise<void> {
  const antwort = await fetch(`${adresse}/api/auth/setup-status`)
  if (!antwort.ok) {
    throw new Error(`HTTP ${antwort.status}`)
  }
  await antwort.json()
}

/**
 * Löst einen Kopplungscode ein und übernimmt die Sitzung.
 *
 * Der Code geht so raus, wie der Mensch ihn eingegeben hat — das Panel liest
 * ihn nachsichtig (Kleinschreibung, fehlende Striche). Danach ist der
 * authStore die Wahrheit: `checkAuth()` lädt Benutzer und Rechte, dieselbe
 * Hydrierung wie beim Panel-Start.
 */
export async function koppeln(code: string, bezeichnung: string): Promise<void> {
  const antwort = await api<TokenAntwort>('/auth/devices/redeem', {
    method: 'POST',
    body: JSON.stringify({ code, label: bezeichnung }),
  })
  setzeAccessToken(antwort.access_token)
  await invoke('refresh_token_speichern', { token: antwort.refresh_token })
  try {
    const k = await konfigLaden()
    await konfigSpeichern({ ...k, eingerichtet: true })
  } catch {}
  await useAuthStore.getState().checkAuth()
}

/**
 * Abmelden: Refresh-Familie serverseitig widerrufen, dann lokal alles räumen.
 *
 * Anders als `authStore.logout()` schickt der native Weg das Refresh-Token im
 * Körper mit — das Backend hat kein Cookie, an dem es die Familie erkennen
 * könnte. Geräumt wird trotzdem über `clearSession()`: es gibt nur ein Ende
 * einer Sitzung, egal auf welchem Weg sie entstand.
 */
export async function abmelden(): Promise<void> {
  const refresh = await invoke<string | null>('refresh_token_laden')
  try {
    await api('/auth/logout', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refresh }),
    })
  } catch {
    // Serverseitig nicht erreichbar — lokal wird trotzdem alles vergessen.
  } finally {
    await sitzungVerwerfen()
    useAuthStore.getState().clearSession()
  }
}
