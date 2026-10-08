/**
 * Welche App gerade läuft: MSS (`desktop`) oder der Browser MSB (`browser`).
 *
 * Die Kopplung meldet es dem Panel (`/auth/devices/redeem`). Nur eine
 * `desktop`-Sitzung bekommt von der KI Werkzeuge für den Rechner; der Browser
 * teilt sich mit MSS Kopplung, Transport und Seiten, aber nicht diese.
 * Der Browser setzt seine Art beim Start, bevor irgendetwas koppelt.
 */
export type GeraeteArt = 'desktop' | 'browser'

let art: GeraeteArt = 'desktop'

export function geraeteArtSetzen(neu: GeraeteArt): void {
  art = neu
}

export function geraeteArt(): GeraeteArt {
  return art
}
