import { describe, expect, it } from 'vitest'

import de from './de.json'
import en from './en.json'

/**
 * Eine Aussage, eine Schreibweise.
 *
 * Im September 2026 trug die Oberfläche 42 deutsche und 77 englische Paare, die
 * dasselbe sagten und sich nur in der Schreibweise unterschieden: „Speichern",
 * „Speichern..." und „Speichern …"; „Test connection" und „Test Connection";
 * „Notiz gelöscht" und „Notiz gelöscht.". Keines davon ist ein Fehler, den ein
 * Test sonst fände — die Oberfläche funktioniert. Sie wirkt nur, als hätten
 * mehrere Leute nebeneinander daran gearbeitet, ohne miteinander zu reden.
 *
 * Dieser Test hält die Entscheidungen fest, damit sie nicht wieder auseinander
 * laufen. Er prüft nicht, ob ein Text gut ist — nur, ob er sich an dieselbe
 * Schreibweise hält wie alle anderen.
 *
 * Bewusst nicht geprüft: Knopf gegen Dialogtitel. „Notiz löschen" und „Notiz
 * löschen?" sind kein Widerspruch, sondern zwei Rollen — der Knopf benennt die
 * Handlung, der Titel stellt die Frage. Ein Test, der beide angleicht, macht
 * die Oberfläche schlechter.
 */
const SPRACHEN = { de, en } as Record<string, unknown>

function flach(baum: unknown, praefix = '', ziel = new Map<string, string>()): Map<string, string> {
  for (const [schluessel, kind] of Object.entries(baum as Record<string, unknown>)) {
    const pfad = praefix ? `${praefix}.${schluessel}` : schluessel
    if (kind && typeof kind === 'object' && !Array.isArray(kind)) flach(kind, pfad, ziel)
    else if (typeof kind === 'string') ziel.set(pfad, kind)
  }
  return ziel
}

/**
 * Wörtlich übernommene Fremdtexte. Sie gehören jemand anderem, und wer sie
 * glättet, macht aus einer Fehlermeldung, die man suchen kann, eine, die man
 * nicht mehr findet.
 */
const WOERTLICH = new Set([
  'docs.troubleshooting.err5Title',              // Docker: "failed to extract layer ... to overlayfs"
  'blueprintBuilder.fields.modSeparator.help',   // Startargument: "-mods=..."
])

describe('Schreibweise der Oberflächentexte', () => {
  it.each(Object.keys(SPRACHEN))('%s: Auslassung als „ …", nie als drei Punkte', (sprache) => {
    const verstoesse = [...flach(SPRACHEN[sprache])]
      .filter(([pfad]) => !WOERTLICH.has(pfad))
      .filter(([, text]) => text.includes('...'))
      .map(([pfad]) => pfad)
    expect(verstoesse).toEqual([])
  })

  it.each(Object.keys(SPRACHEN))('%s: kein geklebtes Auslassungszeichen', (sprache) => {
    const verstoesse = [...flach(SPRACHEN[sprache])]
      .filter(([, text]) => /\S…/.test(text))
      .map(([pfad]) => pfad)
    expect(verstoesse).toEqual([])
  })

  it('de: „z. B." mit Leerzeichen, wie im Duden', () => {
    const verstoesse = [...flach(de)]
      .filter(([, text]) => /\bz\.B\./.test(text))
      .map(([pfad]) => pfad)
    expect(verstoesse).toEqual([])
  })

  it('en: kein Title Case in mehrwortigen Beschriftungen', () => {
    // Eigennamen tragen ihre Grossschreibung zu Recht. Erkannt werden sie
    // daran, dass das deutsche Gegenstück dasselbe Wort ebenso gross schreibt:
    // „Steam Web API" steht in beiden Dateien gleich, „Test Connection" nicht.
    const deutsch = flach(de)
    const klein = new Set([
      'a', 'an', 'the', 'and', 'or', 'but', 'for', 'of', 'to', 'in', 'on',
      'at', 'by', 'with', 'from', 'as', 'is', 'no', 'per', 'via',
    ])
    const verstoesse: string[] = []
    for (const [pfad, text] of flach(en)) {
      if (WOERTLICH.has(pfad)) continue
      if (/[.!?:]$/.test(text.trim())) continue
      if (/\{\{/.test(text)) continue
      if (/\be\.g\.|\bi\.e\./.test(text)) continue
      const woerter = text.trim().split(/\s+/)
      if (woerter.length < 2 || woerter.length > 6) continue
      const gegenstueck = deutsch.get(pfad) ?? ''
      for (const wort of woerter.slice(1)) {
        if (!/^[A-Z][a-z]+$/.test(wort)) continue
        if (klein.has(wort.toLowerCase())) continue
        // Steht dasselbe Wort auch im deutschen Text gross, ist es ein Name.
        if (new RegExp(`\\b${wort}\\b`).test(gegenstueck)) continue
        verstoesse.push(`${pfad}: "${text}" → "${wort}"`)
      }
    }
    expect(verstoesse).toEqual([])
  })
})
