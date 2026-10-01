/**
 * Die Zurück-Taste schließt, was oben liegt, statt die Seite oder die App zu
 * verlassen.
 *
 * Unter Android geht Zurück im WebView einen Verlaufsschritt zurück; gibt es
 * keinen, beendet sich die App. Bis 01.10.2026 legte keine Ansicht einen an:
 * wer in der App eine Datei, ein Foto oder einen Dialog offen hatte, flog mit
 * Zurück ganz hinaus. Im Browser gilt dasselbe für die Zurück-Taste und die
 * Maustaste „Zurück“.
 *
 * Jede offene Ansicht bekommt genau einen Verlaufseintrag (`msmTiefe` im
 * Zustand, der Zustand des Routers bleibt erhalten). Zurück schließt die
 * zuletzt geöffnete. Wer anders schließt (X, Escape, Speichern), nimmt seinen
 * Eintrag wieder heraus. Öffnet das Schließen erst eine Rückfrage (ungesicherte
 * Änderungen), bekommt die ihren eigenen Eintrag. Lehnt eine Ansicht das
 * Schließen ab (sie arbeitet noch), bekommt sie ihren Eintrag kurz danach
 * zurück: Zurück bleibt dann wirkungslos, statt beim nächsten Druck die App zu
 * beenden.
 *
 * Abgeglichen wird gebündelt nach dem Rendern. React montiert Effekte im
 * Entwicklungsmodus doppelt; ein sofortiges `history.back()` beim Abbauen liefe
 * dann in den neuen Eintrag hinein.
 */

import { useEffect, useRef } from 'react'

interface Offen {
  schliessen: () => void
}

/** Offene Ansichten in der Reihenfolge, in der sie aufgingen. */
const stapel: Offen[] = []
/** Wie viele unserer Einträge über dem Ausgangspunkt liegen. */
let gesetzt = 0
/** Ein eigener Sprung zurück läuft; sein `popstate` gehört uns. */
let eigenerSprung = false
let geplant = false
let horcht = false
/** Bis dahin hat React eine geschlossene Ansicht längst abgebaut. */
const NACHSCHAU_MS = 300

function tiefeJetzt(): number {
  const zustand = window.history.state as { msmTiefe?: unknown } | null
  return typeof zustand?.msmTiefe === 'number' ? zustand.msmTiefe : 0
}

function abgleichen() {
  geplant = false
  if (eigenerSprung) return
  // Hat inzwischen jemand anderes navigiert (Router), stehen wir nicht mehr auf
  // unserem Eintrag. Dann zählt, wo wir sind; zurückgesprungen wird nie über
  // fremde Einträge hinweg.
  if (tiefeJetzt() !== gesetzt) gesetzt = Math.min(tiefeJetzt(), stapel.length)
  while (gesetzt < stapel.length) {
    gesetzt += 1
    window.history.pushState({ ...(window.history.state ?? {}), msmTiefe: gesetzt }, '')
  }
  if (gesetzt > stapel.length && tiefeJetzt() === gesetzt) {
    eigenerSprung = true
    const schritte = gesetzt - stapel.length
    gesetzt = stapel.length
    window.history.go(-schritte)
  }
}

function planen() {
  if (geplant) return
  geplant = true
  queueMicrotask(abgleichen)
}

function beiZurueck() {
  if (eigenerSprung) {
    eigenerSprung = false
    planen()
    return
  }
  const ziel = tiefeJetzt()
  gesetzt = Math.min(ziel, gesetzt)
  // Von oben nach unten: was über dem Ziel liegt, schließt. Abgeglichen wird
  // erst, wenn die Ansichten wirklich abgebaut sind; ein Abgleich vorher legte
  // ihre Einträge gleich wieder an.
  for (let i = stapel.length - 1; i >= ziel; i--) stapel[i]?.schliessen()
  setTimeout(planen, NACHSCHAU_MS)
}

/**
 * Solange `offen`, schließt Zurück diese Ansicht über `schliessen`. Ansichten,
 * die nur existieren, solange sie offen sind (Lichtbox, Dialoginhalt),
 * übergeben `true`.
 */
export function useZurueckSchliesst(offen: boolean, schliessen: () => void): void {
  const aktuell = useRef(schliessen)
  aktuell.current = schliessen

  useEffect(() => {
    if (!offen || typeof window === 'undefined') return
    if (!horcht) {
      window.addEventListener('popstate', beiZurueck)
      horcht = true
    }
    const eintrag: Offen = { schliessen: () => aktuell.current() }
    stapel.push(eintrag)
    planen()
    return () => {
      const stelle = stapel.indexOf(eintrag)
      if (stelle >= 0) stapel.splice(stelle, 1)
      planen()
    }
  }, [offen])
}
