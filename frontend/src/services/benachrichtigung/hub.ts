/**
 * **Ein Ort** entscheidet, ob jemand von einem Ereignis erfährt — und wie.
 *
 * Bis 22.10.2026 lagen dieselben Regeln an vier Stellen: im Backend
 * (`NotificationService`), in `NotificationService.shouldNotify` auf dem
 * Client, als `if (!isForeground)` in `ServerIncidentNotifier` und noch einmal
 * in `sw.js`. Vier Meinungen zu „darf ich melden?" heißt: sie laufen
 * auseinander, und niemand merkt es. Genau das war zu sehen — zwei Töne in
 * zwei Fenstern, und in Pontis gar nichts.
 *
 * Hier steht nur die **Entscheidung** (`planeMeldung`, eine reine Funktion ohne
 * Browser, ohne Netz, ohne Ton) und ihre **Ausführung** (`melde`). Die
 * Darstellung selbst bleibt in `lib/benachrichtigung`, damit jeder Client —
 * Web, Pontis, MSS, Android — denselben Weg geht: er nimmt den nativen Befehl,
 * wenn die App einen hat, und fällt sonst auf Browser-Mittel zurück.
 *
 * Was hier **nicht** passiert: Inhalte bauen. Titel, Text und Toast kommen
 * übersetzt vom Aufrufer. Ein geteilter Baustein mit festem Text stand bis
 * 02.10.2026 auch in der englischen App auf Deutsch (AGENTS.md Punkt 4).
 */

import { sendeGeraeteBenachrichtigung, type BenachrichtigungsErgebnis } from '@/lib/benachrichtigung'
import { NotificationService } from '@/services/notificationService'
import { playNotificationChime } from '@/stores/messengerNotificationStore'
import { toast } from '@/stores/toastStore'

import { ereignisIstNeu } from './ereignisAblage'
import { beanspruche } from './einmalig'

export type MeldungsArt = 'chat' | 'anruf' | 'freundschaft' | 'termin' | 'vorfall' | 'erfolg'

/** Warum nichts gemeldet wird. `null` heißt: gemeldet wird. */
export type MeldungsGrund =
  | 'schalter-aus'
  | 'eigene-aktion'
  | 'steuerung'
  | 'nicht-empfaenger'
  | 'stumm'
  | 'blockiert'
  | 'chat-offen'

export interface MeldungsLage {
  art: MeldungsArt
  /** `user.device_notifications`; fehlt der Wert, gilt „an". */
  schalterAn: boolean
  /** Wer das Ereignis ausgelöst hat, soweit bekannt. */
  absenderId?: number | string | null
  eigeneId?: number | string | null
  /** Wen es trifft, soweit der Server das sagen kann (blinde Mailboxen: nie). */
  empfaengerId?: number | string | null
  istSteuerung?: boolean
  stumm?: boolean
  blockiert?: boolean
  /** Genau dieser Chat ist sichtbar **und** das Fenster hat den Fokus. */
  chatOffen?: boolean
  /** Irgendein Fenster dieses Clients ist sichtbar und im Fokus. */
  fensterImVordergrund?: boolean
  /**
   * Der Server hat **diesen** Client als Zusteller gewählt (`melden`).
   *
   * Zwei Fenster oder MSS und Pontis auf einem Gerät hören dasselbe Ereignis.
   * Ohne die Marke meldete jedes davon; mit ihr meldet genau eines. `false`
   * heisst: anzeigen, zählen, klingeln darf ein anderer.
   */
  meldenErlaubt?: boolean
}

export interface MeldungsPlan {
  /** Ungelesen-Zähler fortschreiben. */
  zaehlen: boolean
  /** Der Zweiklang aus `messengerNotificationStore`. */
  ton: boolean
  /** Kurze Einblendung im Interface. */
  toast: boolean
  /** Meldung des Betriebssystems (bzw. Push, wenn die App zu ist). */
  system: boolean
  grund: MeldungsGrund | null
}

const NICHTS: Omit<MeldungsPlan, 'grund'> = { zaehlen: false, ton: false, toast: false, system: false }

function gleicheKennung(a: number | string | null | undefined, b: number | string | null | undefined): boolean {
  if (a === null || a === undefined || b === null || b === undefined) return false
  const links = String(a).trim()
  const rechts = String(b).trim()
  if (links === rechts) return true
  const linksZahl = Number(links)
  const rechtsZahl = Number(rechts)
  return !Number.isNaN(linksZahl) && !Number.isNaN(rechtsZahl) && linksZahl === rechtsZahl
}

/**
 * Die Entscheidung, ohne Nebenwirkung. Jeder Zweig ist ein Test.
 *
 * Die Reihenfolge ist bindend: der Schalter steht vor allem, die eigene
 * Aktion vor dem Empfänger, und ein offener Chat vor Ton und Meldung. Wer
 * „stumm" vor „eigene Aktion" prüfte, zählte die eigene Nachricht in einem
 * stummen Chat als ungelesen.
 */
