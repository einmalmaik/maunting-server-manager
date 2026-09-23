// @vitest-environment node
/**
 * Das Register der Besitznachweise.
 *
 * Klein, aber an einer heiklen Stelle: liefert es für die falsche Mailbox
 * einen Nachweis, schickt der Client ihn an einen Server, der ihn nicht
 * erwartet — und liefert es für die richtige keinen, weist die eigene Mailbox
 * den eigenen Aufruf ab.
 */

import { beforeEach, describe, expect, it } from 'vitest'

import {
  leereMailboxNachweise,
  mailboxNachweis,
  merkeMailboxNachweis,
  nachweisKopf,
} from './mailboxNachweis'

const MID = 'a'.repeat(64)
const ANDERE = 'b'.repeat(64)
const TOKEN = 'c'.repeat(64)

describe('mailboxNachweis', () => {
  beforeEach(() => {
    leereMailboxNachweise()
  })

  it('gibt zurück, was hinterlegt wurde', () => {
    merkeMailboxNachweis(MID, TOKEN)
    expect(mailboxNachweis(MID)).toBe(TOKEN)
  })

  it('kennt für eine fremde Mailbox keinen Nachweis', () => {
    // Der wichtigere der beiden Fälle: einen fremden Nachweis mitzuschicken
    // wäre nicht nur nutzlos, es verriete ihn.
    merkeMailboxNachweis(MID, TOKEN)
    expect(mailboxNachweis(ANDERE)).toBeNull()
    expect(nachweisKopf(ANDERE)).toEqual({})
  })

  it('liest Kennung und Token schreibungsunabhängig', () => {
    merkeMailboxNachweis(MID.toUpperCase(), TOKEN.toUpperCase())
    expect(mailboxNachweis(MID)).toBe(TOKEN)
  })

  it('nimmt einen Nachweis mit einem leeren Wert zurück', () => {
    merkeMailboxNachweis(MID, TOKEN)
    merkeMailboxNachweis(MID, null)
    expect(mailboxNachweis(MID)).toBeNull()
  })

  it('macht aus einer fehlenden Kennung keinen Kopf', () => {
    expect(nachweisKopf(null)).toEqual({})
    expect(nachweisKopf(undefined)).toEqual({})
    expect(nachweisKopf('')).toEqual({})
  })

  it('setzt die Kopfzeile, wenn es einen Nachweis gibt', () => {
    merkeMailboxNachweis(MID, TOKEN)
    expect(nachweisKopf(MID)).toEqual({ 'X-Mailbox-Token': TOKEN })
  })

  it('vergisst alles beim Abmelden', () => {
    merkeMailboxNachweis(MID, TOKEN)
    leereMailboxNachweise()
    expect(mailboxNachweis(MID)).toBeNull()
  })
})
