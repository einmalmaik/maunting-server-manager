/**
 * Der Vordergrunddienst auf Android — einschalten, ausschalten, Stand lesen.
 *
 * **Warum ein eigener Ausweis.** Der Zugangstoken des Panels lebt 15 Minuten.
 * Ein Dienst, der sich damit verbindet, verstummt nach einer Viertelstunde —
 * genau in dem Fall, für den es ihn gibt. Beim Einschalten wird deshalb einmal
 * ein Kanal-Ausweis angelegt (`POST /notifications/channels`), gegen einen
 * frischen Nachweis. Er gilt, bis er widerrufen wird, und der Dienst erneuert
 * ihn nie selbst: zwei Rotationen auf derselben Familie würden sie sperren.
 *
 * **Was der Dienst darf.** Nur zuhören. Er liest den Ereignisstrom, meldet
 * seine Präsenz als „hintergrund" und zeigt eine Meldung, wenn der Server
 * **ihn** als Zusteller wählt (`melden`). Zeigt ein offenes Fenster dieselbe
 * Nachricht, bleibt er still — das entscheidet der Server, nicht der Dienst.
 *
 * **Wo der Ausweis liegt.** In den privaten Einstellungen der App
 * (`SharedPreferences`), nicht im localStorage: die Oberfläche braucht ihn
 * nach dem Einschalten nie wieder. Nur die Kennung des Kanals bleibt hier, um
 * ihn beim Ausschalten widerrufen zu können.
 */

import { invoke } from '@tauri-apps/api/core'

import { api } from '@/api/client'
import { getEffectiveApiUrl } from '@/config/api'
import { beiMailboxAenderung, offeneMailboxEintraege } from '@/services/mailboxAbo'
import { passkeyNachweis } from '@/services/passkeyService'

const KANAL_ABLAGE = 'msm:hintergrundkanal'

/** Läuft die App auf einem Android-Gerät? Nur dort gibt es den Dienst. */
export function aufAndroid(): boolean {
  return typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)
}

function inTauri(): boolean {
  return typeof window !== 'undefined' && ('__TAURI_INTERNALS__' in window || '__TAURI__' in window)
}

/** Der Dienst ist nur auf Android und nur in der App verfügbar. */
export function dienstVerfuegbar(): boolean {
  return aufAndroid() && inTauri()
}

/**
 * Die Adresse des Panels — zur Zeit des Aufrufs, nicht beim Laden des Moduls.
 *
 * In der App setzt erst `konfigLaden()` die Laufzeitadresse; ein beim Import
 * eingefrorener Wert wäre `tauri.localhost` und der Dienst verbände sich mit
 * sich selbst.
 */
function panelBasis(): string {
  const absolut = getEffectiveApiUrl()
  if (absolut) return absolut
  return typeof window !== 'undefined' && window.location?.origin ? window.location.origin : ''
}

export interface DienstStand {
  aktiv: boolean
  /** Ist die App von der Akku-Optimierung ausgenommen? */
  akkuBefreit: boolean
}

export async function stand(): Promise<DienstStand> {
  if (!dienstVerfuegbar()) return { aktiv: false, akkuBefreit: false }
  try {
    return await invoke<DienstStand>('dienst_stand')
  } catch {
    return { aktiv: false, akkuBefreit: false }
  }
}

function merkeKanal(id: number | null): void {
  try {
    if (id === null) localStorage.removeItem(KANAL_ABLAGE)
    else localStorage.setItem(KANAL_ABLAGE, String(id))
  } catch {
    // Ohne Ablage bleibt der Kanal offen; er ist dann nur nicht mehr von hier
    // aus zu widerrufen. Beim nächsten Einschalten entsteht ein neuer.
  }
}

function gemerkterKanal(): number | null {
  try {
    const roh = localStorage.getItem(KANAL_ABLAGE)
    const zahl = Number(roh)
    return Number.isFinite(zahl) && zahl > 0 ? zahl : null
  } catch {
    return null
  }
}

/**
 * Einschalten: Ausweis anlegen, Dienst starten, Akku-Ausnahme anbieten.
 *
 * Der Nachweis ist Pflicht — ein dauerhafter Zugang entsteht nie aus einem
 * bloß vorhandenen Token. `passkeyNachweis` fragt in der App über den Browser
 * (dort liegt der Passkey), im Browser direkt.
 *
 * Gibt `false` zurück, wenn der Dienst hier nicht verfügbar ist oder der
 * Nachweis abgebrochen wurde — der Aufrufer zeigt dann den Grund.
 */
export async function einschalten(label?: string): Promise<boolean> {
  if (!dienstVerfuegbar()) return false

  let nachweis
  try {
    nachweis = await passkeyNachweis('notification_channel')
  } catch {
    return false
  }

  const kanal = await api<{ id: number; token: string }>('/notifications/channels', {
    method: 'POST',
    body: JSON.stringify({
      label: (label || 'Android').slice(0, 64),
      passkey: nachweis,
    }),
  })
  merkeKanal(kanal.id)

  await invoke('dienst_starten', {
    ausweis: kanal.token,
    basis: panelBasis(),
    mailboxen: JSON.stringify(offeneMailboxEintraege()),
  })
  // Die Akku-Ausnahme ist der Unterschied zwischen „kommt an" und „kommt
  // manchmal an": ohne sie beendet Samsung/Xiaomi den Dienst nach Stunden.
  await invoke('dienst_akku_befreien').catch(() => {})
  return true
}

/**
 * Ausschalten: Dienst stoppen und den Ausweis widerrufen.
 *
 * Beides, und in dieser Reihenfolge: erst hört der Dienst auf, dann verliert er
 * seinen Ausweis. Andersherum liefe er kurz gegen ein bereits gesperrtes Konto.
 */
export async function ausschalten(): Promise<void> {
  if (!dienstVerfuegbar()) return
  const kanal = gemerkterKanal()
  await invoke('dienst_stoppen').catch(() => {})
  merkeKanal(null)
  if (kanal !== null) {
    await api(`/notifications/channels/${kanal}`, { method: 'DELETE' }).catch(() => {})
  }
}

/**
 * Die Mailboxen, über die der Dienst Bescheid wissen soll.
 *
 * Der Strom kennt sie nur, wenn sie ihm gesagt werden; die Liste ändert sich
 * mit jedem geöffneten Gespräch und jeder verlassenen Gruppe. Ohne diesen
 * Aufruf bliebe der Dienst bei dem Stand vom Einschalten stehen — und ein
 * Gruppenbeitritt wäre stumm.
 */
export async function mailboxenMelden(): Promise<void> {
  if (!dienstVerfuegbar()) return
  const eintraege = offeneMailboxEintraege()
  if (eintraege.length === 0) return
  await invoke('dienst_mailboxen', {
    mailboxen: JSON.stringify(eintraege),
  }).catch(() => {})
}

// Jeder neue oder verlassene Chat geht sofort an den Dienst. Ohne das bliebe
// er bei dem Stand vom Einschalten stehen — ein Gruppenbeitritt wäre stumm.
if (dienstVerfuegbar()) {
  beiMailboxAenderung(() => {
    void mailboxenMelden()
  })
}
