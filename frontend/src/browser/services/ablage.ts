/**
 * Die lokale Ablage der Browser-Stores (Tabs, Verlauf, Einstellungen).
 *
 * zustand persist schreibt bei jedem `set` synchron in den localStorage. Ist
 * er voll, wirft jedes `set`, und Ereignisse brachen mittendrin ab: eine Seite,
 * die sich ein paar Mal mit Adressen von 1 MB selbst lud, legte so Verlauf,
 * Lesezeichen und Tabs lahm (bis 09.10.2026). Hier scheitert nur das
 * Speichern, und es wird gesagt, höchstens einmal je Minute.
 */
import { createJSONStorage } from 'zustand/middleware'

import i18n from '@/i18n'
import { toast } from '@/stores/toastStore'

/** Länger wird keine Adresse und kein Titel gespeichert, den eine Seite vorgibt. */
export const ADRESSE_MAX = 2048
export const TITEL_MAX = 300

let zuletztGesagt = 0

export const ablage = createJSONStorage(() => ({
  getItem: (name: string) => localStorage.getItem(name),
  setItem: (name: string, wert: string) => {
    try {
      localStorage.setItem(name, wert)
    } catch {
      if (Date.now() - zuletztGesagt < 60_000) return
      zuletztGesagt = Date.now()
      toast.error(i18n.t('browser.ablageVoll'))
    }
  },
  removeItem: (name: string) => localStorage.removeItem(name),
}))

/**
 * Aus einem gespeicherten Stand nur die Werte, die den Typ der Vorgabe haben.
 * Ein Stand einer anderen Fassung oder ein halb geschriebener Wert
 * (`ausgeblendet: null`) legte sonst die Oberfläche lahm (bis 09.10.2026).
 * Wo die Vorgabe `null` ist, gilt ein einfacher Wert oder `null`.
 */
export function passend<T extends object>(vorgabe: T, gespeichert: unknown): Partial<T> {
  if (!gespeichert || typeof gespeichert !== 'object') return {}
  const bekannt = vorgabe as Record<string, unknown>
  const ergebnis: Record<string, unknown> = {}
  for (const [name, wert] of Object.entries(gespeichert)) {
    if (!(name in bekannt) || typeof bekannt[name] === 'function') continue
    const soll = bekannt[name]
    const ok = Array.isArray(soll)
      ? Array.isArray(wert)
      : soll === null
        ? wert === null || ['string', 'number', 'boolean'].includes(typeof wert)
        : typeof wert === typeof soll && wert !== null && !Array.isArray(wert)
    if (ok) ergebnis[name] = wert
  }
  return ergebnis as Partial<T>
}

/** Ein Titel, wie er gespeichert wird. */
export function kurzerTitel(titel: string): string {
  return titel.length > TITEL_MAX ? `${titel.slice(0, TITEL_MAX - 1)}…` : titel
}
