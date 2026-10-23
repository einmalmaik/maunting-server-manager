/**
 * Ein Schlüssel gilt über **alle Fenster dieses Ursprungs** nur einmal.
 *
 * Wozu: Jedes Fenster hängt am E2EE-Ereignisstrom und hört dasselbe Ereignis.
 * Ohne diese Klammer spielte jedes davon den Ton, und bei zwei offenen Tabs
 * kam genau das an, was am 22.10.2026 gemeldet wurde — „manchmal doppelt".
 * `localStorage` ist der einzige Speicher, den zwei Tabs desselben Ursprungs
 * sofort teilen; ein `BroadcastChannel` erreicht nur, wer schon zuhört, und
 * ein Modulzustand gilt je Tab.
 *
 * Nicht für die Ewigkeit: Ein Schlüssel verfällt nach `fristMs`. Ein zweites
 * Ereignis mit demselben Schlüssel — etwa ein erneut zugestellter Umschlag
 * Minuten später — soll wieder melden dürfen.
 *
 * Die Federung ist bewusst weich: Zwei Tabs können im selben Moment lesen,
 * beide finden den Schlüssel frei und beide schreiben. Das Fenster dafür ist
 * ein Lesevorgang. Es zu schließen bräuchte einen Sperrdienst, den es im
 * Browser nicht gibt; die Dopplung, die übrig bleibt, ist ein Ton in einem
 * Millisekundenfenster statt einer bei jeder Nachricht.
 */

/** Ein Eintrag je Schlüssel: bis wann er als verbraucht gilt (ms seit Epoche). */
const ABLAGE = 'msm:meldung:einmalig'

/** Mehr als das halten wir nicht vor. Wer älter ist, ist längst verfallen. */
const HOECHSTZAHL = 100

/**
 * Beansprucht einen Schlüssel. `true` heißt: **dieser** Aufruf darf melden.
 *
 * Ohne Ablage (privater Modus, gesperrter Speicher) meldet jedes Fenster —
 * lieber ein Ton zu viel als eine Nachricht, die gar nicht auffällt.
 */
export function beanspruche(
  schluessel: string,
  fristMs = 5000,
  jetzt: number = Date.now(),
): boolean {
  if (!schluessel) return true
  try {
    const roh = localStorage.getItem(ABLAGE)
    const alle: Record<string, number> = roh ? JSON.parse(roh) : {}
    const bisher = Number(alle?.[schluessel] || 0)
    if (bisher > jetzt) return false

    // Abgelaufenes fällt dabei heraus; das hält die Ablage klein.
    const frisch: Record<string, number> = {}
    for (const [kennung, bis] of Object.entries(alle || {})) {
      const wert = Number(bis)
      if (wert > jetzt) frisch[kennung] = wert
    }
    frisch[schluessel] = jetzt + fristMs

    const eintraege = Object.entries(frisch)
    const gekuerzt = eintraege.length > HOECHSTZAHL ? eintraege.slice(-HOECHSTZAHL) : eintraege
    localStorage.setItem(ABLAGE, JSON.stringify(Object.fromEntries(gekuerzt)))
    return true
  } catch {
    return true
  }
}

/** Nur für Tests und das Abmelden. */
export function vergissEinmalig(): void {
  try {
    localStorage.removeItem(ABLAGE)
  } catch {
    // Ohne Ablage gibt es nichts zu vergessen.
  }
}
