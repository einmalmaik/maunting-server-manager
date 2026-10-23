/**
 * Was aus einem Browserimport in die vorhandenen Ablagen kommt.
 *
 * Reine Funktionen ohne Tauri und ohne Store: der Import liest in Rust, hier
 * wird nur zusammengeführt und begrenzt. Bestehende Lesezeichen und Kacheln
 * bleiben, wo sie sind; importierte Einträge kommen als Block davor.
 */
import type { ImportLesezeichen, ImportTopSite, ImportVerlauf } from './nativ'
import type { Eintrag } from './verlaufStore'
import type { Schnellzugriff } from './einstellungenStore'

export const ADRESSE_MAX = 2048
export const TITEL_MAX = 300
export const ORDNER_NAME_MAX = 80
export const ORDNER_TIEFE_MAX = 24
export const LESEBZEICHEN_MAX = 2000
export const VERLAUF_MAX = 5000
export const BILDER_MAX = 128
export const BILD_LAENGE_MAX = 8 * 1024
export const BILD_GESAMT_MAX = 1024 * 1024
/** Grobe Grenze für `msb:verlauf`; der Quota-Toast der Ablage bleibt die letzte Sicherung. */
export const SPEICHER_VERLAUF_MAX = 4 * 1024 * 1024

const PNG_PRAEFIX = 'data:image/png;base64,'

export interface LesezeichenErgebnis {
  lesezeichen: Eintrag[]
  uebernommen: number
  gekuerzt: boolean
}

export interface VerlaufErgebnis {
  verlauf: Eintrag[]
  uebernommen: number
  gekuerzt: boolean
}

export interface SchnellzugriffErgebnis {
  schnellzugriffe: Schnellzugriff[]
  uebernommen: number
}

/** Nur http(s), ohne Zugangsdaten und ohne Pfad. */
export function herkunft(url: string): string | null {
  try {
    const u = new URL(url)
    if ((u.protocol !== 'http:' && u.protocol !== 'https:') || u.username || u.password) return null
    return u.origin
  } catch {
    return null
  }
}

export function webAdresse(url: string): string | null {
  const text = url.trim()
  if (!text || text.length > ADRESSE_MAX) return null
  try {
    const u = new URL(text)
    if ((u.protocol !== 'http:' && u.protocol !== 'https:') || u.username || u.password) return null
    return text
  } catch {
    return null
  }
}

export function kurzerTitel(titel: string, url: string): string {
  const text = (titel || url).trim() || url
  const zeichen = Array.from(text)
  return zeichen.length > TITEL_MAX ? `${zeichen.slice(0, TITEL_MAX - 1).join('')}…` : text
}

/** Ein gespeicherter Ordnerpfad, bereinigt und begrenzt; kein Fehler bei altem Stand. */
export function ordnerPfad(ordner: unknown): string[] {
  if (!Array.isArray(ordner)) return []
  return ordner
    .filter((teil): teil is string => typeof teil === 'string')
    .map((teil) =>
      Array.from(teil.replace(/[\u0000-\u001f\u007f]/g, '').trim())
        .slice(0, ORDNER_NAME_MAX)
        .join(''),
    )
    .filter(Boolean)
    .slice(0, ORDNER_TIEFE_MAX)
}

/** Lesezeichen gelten als gleich, wenn Adresse und Ordner gleich sind. */
export function lesezeichenSchluessel(url: string, ordner: unknown): string {
  return `${url}\u0000${ordnerPfad(ordner).join('\u0000')}`
}

