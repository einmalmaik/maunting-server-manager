/**
 * Wohin zugestellt wird, wenn die Anwendung zu ist — je Mailbox, nicht je Konto.
 *
 * `pushAbo.ts` daneben meldet dieselbe Adresse an das Konto. Diese Datei meldet
 * sie an die Mailboxen, und das ist der Weg, der für Kennungen funktioniert,
 * die der Server nicht ausrechnen kann: dort gibt es kein Konto zu
 * benachrichtigen, weil der Server nicht weiß, wem die Mailbox gehört.
 *
 * ## Warum das keine Kopie von `mailboxAbo.ts` ist
 *
 * Dieselbe Liste, zwei Empfänger, zwei Lebensdauern. Der Echtzeitstrom hängt an
 * einer `conn_id` und ist mit der Verbindung weg; die Push-Adresse überlebt den
 * geschlossenen Tab — das ist ihr einziger Zweck. Ein gemeinsames Melden hieße,
 * dass ein Netzwechsel die Push-Adresse mitnimmt.
 *
 * Die Liste selbst kommt trotzdem von dort. Sie zweimal zu führen wäre die
 * sichere Art, sie auseinanderlaufen zu lassen.
 *
 * ## Keine Importe außer dem API-Client
 *
 * Mit Absicht, und derselbe Grund wie bei `mailboxNachweis.ts`: `api/social.ts`
 * liest hier den eigenen Abdruck, um ihn an jeden Umschlag zu hängen. Griffe
 * diese Datei ihrerseits nach `api/social.ts`, stünde ein Importzyklus da.
 */

import { api } from '@/api/client'

export interface MailboxPushEintrag {
  mailbox_id: string
  mailbox_token?: string
}

/** Dieselbe Obergrenze wie im Backend (`MAX_MAILBOXES`). */
const HOECHSTZAHL = 200

/**
 * Der SHA-256 der eigenen Zustelladresse, sobald er einmal gerechnet wurde.
 *
 * Er hängt an jedem Umschlag, den dieser Browser absendet, und hält ihn aus der
 * eigenen Zustellung heraus. Ohne ihn bekäme man die Meldung über die eigene
 * Nachricht: auf dem Mailbox-Weg gibt es keine Empfängerkennung mehr, an der
 * der Server den Absender erkennen könnte.
 */
let eigenerAbdruck = ''

/** Was zuletzt gemeldet wurde — damit dieselbe Liste nicht zweimal rausgeht. */
let gemeldet = ''

export function eigenerPushAbdruck(): string {
  return eigenerAbdruck
}

function abdruckDerListe(eintraege: MailboxPushEintrag[], endpunkt: string): string {
  return `${endpunkt}|${eintraege.map((e) => `${e.mailbox_id}:${e.mailbox_token ?? ''}`).join(',')}`
}

async function sha256Hex(text: string): Promise<string> {
  const roh = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(roh))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

function kannPush(): boolean {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window
  )
}

/**
 * Die Zustelladresse dieses Browsers, falls es sie schon gibt.
 *
 * Angelegt wird sie in `pushAbo.abonniere()` bei der Anmeldung. Hier wird
 * ausdrücklich **nicht** abonniert: ein `subscribe()` an dieser Stelle könnte
 * einen Berechtigungsdialog auslösen, den niemand angefordert hat, und wäre der
 * schnellste Weg zu einem dauerhaften „blockiert".
 */
async function eigeneAdresse(): Promise<{ endpoint: string; p256dh: string; auth: string } | null> {
  if (!kannPush()) return null
  try {
    // `serviceWorker.ready` löst nie auf, wenn keine Registrierung existiert —
    // im Tauri-Fenster der Normalfall. Deshalb erst fragen.
    if (!(await navigator.serviceWorker.getRegistration())) return null
    const reg = await navigator.serviceWorker.ready
    const abo = await reg.pushManager.getSubscription()
    if (!abo) return null

    const alsBase64url = (puffer: ArrayBuffer | null): string => {
      if (!puffer) return ''
      const bytes = new Uint8Array(puffer)
      let text = ''
      for (let i = 0; i < bytes.length; i += 1) text += String.fromCharCode(bytes[i])
      return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
    }
    const p256dh = alsBase64url(abo.getKey('p256dh'))
    const auth = alsBase64url(abo.getKey('auth'))
    if (!p256dh || !auth) return null
    return { endpoint: abo.endpoint, p256dh, auth }
  } catch {
    return null
  }
}

/**
 * Meldet diesem Browser die Mailboxen, über die er Bescheid bekommen soll.
 *
 * Immer die **ganze** Liste, nie ein Zuwachs: das Backend räumt ab, was nicht
 * mehr genannt wird. Eine verlassene Gruppe, die als Zustellziel stehenbliebe,
 * hieße sonst ein Gerät, das weiter über Nachrichten gemeldet bekommt, die es
 * nicht mehr lesen kann.
 *
 * Alle Fehlschläge sind still. Push ist eine Verbesserung, kein Bestandteil des
 * Messengers — er läuft im Vordergrund über den Echtzeitstrom, und der hat mit
 * dieser Datei nichts zu tun.
 */
export async function meldeMailboxPush(eintraege: MailboxPushEintrag[]): Promise<void> {
  const adresse = await eigeneAdresse()
  if (!adresse) return

  if (!eigenerAbdruck) {
    try {
      eigenerAbdruck = await sha256Hex(adresse.endpoint)
    } catch {
      // Ohne Abdruck geht es weiter — nur bekommt dieser Browser dann die
      // Meldung über seine eigene Nachricht. Kein Grund, gar nicht zu melden.
    }
  }

  const liste = eintraege.slice(0, HOECHSTZAHL)
  const abdruck = abdruckDerListe(liste, adresse.endpoint)
  if (abdruck === gemeldet) return

  try {
    await api('/social/e2ee/mailbox-push', {
      method: 'POST',
      body: JSON.stringify({ ...adresse, eintraege: liste }),
    })
    gemeldet = abdruck
  } catch {
    // Nicht merken, was nicht angekommen ist: sonst bliebe die Mailbox bis zur
    // nächsten Änderung ohne Zustelladresse.
  }
}

/**
 * Trägt diesen Browser aus allen Mailboxen aus. Beim Abmelden zwingend.
 *
 * Ohne diesen Schritt bekäme das Gerät weiter Meldungen über Mailboxen, deren
 * Schlüssel mit der Abmeldung aus dem Speicher gefallen sind — eine Meldung
 * über eine Nachricht, die dieser Browser nicht mehr lesen kann.
 */
export async function kuendigeMailboxPush(): Promise<void> {
  gemeldet = ''
  const adresse = await eigeneAdresse()
  if (!adresse) return
  try {
    await api(`/social/e2ee/mailbox-push?endpoint=${encodeURIComponent(adresse.endpoint)}`, {
      method: 'DELETE',
    })
  } catch {
    // Die Sitzung ist womöglich schon abgelaufen. Der Server räumt die Zeilen
    // spätestens beim nächsten 410 des Push-Dienstes ab.
  }
}

/** Nur für Tests und das Abmelden: den gemerkten Stand vergessen. */
export function leereMailboxPush(): void {
  gemeldet = ''
  eigenerAbdruck = ''
}
