/**
 * Zahlungskarten und Bankkonten im Tresor (Art `zahlung`).
 *
 * Gespeichert wird nur, was zum Bezahlen nötig ist, im verschlüsselten
 * Umschlag wie jeder andere Eintrag. Die Bezeichnung steht in `service`,
 * Notizen in `notes`. Ohne Store und ohne Netz.
 */
import type { VaultItem } from './vaultEintrag'

export const ZAHLUNG_KATEGORIE = 'zahlung'

export interface KartenAngaben {
  art: 'karte'
  /** Nur Ziffern, 12 bis 19. */
  nummer: string
  inhaber?: string
  /** 1 bis 12. */
  monat?: number
  /** Vierstellig. */
  jahr?: number
  /** Kartenprüfnummer (CVC), drei oder vier Ziffern. */
  pruefnummer?: string
}

export interface KontoAngaben {
  art: 'konto'
  /** Großbuchstaben und Ziffern, ohne Leerzeichen. */
  iban: string
  inhaber?: string
  bic?: string
}

export type ZahlungAngaben = KartenAngaben | KontoAngaben

const NAME_MAX = 100
const KARTENNUMMER = /^\d{12,19}$/
const PRUEFNUMMER = /^\d{3,4}$/
const IBAN = /^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$/
const BIC = /^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$/

const optional = (wert: unknown, pruefen: (w: unknown) => boolean) => wert === undefined || pruefen(wert)
const name = (w: unknown) => typeof w === 'string' && w.length <= NAME_MAX
const ganz = (w: unknown, von: number, bis: number) => typeof w === 'number' && Number.isInteger(w) && w >= von && w <= bis

/** Prüft den Aufbau beim Lesen eines Umschlags; Prüfsummen prüft erst die Eingabe. */
export function istZahlungAngaben(wert: unknown): wert is ZahlungAngaben {
  if (!wert || typeof wert !== 'object') return false
  const z = wert as Record<string, unknown>
  if (z.art === 'karte') {
    return (
      typeof z.nummer === 'string' &&
      KARTENNUMMER.test(z.nummer) &&
      optional(z.inhaber, name) &&
      optional(z.monat, (w) => ganz(w, 1, 12)) &&
      optional(z.jahr, (w) => ganz(w, 2000, 2199)) &&
      optional(z.pruefnummer, (w) => typeof w === 'string' && PRUEFNUMMER.test(w))
    )
  }
  return (
    z.art === 'konto' &&
    typeof z.iban === 'string' &&
    IBAN.test(z.iban) &&
    optional(z.inhaber, name) &&
    optional(z.bic, (w) => typeof w === 'string' && BIC.test(w))
  )
}

/** Luhn-Prüfsumme einer Kartennummer. */
export function luhnStimmt(nummer: string): boolean {
  if (!KARTENNUMMER.test(nummer)) return false
  let summe = 0
  for (let i = 0; i < nummer.length; i++) {
    let ziffer = Number(nummer[nummer.length - 1 - i])
    if (i % 2 === 1) {
      ziffer *= 2
      if (ziffer > 9) ziffer -= 9
    }
    summe += ziffer
  }
  return summe % 10 === 0
}

/** Prüfziffer einer IBAN nach ISO 13616 (Rest 1 bei Division durch 97). */
export function ibanStimmt(iban: string): boolean {
  if (!IBAN.test(iban)) return false
  const umgestellt = iban.slice(4) + iban.slice(0, 4)
  let rest = 0
  for (const zeichen of umgestellt) {
    const wert = zeichen >= 'A' ? String(zeichen.charCodeAt(0) - 55) : zeichen
    for (const ziffer of wert) rest = (rest * 10 + Number(ziffer)) % 97
  }
  return rest === 1
}

/** Ohne Steuer-, Format- und Richtungszeichen: ein Name zeigt, was er ist. */
export const ohneSteuerzeichen = (text: string) => text.replace(/[\p{Cc}\p{Cf}]/gu, '').trim()

/** Wie getippt, ohne Leerzeichen und Bindestriche. */
export const nurZiffern = (text: string) => text.replace(/[\s-]/g, '')
export const ibanAusEingabe = (text: string) => text.replace(/\s/g, '').toUpperCase()

export type Kartenmarke = 'visa' | 'mastercard' | 'amex'

export function kartenmarke(nummer: string): Kartenmarke | null {
  if (/^4/.test(nummer)) return 'visa'
  if (/^3[47]/.test(nummer)) return 'amex'
  const sechs = Number(nummer.slice(0, 6))
  if (/^5[1-5]/.test(nummer) || (sechs >= 222100 && sechs <= 272099)) return 'mastercard'
  return null
}

/** In Vierergruppen; American Express als 4-6-5 wie auf der Karte. */
export function nummerGruppiert(nummer: string): string {
  if (kartenmarke(nummer) === 'amex' && nummer.length === 15) return `${nummer.slice(0, 4)} ${nummer.slice(4, 10)} ${nummer.slice(10)}`
  return nummer.replace(/(.{4})(?=.)/g, '$1 ')
}

/** Nur das Ende, für Listen und Rückfragen. */
export function verdeckt(z: ZahlungAngaben): string {
  return `•••• ${(z.art === 'karte' ? z.nummer : z.iban).slice(-4)}`
}

/** „MM/JJ“ oder leer. */
export function ablaufText(k: KartenAngaben): string {
  if (!k.monat || !k.jahr) return ''
  return `${String(k.monat).padStart(2, '0')}/${String(k.jahr % 100).padStart(2, '0')}`
}

/** Abgelaufen ist eine Karte nach dem letzten Tag ihres Monats. */
export function istAbgelaufen(k: KartenAngaben, jetzt = new Date()): boolean {
  if (!k.monat || !k.jahr) return false
  return jetzt.getFullYear() * 12 + jetzt.getMonth() > k.jahr * 12 + (k.monat - 1)
}

/** Was in der Liste steht: nicht im Papierkorb, nicht im Archiv; gesucht wird in Bezeichnung, Inhaber und den letzten vier Stellen. */
export function zahlungsmittel(items: VaultItem[], suche = ''): (VaultItem & { zahlung: ZahlungAngaben })[] {
  const begriff = suche.trim().toLowerCase()
  return items
    .filter((i): i is VaultItem & { zahlung: ZahlungAngaben } => i.category === ZAHLUNG_KATEGORIE && !!i.zahlung && !i.trashedAt && !i.archivedAt)
    .filter((i) => !begriff || [i.service, i.zahlung.inhaber ?? '', verdeckt(i.zahlung)].some((w) => w.toLowerCase().includes(begriff)))
    .sort((a, b) => a.zahlung.art.localeCompare(b.zahlung.art) || a.service.localeCompare(b.service))
}