export function lesezeichenZusammenfuehren(
  bestehend: Eintrag[],
  neu: ImportLesezeichen[],
  max = LESEBZEICHEN_MAX,
): LesezeichenErgebnis {
  const vorhanden = new Set(bestehend.map((e) => lesezeichenSchluessel(e.url, e.ordner)))
  const platz = Math.max(0, max - bestehend.length)
  const importiert: Eintrag[] = []
  let gekuerzt = false

  for (const e of neu) {
    const url = webAdresse(e.url)
    if (!url) continue
    const ordner = ordnerPfad(e.ordner)
    const schluessel = lesezeichenSchluessel(url, ordner)
    if (vorhanden.has(schluessel)) continue
    if (importiert.length >= platz) {
      gekuerzt = true
      break
    }
    vorhanden.add(schluessel)
    const favicon = herkunft(url)
    importiert.push({
      url,
      titel: kurzerTitel(e.titel, url),
      zeit: Number.isFinite(e.zeit) && e.zeit > 0 ? Math.trunc(e.zeit) : 0,
      ...(ordner.length > 0 ? { ordner } : {}),
      ...(favicon ? { favicon } : {}),
    })
  }

  return {
    lesezeichen: [...importiert, ...bestehend].slice(0, Math.max(max, bestehend.length)),
    uebernommen: importiert.length,
    gekuerzt,
  }
}

export function verlaufZusammenfuehren(bestehend: Eintrag[], neu: ImportVerlauf[], max = VERLAUF_MAX): VerlaufErgebnis {
  const verlauf = bestehend.map((e) => ({ ...e }))
  const neuester = new Map<string, number>()
  verlauf.forEach((eintrag, index) => {
    const alt = neuester.get(eintrag.url)
    if (alt === undefined || verlauf[alt].zeit < eintrag.zeit) neuester.set(eintrag.url, index)
  })

  let uebernommen = 0
  for (const e of neu) {
    const url = webAdresse(e.url)
    if (!url) continue
    const zeit = Number.isFinite(e.zeit) && e.zeit > 0 ? Math.trunc(e.zeit) : 0
    if (!zeit) continue
    const anzahl = Number.isFinite(e.anzahl) && e.anzahl > 0 ? Math.trunc(e.anzahl) : 1
    const index = neuester.get(url)
    if (index === undefined) {
      verlauf.push({ url, titel: kurzerTitel(e.titel, url), zeit, anzahl })
      neuester.set(url, verlauf.length - 1)
      uebernommen++
      continue
    }
    const alt = verlauf[index]
    const neuerTitel = zeit >= alt.zeit ? kurzerTitel(e.titel, url) : alt.titel
    const neuerStand = {
      ...alt,
      zeit: Math.max(alt.zeit, zeit),
      anzahl: Math.max(alt.anzahl ?? 1, anzahl),
      titel: neuerTitel,
    }
    if (neuerStand.zeit !== alt.zeit || neuerStand.anzahl !== (alt.anzahl ?? 1) || neuerStand.titel !== alt.titel) {
      uebernommen++
    }
    verlauf[index] = neuerStand
  }

  verlauf.sort((a, b) => b.zeit - a.zeit)
  return { verlauf: verlauf.slice(0, max), uebernommen, gekuerzt: verlauf.length > max }
}

/**
 * Standardkacheln werden nur ersetzt, solange sie unverändert sind. Wer sie
 * schon angepasst hat, behält sie und bekommt die importierten dahinter.
 */
export function schnellzugriffeZusammenfuehren(
  bestehend: Schnellzugriff[],
  neu: ImportTopSite[],
  vorgabe: readonly Schnellzugriff[],
  max = 24,
): SchnellzugriffErgebnis {
  const unveraendert =
    bestehend.length === vorgabe.length &&
    bestehend.every((eintrag, i) => eintrag.url === vorgabe[i].url && eintrag.titel === vorgabe[i].titel)
  const grundlage = unveraendert ? [] : bestehend.slice(0, max)
  const gesehen = new Set(grundlage.map((eintrag) => eintrag.url))
  const importiert: Schnellzugriff[] = []

  for (const e of neu) {
    const url = webAdresse(e.url)
    if (!url || gesehen.has(url)) continue
    if (grundlage.length + importiert.length >= max) break
    gesehen.add(url)
    importiert.push({ url, titel: kurzerTitel(e.titel, url) })
  }
  // Ohne brauchbare Top-Sites bleiben die Standardkacheln stehen.
  if (unveraendert && importiert.length === 0) {
    return { schnellzugriffe: bestehend, uebernommen: 0 }
  }
  return { schnellzugriffe: [...grundlage, ...importiert], uebernommen: importiert.length }
}

