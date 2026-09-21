/**
 * Das Abonnement dieses Browsers für Benachrichtigungen bei geschlossener App.
 *
 * Der Vordergrund läuft nicht hierüber: ein offener Tab bekommt das Ereignis
 * über den Social-WebSocket, und `lib/benachrichtigung` macht daraus direkt
 * eine Meldung. Diese Datei ist ausschließlich für den anderen Fall da — die
 * Anwendung ist zu, und zustellen kann nur noch der Push-Dienst des Browsers
 * an den `push`-Listener in `public/sw.js`.
 *
 * ## Was hier bewusst nicht passiert
 *
 * Es wird **nicht** nach der Benachrichtigungserlaubnis gefragt. Ein
 * Berechtigungsdialog, den niemand angefordert hat, ist die schnellste Art,
 * ein dauerhaftes „blockiert" zu kassieren. Gefragt wird an der Stelle, an der
 * der Benutzer den Schalter umlegt (`pruefeUndFrageGeraeteBerechtigung`); hier
 * wird nur abonniert, wenn die Erlaubnis schon vorliegt.
 *
 * ## Warum das Abonnement erneut gemeldet wird
 *
 * `pushManager.subscribe` gibt bei bestehendem Abonnement dasselbe zurück,
 * ohne den Server zu fragen. Der Server kann die Zeile aber verloren haben
 * (zurückgespielte Sicherung, gelöschtes Konto, abgelaufenes Abo anderswo
 * entfernt). Deshalb wird bei jeder Anmeldung gemeldet, nicht nur beim ersten
 * Mal — `webpush_service.eintragen` übernimmt eine vorhandene Zeile, statt eine
 * zweite anzulegen.
 */

import { getPushPublicKey, loeschePushAbo, meldePushAbo } from '@/api/social'

/**
 * base64url aus dem Backend in das `Uint8Array`, das `subscribe` erwartet.
 *
 * Der Puffer wird ausdrücklich angelegt, statt `new Uint8Array(länge)` zu
 * nehmen: seit TypeScript 5.7 ist `Uint8Array` über seinem Puffer generisch,
 * und die kurze Form ergibt `ArrayBufferLike` — was auch ein
 * `SharedArrayBuffer` sein könnte und deshalb nicht als `BufferSource`
 * durchgeht.
 */
function alsBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const gepolstert = base64url.replace(/-/g, '+').replace(/_/g, '/')
  const roh = atob(gepolstert + '='.repeat((4 - (gepolstert.length % 4)) % 4))
  const bytes = new Uint8Array(new ArrayBuffer(roh.length))
  for (let i = 0; i < roh.length; i += 1) bytes[i] = roh.charCodeAt(i)
  return bytes
}

function alsBase64url(puffer: ArrayBuffer | null): string {
  if (!puffer) return ''
  let text = ''
  const bytes = new Uint8Array(puffer)
  for (let i = 0; i < bytes.length; i += 1) text += String.fromCharCode(bytes[i])
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function kannPush(): boolean {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  )
}

/**
 * Abonniert und meldet die Adresse ans Panel. `false` heißt: nicht möglich.
 *
 * Alle Fehlschläge sind still. Push ist eine Verbesserung, kein Bestandteil des
 * Messengers: dass ein Browser nicht kann, nicht darf oder das Panel keinen
 * Schlüssel hat, darf an keiner Stelle als Fehler vor dem Benutzer landen.
 */
export async function abonniere(): Promise<boolean> {
  if (!kannPush() || Notification.permission !== 'granted') return false

  try {
    const schluessel = await getPushPublicKey()
    // Leer heißt: das Panel kann nicht zustellen. Dann gar nicht erst
    // abonnieren — ein Abo ohne Gegenstelle bindet den Browser an einen
    // Schlüssel, der nie passt.
    if (!schluessel) return false

    // Erst fragen, ob überhaupt einer registriert ist. `serviceWorker.ready`
    // löst **nie** auf, wenn die Registrierung fehlgeschlagen ist — im
    // Tauri-Fenster ist das der Normalfall, weil `sw.js` dort über ein eigenes
    // Protokoll ausgeliefert wird. Ein `await` darauf bliebe still für immer
    // stehen. `getRegistration` liefert stattdessen `undefined`.
    if (!(await navigator.serviceWorker.getRegistration())) return false

    const reg = await navigator.serviceWorker.ready
    const abo =
      (await reg.pushManager.getSubscription()) ??
      (await reg.pushManager.subscribe({
        // Pflicht in Chrome: ein Push darf nie stumm bleiben. Der
        // `push`-Listener zeigt auch immer etwas an.
        userVisibleOnly: true,
        applicationServerKey: alsBytes(schluessel),
      }))

    const p256dh = alsBase64url(abo.getKey('p256dh'))
    const auth = alsBase64url(abo.getKey('auth'))
    if (!p256dh || !auth) return false

    await meldePushAbo({ endpoint: abo.endpoint, p256dh, auth })
    return true
  } catch {
    return false
  }
}

/**
 * Kündigt beim Panel und beim Browser. Beim Abmelden zwingend.
 *
 * Ohne diesen Schritt bekäme das Gerät weiter Benachrichtigungen für ein Konto,
 * das sich hier abgemeldet hat — auf einem geteilten Rechner genau der Fall,
 * den niemand erwartet. Erst beim Panel austragen, dann beim Browser: wäre es
 * andersherum und der zweite Schritt schlüge fehl, bliebe eine Zeile stehen,
 * deren Adresse niemand mehr kennt.
 */
export async function kuendige(): Promise<void> {
  if (!kannPush()) return
  try {
    const reg = await navigator.serviceWorker.getRegistration()
    const abo = await reg?.pushManager.getSubscription()
    if (!abo) return
    try {
      await loeschePushAbo(abo.endpoint)
    } catch {
      // Die Sitzung ist womöglich schon abgelaufen. Der Server räumt die Zeile
      // spätestens beim nächsten 410 des Push-Dienstes ab.
    }
    await abo.unsubscribe()
  } catch {
    // Still: das Abmelden darf daran nicht scheitern.
  }
}
