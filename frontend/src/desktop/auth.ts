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
import { geraetVeroeffentlichen, sicherheitsnummer } from '@/services/e2eeGeraet'
import { MessengerVerschlossenError } from '@/services/lokaleVersiegelung'
import { holeVerlaufAb } from '@/services/verlaufsUebergabe'
import { useAuthStore } from '@/stores/authStore'
import { konfigAendern } from './tauri'
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

/** Was nach dem Koppeln auf dem Schirm stehen soll. */
export interface Kopplungsergebnis {
  /**
   * Die Sicherheitsnummer dieses Geräts — dieselbe, die das Panel neben der
   * Rückfrage zeigt, ob es den Verlauf hierher übergeben soll. `null`, wenn
   * sich das Gerät (noch) nicht veröffentlichen ließ.
   */
  sicherheitsnummer: string | null
  /**
   * Der Geräteschlüssel liegt hinter dem Messenger-PIN. Erst nach dem
   * Entsperren lässt sich das Gerät veröffentlichen — `geraetMelden()`.
   */
  gesperrt?: boolean
  /** Das Veröffentlichen scheiterte aus einem anderen Grund. */
  fehler?: string
}

/**
 * Löst einen Kopplungscode ein und übernimmt die Sitzung.
 *
 * Der Code geht so raus, wie der Mensch ihn eingegeben hat — das Panel liest
 * ihn nachsichtig (Kleinschreibung, fehlende Striche). Danach ist der
 * authStore die Wahrheit: `checkAuth()` lädt Benutzer und Rechte, dieselbe
 * Hydrierung wie beim Panel-Start.
 */
export async function koppeln(code: string, bezeichnung: string): Promise<Kopplungsergebnis> {
  const antwort = await api<TokenAntwort>('/auth/devices/redeem', {
    method: 'POST',
    body: JSON.stringify({ code, label: bezeichnung }),
  })
  setzeAccessToken(antwort.access_token)
  await invoke('refresh_token_speichern', { token: antwort.refresh_token })
  try {
    await konfigAendern({ eingerichtet: true })
  } catch {}
  await useAuthStore.getState().checkAuth()

  // Die Kopplung selbst steht ab hier. Was folgt, entscheidet nur noch, ob
  // das Gerät seinen Geräteschlüssel zeigen kann — und damit, ob es in der
  // Freigabeliste auftaucht. Bis 5.0.3 wurde jeder Fehler hier verschluckt:
  // mit Messenger-PIN kam das Gerät nie in die Liste, ohne dass es jemand sah.
  try {
    return { sicherheitsnummer: await geraetMelden(code, bezeichnung) }
  } catch (e) {
    if (e instanceof MessengerVerschlossenError) return { sicherheitsnummer: null, gesperrt: true }
    return { sicherheitsnummer: null, fehler: e instanceof Error ? e.message : String(e) }
  }
}

/**
 * Veröffentlicht den Geräteschlüssel und liefert die Sicherheitsnummer.
 *
 * Erst den eigenen Geräteschlüssel veröffentlichen — daran erkennt die andere
 * Seite, für wen sie versiegeln soll. Das wird abgewartet, weil die
 * Sicherheitsnummer daraus kommt: das Panel übergibt erst, wenn dort jemand
 * bestätigt, dass seine Nummer dieselbe ist wie hier.
 *
 * Das Abholen des Verlaufs läuft im Hintergrund: ein gekoppeltes Gerät soll
 * benutzbar sein, auch wenn nebenan noch jemand vergleicht. Scheitert es,
 * beginnt das Gerät mit einem leeren Verlauf — der Normalfall bei einem Konto
 * ohne bisherige Nachrichten.
 *
 * Wirft `MessengerVerschlossenError`, solange der Messenger-PIN den
 * Geräteschlüssel versiegelt.
 */
export async function geraetMelden(code: string, bezeichnung: string): Promise<string> {
  const geraet = await geraetVeroeffentlichen(bezeichnung)
  void holeVerlaufAb(code, geraet.paar.privateKeyJwk).catch(() => 0)
  return sicherheitsnummer(geraet.paar.publicKeyJwk)
}

/** Fensterereignis nach `abmelden()`: die Sitzung ist lokal verworfen. */
export const ABGEMELDET = 'mss:abgemeldet'

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
  // Vor dem Logout, solange die Sitzung den Zugang noch entfernen darf.
  await import('./vault/kameraSicherung').then((k) => k.kameraBeimAbmelden()).catch(() => {})
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
    // Die Hauptansicht geht sofort zur Kopplung, auch offline. Bis 5.0.3
    // fand sie erst nach einem Neustart dorthin.
    window.dispatchEvent(new Event(ABGEMELDET))
  }
}
