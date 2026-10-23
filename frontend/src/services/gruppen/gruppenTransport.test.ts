/**
 * Die Fähigkeitsprüfung einer Gruppe: Postfächer oder geteilte Mailbox.
 *
 * Der eigentliche Versand steht in `useKonversation` und `nachrichtVersand`;
 * hier geht es nur um die eine Entscheidung davor. Sie muss **frisch** fragen
 * (ein gerade freigegebenes Gerät gehört dazu) und bei einem Verzeichnisfehler
 * den Altweg wählen — nicht „alles fähig" annehmen.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const verzeichnis = vi.hoisted(() => ({
  eigene: vi.fn(),
  geraete: vi.fn(),
  mitglieder: vi.fn(),
}))

vi.mock('../e2eeGeraet', () => ({
  eigenesGeraet: verzeichnis.eigene,
  verlangeGeraeteVon: verzeichnis.geraete,
}))
vi.mock('./gruppenVerteilung', () => ({ frischeMitglieder: verzeichnis.mitglieder }))

import { gruppenTransportZiele } from './gruppenTransport'
import type { GruppenKontext } from './typen'

const KONTEXT: GruppenKontext = {
  groupId: 7,
  blindMailboxId: 'gruppe-7',
  eigeneId: 1,
  mitglieder: [1, 2],
  istEigentuemer: false,
}

function geraet(deviceId: string, inbox?: string) {
  return {
    device_id: deviceId,
    public_key: `pub-${deviceId}`,
    signing_public_key: '',
    label: deviceId,
    ...(inbox ? { inbox_hash: inbox } : {}),
  }
}

function inbox(n: number): string {
  return n.toString(16).padStart(64, '0')
}

describe('gruppenTransportZiele', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    verzeichnis.eigene.mockResolvedValue({ kennung: 'eigen-a' })
    verzeichnis.mitglieder.mockResolvedValue([1, 2])
    verzeichnis.geraete.mockImplementation(async (konto: number) =>
      konto === 1
        ? [geraet('eigen-a', inbox(1)), geraet('eigen-b', inbox(2))]
        : [geraet('fremd-a', inbox(3)), geraet('fremd-b', inbox(4))],
    )
  })

  it('nimmt alle Zielgeraete, wenn jedes eine Inbox veroeffentlicht hat', async () => {
    const ergebnis = await gruppenTransportZiele(KONTEXT)

    expect(ergebnis).toEqual({
      art: 'inbox',
      // Das eigene, gerade sendende Geraet fehlt; die eigenen uebrigen sind
      // dabei.
      inboxen: [inbox(2), inbox(3), inbox(4)],
    })
  })

  it('fragt das Verzeichnis frisch und ohne Zwischenspeicher', async () => {
    await gruppenTransportZiele(KONTEXT)

    expect(verzeichnis.geraete).toHaveBeenCalledWith(1, { frisch: true })
    expect(verzeichnis.geraete).toHaveBeenCalledWith(2, { frisch: true })
    // Und die Mitglieder kommen vom Server, nicht aus der Momentaufnahme der
    // Oberflaeche.
    expect(verzeichnis.mitglieder).toHaveBeenCalledWith(KONTEXT)
  })

  it('waehlt den Altweg, sobald ein einziges Geraet die Faehigkeit nicht hat', async () => {
    verzeichnis.geraete.mockImplementation(async (konto: number) =>
      konto === 1
        ? [geraet('eigen-a', inbox(1)), geraet('eigen-b', inbox(2))]
        : [geraet('fremd-a', inbox(3)), geraet('fremd-b')],
    )

    expect(await gruppenTransportZiele(KONTEXT)).toEqual({
      art: 'mailbox',
      grund: 'unfaehig',
    })
  })

  it('waehlt den Altweg, wenn das Verzeichnis nicht antwortet', async () => {
    verzeichnis.geraete.mockRejectedValue(new Error('kein Netz'))

    expect(await gruppenTransportZiele(KONTEXT)).toEqual({
      art: 'mailbox',
      grund: 'unbekannt',
    })
  })

  it('waehlt den Altweg, wenn nur das eigene Geraet da ist', async () => {
    verzeichnis.geraete.mockImplementation(async (konto: number) =>
      konto === 1 ? [geraet('eigen-a', inbox(1))] : [],
    )

    expect(await gruppenTransportZiele(KONTEXT)).toEqual({
      art: 'mailbox',
      grund: 'keine-geraete',
    })
  })

  it('verlangt die Faehigkeit nicht vom eigenen, gerade sendenden Geraet', async () => {
    // Ein Geraet, das den neuen Weg nicht anbietet, aber selbst sendet, ist
    // kein Ziel. Massgeblich ist nur, was die Empfaenger koennen.
    verzeichnis.geraete.mockImplementation(async (konto: number) =>
      konto === 1
        ? [geraet('eigen-a'), geraet('eigen-b', inbox(2))]
        : [geraet('fremd-a', inbox(3))],
    )

    expect(await gruppenTransportZiele(KONTEXT)).toEqual({
      art: 'inbox',
      inboxen: [inbox(2), inbox(3)],
    })
  })
})
