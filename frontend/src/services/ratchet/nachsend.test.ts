// @vitest-environment node
/**
 * Der Wiederaufnehmer für Versandabsichten.
 *
 * Die Absicht entsteht atomar mit dem Ratchet-Schritt (siehe
 * `absturzMatrix.test.ts`). Hier geht es um das, was danach kommt: dieselben
 * Bytes, dieselben Kennungen, und ein Weg, der nur bei einem endgültigen
 * Fehler aufgibt.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const transport = vi.hoisted(() => ({
  relay: vi.fn(),
  zustellen: vi.fn(),
}))
const lokal = vi.hoisted(() => ({ updateMessageInLocalStore: vi.fn(async () => {}) }))

vi.mock('@/api/social', () => ({ relayE2eeEnvelope: transport.relay }))
vi.mock('../e2eeZustellung', () => ({ sendeZustellungen: transport.zustellen }))
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
const INBOX = 'i'.repeat(64)

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
      async loesche(id) {
        daten.delete(id)
      },
      async alleIds() {
        return [...daten.keys()]
      },
    },
  }
}

function absicht(teil: Partial<AusstehendeZustellung> = {}): AusstehendeZustellung {
  return {
    clientUuid: 'basis#geraet',
    eigeneId: ICH,
    peerId: DU,
    mailbox: 'mailbox-1',
    ciphertext: 'exakte-bytes',
    zielInbox: INBOX,
    angelegtAm: '2026-10-24T10:00:00.000Z',
    reihenfolge: 1,
    ...teil,
  }
}

describe('sendeAusstehendeZustellungen', () => {
  let umgebung: ReturnType<typeof neueAblage>

  beforeEach(() => {
    umgebung = neueAblage()
    setzeAblageFuerTest(umgebung.ablage)
    transport.relay.mockReset()
    transport.zustellen.mockReset()
    lokal.updateMessageInLocalStore.mockClear()
  })

  function lege(teil: Partial<AusstehendeZustellung> = {}): AusstehendeZustellung {
    const wert = absicht(teil)
    umgebung.daten.set(`versand:${wert.clientUuid}`, JSON.stringify(wert))
    return wert
  }

  it('schickt über die Inbox und hält die Kennungen vor dem HTTP fest', async () => {
    lege()
    transport.zustellen.mockImplementation(async () => {
      // Was jetzt in der Ablage steht, ist die Absicht mit Kennungen — vor
      // dem Aufruf geschrieben, nicht danach.
      const offen = await ausstehendeZustellungen(ICH)
      expect(offen[0].zustellung?.deliveryId).toBeTruthy()
      return { angenommen: [{ delivery_id: offen[0].zustellung!.deliveryId, seq: 5, ergebnis: 'ok' }], abgelehnt: [] }
    })

    const ergebnis = await sendeAusstehendeZustellungen(ICH)

    expect(ergebnis).toEqual({ gesendet: 1, offen: 0 })
    expect(transport.zustellen).toHaveBeenCalledTimes(1)
    expect(transport.relay).not.toHaveBeenCalled()
    expect(await ausstehendeZustellungen(ICH)).toEqual([])
  })

  it('lässt die Absicht bei einem Netzfehler liegen und wechselt nicht den Weg', async () => {
    lege()
    transport.zustellen.mockRejectedValue(new TypeError('Failed to fetch'))

    expect(await sendeAusstehendeZustellungen(ICH)).toEqual({ gesendet: 0, offen: 1 })
    expect(transport.relay).not.toHaveBeenCalled()
    expect(await ausstehendeZustellungen(ICH)).toHaveLength(1)
  })

  it('wiederholt mit denselben Kennungen, wenn die Antwort verloren ging', async () => {
    lege()
    transport.zustellen.mockRejectedValue(new TypeError('Failed to fetch'))
    await sendeAusstehendeZustellungen(ICH)
    const erste = (await ausstehendeZustellungen(ICH))[0].zustellung!
    expect(erste.deliveryId).toBeTruthy()

    let zweite: { delivery_id: string; payload_id: string } | null = null
    transport.zustellen.mockImplementation(async (_mid, _payloads, eintraege) => {
      zweite = eintraege[0]
      return { angenommen: [{ delivery_id: eintraege[0].delivery_id, seq: 0, ergebnis: 'bereits' }], abgelehnt: [] }
    })

    expect(await sendeAusstehendeZustellungen(ICH)).toEqual({ gesendet: 1, offen: 0 })
    expect(zweite).toEqual({ delivery_id: erste.deliveryId, payload_id: erste.payloadId, inbox_hash: INBOX })
    expect(await ausstehendeZustellungen(ICH)).toEqual([])
  })

  it('geht über die Mailbox, wenn die Inbox den Auftrag ablehnt', async () => {
    lege()
    transport.zustellen.mockResolvedValue({ angenommen: [], abgelehnt: [{ delivery_id: 'egal', grund: 'unbekannt' }] })
    transport.relay.mockResolvedValue({ id: 9 })

    expect(await sendeAusstehendeZustellungen(ICH)).toEqual({ gesendet: 1, offen: 0 })
    expect(transport.relay).toHaveBeenCalledWith({
      blind_mailbox_id: 'mailbox-1',
      ciphertext_envelope: 'exakte-bytes',
      client_uuid: 'basis#geraet',
      is_control: undefined,
      control_type: null,
    })
    expect(await ausstehendeZustellungen(ICH)).toEqual([])
  })

  it('macht aus einer angenommenen Nachricht ohne Antwort keine zweite', async () => {
    lege()
    const server = new Map<string, string>()
    transport.zustellen.mockImplementation(async (_mid, _payloads, eintraege) => {
      const eintrag = eintraege[0]
      // Der Server nimmt an — die Antwort geht auf dem Rückweg verloren.
      server.set(eintrag.delivery_id, eintrag.payload_id)
      throw new TypeError('Failed to fetch')
    })
    await sendeAusstehendeZustellungen(ICH)
    expect(server.size).toBe(1)

    // Derselbe Auftrag, dieselbe Kennung: der Server erkennt ihn wieder.
    transport.zustellen.mockImplementation(async (_mid, _payloads, eintraege) => {
      const eintrag = eintraege[0]
      const bekannt = server.get(eintrag.delivery_id)
      if (bekannt === eintrag.payload_id) {
        return {
          angenommen: [{ delivery_id: eintrag.delivery_id, seq: 0, ergebnis: 'bereits' }],
          abgelehnt: [],
        }
      }
      server.set(eintrag.delivery_id, eintrag.payload_id)
      return { angenommen: [{ delivery_id: eintrag.delivery_id, seq: 0, ergebnis: 'bereits' }], abgelehnt: [] }
    })

    expect(await sendeAusstehendeZustellungen(ICH)).toEqual({ gesendet: 1, offen: 0 })
    expect(server.size).toBe(1)
    expect(await ausstehendeZustellungen(ICH)).toEqual([])
  })

  it('wiederholt über die Mailbox dieselbe Kennung, statt eine neue zu vergeben', async () => {
    lege({ zielInbox: null })
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
    transport.zustellen.mockResolvedValue({ angenommen: [], abgelehnt: [{ delivery_id: 'egal', grund: 'unbekannt' }] })
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
    expect(transport.zustellen).not.toHaveBeenCalled()
    expect(transport.relay).not.toHaveBeenCalled()
  })
})
