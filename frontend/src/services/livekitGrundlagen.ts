/**
 * Was der Anruf-Store schon vor dem ersten Anruf braucht, ohne `livekit-client`.
 *
 * Das SDK ist über 500 kB groß und wird erst mit dem ersten Anruf geladen
 * (`ladeLivekit` in `useCallStore.ts`). Vorher braucht der Store nur die
 * Voreinstellung der Bildschirmfreigabe, die Fehlerklasse und das Lesen der
 * Kennung; die stehen deshalb hier und nicht in `livekitRaum.ts`, das sie
 * für alle anderen Leser weiterreicht.
 */

/** Auflösungen, die die Bildschirmfreigabe anbietet. */
export type FreigabeAufloesung = '720p' | '1080p' | '1440p' | '2160p' | 'quelle'
export type FreigabeBildrate = 30 | 60

export interface FreigabeOptionen {
  aufloesung: FreigabeAufloesung
  bildrate: FreigabeBildrate
  /** System- bzw. Spielton mitübertragen. Nur Chromium-Browser können das. */
  systemton: boolean
}

export const FREIGABE_STANDARD: FreigabeOptionen = {
  aufloesung: '1080p',
  bildrate: 60,
  systemton: true,
}

export class E2eeNichtUnterstuetzt extends Error {
  constructor() {
    super(
      'Dieser Browser kann verschlüsselte Anrufe nicht. MSM überträgt Gespräche nur ' +
        'verschlüsselt, deshalb ist der Anruf hier nicht möglich. Aktuelles Chrome, ' +
        'Edge, Firefox oder Safari ab 15.4 funktionieren.',
    )
    this.name = 'E2eeNichtUnterstuetzt'
  }
}

/** `u42` → 42. Gibt `null`, wenn die Kennung nicht von MSM stammt. */
export function benutzerIdAusIdentity(identity: string): number | null {
  const treffer = /^u(\d+)$/.exec(identity)
  return treffer ? Number(treffer[1]) : null
}
