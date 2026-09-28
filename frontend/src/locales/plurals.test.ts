import { describe, expect, it } from 'vitest'

import i18n from '@/i18n'

import de from './de.json'
import en from './en.json'

/**
 * Ein Pluralschlüssel braucht beide Formen — und keinen Basisschlüssel daneben.
 *
 * i18next mit JSON-v4 löst `t('x', { count })` über `x_one`/`x_other` auf; der
 * flache Schlüssel `x` wird dabei nie gelesen. Im September 2026 lagen trotzdem
 * sieben solcher Zwillinge in der Datei, und sie waren nicht wortgleich mit
 * ihren Geschwistern:
 *
 *   mods.etaHours        "Noch etwa {{count}} Stunden"
 *   mods.etaHours_other  "Ca. {{count}} Stunden verbleibend."
 *
 * Der erste erschien nie. Wer die Meldung ändern wollte, fand über die Suche
 * aber zuerst ihn — und wunderte sich, dass die Oberfläche gleich blieb. Genau
 * deshalb ist ein toter Text schlimmer als gar keiner: er sieht aus wie der
 * richtige.
 *
 * Umgekehrt fehlte `teams.memberCount_one` ganz, während der Basisschlüssel die
 * Einzahl trug ("{{count}} Mitglied"). Das hing an i18nexts Rückfall auf den
 * Basisnamen — eine Eigenschaft, auf die sich niemand verlassen sollte, wenn
 * die Zweiteilung überall sonst sauber durchgezogen ist.
 */
const SPRACHEN = { de, en } as Record<string, unknown>

function flach(baum: unknown, praefix = '', ziel = new Map<string, unknown>()): Map<string, unknown> {
  for (const [schluessel, kind] of Object.entries(baum as Record<string, unknown>)) {
    const pfad = praefix ? `${praefix}.${schluessel}` : schluessel
    if (kind && typeof kind === 'object' && !Array.isArray(kind)) flach(kind, pfad, ziel)
    else ziel.set(pfad, kind)
  }
  return ziel
}

const SUFFIXE = ['one', 'other'] as const

describe('Pluralschlüssel', () => {
  it.each(Object.keys(SPRACHEN))('%s: jede Pluralform hat Einzahl und Mehrzahl', (sprache) => {
    const keys = flach(SPRACHEN[sprache])
    const basen = new Set(
      [...keys.keys()]
        .filter((k) => /_(?:one|other)$/.test(k))
        .map((k) => k.replace(/_(?:one|other)$/, '')),
    )
    const unvollstaendig = [...basen].filter((basis) =>
      SUFFIXE.some((suffix) => !keys.has(`${basis}_${suffix}`)),
    )
    expect(unvollstaendig).toEqual([])
  })

  it.each(Object.keys(SPRACHEN))('%s: kein toter Basisschlüssel neben den Formen', (sprache) => {
    const keys = flach(SPRACHEN[sprache])
    const basen = new Set(
      [...keys.keys()]
        .filter((k) => /_(?:one|other)$/.test(k))
        .map((k) => k.replace(/_(?:one|other)$/, '')),
    )
    const zwillinge = [...basen].filter((basis) => keys.has(basis))
    expect(zwillinge).toEqual([])
  })

  it('löst Einzahl und Mehrzahl auf, statt den Schlüsselnamen zu zeigen', async () => {
    await i18n.changeLanguage('de')
    expect(i18n.t('teams.memberCount', { count: 1 })).toBe('1 Mitglied')
    expect(i18n.t('teams.memberCount', { count: 3 })).toBe('3 Mitglieder')
    expect(i18n.t('mods.etaHours', { count: 1 })).toBe('Ca. 1 Stunde verbleibend.')
    expect(i18n.t('mods.etaHours', { count: 4 })).toBe('Ca. 4 Stunden verbleibend.')
  })
})
