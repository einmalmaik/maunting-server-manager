/**
 * Wischen und langes Drücken: die Rechnerei dahinter.
 *
 * Der Messenger wird überwiegend am Telefon benutzt, und dort sind Geste und
 * Daumen die Hauptbedienung. Antworten, Anpinnen, Archivieren, Reagieren und
 * das Auswählen mehrerer Nachrichten brauchen alle dieselben zwei Erkennungen.
 * Sie stehen hier einmal, ohne Zustand und ohne DOM, damit sie prüfbar bleiben
 * und nicht als `onTouchStart`-Abschrift durch sechs Komponenten wandern.
 *
 * Vorbild und Nachbar ist `videoNotizGesten.ts`, das für die Videonotiz genau
 * so aufgebaut ist.
 *
 * **Eine Geste ist nie der einzige Weg.** Was sich hier auslösen lässt, muss
 * auch über einen sichtbaren Knopf erreichbar sein: eine Geste findet niemand
 * von allein, und wer seine Hände nicht frei bewegen kann, kommt sonst gar
 * nicht hin.
 */

/** So lange muss gedrückt werden, bis ein Langdruck gilt. */
export const LANGDRUCK_MS = 500

/**
 * So weit darf der Finger dabei wandern.
 *
 * Ohne diese Toleranz zählt jedes Scrollen als Langdruck; mit einer zu großen
 * lässt sich eine Liste nicht mehr bewegen, ohne etwas auszulösen. Zehn Punkte
 * sind das, was ein ruhig gehaltener Daumen von selbst zittert.
 */
export const LANGDRUCK_TOLERANZ_PX = 10

/** Ab hier gilt eine waagerechte Bewegung als Wischen. */
export const WISCH_SCHWELLE_PX = 64

/** Weiter als das wandert das Angefasste nicht mit. */
export const WISCH_HOECHSTWEG_PX = 96

/**
 * Um so viel muss die waagerechte Bewegung die senkrechte übertreffen.
 *
 * Sonst löst jeder schräge Daumenzug beim Scrollen eine Antwort aus.
 */
export const WISCH_WAAGERECHT_FAKTOR = 1.5

export type Wischrichtung = 'rechts' | 'links' | null

/** Ob der Finger seit dem Aufsetzen mehr als nur gezittert hat. */
export function hatSichBewegt(
  deltaX: number,
  deltaY: number,
  toleranz: number = LANGDRUCK_TOLERANZ_PX,
): boolean {
  return Math.abs(deltaX) > toleranz || Math.abs(deltaY) > toleranz
}

/**
 * Entscheidet, ob aus einer Bewegung ein Wischen wird, und wohin.
 *
 * `null` heißt „noch nicht weit genug" oder „das war senkrecht gemeint".
 */
export function erkenneWischen(
  deltaX: number,
  deltaY: number,
  schwelle: number = WISCH_SCHWELLE_PX,
): Wischrichtung {
  if (Math.abs(deltaX) < schwelle) return null
  if (Math.abs(deltaX) < Math.abs(deltaY) * WISCH_WAAGERECHT_FAKTOR) return null
  return deltaX > 0 ? 'rechts' : 'links'
}

/**
 * Wie weit das Angefasste dem Finger folgt.
 *
 * Bis zur Schwelle eins zu eins, danach zäh und gedeckelt. Das ist kein
 * Schmuck: der Widerstand ist die Rückmeldung „hier ist die Grenze", ohne die
 * man raten muss, ob die Geste schon zählt.
 */
export function wischWeg(
  deltaX: number,
  schwelle: number = WISCH_SCHWELLE_PX,
  hoechstens: number = WISCH_HOECHSTWEG_PX,
): number {
  const weg = Math.abs(deltaX)
  const gerichtet = deltaX < 0 ? -1 : 1
  if (weg <= schwelle) return deltaX
  return gerichtet * Math.min(hoechstens, schwelle + (weg - schwelle) * 0.35)
}

/**
 * Ein kurzes Rütteln als Rückmeldung, dass eine Geste gegriffen hat.
 *
 * Die einzige Stelle in dieser Datei, die etwas tut statt zu rechnen — sie
 * gehört trotzdem hierher, weil sie zu jeder Geste gehört und sonst an vier
 * Stellen einzeln abgesichert würde. Defensiv, weil iOS `vibrate` nicht kennt
 * und manche Browser es ohne vorangegangene Nutzergeste verweigern.
 */
export function rueckmeldung(dauerMs: number = 12): void {
  try {
    if (typeof navigator === 'undefined') return
    navigator.vibrate?.(dauerMs)
  } catch {
    // Ein ausbleibendes Rütteln ist kein Grund, die Geste scheitern zu lassen.
  }
}
