/**
 * Was das Nachrichten-Widget zeigt. Die Schlagzeilen kommen vom eigenen Server
 * (`GET /api/browser/nachrichten`, über dessen SearXNG), für alle Konten
 * gleich; Themen und Schlagworte filtert nur der Browser, sie verlassen das
 * Gerät nicht. Zuerst gilt der lokale Spiegel, beim Panel wird höchstens alle
 * 30 Minuten nachgefragt, und nur gekoppelt.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '@/api/client'

import { NACHRICHTEN_THEMEN, type NachrichtenThema } from '../../services/einstellungenStore'
import { useSitzung } from '../../services/sitzung'

export interface Meldung {
  titel: string
  url: string
  inhalt: string
  /** ISO-Zeit der Veröffentlichung, wenn die Quelle eine nennt. */
  zeit: string | null
}

export interface NachrichtenStand {
  sprache: string
  themen: Partial<Record<NachrichtenThema, Meldung[]>>
}

export const SPIEGEL = 'msb:nachrichten'
const AUFFRISCHEN_MS = 30 * 60_000
export const NACHRICHTEN_ANZAHL = 6

const geholt: Record<string, number> = {}

/** Für Tests: der nächste Aufruf fragt wieder beim Panel nach. */
export function nachrichtenVergessen() {
  for (const k of Object.keys(geholt)) delete geholt[k]
}

function istMeldung(m: unknown): m is Meldung {
  const x = m as Record<string, unknown> | null
  return (
    !!x &&
    typeof x.titel === 'string' &&
    typeof x.url === 'string' &&
    x.url.startsWith('https://') &&
    typeof x.inhalt === 'string' &&
    (x.zeit === null || typeof x.zeit === 'string')
  )
}

/** Was vom Server oder aus dem Spiegel kommt, wird geprüft wie eine Eingabe (AGENTS.md Punkt 137). */
export function standLesen(roh: unknown, sprache: string): NachrichtenStand | null {
  const themen = (roh as { themen?: unknown } | null)?.themen
  if (!themen || typeof themen !== 'object') return null
  const stand: NachrichtenStand = { sprache, themen: {} }
  for (const thema of NACHRICHTEN_THEMEN) {
    const liste = (themen as Record<string, unknown>)[thema]
    if (Array.isArray(liste)) stand.themen[thema] = liste.filter(istMeldung)
  }
  return stand
}

function spiegelLesen(sprache: string): NachrichtenStand | null {
  try {
    const roh = JSON.parse(localStorage.getItem(SPIEGEL) ?? 'null')
    return roh?.sprache === sprache ? standLesen(roh, sprache) : null
  } catch {
    return null
  }
}

function spiegelSchreiben(stand: NachrichtenStand) {
  try {
    localStorage.setItem(SPIEGEL, JSON.stringify(stand))
  } catch {
    // Voller Speicher: das Widget zeigt den Stand trotzdem, nur nicht nach einem Neustart.
  }
}

function worteAus(text: string): string[] {
  return text
    .split(',')
    .map((w) => w.trim().toLocaleLowerCase())
    .filter(Boolean)
}

/** Die neuesten Meldungen aus den gewählten Themen, ohne doppelte, auf die Schlagworte beschränkt. */
export function auswahl(stand: NachrichtenStand, themen: NachrichtenThema[], worte: string, anzahl = NACHRICHTEN_ANZAHL): Meldung[] {
  const gesucht = worteAus(worte)
  const gesehen = new Set<string>()
  const alle: Meldung[] = []
  for (const thema of NACHRICHTEN_THEMEN) {
    if (!themen.includes(thema)) continue
    for (const m of stand.themen[thema] ?? []) {
      if (gesehen.has(m.url)) continue
      gesehen.add(m.url)
      const text = `${m.titel} ${m.inhalt}`.toLocaleLowerCase()
      if (gesucht.length === 0 || gesucht.some((w) => text.includes(w))) alle.push(m)
    }
  }
  const zeit = (m: Meldung) => (m.zeit ? Date.parse(m.zeit) || 0 : 0)
  return alle.sort((a, b) => zeit(b) - zeit(a)).slice(0, anzahl)
}

export type NachrichtenLage = { stand: NachrichtenStand | null; fehler: 'nichtEingerichtet' | 'fehler' | null }

export function useNachrichten(): NachrichtenLage {
  const { i18n } = useTranslation()
  const sprache = i18n.language.slice(0, 2)
  const sitzung = useSitzung((s) => s.stand)
  const [lage, setLage] = useState<NachrichtenLage>(() => ({ stand: spiegelLesen(sprache), fehler: null }))
  useEffect(() => {
    let weg = false
    setLage((l) => ({ stand: l.stand?.sprache === sprache ? l.stand : spiegelLesen(sprache), fehler: null }))
    if (sitzung !== 'an' || Date.now() - (geholt[sprache] ?? 0) < AUFFRISCHEN_MS) return
    geholt[sprache] = Date.now()
    api<unknown>(`/browser/nachrichten?sprache=${encodeURIComponent(sprache)}`).then(
      (roh) => {
        const stand = standLesen(roh, sprache)
        if (!stand) return
        spiegelSchreiben(stand)
        if (!weg) setLage({ stand, fehler: null })
      },
      (fehler: unknown) => {
        // Beim nächsten Öffnen wieder fragen, nicht erst in 30 Minuten.
        delete geholt[sprache]
        const code = (fehler as { code?: string | null })?.code
        if (!weg) setLage((l) => ({ stand: l.stand, fehler: code === 'BROWSER_NACHRICHTEN_NICHT_EINGERICHTET' ? 'nichtEingerichtet' : 'fehler' }))
      },
    )
    return () => {
      weg = true
    }
  }, [sprache, sitzung])
  return lage
}
