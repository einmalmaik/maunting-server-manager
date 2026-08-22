/**
 * Die einmalige Übernahme der Modellwahl vom Browser ans Konto.
 *
 * Konten aus der Zeit vor `users.ai_provider_id` tragen ihre Wahl nur im
 * localStorage — und den sieht nur genau dieses Fenster. Das Overlay der
 * Desktop-App fragt allein das Konto und liefe sonst bis zum nächsten
 * manuellen Modellwechsel auf dem erstbesten Zugang, beliebig langsam.
 *
 * Aufgerufen vom Chat (`AiChat`) und vom Sprachmodus (`pages/Ai`) beim
 * Öffnen. Validiert wird beim Backend, nicht hier: eine gelöschte oder
 * stimm-only gewordene Browser-Wahl lehnt PATCH mit 404 ab, und dann bleibt
 * das Konto leer — genau richtig.
 */
import { api } from '@/api/client'
import { aiChatPreferenceKeys, readAiProviderChoice } from '@/lib/aiChatPreferences'
import { useAuthStore } from '@/stores/authStore'

/**
 * Der Marker „schon übernommen" — je Benutzer, neben den anderen
 * Chat-Schlüsseln. Ohne ihn liefe die Übernahme bei jedem Öffnen erneut,
 * und ein per API bewusst geleertes Konto (`provider_id: null` ist ein
 * dokumentierter Vertrag) bekäme die alte Browser-Wahl immer wieder
 * eingetragen — „keine Wahl" wäre unerreichbar.
 */
function markerSchluessel(userId: string | number): string {
  return `msm_ai_chat:konto_uebernahme:${userId}`
}

export async function browserWahlInsKontoUebernehmen(): Promise<void> {
  const user = useAuthStore.getState().user
  // Nur wenn das Konto noch nie gewählt hat — eine vorhandene Wahl (auch
  // eine bewusst andere) wird nie überschrieben.
  if (!user || user.ai_provider_id != null) return
  try {
    if (localStorage.getItem(markerSchluessel(user.id)) !== null) return
  } catch {
    return // Ohne localStorage gibt es auch keine Browser-Wahl zu übernehmen.
  }
  const wahl = readAiProviderChoice(aiChatPreferenceKeys(user.id).provider)
  if (wahl === null) return
  try {
    const antwort = await api<{ ai_provider_id: number | null }>('/auth/me/ai-provider', {
      method: 'PATCH',
      body: JSON.stringify({ provider_id: wahl }),
    })
    // Der Benutzer kann während des Roundtrips selbst gewählt haben
    // (`waehleProvider`) — dann gilt seine Wahl, nicht die Übernahme.
    const inzwischen = useAuthStore.getState().user
    if (inzwischen && inzwischen.ai_provider_id == null) {
      useAuthStore.getState().updateUser({ ai_provider_id: antwort.ai_provider_id })
    }
    try {
      localStorage.setItem(markerSchluessel(user.id), '1')
    } catch {
      // Voller oder gesperrter Speicher: dann läuft die Übernahme eben erneut.
    }
  } catch {
    // Ungültige alte Wahl oder Netzfehler: nichts gespeichert, nichts kaputt.
  }
}
