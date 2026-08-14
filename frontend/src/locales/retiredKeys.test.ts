import { describe, expect, it } from 'vitest'

import de from './de.json'
import en from './en.json'

/**
 * Abgelöste Schlüssel müssen verschwinden, nicht nur unbenutzt herumliegen.
 *
 * Beim Umbau des Gedächtnisbereichs sind `ai.memory.title`, `.description`,
 * `.teamTitle` und `.teamDescription` durch die Bereichsform
 * `ai.memory.titles.<kind>` / `ai.memory.descriptions.<kind>` ersetzt worden —
 * AiMemoryManager.tsx:204-205 bildet den Schlüssel zur Laufzeit aus
 * `scope.kind`. `teams.personalHint` wurde von `teams.personalKnowledgeHint`
 * abgelöst (Teams.tsx). Die alten Zeilen blieben stehen, wortgleich mit ihren
 * Nachfolgern: wer den Teamtext ändert, ändert mit hoher Wahrscheinlichkeit
 * den toten Zwilling und wundert sich, dass die Oberfläche gleich bleibt.
 *
 * Warum eine gepflegte Liste und keine allgemeine „jeder Schlüssel wird
 * benutzt"-Regel: die Oberfläche setzt Schlüssel zur Laufzeit zusammen, eine
 * solche Regel wäre entweder löchrig oder bestünde aus Ausnahmen.
 * `scripts/check-i18n.mjs` prüft die Gegenrichtung (benutzt, aber nicht
 * übersetzt); dies hier ist die fehlende Hälfte.
 *
 * Der ganze Namensraum `permissions` ist entfallen: 34 Kurzbeschriftungen für
 * Rechte, die kein `t()`-Aufruf je gelesen hat, weil der PermissionEditor seine
 * Texte fest verdrahtet im Quelltext trug. Jetzt liest er sie aus
 * `permissionDetails.<schlüssel mit _ statt .>` — und zwei Fassungen desselben
 * Rechtetextes wären schlimmer als die eine deutsche von vorher.
 */
const ABGELOESTE_SCHLUESSEL = [
  'ai.memory.title',
  'ai.memory.description',
  'ai.memory.teamTitle',
  'ai.memory.teamDescription',
  'teams.personalHint',
  'permissions',
]

/** Die Nachfolger muss es geben — sonst wäre das Löschen ein Verlust. */
const NACHFOLGER = [
  'ai.memory.titles.user',
  'ai.memory.titles.team',
  'ai.memory.titles.panel',
  'ai.memory.descriptions.user',
  'ai.memory.descriptions.team',
  'ai.memory.descriptions.panel',
  'teams.personalKnowledgeHint',
  'permissionDetails.users_read.title',
  'permissionDetails.users_read.desc',
  'permissionDetails.server_databases_admin.title',
  'permissionEditor.groups.users',
]

// Nur die beiden Basissprachen: die übrigen neun sind bewusst Teilmengen mit
// englischem Rückfall (scripts/check-i18n.mjs) und kennen die Schlüssel nicht.
const SPRACHEN: Record<string, unknown> = { de, en }

function blatt(baum: unknown, pfad: string): unknown {
  return pfad.split('.').reduce<unknown>(
    (knoten, teil) =>
      knoten && typeof knoten === 'object'
        ? (knoten as Record<string, unknown>)[teil]
        : undefined,
    baum,
  )
}

describe('abgelöste Übersetzungsschlüssel', () => {
  it.each(Object.keys(SPRACHEN))('%s hat keinen abgelösten Schlüssel mehr', (sprache) => {
    const uebrig = ABGELOESTE_SCHLUESSEL.filter(
      (pfad) => blatt(SPRACHEN[sprache], pfad) !== undefined,
    )
    expect(uebrig).toEqual([])
  })

  it.each(Object.keys(SPRACHEN))('%s kennt alle Nachfolger', (sprache) => {
    const fehlend = NACHFOLGER.filter(
      (pfad) => typeof blatt(SPRACHEN[sprache], pfad) !== 'string',
    )
    expect(fehlend).toEqual([])
  })
})
