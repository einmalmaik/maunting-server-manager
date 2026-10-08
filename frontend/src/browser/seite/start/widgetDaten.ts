/**
 * Was die Widgets der Startseite zeigen. Termine und Notizen kommen zuerst aus
 * dem lokalen Spiegel (`lib/offlineSync.ts`), angemeldet danach einmal vom
 * Panel, höchstens alle fünf Minuten: jeder neue Tab zeigt eine Startseite, und
 * jeder sollte nicht beim Panel nachfragen. `offlineSync` wird erst hier
 * geladen, damit es samt Kryptografie nicht im Startbündel liegt (AGENTS.md
 * Punkt 48).
 */
import { useEffect, useState } from 'react'

import type { NoteItem } from '@/pages/Notes'
import type { KalenderVorkommen } from '@/pages/Calendar'

import { useSitzung } from '../../services/sitzung'

const AUFFRISCHEN_MS = 5 * 60_000
const TAG_MS = 24 * 3600_000
export const ANZAHL = 4

const geholt = { termine: 0, notizen: 0 }

const laden = () => Promise.all([import('@/lib/offlineSync'), import('@/services/notesCalendarCrypto')])

/** Für Tests: der nächste Aufruf fragt wieder beim Panel nach. */
export function abrufeVergessen() {
  geholt.termine = 0
  geholt.notizen = 0
}

/** Die aktuelle Zeit, neu zu jeder vollen Minute. */
export function useJetzt(): number {
  const [jetzt, setJetzt] = useState(() => Date.now())
  useEffect(() => {
    let uhr: ReturnType<typeof setTimeout>
    const weiter = () => {
      uhr = setTimeout(() => {
        setJetzt(Date.now())
        weiter()
      }, 60_000 - (Date.now() % 60_000))
    }
    weiter()
    return () => clearTimeout(uhr)
  }, [])
  return jetzt
}

/** Was ein Titel ist, solange er nicht entschlüsselt ist: `null`, die Ansicht sagt es. */
function lesbar(text: string | undefined | null, praefix: string): string | null {
  if (!text || text.startsWith(praefix)) return null
  return text
}

export interface Termin {
  schluessel: string
  titel: string | null
  start: Date
  ganztags: boolean
}

export interface Notiz {
  schluessel: string
  titel: string | null
  geaendert: Date
}

/** `praefix`: woran ein noch verschlüsselter Titel zu erkennen ist (`sv-cal-v1:`). */
export function naechsteTermine(vorkommen: KalenderVorkommen[], jetzt: number, praefix: string): Termin[] {
  return vorkommen
    .filter((v) => new Date(v.end).getTime() > jetzt)
    .slice(0, ANZAHL)
    .map((v) => ({ schluessel: v.schluessel, titel: lesbar(v.title, praefix), start: new Date(v.start), ganztags: Boolean(v.all_day) }))
}

export function letzteNotizen(notizen: NoteItem[], praefix: string): Notiz[] {
  return notizen
    .filter((n) => !n.is_archived)
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
    .slice(0, ANZAHL)
    .map((n) => {
      const erste = lesbar(n.content, praefix)?.split('\n').find((z) => z.trim())
      return { schluessel: String(n.note_uid ?? n.id), titel: lesbar(n.title, praefix) || erste?.trim().slice(0, 80) || null, geaendert: new Date(n.updated_at) }
    })
}

/** `null`, bis der Spiegel gelesen ist. */
export function useTermine(jetzt: number): Termin[] | null {
  const stand = useSitzung((s) => s.stand)
  const [termine, setTermine] = useState<Termin[] | null>(null)
  useEffect(() => {
    let weg = false
    const von = new Date(jetzt).toISOString()
    const bis = new Date(jetzt + 7 * TAG_MS).toISOString()
    void laden()
      .then(async ([sync, { CALENDAR_CIPHERTEXT_PREFIX: praefix }]) => {
        if (weg) return
        setTermine(naechsteTermine(sync.kalenderVorkommenLokal(von, bis), jetzt, praefix))
        if (stand !== 'an' || Date.now() - geholt.termine < AUFFRISCHEN_MS) return
        geholt.termine = Date.now()
        const { events } = await sync.loadCalendarEventsOfflineFirst(von, bis)
        if (!weg) setTermine(naechsteTermine(events, jetzt, praefix))
      })
      .catch(() => undefined)
    return () => {
      weg = true
    }
  }, [jetzt, stand])
  return termine
}

export function useNotizen(jetzt: number): Notiz[] | null {
  const stand = useSitzung((s) => s.stand)
  const [notizen, setNotizen] = useState<Notiz[] | null>(null)
  useEffect(() => {
    let weg = false
    void laden()
      .then(async ([sync, { NOTE_CIPHERTEXT_PREFIX: praefix }]) => {
        if (weg) return
        setNotizen(letzteNotizen(sync.getOfflineNotes(), praefix))
        if (stand !== 'an' || Date.now() - geholt.notizen < AUFFRISCHEN_MS) return
        geholt.notizen = Date.now()
        const { notes } = await sync.loadNotesOfflineFirst()
        if (!weg) setNotizen(letzteNotizen(notes, praefix))
      })
      .catch(() => undefined)
    return () => {
      weg = true
    }
  }, [jetzt, stand])
  return notizen
}