export function planeMeldung(lage: MeldungsLage): MeldungsPlan {
  const nichts = (grund: MeldungsGrund): MeldungsPlan => ({ ...NICHTS, grund })

  if (!lage.schalterAn) return nichts('schalter-aus')
  if (NotificationService.isOutgoingEcho(lage.absenderId, lage.eigeneId)) return nichts('eigene-aktion')
  if (lage.istSteuerung) return nichts('steuerung')
  if (lage.empfaengerId != null && lage.eigeneId != null && !gleicheKennung(lage.empfaengerId, lage.eigeneId)) {
    return nichts('nicht-empfaenger')
  }
  if (lage.stumm) return nichts('stumm')
  if (lage.blockiert) return nichts('blockiert')
  if (lage.art === 'chat' && lage.chatOffen) return nichts('chat-offen')

  const vordergrund = lage.fensterImVordergrund ?? false
  // Ein anderer Client dieses Kontos zeigt die Meldung bereits — dann keine
  // zweite des Betriebssystems, auch nicht im Hintergrund.
  const darfSystem = lage.meldenErlaubt !== false

  switch (lage.art) {
    case 'chat':
      // Zähler, Ton und Einblendung immer — im Vordergrund nur keine Meldung
      // des Betriebssystems, weil der Mensch schon hinsieht.
      return { zaehlen: true, ton: true, toast: true, system: !vordergrund && darfSystem, grund: null }
    case 'anruf':
      // Ein Anruf hat seine eigene Oberfläche; ein Toast dazu wäre der zweite
      // Hinweis auf dieselbe Sache.
      return { zaehlen: false, ton: true, toast: false, system: !vordergrund && darfSystem, grund: null }
    case 'freundschaft':
      return { zaehlen: false, ton: false, toast: true, system: !vordergrund && darfSystem, grund: null }
    case 'termin':
    case 'vorfall':
      // Fällige Termine und Server-Vorfälle sollen auch dann ankommen, wenn
      // das Fenster offen ist: sie stehen nirgends sonst im Bild.
      return { zaehlen: false, ton: false, toast: true, system: darfSystem, grund: null }
    case 'erfolg':
      return { zaehlen: false, ton: false, toast: true, system: false, grund: null }
  }
}

export interface Meldungstext {
  /**
   * Macht die Meldung über Fenster hinweg einmalig, z. B. `chat:<mailbox>:<id>`.
   * Ohne Schlüssel meldet jedes Fenster.
   */
  schluessel?: string
  titel: string
  text: string
  /** Abweichender Text für die Einblendung; sonst `titel`. */
  toastText?: string
  /** Wie lange der Schlüssel verbraucht bleibt. */
  fristMs?: number
}

export interface MeldungsErgebnis {
  plan: MeldungsPlan
  /** Nur gesetzt, wenn eine Systemmeldung versucht wurde. */
  system: BenachrichtigungsErgebnis | null
  /** Ein anderes Fenster war schneller; nichts wurde gezeigt. */
  doppelt: boolean
}

/**
 * Entscheiden **und** melden. Genau ein Weg je Ereignis.
 *
 * Der Schlüssel wird auch dann verbraucht, wenn nichts zu tun war — sonst
 * meldete das zweite Fenster die Nachricht, die das erste schon gesehen hat.
 * Umgekehrt gilt: was `planeMeldung` ablehnt, verbraucht nichts.
 */
export async function melde(lage: MeldungsLage, text: Meldungstext): Promise<MeldungsErgebnis> {
  const plan = planeMeldung(lage)
  if (plan.grund) return { plan, system: null, doppelt: false }

  if (text.schluessel && !beanspruche(text.schluessel, text.fristMs ?? 5000)) {
    return { plan, system: null, doppelt: true }
  }

  if (plan.ton) playNotificationChime()
  if (plan.toast) toast.success(text.toastText ?? text.titel)

  let system: BenachrichtigungsErgebnis | null = null
  // Dieselbe Kennung teilt sich diese Seite mit dem Service Worker. Hat der
  // Push sie schon gezeigt, bleibt die Systemmeldung hier aus — Ton und
  // Einblendung bleiben, die gehören zum offenen Fenster.
  const systemErlaubt = plan.system && (await ereignisIstNeu(text.schluessel ?? null))
  if (systemErlaubt) {
    // Ein Weg, der wirft, darf die Meldung nicht mitnehmen: der Aufrufer ist
    // ein Ereignis-Handler, und eine unbehandelte Ablehnung dort ist im Log
    // ein Fehler ohne Adresse.
    system = await sendeGeraeteBenachrichtigung({ titel: text.titel, text: text.text }).catch(
      (fehler: unknown): BenachrichtigungsErgebnis => ({
        weg: 'keiner',
        ok: false,
        fehler: fehler instanceof Error ? fehler.message : String(fehler),
      }),
    )
    if (system && !system.ok) {
      // Nicht verschweigen: Ein Client, der keine Meldung zeigen kann, ist ein
      // Befund (AGENTS.md Punkt 68). Ein Toast wäre hier zu viel — der Grund
      // steht in der Konsole, samt Weg.
      console.warn('[benachrichtigung] Systemmeldung nicht zugestellt', system)
    }
  }

  return { plan, system, doppelt: false }
}
