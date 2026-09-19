/**
 * Die automatische Sperre — einmal, für Tresor und Messenger.
 *
 * Bis 09/2026 gab es sie nur im Tresor, und zwar dreimal: die Einstellungen im
 * `vaultStore`, die Ereignis-Anmeldungen in `DesktopApp` und noch einmal ein
 * Teil davon in `VaultView`. Als der Messenger einen PIN bekam, wäre das die
 * vierte Fassung geworden.
 *
 * Geteilt wird die Mechanik, nicht die Einstellung. Wer seinen Tresor nach fünf
 * Minuten zugehen lässt, will den Messenger deshalb noch lange nicht nach fünf
 * Minuten zumachen — die beiden haben eigene Schlüssel im localStorage und
 * eigene Zahlen. Gemeinsam ist nur, wie gezählt wird und worauf gehört wird.
 *
 * Die Schlüsselnamen des Tresors bleiben, wie sie waren. Ein umbenannter
 * Schlüssel wäre für jede bestehende Installation eine zurückgesetzte
 * Einstellung, und das ausgerechnet bei einer Sicherheitsfunktion.
 */

/** Wie oft nachgesehen wird, ob die Frist abgelaufen ist. */
export const PRUEFTAKT_MS = 10_000

/** Die Auswahl, die beide Oberflächen anbieten. `0` heißt „nie". */
export const SPERRFRISTEN_MINUTEN = [0, 1, 5, 15, 60] as const

function minutenKey(praefix: string): string {
  return `${praefix}_autolock_minutes`
}

function fensterwechselKey(praefix: string): string {
  return `${praefix}_lock_on_blur`
}

export function liesSperrfrist(praefix: string, standard: number): number {
  try {
    if (typeof localStorage === 'undefined') return standard
    const roh = localStorage.getItem(minutenKey(praefix))
    if (roh === null) return standard
    const zahl = parseInt(roh, 10)
    return Number.isFinite(zahl) && zahl >= 0 ? zahl : standard
  } catch {
    return standard
  }
}

export function schreibeSperrfrist(praefix: string, minuten: number): void {
  try {
    if (typeof localStorage === 'undefined') return
    localStorage.setItem(minutenKey(praefix), String(minuten))
  } catch {
    /* Ohne Ablage bleibt die Einstellung für diese Sitzung. */
  }
}

export function liesFensterwechsel(praefix: string): boolean {
  try {
    if (typeof localStorage === 'undefined') return false
    return localStorage.getItem(fensterwechselKey(praefix)) === 'true'
  } catch {
    return false
  }
}

export function schreibeFensterwechsel(praefix: string, an: boolean): void {
  try {
    if (typeof localStorage === 'undefined') return
    localStorage.setItem(fensterwechselKey(praefix), an ? 'true' : 'false')
  } catch {
    /* Siehe oben. */
  }
}

/** Ist seit der letzten Aktivität genug Zeit vergangen? `0` heißt nie. */
export function fristAbgelaufen(letzteAktivitaet: number, minuten: number): boolean {
  if (minuten <= 0) return false
  return Date.now() - letzteAktivitaet >= minuten * 60_000
}

/**
 * Was der Haken braucht, um eine Sache zu bewachen.
 *
 * Bewusst Funktionen und keine Werte: die Anmeldungen laufen einmal und sollen
 * bei jedem Ereignis den **aktuellen** Stand sehen, nicht den vom Zeitpunkt der
 * Anmeldung. Ein eingefrorener `lockOnWindowBlur` wäre ein Schalter, der erst
 * nach dem nächsten Neuladen wirkt.
 */
export interface AutoSperrQuelle {
  istEntsperrt: () => boolean
  /** Läuft gerade ein Entsperrvorgang? Dann nicht dazwischenfunken. */
  istBeschaeftigt: () => boolean
  sperrtBeiFensterwechsel: () => boolean
  /** Merkt Aktivität — und sperrt, falls die Frist längst abgelaufen war. */
  merkeAktivitaet: () => void
  /** Sperrt, falls die Frist abgelaufen ist. */
  pruefeFrist: () => void
  sperre: () => void
}
