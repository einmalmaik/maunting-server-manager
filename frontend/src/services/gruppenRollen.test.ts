/**
 * Was darf dieses Konto in dieser Gruppe?
 *
 * Diese Datei gibt es, weil es auf die Frage bis 09/2026 zwei Antworten gab:
 * eine im Rechte-Dialog und eine im Verlauf. Die im Verlauf prüfte beim Löschen
 * einer fremden Nachricht `can_pin_messages` — das Recht, eine Nachricht
 * **anzuheften**. Wer löschen durfte, aber nicht heften, wurde ignoriert; wer
 * heften durfte, aber nicht löschen, kam durch.
 *
 * Festgehalten wird vor allem die Regel, die am leichtesten verloren geht:
 * **vereinigt, nie abgezogen.** Ein Client, der einem Mitglied ein Recht
 * abspräche, das der Server ihm zugesteht, behauptete eine Schranke, die es
 * nicht gibt.
 */

import { describe, expect, it } from 'vitest'

import de from '@/locales/de.json'
import en from '@/locales/en.json'
import { KONFIG_FORMAT, type Gruppenzustand } from './gruppenKonfig'
import {
  GRUPPEN_RECHTE,
  leseRechte,
  permissionDescKey,
  permissionTitleKey,
  wirksameGruppenrechte,
} from './gruppenRollen'

const ANNA = 101
const BERT = 102

function zustand(
  rollen: Gruppenzustand['rollen'],
  zuordnung: Record<string, number[]> = {},
): Gruppenzustand {
  return { v: KONFIG_FORMAT, rollen, zuordnung }
}

describe('leseRechte', () => {
  it('übersetzt die alten Namen', () => {
    expect([...leseRechte('call_moderate')].sort()).toEqual(['kick_from_calls', 'mute_in_calls'])
    expect([...leseRechte('call_join')]).toEqual(['join_group_calls'])
  })

  it('verwirft, was im Vokabular nicht steht', () => {
    // Ein Tippfehler darf kein Recht erfinden.
    expect([...leseRechte('send_messages,loesche_alles')]).toEqual(['send_messages'])
  })

  it('verträgt Leerzeichen, leere Einträge und nichts', () => {
    expect([...leseRechte(' send_messages , , attach_media ')].sort()).toEqual([
      'attach_media',
      'send_messages',
    ])
    expect(leseRechte(null).size).toBe(0)
  })
})

describe('wirksameGruppenrechte', () => {
  // ── Die Mitgliederzeile ────────────────────────────────────────────────

  it('gibt dem Eigentümer alles', () => {
    const rechte = wirksameGruppenrechte({
      konto: ANNA,
      systemRolle: 'member',
      istEigentuemer: true,
      standardrechte: '',
    })
    expect(rechte.size).toBe(GRUPPEN_RECHTE.length)
  })

  it('gibt einem Administrator alles', () => {
    // Nicht weil es eingetragen wäre, sondern weil er es sich eintragen könnte.
    const rechte = wirksameGruppenrechte({ konto: ANNA, systemRolle: 'admin' })
    expect(rechte.has('manage_roles')).toBe(true)
    expect(rechte.has('delete_messages')).toBe(true)
  })

  it('gibt einem gewöhnlichen Mitglied die Standardrechte', () => {
    const rechte = wirksameGruppenrechte({
      konto: ANNA,
      systemRolle: 'member',
      standardrechte: 'send_messages,attach_media',
    })
    expect([...rechte].sort()).toEqual(['attach_media', 'send_messages'])
  })

  it('lässt eigene Rechte die Standardrechte ersetzen', () => {
    const rechte = wirksameGruppenrechte({
      konto: ANNA,
      systemRolle: 'member',
      eigeneRechte: 'send_messages',
      standardrechte: 'send_messages,attach_media,invite_members',
    })
    expect([...rechte]).toEqual(['send_messages'])
  })

  it('nimmt eine leere eigene Rechteliste ernst', () => {
    // `''` ist eine Aussage („nichts"), `null` ist keine („nimm den Standard").
    expect(
      wirksameGruppenrechte({
        konto: ANNA,
        systemRolle: 'member',
        eigeneRechte: '',
        standardrechte: 'send_messages',
      }).size,
    ).toBe(0)
  })

  it('gibt einem Moderator ohne Überschreibung seine eingebauten Rechte', () => {
    const rechte = wirksameGruppenrechte({
      konto: ANNA,
      systemRolle: 'moderator',
      standardrechte: 'send_messages',
      zustand: zustand([]),
    })
    expect(rechte.has('delete_messages')).toBe(true)
    expect(rechte.has('manage_roles')).toBe(false)
  })

  it('hält die Vorlage „Mitglied" aus den wirklichen Rechten heraus', () => {
    // Sonst wäre der Reiter „@everyone" wirkungslos: jedes Mitglied bekäme die
    // vier Rechte der Vorlage ohnehin.
    const rechte = wirksameGruppenrechte({
      konto: ANNA,
      systemRolle: 'member',
      standardrechte: 'send_messages',
      zustand: zustand([]),
    })
    expect([...rechte]).toEqual(['send_messages'])
  })

  // ── Der verschlüsselte Block ───────────────────────────────────────────

  it('wendet eine überschriebene Systemrolle auf ihre Träger an', () => {
    // Das ist der Sinn davon, „Moderator" im Dialog bearbeiten zu können.
    const rechte = wirksameGruppenrechte({
      konto: ANNA,
      systemRolle: 'moderator',
      standardrechte: '',
      zustand: zustand([
        { id: 'moderator', name: 'moderator', beschreibung: '', rechte: ['kick_members'] },
      ]),
    })
    expect(rechte.has('kick_members')).toBe(true)
  })

  it('gibt eine eigene Rolle nur ihren Zugeordneten', () => {
    const z = zustand(
      [{ id: 'custom_1', name: 'Aufsicht', beschreibung: '', rechte: ['delete_messages'] }],
      { custom_1: [BERT] },
    )

    expect(
      wirksameGruppenrechte({ konto: BERT, systemRolle: 'member', zustand: z }).has(
        'delete_messages',
      ),
    ).toBe(true)
    expect(
      wirksameGruppenrechte({ konto: ANNA, systemRolle: 'member', zustand: z }).has(
        'delete_messages',
      ),
    ).toBe(false)
  })

  it('verwirft ein Recht aus dem Block, das im Vokabular nicht steht', () => {
    const z = zustand([{ id: 'custom_1', name: 'X', beschreibung: '', rechte: ['gott'] }], {
      custom_1: [ANNA],
    })
    expect(wirksameGruppenrechte({ konto: ANNA, systemRolle: 'member', zustand: z }).size).toBe(0)
  })

  it('zieht nichts ab, was die Mitgliederzeile bereits gibt', () => {
    // Der Block sagt weniger als der Server. Der Server bleibt zuständig —
    // alles andere wäre eine Schranke, die keine ist.
    const rechte = wirksameGruppenrechte({
      konto: ANNA,
      systemRolle: 'member',
      eigeneRechte: 'send_messages,attach_media,pin_messages',
      zustand: zustand([{ id: 'custom_1', name: 'X', beschreibung: '', rechte: [] }], {
        custom_1: [ANNA],
      }),
    })
    expect(rechte.has('pin_messages')).toBe(true)
  })

  it('kommt ohne Block aus', () => {
    // Diesem Gerät fehlt der Gruppenschlüssel. Dann zählt die Mitgliederzeile —
    // und nicht etwa „keine Rechte".
    const rechte = wirksameGruppenrechte({
      konto: ANNA,
      systemRolle: 'member',
      standardrechte: 'send_messages',
      zustand: null,
    })
    expect([...rechte]).toEqual(['send_messages'])
  })

  it('gibt einem Nichtmitglied nichts', () => {
    // Kein Eintrag in der Mitgliederzeile, keine Rolle im Block.
    expect(
      wirksameGruppenrechte({
        konto: 999,
        systemRolle: undefined,
        standardrechte: null,
        zustand: zustand([{ id: 'custom_1', name: 'X', beschreibung: '', rechte: ['kick_members'] }], {
          custom_1: [ANNA],
        }),
      }).size,
    ).toBe(0)
  })
})

