/**
 * Terminerinnerungen, die Android selbst zur rechten Zeit zeigt.
 *
 * Bis 03.10.2026 sollte ein eigener Dienst alle 25 s beim Panel nachfragen. Er
 * las Datei und Token am falschen Ort und hatte nie einen gültigen Zugang;
 * Erinnerungen kamen auf dem Handy nur bei offener App. Jetzt plant die offene
 * App die nächsten Tage selbst (`erinnerungen_planen`), mit dem Titel, den nur
 * dieses Gerät entschlüsseln kann, und Android meldet sie auch bei
 * geschlossener App. Ein Termin, der auf einem anderen Gerät entsteht, kommt
 * erst mit dem nächsten Öffnen hinzu.
 *
 * Die Stufen sind dieselben wie in `CalendarService.get_due_reminders`: 49 und
 * 25 Stunden vor Beginn, derselbe Schlüssel je Vorkommen und Stufe.
 */
import type { KalenderVorkommen } from '@/pages/Calendar'

export interface GeplanteErinnerung {
  schluessel: string
  /** Wann Android sie zeigt, Millisekunden seit 1970. */
  zeit: number
  titel: string
  text: string
  /** Was auf dem Sperrbildschirm steht: kein Titel, kein Ort. */
  oeffentlich: string
}

/** Wie viele Tage im Voraus geplant wird. Danach braucht es ein erneutes Öffnen. */
export const PLAN_TAGE = 8
/** Android hält je App höchstens 500 Wecker; das hier bleibt weit darunter. */
export const HOECHSTENS = 100

const STUNDE = 3_600_000

type Texte = (schluessel: string, werte?: Record<string, unknown>) => string

export function erinnerungenAus(
  termine: KalenderVorkommen[],
  jetzt: number,
  konto: number,
  t: Texte,
  zeitFormat: (datum: Date) => string,
  unlesbar: (titel: string) => boolean,
): GeplanteErinnerung[] {
  const plan: GeplanteErinnerung[] = []
  for (const termin of termine) {
    const beginn = new Date(termin.start).getTime()
    if (!Number.isFinite(beginn) || beginn < jetzt) continue
    const titel = unlesbar(termin.title) ? t('notifications.reminderUntitled') : termin.title
    const stufen: Array<{ stufe: '48h' | '24h'; ab: number; bis: number }> = [
      { stufe: '48h', ab: beginn - 49 * STUNDE, bis: beginn - 25 * STUNDE },
      { stufe: '24h', ab: beginn - 25 * STUNDE, bis: beginn },
    ]
    for (const { stufe, ab, bis } of stufen) {
      // Eine Stufe, deren Fenster schon vorbei ist, fällt weg; eine, in deren
      // Fenster wir gerade stehen, kommt sofort. Android merkt sich gezeigte
      // Schlüssel und zeigt keinen zweimal (`Erinnerungen.kt`).
      if (bis <= jetzt) continue
      const zeit = Math.max(ab, jetzt)
      const wann =
        stufe === '48h'
          ? t('notifications.whenTwoDays')
          : beginn - zeit > 2 * STUNDE
            ? t('notifications.whenOneDay')
            : t('notifications.whenSoon')
      plan.push({
        schluessel: `${konto}_${termin.event_id}_${termin.vorkommen}_${stufe}`,
        zeit,
        titel: t('notifications.reminderTitle', { wann }),
        text: t('notifications.reminderText', { titel, start: zeitFormat(new Date(beginn)) }),
        oeffentlich: t('notifications.reminderPublic'),
      })
    }
  }
  plan.sort((a, b) => a.zeit - b.zeit)
  return plan.slice(0, HOECHSTENS)
}
