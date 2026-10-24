// @vitest-environment node
/**
 * Der Wiederaufnehmer für Versandabsichten.
 *
 * Die Absicht entsteht atomar mit dem Ratchet-Schritt.
 * Hier geht es um das Nachsenden: dieselben Bytes, dieselben Kennungen,
 * und ein Weg, der nur bei einem endgültigen Fehler aufgibt.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const transport = vi.hoisted(() => ({
  relay: vi.fn(),
}))
const lokal = vi.hoisted(() => ({ updateMessageInLocalStore: vi.fn(async () => {}) }))

vi.mock('@/api/social', () => ({ relayE2eeEnvelope: transport.relay }))
vi.mock('../messengerLocalStore', () => lokal)

import {
  ausstehendeZustellungen,
  setzeAblageFuerTest,
  type AusstehendeZustellung,
  type RatchetAblage,
} from '../ratchetSpeicher'
import { sendeAusstehendeZustellungen } from './nachsend'

const ICH = 1
const DU = 2

function neueAblage(): { ablage: RatchetAblage; daten: Map<string, string> } {
  const daten = new Map<string, string>()
  return {
    daten,
    ablage: {
      async lies(id) {
        return daten.get(id) ?? null
      },
      async schreibe(id, zustand, marke, begleiter) {
        daten.set(id, zustand)
        const marken = marke === undefined ? [] : Array.isArray(marke) ? marke : [marke]
        for (const eine of marken) daten.set(eine, '1')
        for (const [platz, wert] of Object.entries(begleiter ?? {})) {
          if (wert === null) daten.delete(platz)
          else daten.set(platz, wert)
        }
      },
      async loesche(id, marke, begleiter) {
        daten.delete(id)
        const marken = marke === undefined ? [] : Array.isArray(marke) ? marke : [marke]
        for (const eine of marken) daten.delete(eine)
        for (const platz of begleiter ?? []) daten.delete(platz)
      },
      async marken(muster) {
        const ergebnis: string[] = []
        for (const key of daten.keys()) {
          if (muster === 'versand:*' && key.startsWith('versand:')) ergebnis.push(key)
        }
        return ergebnis
      },
      async alleIds() {
        return [...daten.keys()]
      },
    },
  }
}

function absicht(teil: Partial<AusstehendeZustellung> = {}): AusstehendeZustellung {
  return {
    eigeneId: ICH,
    peerId: DU,
    zielGeraet: 'g-ziel',
    mailbox: 'mailbox-1',
    clientUuid: 'basis#geraet',
    ciphertext: 'exakte-bytes',
    reihenfolge: 1,
    erzeugtAm: '2026-10-23T10:00:00.000Z',
    ...teil,
  }
}

describe('sendeAusstehendeZustellungen', () => {
  let umgebung: ReturnType<typeof neueAblage>

  beforeEach(() => {
    umgebung = neueAblage()
    setzeAblageFuerTest(umgebung.ablage)
    transport.relay.mockReset()
    lokal.updateMessageInLocalStore.mockClear()
  })

  function lege(teil: Partial<AusstehendeZustellung> = {}): AusstehendeZustellung {
    const wert = absicht(teil)
    umgebung.daten.set(`versand:${wert.clientUuid}`, JSON.stringify(wert))
    return wert
  }

  it('schickt die ausstehende Absicht über das Relais und räumt sie weg', async () => {
    lege()
    transport.relay.mockResolvedValue({ id: 5 })

    const ergebnis = await sendeAusstehendeZustellungen(ICH)

    expect(ergebnis).toEqual({ gesendet: 1, offen: 0 })
    expect(transport.relay).toHaveBeenCalledTimes(1)
    expect(await ausstehendeZustellungen(ICH)).toEqual([])
  })

  it('lässt die Absicht bei einem Netzfehler liegen', async () => {
    lege()
    transport.relay.mockRejectedValue(new TypeError('Failed to fetch'))

    expect(await sendeAusstehendeZustellungen(ICH)).toEqual({ gesendet: 0, offen: 1 })
    expect(transport.relay).toHaveBeenCalledTimes(1)
    expect(await ausstehendeZustellungen(ICH)).toHaveLength(1)
  })

  it('wiederholt über die Mailbox dieselbe Kennung, statt eine neue zu vergeben', async () => {
    lege()
    const gesehen = new Set<string>()
    transport.relay.mockImplementation(async (auftrag: { client_uuid: string }) => {
      gesehen.add(auftrag.client_uuid)
      throw new TypeError('Failed to fetch')
    })
    await sendeAusstehendeZustellungen(ICH)
    expect(await ausstehendeZustellungen(ICH)).toHaveLength(1)

    transport.relay.mockImplementation(async (auftrag: { client_uuid: string }) => {
      gesehen.add(auftrag.client_uuid)
      return { id: 9 }
    })
    expect(await sendeAusstehendeZustellungen(ICH)).toEqual({ gesendet: 1, offen: 0 })
    expect([...gesehen]).toEqual(['basis#geraet'])
    expect(await ausstehendeZustellungen(ICH)).toEqual([])
  })

  it('gibt nur bei einem endgültigen Fehler auf und macht das sichtbar', async () => {
    lege()
    transport.relay.mockRejectedValue({ status: 400 })

    expect(await sendeAusstehendeZustellungen(ICH)).toEqual({ gesendet: 0, offen: 0 })
    expect(await ausstehendeZustellungen(ICH)).toEqual([])
    expect(lokal.updateMessageInLocalStore).toHaveBeenCalledWith(
      'mailbox-1',
      'basis',
      { status: 'failed' },
    )
  })

  it('lässt eine Absicht eines anderen Kontos unangetastet', async () => {
    lege({ eigeneId: 99 })
    expect(await sendeAusstehendeZustellungen(ICH)).toEqual({ gesendet: 0, offen: 0 })
    expect(transport.relay).not.toHaveBeenCalled()
  })
})
