/**
 * Der Hintergrundlauf über das Gerätepostfach.
 *
 * Geprüft wird die Zuordnung und die Reihenfolge, nicht die Entschlüsselung
 * selbst (die steht in `verarbeitung`/`uebernahme` und hat eigene Tests):
 *
 * 1. Ein Ratchet-Eintrag landet bei seinem Gespräch.
 * 2. Ein Gruppeneintrag landet bei der Gruppe, deren Schlüsselkennung passt.
 * 3. Ein Sitzungsaufbau landet bei der Gegenstelle.
 * 4. Was keinem Gespräch gehört, wird **nicht** quittiert.
 * 5. Quittiert wird erst nach dem lokalen Commit.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const zustellung = vi.hoisted(() => ({
  eigeneInboxAdresse: vi.fn(async () => 'a'.repeat(64)),
  holeZustellungen: vi.fn(),
  quittiereZustellungen: vi.fn(),
}))
const verarbeitung = vi.hoisted(() => ({ verarbeiteGespraech: vi.fn() }))
const uebernahme = vi.hoisted(() => ({ uebernimmLesungen: vi.fn() }))
const reihenfolge: string[] = []

vi.mock('@/services/e2eeZustellung', () => zustellung)
vi.mock('./verarbeitung', () => verarbeitung)
vi.mock('./uebernahme', () => uebernahme)
vi.mock('@/services/e2eeCrypto', () => ({
  decryptE2eeHybridWithKeyring: vi.fn(async (umschlag: string) => umschlag),
}))
vi.mock('@/services/ratchetSitzung', () => ({
  DR_PREFIX: 'sv-e2ee-dr-v1:',
  drUrheber: (umschlag: string) =>
    umschlag.startsWith('sv-e2ee-dr-v1:')
      ? { vonKonto: Number(umschlag.split(':')[1]), vonGeraet: 'g2' }
      : null,
}))
vi.mock('@/services/gruppenSchluessel', () => ({ GRUPPE_PREFIX: 'sv-e2ee-group-v1:' }))
vi.mock('@/services/gruppen/typen', () => ({
  lieseGruppenKopf: (umschlag: string) =>
    umschlag.startsWith('sv-e2ee-group-v1:')
      ? { keyId: umschlag.split(':')[1].split('.')[0], rumpf: '' }
      : null,
}))
vi.mock('@/services/gruppen/gruppenAblage', () => ({
  ablage: { liesAktuellen: vi.fn(async (groupId: number) => ({ keyId: `k${groupId}` })) },
}))
vi.mock('@/services/quittungsstand', () => ({
  partnerstand: () => ({ gelesen: 0, zugestellt: 0 }),
  merkePartnerQuittung: vi.fn(),
}))

import { verarbeitePostfach, type PostfachGespraech } from './postfach'

function eintrag(seq: number, ciphertext: string) {
  return {
    delivery_id: `d-${seq}`,
    payload_id: `p-${seq}`,
    ciphertext,
    ciphertext_sha256: 'h',
    seq,
    angenommen_am: '2026-10-23T10:00:00.000Z',
    ablauf_am: '2026-11-22T10:00:00.000Z',
  }
}

const DM: PostfachGespraech = {
  mailbox: 'm-dm',
  drKontext: { eigeneId: 1, peerId: 2 },
  gruppenKontext: null,
  contact: { userId: 2, isFriend: true },
  group: null,
}

const GRUPPE: PostfachGespraech = {
  mailbox: 'm-gruppe',
  drKontext: null,
  gruppenKontext: {
    groupId: 7,
    blindMailboxId: 'm-gruppe',
    eigeneId: 1,
    mitglieder: [1, 2],
    istEigentuemer: false,
  },
  contact: null,
  group: { id: 7, name: 'Gruppe', members: [] } as never,
}

const lauf = {
  eigeneId: 1,
  decryptionKeys: ['k'],
  gespraeche: [DM, GRUPPE],
  gruppenrechteVon: () => new Set<string>(),
  t: (s: string) => s,
  markAsRead: () => {},
  setVerfallSekunden: () => {},
}

describe('verarbeitePostfach', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    reihenfolge.length = 0
    zustellung.quittiereZustellungen.mockImplementation(async (_inbox: string, ids: string[]) => {
      reihenfolge.push(`quittung:${ids.join(',')}`)
      return { quittiert: ids.length }
    })
    verarbeitung.verarbeiteGespraech.mockImplementation(async () => {
      reihenfolge.push('verarbeitet')
      return { lesungen: [], stand: new Map() }
    })
    uebernahme.uebernimmLesungen.mockImplementation(async () => {
      reihenfolge.push('commit')
      return {
        processedList: [{ id: 1 }],
        lokalerVerlauf: [],
        combined: [],
        verlaufsArt: 'direkt',
        mailbox: 'm',
        maxIncomingId: 1,
        unlesbar: 0,
      }
    })
  })

  it('ordnet jeden Eintrag seinem Gespraech zu und quittiert erst nach dem Commit', async () => {
    zustellung.holeZustellungen.mockResolvedValue({
      eintraege: [
        eintrag(1, 'sv-e2ee-dr-v1:2:rumpf'),
        eintrag(2, 'sv-e2ee-group-v1:k7:rumpf'),
      ],
      naechste_seq: 2,
    })

    const ergebnis = await verarbeitePostfach(lauf)

    expect(ergebnis.quittiert).toBe(2)
    // Zwei Gespraeche, also zwei Laeufe; je Lauf erst verarbeiten, dann
    // schreiben, dann quittieren.
    expect(reihenfolge).toEqual([
      'verarbeitet',
      'commit',
      'quittung:d-1',
      'verarbeitet',
      'commit',
      'quittung:d-2',
    ])
    const mailboxen = verarbeitung.verarbeiteGespraech.mock.calls.map(
      (c) => (c[0] as { mailbox: string }).mailbox,
    )
    expect(mailboxen.sort()).toEqual(['m-dm', 'm-gruppe'])
  })

  it('laesst liegen, was keinem Gespraech gehoert', async () => {
    zustellung.holeZustellungen.mockResolvedValue({
      // Absenderkonto 9 ist kein Kontakt; Gruppe 99 gibt es nicht.
      eintraege: [eintrag(1, 'sv-e2ee-dr-v1:9:rumpf'), eintrag(2, 'sv-e2ee-group-v1:k99:rumpf')],
      naechste_seq: 2,
    })

    const ergebnis = await verarbeitePostfach(lauf)

    expect(ergebnis).toEqual({ verarbeitet: 0, quittiert: 0 })
    expect(verarbeitung.verarbeiteGespraech).not.toHaveBeenCalled()
    expect(zustellung.quittiereZustellungen).not.toHaveBeenCalled()
  })

  it('ordnet einen Sitzungsaufbau der Gegenstelle zu', async () => {
    zustellung.holeZustellungen.mockResolvedValue({
      eintraege: [eintrag(1, '{"typ":"dr-init","vonKonto":2}')],
      naechste_seq: 1,
    })
    // Der Hybrid-Stellvertreter gibt den Umschlag selbst zurueck; hier ist das
    // der JSON-Text.
    const { decryptE2eeHybridWithKeyring } = await import('@/services/e2eeCrypto')
    vi.mocked(decryptE2eeHybridWithKeyring).mockResolvedValueOnce(
      JSON.stringify({ typ: 'dr-init', vonKonto: 2 }),
    )

    await verarbeitePostfach(lauf)

    expect(verarbeitung.verarbeiteGespraech).toHaveBeenCalledTimes(1)
    const kontext = verarbeitung.verarbeiteGespraech.mock.calls[0][0] as {
      mailbox: string
      nurPostfach: boolean
    }
    expect(kontext.mailbox).toBe('m-dm')
    expect(kontext.nurPostfach).toBe(true)
  })

  it('quittiert nichts, wenn das Schreiben scheitert', async () => {
    zustellung.holeZustellungen.mockResolvedValue({
      eintraege: [eintrag(1, 'sv-e2ee-dr-v1:2:rumpf')],
      naechste_seq: 1,
    })
    uebernahme.uebernimmLesungen.mockResolvedValue(null)

    const ergebnis = await verarbeitePostfach(lauf)

    expect(ergebnis.quittiert).toBe(0)
    expect(zustellung.quittiereZustellungen).not.toHaveBeenCalled()
  })

  it('holt nichts, solange die Schluessel fehlen', async () => {
    const ergebnis = await verarbeitePostfach({ ...lauf, decryptionKeys: [] })
    expect(ergebnis).toEqual({ verarbeitet: 0, quittiert: 0 })
    expect(zustellung.holeZustellungen).not.toHaveBeenCalled()
  })
})
