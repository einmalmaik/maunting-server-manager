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

import { KONFIG_FORMAT, type Gruppenzustand } from './gruppenKonfig'
import { GRUPPEN_RECHTE, leseRechte, wirksameGruppenrechte } from './gruppenRollen'

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
