/**
 * Sagt dem laufenden Strom, ob dieser Client vorne oder hinten steht.
 *
 * Der Server entscheidet damit, **wer** eine Meldung zeigt
 * (`SyncEventService.zustelllage`) und ob überhaupt ein Push nötig ist. Ohne
 * diese Angabe gälte weiter „irgendeine offene Verbindung unterdrückt den
 * Push" — und ein Fenster im Hintergrund verschluckte die Meldung, während
 * zwei Fenster sie doppelt zeigten.
 *
 * Der Rückweg ist derselbe wie bei `mailboxAbo`: die `conn_id` aus dem
 * `ready`-Signal. Sie stirbt mit der Verbindung; ein neuer Strom meldet sich
 * deshalb von selbst erneut an.
 *
 * Gemeldet wird bei jeder Änderung von Sichtbarkeit oder Fokus — das ist der
 * einzige Moment, in dem sich der Wert ändern kann. Ein Takt wäre Last ohne
 * Erkenntnis.
 */

import { api } from '@/api/client'

export type Praesenz = 'vordergrund' | 'hintergrund'

let kennung: string | null = null
let zuletztGemeldet: Praesenz | null = null
let zuhoererAngehaengt = false

function aktuellerStand(): Praesenz {
  if (typeof document === 'undefined') return 'vordergrund'
  return document.visibilityState === 'visible' ? 'vordergrund' : 'hintergrund'
}

async function melde(erzwingen = false): Promise<void> {
  const kennungJetzt = kennung
  if (!kennungJetzt) return
  const stand = aktuellerStand()
  if (!erzwingen && stand === zuletztGemeldet) return
  zuletztGemeldet = stand
  try {
    await api('/events/presence', {
      method: 'POST',
      body: JSON.stringify({ conn_id: kennungJetzt, zustand: stand }),
    })
  } catch {
    // Kein Netz, alter Server, abgerissener Strom. Beim nächsten Anlass
    // erneut — ein Fehlschlag darf nicht als „schon gemeldet" gelten.
    if (kennung === kennungJetzt) zuletztGemeldet = null
  }
}

/** Ein neuer Strom hat sich vorgestellt. */
export function merkePraesenzKennung(connId: string | null): void {
  const sauber = typeof connId === 'string' ? connId.trim() : ''
  if (!sauber || sauber === kennung) return
  kennung = sauber
  // Ein neuer Strom kennt den alten Stand nicht.
  zuletztGemeldet = null
  haengeZuhoererAn()
  void melde()
}

/** Der Strom ist weg. Was gemeldet war, gilt nicht mehr. */
export function vergissPraesenzKennung(): void {
  kennung = null
  zuletztGemeldet = null
}

/**
 * Der Strom kann mitten im Betrieb die Sichtbarkeit wechseln: Tab in den
 * Hintergrund, Fenster verlieren den Fokus, Bildschirm aus. Genau dann muss
 * der Server es wissen, sonst zeigt niemand die nächste Nachricht.
 */
function haengeZuhoererAn(): void {
  if (zuhoererAngehaengt || typeof document === 'undefined') return
  zuhoererAngehaengt = true
  const beiWechsel = () => void melde()
  document.addEventListener('visibilitychange', beiWechsel)
  if (typeof window !== 'undefined') {
    window.addEventListener('focus', beiWechsel)
    window.addEventListener('blur', beiWechsel)
  }
}

/** Nur für Tests. */
export function setzePraesenzFuerTests(connId: string | null, stand: Praesenz | null): void {
  kennung = connId
  zuletztGemeldet = stand
}
