import { api } from './client'

/**
 * Welcher Notizschlüssel für alle Geräte des Kontos gilt — als Fingerabdruck,
 * mit der Unterschrift des Geräts, das ihn gesetzt hat.
 *
 * Der Server kennt nur den Abdruck (`abdruckVon` in `notesCalendarCrypto`),
 * nie den Schlüssel. Die Geräte glauben ihm den Abdruck nur, wenn die
 * Unterschrift von einem vertrauten eigenen Gerät stammt
 * (`kontoschluesselDaten`).
 */
export interface KontoschluesselStand {
  abdruck: string | null
  /** Wächst mit jedem Setzen; 0, solange nie einer gesetzt war. */
  stand: number
  geraet: string | null
  signatur: string | null
}

function stand(antwort: Partial<KontoschluesselStand> | null | undefined): KontoschluesselStand {
  return {
    abdruck: antwort?.abdruck ?? null,
    stand: typeof antwort?.stand === 'number' ? antwort.stand : 0,
    geraet: antwort?.geraet ?? null,
    signatur: antwort?.signatur ?? null,
  }
}

export async function holeKontoschluessel(): Promise<KontoschluesselStand> {
  return stand(await api<Partial<KontoschluesselStand>>('/notes/kontoschluessel'))
}

/**
 * Setzt den Eintrag als Stand `stand` — genau einer mehr als der geltende.
 *
 * Liefert den Stand danach. War ein anderes Gerät schneller (409), ist das
 * dessen Eintrag — der Aufrufer prüft ihn wie jeden anderen.
 */
export async function setzeKontoschluessel(neu: {
  abdruck: string
  stand: number
  geraet: string
  signatur: string
}): Promise<KontoschluesselStand> {
  try {
    return stand(
      await api<Partial<KontoschluesselStand>>('/notes/kontoschluessel', {
        method: 'PUT',
        body: JSON.stringify(neu),
      }),
    )
  } catch (fehler: any) {
    if (fehler?.status === 409) return holeKontoschluessel()
    throw fehler
  }
}