export function istBildDaten(daten: unknown): daten is string {
  if (typeof daten !== 'string' || !daten.startsWith(PNG_PRAEFIX) || daten.length > BILD_LAENGE_MAX) return false
  const base64 = daten.slice(PNG_PRAEFIX.length)
  return base64.length > 0 && base64.length % 4 === 0 && /^[A-Za-z0-9+/]+={0,2}$/.test(base64)
}

/** Nur PNG-Datenadressen zu http(s)-Herkünften, höchstens 128 und 1 MiB. */
export function bereinigeBilder(bilder: unknown): Record<string, string> {
  if (!Array.isArray(bilder)) return {}
  const aus: Record<string, string> = {}
  let gesamt = 0
  for (const wert of bilder) {
    if (Object.keys(aus).length >= BILDER_MAX) break
    if (!wert || typeof wert !== 'object') continue
    const bild = wert as { herkunft?: unknown; daten?: unknown }
    if (typeof bild.herkunft !== 'string' || !istBildDaten(bild.daten)) continue
    const h = herkunft(bild.herkunft)
    if (!h || aus[h]) continue
    if (gesamt + bild.daten.length > BILD_GESAMT_MAX) break
    aus[h] = bild.daten
    gesamt += bild.daten.length
  }
  return aus
}

/** Wie viel `msb:verlauf` schon belegt, grob in Zeichen. */
export function belegterPlatz(): number {
  try {
    return localStorage.getItem('msb:verlauf')?.length ?? 0
  } catch {
    return 0
  }
}

/**
 * Vor dem Schreiben: passt der Import noch neben den vorhandenen Stand?
 * Gekappt wird zuerst der älteste importierte Verlauf, dann der letzte
 * importierte Lesezeichenblock.
 */
export function kappeAufBudgets(
  lesezeichen: ImportLesezeichen[],
  verlauf: ImportVerlauf[],
  belegt = 0,
  budget = SPEICHER_VERLAUF_MAX,
): { lesezeichen: ImportLesezeichen[]; verlauf: ImportVerlauf[]; gekuerzt: boolean } {
  let l = [...lesezeichen]
  let v = [...verlauf]
  const frei = Math.max(0, budget - belegt)
  const groesse = (lese: ImportLesezeichen[], his: ImportVerlauf[]) => {
    const text = JSON.stringify({ lesezeichen: lese, verlauf: his })
    return typeof TextEncoder === 'undefined' ? text.length * 2 : new TextEncoder().encode(text).length
  }
  if (l.length + v.length === 0) {
    return { lesezeichen: l, verlauf: v, gekuerzt: false }
  }
  if (frei === 0) {
    return { lesezeichen: [], verlauf: [], gekuerzt: true }
  }
  if (groesse(l, v) <= frei) {
    return { lesezeichen: l, verlauf: v, gekuerzt: false }
  }

  // Die Größe wächst mit jedem Eintrag; die größte passende Anzahl findet die
  // binäre Suche in wenigen Schritten statt einmal je gekapptem Eintrag.
  let gekuerzt = false
  if (v.length > 0) {
    let low = 0
    let high = v.length
    while (low < high) {
      const mid = Math.ceil((low + high) / 2)
      if (groesse(l, v.slice(0, mid)) <= frei) low = mid
      else high = mid - 1
    }
    if (low < v.length) {
      v = v.slice(0, low)
      gekuerzt = true
    }
  }
  if (groesse(l, v) > frei && l.length > 0) {
    let low = 0
    let high = l.length
    while (low < high) {
      const mid = Math.ceil((low + high) / 2)
      if (groesse(l.slice(0, mid), v) <= frei) low = mid
      else high = mid - 1
    }
    if (low < l.length) {
      l = l.slice(0, low)
      gekuerzt = true
    }
  }
  return { lesezeichen: l, verlauf: v, gekuerzt }
}
