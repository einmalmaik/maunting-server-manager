/**
 * Welche Mailboxen der laufende Echtzeitstrom melden soll.
 *
 * Bis 09/2026 brauchte es das nicht: jede Zustellung lief über das Konto, und
 * der Server schlug selbst nach, wer gemeint ist. Genau dieses Nachschlagen
 * soll ihm genommen werden — und sobald eine Mailbox-Kennung nicht mehr aus
 * einer Gruppen-Id fällt, findet er niemanden mehr. Dann ist die Kennung die
 * Adresse, und der Client muss sagen, welche ihn angehen.
 *
 * Der Strom ist SSE und spricht nur in eine Richtung. Der Rückweg ist die
 * `conn_id`, die im `ready`-Signal steht: mit ihr meldet diese Datei über
 * einen gewöhnlichen Aufruf an, was zu hören ist.
 *
 * Zwei Eigenschaften, die der Aufrufer kennen muss:
 *
 * 1. **Die Liste ersetzt.** Wer eine Gruppe verlässt, meldet die neue Liste
 *    ohne sie — ein Abbestellen gibt es nicht.
 * 2. **Die Reihenfolge ist egal.** Kennung und Strom treffen in beliebiger
 *    Folge ein; was zuerst da ist, wartet auf das andere. Beim Neuverbinden
 *    kommt eine neue `conn_id`, und die Liste geht von selbst erneut raus —
 *    ohne das wäre der Messenger nach jedem Netzwechsel stumm.
 *
 * Dieselbe Liste geht an einen zweiten Empfänger: `mailboxPush.ts` meldet sie
 * als Zustelladresse für den geschlossenen Tab. Sie steht hier und nicht
 * zweimal, weil zwei Listen derselben Mailboxen die sichere Art wären, sie
 * auseinanderlaufen zu lassen — der Strom hat dann Kennungen, von denen Push
 * nichts weiß, und der Benutzer erfährt je nach Tab-Zustand etwas anderes.
 */

import { api } from '@/api/client'
import { meldeMailboxPush } from '@/services/mailboxPush'

export interface MailboxAbo {
  mailboxId: string
  /** Nur nötig, wo der Server die Kennung nicht selbst ausrechnen kann. */
  token?: string | null
}

let stromKennung: string | null = null
let gewuenscht = new Map<string, string | null>()
/** Was zuletzt erfolgreich gemeldet wurde — verhindert dieselbe Meldung zweimal. */
let gemeldet = ''
let laeuft: Promise<void> | null = null

function abdruck(eintraege: Map<string, string | null>): string {
  return [...eintraege.entries()]
    .map(([id, token]) => `${id}:${token ?? ''}`)
    .sort()
    .join('|')
}

function alsListe(): { mailbox_id: string; mailbox_token?: string }[] {
  return [...gewuenscht.entries()].map(([mailbox_id, token]) => ({
    mailbox_id,
    ...(token ? { mailbox_token: token } : {}),
  }))
}

/**
 * Gibt die Liste an den zweiten Empfänger weiter: die Push-Adresse.
 *
 * Getrennt von `melde()` und ohne dessen `conn_id`-Bedingung, denn die beiden
 * haben verschiedene Lebensdauern. Der Strom ist mit der Verbindung weg; die
 * Zustelladresse soll gerade dann noch stehen, wenn keine Verbindung mehr da
 * ist — das ist ihr einziger Zweck. Hinge sie am Strom, nähme ein Netzwechsel
 * sie mit.
 */
function meldePush(): void {
  void meldeMailboxPush(alsListe())
}

/**
 * Schickt die aktuelle Liste, wenn es etwas zu schicken gibt.
 *
 * Läuft nie zweimal gleichzeitig: ein zweiter Aufruf während eines laufenden
 * hängt sich an. Ohne das erzeugte jede Nachrichtenankunft — die den Nachweis
 * berührt — einen eigenen Aufruf, und die gingen überholend durcheinander.
 */
async function melde(): Promise<void> {
  if (!stromKennung) return
  const jetzt = abdruck(gewuenscht)
  if (jetzt === gemeldet) return
  if (laeuft) {
    await laeuft
    // Nach dem Warten neu entscheiden: der vorige Lauf hat vielleicht schon
    // genau diesen Stand gemeldet.
    if (abdruck(gewuenscht) === gemeldet) return
  }

  const kennung = stromKennung
  const stand = abdruck(gewuenscht)
  const eintraege = alsListe()

  laeuft = (async () => {
    try {
      await api('/events/mailboxes', {
        method: 'POST',
        body: JSON.stringify({ conn_id: kennung, eintraege }),
      })
      // Nur merken, wenn es auch angekommen ist — und nur, wenn der Strom
      // derselbe geblieben ist. Nach einem Neuverbinden gehört die Liste
      // erneut raus, egal was vorher gemeldet wurde.
      if (stromKennung === kennung) gemeldet = stand
    } catch {
      // Kein Netz, alter Server, abgerissener Strom. Der nächste Anlass
      // meldet erneut; bis dahin trägt der kontogebundene Weg.
    } finally {
      laeuft = null
    }
  })()
  await laeuft
}

/** Der Strom steht und hat sich vorgestellt. */
export function merkeStromKennung(connId: string | null): void {
  const sauber = typeof connId === 'string' ? connId.trim() : ''
  if (!sauber) return
  if (sauber === stromKennung) return
  stromKennung = sauber
  // Ein neuer Strom kennt die Liste nicht — der Abdruck muss zurück, sonst
  // hielte `melde` sie für schon gemeldet und der Client bliebe stumm.
  gemeldet = ''
  void melde()
}

/** Der Strom ist weg. Was gemeldet war, gilt nicht mehr. */
export function vergissStromKennung(): void {
  stromKennung = null
  gemeldet = ''
}

/**
 * Trägt eine Mailbox in die Liste ein (oder aktualisiert ihr Token).
 *
 * Absichtlich additiv: der Messenger erfährt seine Mailboxen an mehreren
 * Stellen — beim Öffnen eines Gesprächs, beim Ankommen eines Gruppenschlüssels
 * — und keine dieser Stellen kennt die ganze Liste.
 */
export function abonniereMailbox(mailboxId: string, token?: string | null): void {
  const id = (mailboxId || '').trim().toLowerCase()
  if (!id) return
  const bisher = gewuenscht.get(id)
  const neu = token ?? bisher ?? null
  if (gewuenscht.has(id) && bisher === neu) return
  gewuenscht.set(id, neu)
  void melde()
  meldePush()
}

/** Nimmt eine Mailbox aus der Liste. Beim Verlassen einer Gruppe fällig. */
export function kuendigeMailbox(mailboxId: string): void {
  const id = (mailboxId || '').trim().toLowerCase()
  if (!gewuenscht.delete(id)) return
  void melde()
  // Auch hier, und hier besonders: was nicht mehr gemeldet wird, räumt das
  // Backend ab. Ohne diesen Aufruf bliebe die verlassene Gruppe als
  // Zustellziel stehen, und das Gerät bekäme weiter Meldungen über
  // Nachrichten, die es nicht mehr lesen kann.
  meldePush()
}

/** Vergisst alles. Gehört zum Abmelden. */
export function leereMailboxAbos(): void {
  gewuenscht = new Map()
  gemeldet = ''
  stromKennung = null
}

/** Nur für Tests und Diagnose: was gerade gemeldet werden soll. */
export function offeneMailboxAbos(): string[] {
  return [...gewuenscht.keys()].sort()
}
