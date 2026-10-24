/**
 * Die dauerhafte Outbox der Steuerwirkungen.
 *
 * Bis 10/2026 schickte `sendE2eeControlMessage` ein Steuerpaket genau einmal.
 * Scheiterte das, nahm der Aufrufer die lokale Wirkung zurück und zeigte einen
 * Toast: offline war die Reaktion, die Bearbeitung oder die Löschung weg, und
 * die Gegenseite erfuhr nie davon. Diese Datei prüft den Weg, der das
 * verhindert — und die Grenzen, die er trotzdem hat.
 *
 * Der Hybridumschlag ist hier ein Stellvertreter: die echte Verschlüsselung
 * prüfen `e2eeCrypto.test.ts` und `gruppenSchluessel.test.ts`. Was hier zählt,
 * ist der Auftrag: overlebt er einen Neustart, geht er bei `online` hinaus,
 * fällt eine endgültige Abweisung heraus, und bleibt eine Löschung die letzte
 * Wirkung zu einer Nachricht?
 */

import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const relay = vi.hoisted(() => vi.fn())

vi.mock('@/api/social', () => ({
  relayE2eeEnvelope: relay,
}))

vi.mock('./e2eeCrypto', () => ({
  encryptE2eeHybrid: async (nachricht: string, oeffentlich: string) =>
    `sv-e2ee-hybrid-v1:${oeffentlich}:${Buffer.from(nachricht, 'utf-8').toString('base64')}`,
  deriveUserDeviceMailboxId: async (uid: number) => `geraete:${uid}`,
}))

const geraete = new Map<
  number,
  { device_id: string; public_key: string; label: string }[]
>()

vi.mock('./e2eeGeraet', () => ({
  eigenesGeraet: async () => ({
    kennung: 'geraet-ich',
    konto: 1,
    paar: { publicKeyJwk: 'pub-ich', privateKeyJwk: 'priv-ich' },
    signaturPaar: { privateKeyJwk: 'sig-priv' },
  }),
  verlangeGeraeteVon: async (uid: number) => geraete.get(uid) ?? [],
}))

vi.mock('./e2eeIdentity', () => ({
  resolveIdentity: async () => ({
    state: 'ready',
    sendPair: { publicKeyJwk: 'pub-ich', privateKeyJwk: 'priv-ich' },
    decryptionKeys: [],
  }),
}))

vi.mock('./nutzlastSignatur', () => ({
  signiereNutzlast: async (
    _mailbox: string,
    _eigeneId: number,
    roh: Record<string, unknown>,
  ) => ({ ...roh, _beleg: 'geprueft' }),
}))

vi.mock('./gruppenSchluessel', () => ({
  dmZiele: async () => ({ senden: 'dm-ziel', lesen: [] }),
  verschluesseleFuerGruppe: async () => 'sv-gruppe-v1:schluessel.rund',
  umgezogeneMailbox: () => null,
}))

import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import type { SteuerKontext } from '@/services/gespraech/steuerung'
import { setzeSiegelAktiv } from './lokaleVersiegelung'
import {
  ladeSteuerAuftraege,
  legeSteuerAuftragAn,
  loadLocalMessages,
  saveLocalMessages,
} from './messengerLocalStore'
import { tilgeNachrichtLokal } from './nachrichtLoeschen'
import { sendeWirkung } from './steuerOutbox'
import { setzeSteuerOutboxMelder, stosseSteuerOutboxAn } from './steuerOutboxLauf'
import type { SteuerEintrag } from './steuerOutboxTypen'

const MAILBOX = 'dm-mailbox'
const ICH = 1
const PARTNER = 2

const KONTEXT: SteuerKontext = {
  eigeneId: ICH,
  mailbox: MAILBOX,
  drKontext: { eigeneId: ICH, peerId: PARTNER },
  gruppenKontext: null,
  identitaet: {
    state: 'ready',
    sendPair: { publicKeyJwk: 'pub-ich', privateKeyJwk: 'priv-ich' },
    decryptionKeys: [],
  },
}

const REAKTION: SteuerEintrag = {
  typ: 'reaction',
  felder: {
    type: 'reaction',
    target_client_uuid: 'u-1',
    emoji: '👍',
    aktion: 'setzen',
    actor_id: ICH,
    zeitpunkt: '2026-10-24T10:00:00.000Z',
  },
  ziel: 'u-1',
  zielId: 5,
  ruecknahme: {
    art: 'reaktion',
    mailbox: MAILBOX,
    ziel: 'u-1',
    zielId: 5,
    vorher: {},
    nachher: { '👍': [ICH] },
  },
}