/**
 * Das Recht, die Verfallsfrist einer Gruppe zu stellen.
 *
 * Bis 09/2026 gab es keines: jedes Mitglied stellte die Frist für alle. Es
 * gehört ins Vokabular, damit der Dialog es anbietet und `leseRechte` es nicht
 * als Tippfehler verwirft. Ob eine Umstellung beim Empfänger gilt, entscheidet
 * trotzdem die Marke vom Server (`durfteVerfallStellen`), nicht diese Datei.
 */
describe('set_disappearing_messages', () => {
  it('steht im Vokabular und überlebt das Lesen einer Rechteliste', () => {
    expect([...leseRechte('send_messages,set_disappearing_messages')].sort()).toEqual([
      'send_messages',
      'set_disappearing_messages',
    ])
  })

  it('gehört dem Administrator, aber nicht der Vorlage „Moderator"', () => {
    // Die Frist entscheidet, wie lange die Nachrichten **aller** leben — eine
    // Einstellung der Gruppe, keine Moderation eines Gesprächs. In der Vorlage
    // wäre sie obendrein keine Zusage: der Empfänger fragt die Marke vom
    // Server, und der gibt einem Moderator ohne Eintrag nichts.
    expect(
      wirksameGruppenrechte({ konto: ANNA, systemRolle: 'admin' }).has('set_disappearing_messages'),
    ).toBe(true)
    expect(
      wirksameGruppenrechte({
        konto: ANNA,
        systemRolle: 'moderator',
        standardrechte: 'send_messages',
        zustand: zustand([]),
      }).has('set_disappearing_messages'),
    ).toBe(false)
  })
})

/** Ein Punktpfad durch eine Sprachdatei, ohne Rückfall auf die andere Sprache. */
function nachschlagen(baum: unknown, schluessel: string): unknown {
  return schluessel
    .split('.')
    .reduce<unknown>(
      (knoten, teil) =>
        knoten && typeof knoten === 'object' ? (knoten as Record<string, unknown>)[teil] : undefined,
      baum,
    )
}

describe('Beschriftung der Rechte', () => {
  it('hat für jedes Recht Titel und Beschreibung, auf Deutsch und auf Englisch', () => {
    // Der Dialog setzt den Schlüssel erst zur Laufzeit zusammen
    // (`permissionTitleKey`), `check-i18n` sieht ihn deshalb nie. Ein
    // vergessenes Paar stünde dort als roher Schlüssel — und die Beschreibung
    // ist die Zusage, unter der jemand das Recht vergibt.
    const fehlend: string[] = []
    for (const [sprache, baum] of [['de', de], ['en', en]] as const) {
      for (const { key } of GRUPPEN_RECHTE) {
        for (const schluessel of [permissionTitleKey(key), permissionDescKey(key)]) {
          const text = nachschlagen(baum, schluessel)
          if (typeof text !== 'string' || !text.trim()) fehlend.push(`${sprache}: ${schluessel}`)
        }
      }
    }
    expect(fehlend).toEqual([])
  })
})
