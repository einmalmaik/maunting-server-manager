import { describe, expect, it } from 'vitest'


import de from './de.json'
import en from './en.json'

/**
 * Jedes Schreibwerkzeug braucht einen Namen **und** einen Bestätigungstext.
 *
 * Eine Reviewrunde am 2026-08-11 fand fünf Werkzeuge ohne Bestätigungstext —
 * `propose_server_delete`, `propose_backup_restore`, `propose_bind_ip_update`,
 * `propose_blueprint_change`, `propose_server_blueprint_switch`. Der Dialog
 * zeigte dort wörtlich `ai.actions.confirm.propose_server_delete`, weil
 * `parseMissingKeyHandler` in `i18n.ts` bei fehlendem `defaultValue` den
 * Schlüssel zurückgibt.
 *
 * Das ist genau die Stelle, an der jemand entscheidet, ob ein Server gelöscht
 * wird. Ein Test ist hier besser als Sorgfalt: die Liste wächst mit jedem neuen
 * Werkzeug im Backend, und wer eines hinzufügt, denkt an den Namen — der Satz
 * im Bestätigungsmoment fällt später auf, wenn überhaupt.
 *
 * Die Liste steht bewusst hier und nicht als Import aus `api/ai.ts`: sie ist
 * die Abschrift von `ai_tool_registry.WERKZEUGE` und soll auch dann brechen,
 * wenn jemand den TypeScript-Typ ändert, ohne die Texte zu pflegen.
 */
const SCHREIBWERKZEUGE = [
  'propose_server_lifecycle',
  'propose_backup',
  'propose_backup_restore',
  'propose_config_update',
  'propose_config_patch',
  'propose_mod_install',
  'propose_bind_ip_update',
  'propose_server_create',
  'propose_server_delete',
  'propose_blueprint_change',
  'propose_blueprint_delete',
  'propose_server_blueprint_switch',
  'propose_hoster_integration',
  'propose_hoster_product',
  'propose_ai_tarif_role',
  'propose_task_set',
  'propose_task_delete',
  // Die Guardian-Kopplung. `propose_file_delete` steht im Bestätigungsdialog
  // rot (`UNUMKEHRBAR` in AiActionProposalCard.tsx), obwohl es in der Registry
  // nicht `immer_bestaetigen` ist — die Registry entscheidet, ob eine Freigabe
  // übersprungen werden darf, die Farbe entscheidet, wie gefragt wird.
  'propose_server_repair',
  'propose_file_delete',
  // Guardian je Server anders einstellen. Steht hier, seit die Reparatur den
  // Fall „der Blueprint erwartet etwas, das diese Node nicht leisten kann"
  // beheben darf, ohne die Vorlage für alle Server dieses Spiels zu ändern.
  'propose_guardian_tuning',
] as const

const SPRACHEN = { de, en } as Record<string, typeof de>

describe('Texte der KI-Aktionen', () => {
  for (const [sprache, daten] of Object.entries(SPRACHEN)) {
    it(`${sprache}: jedes Schreibwerkzeug hat einen Namen und einen Bestätigungstext`, () => {
      const namen = daten.ai.actions.tools as Record<string, string>
      const bestaetigung = daten.ai.actions.confirm as Record<string, string>

      const ohneNamen = SCHREIBWERKZEUGE.filter((w) => !namen[w]?.trim())
      const ohneText = SCHREIBWERKZEUGE.filter((w) => !bestaetigung[w]?.trim())

      expect(ohneNamen, `ohne Namen in ${sprache}.json`).toEqual([])
      expect(ohneText, `ohne Bestätigungstext in ${sprache}.json`).toEqual([])
    })

    it(`${sprache}: kein Bestätigungstext ohne zugehöriges Werkzeug`, () => {
      // Die Gegenrichtung: ein Text für ein Werkzeug, das es nicht mehr gibt,
      // ist toter Ballast und verdeckt beim Suchen den echten Eintrag.
      const uebrig = Object.keys(daten.ai.actions.confirm)
        .filter((k) => !SCHREIBWERKZEUGE.includes(k as (typeof SCHREIBWERKZEUGE)[number]))
      expect(uebrig).toEqual([])
    })
  }

  // Hier standen zwei Zusagen ueber die acht Realtime-Stimmen: jede braucht
  // eine Beschriftung, und keine Beschriftung ohne Stimme. Beide sind mit dem
  // 16.08.2026 gegenstandslos geworden — eine ElevenLabs-Stimme hat keine
  // Beschriftung im Panel, weil sie MSM gar nicht kennt. Sie gehoert dem Konto
  // des Betreibers, er traegt ihre Kennung als Text ein, und geprueft wird
  // nicht ihr Name, sondern ihre **Form**: sie steht in einem URL-Pfad
  // (`backend/tests/test_ai_voice_provider.py`).

  it('die unumkehrbaren Werkzeuge sagen im Text, dass es unumkehrbar ist', () => {
    // Kein Stilcheck: der Dialog färbt diese Werkzeuge rot, und die Farbe ohne
    // den passenden Satz ist eine Warnung ohne Grund.
    for (const werkzeug of ['propose_server_delete', 'propose_backup_restore'] as const) {
      const text = (de.ai.actions.confirm as Record<string, string>)[werkzeug]
      expect(text).toMatch(/rückgängig|überschrieben|weg\b|endgültig/i)
    }
  })
})