let konto = 900

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  konto += 1
  setzeAngemeldetesKonto(konto)
  setzeSiegelAktiv(false)
  geraete.clear()
  geraete.set(PARTNER, [
    { device_id: 'geraet-beta', public_key: 'pub-beta', label: 'Beta' },
  ])
  relay.mockImplementation(async () => ({ id: 1 }))
})

/** Ein Relais, das erst antwortet, wenn der Test es sagt. */
function steuerbaresRelais() {
  let loese: (wert?: unknown) => void = () => {}
  let brich: (grund?: unknown) => void = () => {}
  const versprechen = new Promise((res, rej) => {
    loese = res
    brich = rej
  })
  relay.mockImplementation(() => versprechen)
  return { loese, brich }
}

/** Eine abgelegte Nachricht, an der eine Wirkung hängt. */
async function legeNachrichtAn(reaktionen?: Record<string, number[]>) {
  await saveLocalMessages(MAILBOX, [
    {
      id: 5,
      clientUuid: 'u-1',
      senderId: ICH,
      text: 'Hallo',
      createdAt: '2026-10-24T09:00:00.000Z',
      isSelf: true,
      reaktionen,
    },
  ])
}

describe('Outbox der Steuerwirkungen', () => {
  it('reiht eine Reaktion ohne Netz ein und schickt sie beim nächsten Anlass', async () => {
    relay.mockRejectedValueOnce(new TypeError('Failed to fetch'))

    const ausgang = await sendeWirkung(KONTEXT, REAKTION)
    expect(ausgang).toBe('eingereiht')

    // Der Auftrag liegt mit seinen Feldern in der Ablage — das ist der
    // Neustart: nichts davon lebt im Arbeitsspeicher.
    const [auftrag] = await ladeSteuerAuftraege()
    expect(auftrag).toMatchObject({
      mailbox: MAILBOX,
      art: 'direkt',
      peerId: PARTNER,
      groupId: null,
      typ: 'reaction',
      ziel: 'u-1',
      zielId: 5,
      versuche: 1,
    })
    expect(auftrag.id).toMatch(/^[0-9a-f-]{36}$/)
    // Die abgeleiteten Felder stehen nicht in der Ablage: Mailbox und Kennung
    // vergibt der Aufruf beim Bauen neu, sonst wäre eine alte Mailbox
    // festgeschrieben.
    expect(auftrag.felder).not.toHaveProperty('mailbox')
    expect(auftrag.felder).not.toHaveProperty('client_uuid')

    relay.mockClear()
    relay.mockImplementation(async () => ({ id: 1 }))
    await stosseSteuerOutboxAn({ jetzt: () => Date.now() + 10 * 60_000 })

    expect(await ladeSteuerAuftraege()).toEqual([])
    expect(relay).toHaveBeenCalledTimes(1)
    const gesendet = relay.mock.calls[0][0]
    expect(gesendet).toMatchObject({
      blind_mailbox_id: 'dm-ziel',
      is_control: true,
      control_type: 'reaction',
    })
    // Dieselbe Kennung wie der Auftrag, mit dem Zusatz je Zielgerät: daran
    // erkennt das Relais die Wiederholung und legt nichts doppelt ab.
    expect(gesendet.client_uuid).toBe(`${auftrag.id}#0`)
  })

  it('reiht auch Bearbeitung und Löschung ein und schickt alle drei nach dem Neustart', async () => {
    relay.mockRejectedValue(new TypeError('Failed to fetch'))
    await legeNachrichtAn()

    await sendeWirkung(KONTEXT, REAKTION)
    await sendeWirkung(KONTEXT, {
      typ: 'edit_message',
      felder: {
        type: 'edit_message',
        target_client_uuid: 'u-1',
        target_id: 5,
        new_text: 'Hallo neu',
        edited_at: '2026-10-24T10:00:00.000Z',
      },
      ziel: 'u-1',
      zielId: 5,
      ruecknahme: {
        art: 'bearbeitung',
        mailbox: MAILBOX,
        ziel: 'u-1',
        zielId: 5,
        vorher: { text: 'Hallo', isEdited: false },
        nachher: { text: 'Hallo neu', isEdited: true },
      },
    })
    await sendeWirkung(KONTEXT, {
      typ: 'delete_message',
      felder: { type: 'delete_message', target_client_uuid: 'u-1', target_id: 5 },
      ziel: 'u-1',
      zielId: 5,
      ruecknahme: { art: 'keine' },
    })

    expect((await ladeSteuerAuftraege()).map((a) => a.typ).sort()).toEqual([
      'delete_message',
      'edit_message',
      'reaction',
    ])

    relay.mockReset()
    relay.mockImplementation(async () => ({ id: 1 }))
    await stosseSteuerOutboxAn({ jetzt: () => Date.now() + 10 * 60_000 })

    expect(await ladeSteuerAuftraege()).toEqual([])
    expect(
      relay.mock.calls.map((c) => (c[0] as { control_type?: string }).control_type).sort(),
    ).toEqual(['delete_message', 'edit_message', 'reaction'])
  })

  it('nimmt eine endgültig abgewiesene Wirkung zurück und hält die folgenden nicht an', async () => {
    await legeNachrichtAn({ '👍': [ICH] })

    const meldungen: string[] = []
    setzeSteuerOutboxMelder((_auftrag, grund) => meldungen.push(grund))

    relay.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await sendeWirkung(KONTEXT, REAKTION)
    const [erste] = await ladeSteuerAuftraege()

    relay.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await sendeWirkung(KONTEXT, {
      ...REAKTION,
      felder: { ...REAKTION.felder, emoji: '🎉' },
    })
    const auftraege = await ladeSteuerAuftraege()
    expect(auftraege).toHaveLength(2)
    const zweite = auftraege.find((a) => a.id !== erste.id)!

    relay.mockReset()
    relay.mockImplementation(async (payload: { client_uuid?: string }) => {
      if (payload.client_uuid?.startsWith(erste.id)) {
        throw Object.assign(new Error('unbrauchbar'), { status: 422 })
      }
      return { id: 1 }
    })

    await stosseSteuerOutboxAn({ jetzt: () => Date.now() + 10 * 60_000 })

    // Die abgewiesene ist weg und gemeldet, die folgende ging hinaus.
    expect(meldungen).toEqual(['abgelehnt'])
    expect(await ladeSteuerAuftraege()).toEqual([])
    const erfolgreich = relay.mock.calls
      .map((c) => c[0] as { client_uuid?: string })
      .filter((p) => p.client_uuid?.startsWith(zweite.id))
    expect(erfolgreich).toHaveLength(1)

    // Und die lokale Reaktion ist zurückgenommen: sie stand nur hier, weil
    // sie ankommen sollte.
    const [zeile] = await loadLocalMessages(MAILBOX)
    expect(zeile.reaktionen).toEqual({})
  })

  it('holt einen Anstoß nach, der während eines Laufs kommt', async () => {
    relay.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await sendeWirkung(KONTEXT, REAKTION)
    const [erste] = await ladeSteuerAuftraege()

    const relais = steuerbaresRelais()
    const lauf = stosseSteuerOutboxAn({ jetzt: () => Date.now() + 10 * 60_000 })

    // Während der erste Auftrag auf das Relais wartet, kommt ein zweiter
    // hinzu — genau der Fall „Start und online kurz hintereinander".
    await legeSteuerAuftragAn({
      id: 'nachzuegler',
      mailbox: MAILBOX,
      art: 'direkt',
      peerId: PARTNER,
      groupId: null,
      mitglieder: [],
      istEigentuemer: false,
      typ: 'pin_message',
      ziel: 'u-1',
      zielId: 5,
      felder: { type: 'pin_message', aktion: 'anheften' },
      angelegtAm: Date.now() - 1000,
      versuche: 0,
      naechsterVersuch: 0,
    })
    // Der Anstoß währenddessen geht nicht verloren.
    void stosseSteuerOutboxAn({ jetzt: () => Date.now() + 10 * 60_000 })

    relais.loese({ id: 1 })
    await lauf

    expect(await ladeSteuerAuftraege()).toEqual([])
    const kennungen = relay.mock.calls.map(
      (c) => (c[0] as { client_uuid: string }).client_uuid,
    )
    expect(kennungen).toContain(`${erste.id}#0`)
    expect(kennungen).toContain('nachzuegler#0')
  })

  it('bricht bei einem Kontowechsel ab und lässt den Auftrag liegen', async () => {
    relay.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await sendeWirkung(KONTEXT, REAKTION)
    const [auftrag] = await ladeSteuerAuftraege()

    const relais = steuerbaresRelais()
    const lauf = stosseSteuerOutboxAn({ jetzt: () => Date.now() + 10 * 60_000 })
    // Der Wechsel trifft mitten im Lauf ein — die Antwort des alten Kontos
    // darf nicht mit der Sitzung des neuen Kontos weitergehen.
    setzeAngemeldetesKonto(konto + 1)
    relais.loese({ id: 1 })
    await lauf

    // Das alte Konto behält seinen Auftrag; abgetragen wurde nichts.
    setzeAngemeldetesKonto(konto)
    const uebrig = await ladeSteuerAuftraege()
    expect(uebrig.map((a) => a.id)).toEqual([auftrag.id])
  })

  it('räumt die ausstehende Bearbeitung einer gelöschten Nachricht ab, nicht die Löschung', async () => {
    await legeNachrichtAn()
    relay.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await sendeWirkung(KONTEXT, {
      typ: 'edit_message',
      felder: {
        type: 'edit_message',
        target_client_uuid: 'u-1',
        target_id: 5,
        new_text: 'Hallo neu',
        edited_at: '2026-10-24T10:00:00.000Z',
      },
      ziel: 'u-1',
      zielId: 5,
      ruecknahme: {
        art: 'bearbeitung',
        mailbox: MAILBOX,
        ziel: 'u-1',
        zielId: 5,
        vorher: { text: 'Hallo', isEdited: false },
        nachher: { text: 'Hallo neu', isEdited: true },
      },
    })
    relay.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await sendeWirkung(KONTEXT, {
      typ: 'delete_message',
      felder: { type: 'delete_message', target_client_uuid: 'u-1', target_id: 5 },
      ziel: 'u-1',
      zielId: 5,
      ruecknahme: { art: 'keine' },
    })
    expect((await ladeSteuerAuftraege()).map((a) => a.typ).sort()).toEqual([
      'delete_message',
      'edit_message',
    ])

    await tilgeNachrichtLokal(MAILBOX, { id: 5, clientUuid: 'u-1', text: 'Hallo neu' }, '2026-10-24T11:00:00.000Z')

    // Die Bearbeitung würde die gelöschte Nachricht bei der Gegenseite wieder
    // aufleben lassen; die Löschung muss weitergehen.
    const uebrig = await ladeSteuerAuftraege()
    expect(uebrig.map((a) => a.typ)).toEqual(['delete_message'])
  })

  it('schickt bei einer Wiederholung dieselbe Kennung, nicht eine zweite Wirkung', async () => {
    relay.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await sendeWirkung(KONTEXT, REAKTION)
    const [auftrag] = await ladeSteuerAuftraege()

    await stosseSteuerOutboxAn({ jetzt: () => Date.now() + 10 * 60_000 })

    const kennungen = relay.mock.calls.map(
      (c) => (c[0] as { client_uuid: string }).client_uuid,
    )
    expect(kennungen).toEqual([`${auftrag.id}#0`, `${auftrag.id}#0`])
    expect(new Set(kennungen).size).toBe(1)
    expect(await ladeSteuerAuftraege()).toEqual([])
  })

  it('behält die Gruppenregel: ein Umschlag über den Gruppenschlüssel', async () => {
    const gruppe: SteuerKontext = {
      ...KONTEXT,
      drKontext: null,
      gruppenKontext: {
        groupId: 77,
        blindMailboxId: MAILBOX,
        eigeneId: ICH,
        mitglieder: [ICH, PARTNER, 3],
        istEigentuemer: true,
      },
    }
    relay.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await sendeWirkung(gruppe, { ...REAKTION, typ: 'reaction' })

    const [auftrag] = await ladeSteuerAuftraege()
    expect(auftrag).toMatchObject({
      art: 'gruppe',
      groupId: 77,
      peerId: null,
      mitglieder: [ICH, PARTNER, 3],
      istEigentuemer: true,
    })

    relay.mockClear()
    relay.mockImplementation(async () => ({ id: 1 }))
    await stosseSteuerOutboxAn({ jetzt: () => Date.now() + 10 * 60_000 })

    expect(relay).toHaveBeenCalledTimes(1)
    expect(relay.mock.calls[0][0]).toMatchObject({
      blind_mailbox_id: MAILBOX,
      ciphertext_envelope: 'sv-gruppe-v1:schluessel.rund',
      client_uuid: auftrag.id,
    })
  })

  it('gibt nach sechs Versuchen auf und sagt es', async () => {
    relay.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await sendeWirkung(KONTEXT, REAKTION)
    const meldungen: string[] = []
    setzeSteuerOutboxMelder((_auftrag, grund) => meldungen.push(grund))

    relay.mockReset()
    relay.mockImplementation(async () => {
      throw Object.assign(new Error('Server weg'), { status: 503 })
    })

    // Fünf Läufe holen je einen Versuch nach; der sechste ist der letzte.
    let zeit = Date.now()
    for (let i = 0; i < 6; i += 1) {
      zeit += 7 * 60 * 60_000
      await stosseSteuerOutboxAn({ jetzt: () => zeit })
    }

    expect(meldungen).toEqual(['aufgegeben'])
    expect(await ladeSteuerAuftraege()).toEqual([])
  })
})
