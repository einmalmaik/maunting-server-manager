/**
 * Das Muster fuer Rollennamen — in der Sprache des Browsers und des Backends.
 *
 * Anlass ist eine Meldung vom 18.08.2026: die Rollenmaske warf beim Tippen
 *
 *   Pattern attribute value ^[a-zA-Z0-9_-]+$ is not a valid regular
 *   expression: Invalid character in character class
 *
 * Chrome uebersetzt `pattern` mit dem `v`-Flag (RegExp v-mode). Dort ist der
 * Bindestrich in einer Zeichenklasse ein reserviertes Zeichen und muss
 * maskiert werden. Ein `pattern`, das der Browser nicht uebersetzen kann,
 * wird nicht grosszuegig ausgelegt — es faellt **ganz** aus. Die Maske liess
 * damit jeden Namen durch, und das Backend wies ihn danach mit 422 ab.
 *
 * Diese Datei prueft beide Richtungen, weil ein Fix nur einer Seite den
 * Fehler bloss verschiebt:
 *   1. der Browser kann das Muster uebersetzen (auch im strengen `v`-Modus),
 *   2. es beschreibt dieselbe Menge wie `backend/schemas/role.py`.
 */

import { describe, expect, it } from 'vitest'

import { ROLLENNAME_MUSTER } from './Roles'

// Woertlich aus `backend/schemas/role.py`. Bewusst kopiert und nicht
// importiert — es ist die *fremde* Seite des Vertrags, und wenn sie sich
// aendert, soll dieser Test rot werden statt stillschweigend mitzuwandern.
const BACKEND_MUSTER = /^[a-zA-Z0-9_-]+$/

describe('Rollenname-Muster', () => {
  it('laesst sich im strengen v-Modus uebersetzen', () => {
    // Genau der Aufruf, an dem Chrome gescheitert ist. `new RegExp(..., 'v')`
    // wirft bei einem unmaskierten Bindestrich in der Zeichenklasse.
    expect(() => new RegExp(ROLLENNAME_MUSTER, 'v')).not.toThrow()
  })

  it('laesst sich auch ohne v-Modus uebersetzen', () => {
    // Aeltere Browser und Firefox uebersetzen ohne `v`. Die Maskierung darf
    // dort nicht zu einer anderen Bedeutung fuehren.
    expect(() => new RegExp(ROLLENNAME_MUSTER)).not.toThrow()
  })

  it.each([
    ['KI-Nutzung', true],
    ['admin', true],
    ['team_lead', true],
    ['A1', true],
    ['---', true],
    ['mit leerzeichen', false],
    ['punkt.name', false],
    ['umlaut-ä', false],
    ['', false],
  ])('beurteilt %j wie das Backend', (name, erwartet) => {
    const browser = new RegExp(ROLLENNAME_MUSTER, 'v')
    expect(browser.test(name)).toBe(erwartet)
    // Und dieselbe Antwort gibt das Backend. Ein Muster, das strenger oder
    // lockerer waere als `schemas/role.py`, hiesse: die Maske sagt ja und der
    // Server sagt 422 — oder die Maske sperrt einen Namen, den der Server
    // angenommen haette.
    expect(BACKEND_MUSTER.test(name)).toBe(erwartet)
  })

  it('nimmt den Bindestrich an, an dem der Fehler haing', () => {
    // Der konkrete Name aus der Meldung. Ohne den Fix kam er gar nicht erst
    // zur Pruefung, weil das Muster vorher zerbrach.
    expect(new RegExp(ROLLENNAME_MUSTER, 'v').test('KI-Nutzung')).toBe(true)
  })
})
