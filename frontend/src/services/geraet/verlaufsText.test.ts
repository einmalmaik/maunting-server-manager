/**
 * Der Textvertrag der Verlaufsübergabe: Manifest, Teile, Quittungen.
 *
 * Die Tests spielen Quelle und Ziel in einem Prozess, aber mit getrennten
 * lokalen Ablagen. Was zwischen ihnen reist, ist genau das, was auch im
 * Betrieb durch die Geräte-Mailbox geht: die äußere Hülle mit Ziel, Gerät und
 * Unterschrift, darin das versiegelte Paket.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { LocalStoredMessage } from '../messengerLocalStore'
import {
  VERLAUF_DONE_TYP,
  VERLAUF_MANIFEST_TYP,
  VERLAUF_PART_ACK_TYP,
  VERLAUF_SYNC_TYP,
} from './verlaufsTypen'

const relay = vi.fn()
const encrypt = vi.fn(async (text: string) => `enc:${btoa(text)}`)
const signiere = vi.fn(async (_mid: string, konto: number, roh: Record<string, unknown>) => ({
  ...roh,
  von_konto: konto,
  von_geraet: geraet.aktuell,
  sig: `sig:${geraet.aktuell}`,
}))
const pruefe = vi.fn(async (_mid: string, roh: Record<string, unknown>) => {
  if (roh.sig !== `sig:${String(roh.von_geraet)}`) {
    return { art: 'gefaelscht' as const, behauptet: Number(roh.von_konto) || 0 }
  }
  if (!geraete.some((g) => g.device_id === roh.von_geraet)) {
    return { art: 'gefaelscht' as const, behauptet: Number(roh.von_konto) || 0 }
  }
  return { art: 'geprueft' as const, vonKonto: Number(roh.von_konto) }
})

const geraet = vi.hoisted(() => ({ aktuell: 'sender-1' }))
const speicher = new Map<string, Map<string, LocalStoredMessage[]>>()
const postfach: Record<string, unknown>[] = []
const geliefert = new Set<number>()
const versuche: Record<string, unknown>[] = []
let fehlschlag: ((auftrag: Record<string, unknown>) => boolean) | null = null

const geraete = [
  { device_id: 'sender-1', public_key: '{"kty":"RSA"}' },
  { device_id: 'ziel-1', public_key: '{"kty":"RSA"}' },
  { device_id: 'fremd-1', public_key: '{"kty":"RSA"}' },
]

vi.mock('@/api/social', () => ({
  relayE2eeEnvelope: (auftrag: Record<string, unknown>) => {
    relay(auftrag)
    versuche.push(auftrag)
    if (fehlschlag?.(auftrag)) return Promise.reject(new Error('Netz weg'))
    postfach.push(auftrag)
    return Promise.resolve({ id: postfach.length })
  },
}))
vi.mock('@/lib/angemeldetesKonto', async (original) => {
  const echt = await original<typeof import('@/lib/angemeldetesKonto')>()
  return { ...echt, angemeldetesKonto: () => 3 }
})
vi.mock('../e2eeCrypto', () => ({
  deriveUserDeviceMailboxId: async (id: number) => `geraete-${id}`,
  deriveBlindMailboxId: async () => 'chat-a',
  encryptE2eeHybrid: (text: string) => encrypt(text),
}))
vi.mock('../gespraechsListe', () => ({
  ladeGespraeche: async () =>
    new Map([[4, { userId: 4, username: 'dave', blindMailboxId: 'chat-a', zuletzt: 0 }]]),
}))
vi.mock('../gruppen/gruppenAblage', () => ({
  ablage: { liesDmGeheimnis: async () => null },
}))
vi.mock('../gruppen/dmGeheimnis', () => ({
  uebernimmDmGeheimnis: vi.fn(async () => true),
}))
vi.mock('./geraeteAblage', () => ({
  eigenesGeraet: async () => ({
    kennung: geraet.aktuell,
    paar: { publicKeyJwk: '{"kty":"RSA"}', privateKeyJwk: '{}' },
    signaturPaar: { publicKeyJwk: '{}', privateKeyJwk: '{}' },
  }),
}))
vi.mock('./geraeteVerzeichnis', () => ({
  geraeteVon: async () => geraete,
  verzeichnisVon: async () => geraete,
}))
vi.mock('../nutzlastSignatur', () => ({
  signiereNutzlast: (mid: string, konto: number, roh: Record<string, unknown>) =>
    signiere(mid, konto, roh),
  pruefeNutzlast: (mid: string, roh: Record<string, unknown>) => pruefe(mid, roh),
}))
vi.mock('../messengerLocalStore', async (original) => {
  const echt = await original<typeof import('../messengerLocalStore')>()
  return {
    ...echt,
    listeLokaleMailboxen: async () => [...(speicher.get(geraet.aktuell)?.keys() ?? [])],
    listeLokaleMailboxenStrict: async () => [...(speicher.get(geraet.aktuell)?.keys() ?? [])],
    loadLocalMessages: async (mid: string) => speicher.get(geraet.aktuell)?.get(mid) ?? [],
    loadLocalMessagesStrict: async (mid: string) => speicher.get(geraet.aktuell)?.get(mid) ?? [],
    saveLocalMessages: async (mid: string, nachrichten: LocalStoredMessage[]) => {
      const ablage = speicher.get(geraet.aktuell) ?? new Map<string, LocalStoredMessage[]>()
      ablage.set(mid, nachrichten)
      speicher.set(geraet.aktuell, ablage)
    },
  }
})

import { verarbeiteVerlaufsAbgleich, sendeVerlaufAnGeraet } from './verlaufsAbgleich'
import { eigenesGeraet } from './geraeteAblage'
import { sendeSigniertesPaket } from './verlaufsVersand'

const ziel = {
  device_id: 'ziel-1',
  public_key: '{"kty":"RSA"}',
  signing_public_key: '',
  label: 'Neu',
  is_approved: true,
} as any
const zielFremd = { ...ziel, device_id: 'fremd-1' }

function nachricht(id: number, text: string): LocalStoredMessage {
  return {
    id,
    clientUuid: `u-${id}`,
    senderId: 3,
    text,
    createdAt: new Date(2026, 9, 1, 12, 0, id).toISOString(),
    isSelf: true,
  } as LocalStoredMessage
}

function frischeAblage(mailboxen: Record<string, LocalStoredMessage[]>): void {
  for (const [name, nachrichten] of Object.entries(mailboxen)) {
    speicher.set('sender-1', new Map([[name, nachrichten]]))
  }
  speicher.set('ziel-1', new Map())
}

async function entsiegle(umschlag: string): Promise<string> {
  if (!umschlag.startsWith('enc:')) throw new Error('fremde Hülle')
  return atob(umschlag.slice(4))
}

async function rohVon(auftrag: Record<string, unknown>): Promise<Record<string, unknown>> {
  return JSON.parse(await entsiegle(String(auftrag.ciphertext_envelope)))
}

async function zustellen(roh: Record<string, unknown>, empfaenger: string): Promise<number> {
  geraet.aktuell = empfaenger
  return await verarbeiteVerlaufsAbgleich(roh, 3, entsiegle)
}

/** Stellt alle noch nicht zugestellten Pakete an ein Gerät zu. */
async function zustellenAn(empfaenger: string): Promise<number> {
  let summe = 0
  for (let i = 0; i < postfach.length; i += 1) {
    if (geliefert.has(i)) continue
    const roh = await rohVon(postfach[i])
    if (String(roh.ziel || '') !== empfaenger) continue
    geliefert.add(i)
    summe += await zustellen(roh, empfaenger)
  }
  return summe
}

async function baueManifest(optionen: {
  transferId?: string
  ablauf?: number
  posten?: unknown[]
  zielDevice?: 'ziel-1' | 'fremd-1'
}): Promise<Record<string, unknown>> {
  geraet.aktuell = 'sender-1'
  const meins = await eigenesGeraet()
  const huelle = await sendeSigniertesPaket({
    konto: 3,
    meins,
    ziel: optionen.zielDevice === 'fremd-1' ? zielFremd : ziel,
    mailbox: 'geraete-3',
    typ: VERLAUF_MANIFEST_TYP,
    clientUuid: `test-manifest-${optionen.transferId ?? 'x'}`,
    felder: {
      transferId: optionen.transferId ?? 't-1',
      ablauf: optionen.ablauf ?? Date.now() + 60_000,
    },
    innereDaten: JSON.stringify({
      v: 1,
      posten: optionen.posten ?? [{ mailbox: 'chat-a', art: 'direkt', teile: 1, anzahl: 1 }],
    }),
  })
  return JSON.parse(await entsiegle(huelle))
}

async function baueTeil(
  transferId: string,
  teil: number,
  teile: number,
  optionen: { ablauf?: number; mailbox?: string; nachrichten?: LocalStoredMessage[] } = {},
): Promise<Record<string, unknown>> {
  geraet.aktuell = 'sender-1'
  const meins = await eigenesGeraet()
  const mailbox = optionen.mailbox ?? 'chat-a'
  const huelle = await sendeSigniertesPaket({
    konto: 3,
    meins,
    ziel,
    mailbox: 'geraete-3',
    typ: VERLAUF_SYNC_TYP,
    clientUuid: `test-teil-${transferId}-${teil}`,
    felder: {
      transferId,
      mailbox,
      art: 'direkt',
      teil,
      teile,
      ablauf: optionen.ablauf ?? Date.now() + 60_000,
    },
    innereDaten: JSON.stringify({
      v: 1,
      mailbox,
      art: 'direkt',
      nachrichten: optionen.nachrichten ?? [nachricht(1, 'Teil')],
    }),
  })
  return JSON.parse(await entsiegle(huelle))
}

function paketeVomTyp(typ: string): Record<string, unknown>[] {
  return postfach.filter((auftrag) => auftrag.control_type === typ)
}

describe('Textverlauf mit Manifest und Empfangsquittung', () => {
  beforeEach(() => {
    relay.mockClear()
    encrypt.mockClear()
    signiere.mockClear()
    pruefe.mockClear()
    postfach.length = 0
    versuche.length = 0
    geliefert.clear()
    speicher.clear()
    fehlschlag = null
    geraet.aktuell = 'sender-1'
    window.localStorage.clear()
  })

  it('schickt Manifest, Teile, Quittungen und merkt erst nach history_done', async () => {
    frischeAblage({
      'chat-a': Array.from({ length: 250 }, (_, i) => nachricht(i + 1, `Nachricht ${i + 1}`)),
    })

    const anzahl = await sendeVerlaufAnGeraet(ziel)

    expect(anzahl).toBe(2)
    expect(paketeVomTyp(VERLAUF_MANIFEST_TYP)).toHaveLength(1)
    expect(paketeVomTyp(VERLAUF_SYNC_TYP)).toHaveLength(2)
    // Der Merker fällt nicht beim Absenden.
    expect(window.localStorage.getItem('msm:history_sent:3:ziel-1')).toBeNull()

    const manifest = await rohVon(paketeVomTyp(VERLAUF_MANIFEST_TYP)[0])
    const transferId = String(manifest.transferId)
    expect(transferId).toHaveLength(40)

    await zustellenAn('ziel-1')
    expect(speicher.get('ziel-1')?.get('chat-a')).toHaveLength(250)
    expect(paketeVomTyp(VERLAUF_PART_ACK_TYP)).toHaveLength(2)
    expect(paketeVomTyp(VERLAUF_DONE_TYP)).toHaveLength(1)

    // Auch nach allen Teilen und Quittungen noch nicht: erst die Schlussquittung.
    expect(window.localStorage.getItem('msm:history_sent:3:ziel-1')).toBeNull()

    await zustellenAn('sender-1')
    expect(window.localStorage.getItem('msm:history_sent:3:ziel-1')).toBeTruthy()
  })

  it('holt ein verlorenes Teil beim nächsten Lauf unter derselben Kennung nach', async () => {
    frischeAblage({
      'chat-a': Array.from({ length: 250 }, (_, i) => nachricht(i + 1, `Nachricht ${i + 1}`)),
    })
    fehlschlag = (auftrag) => {
      if (auftrag.control_type !== VERLAUF_SYNC_TYP) return false
      const roh = JSON.parse(atob(String(auftrag.ciphertext_envelope).slice(4)))
      return Number(roh.teil) === 1
    }

    const teilfehler = vi.fn()
    expect(await sendeVerlaufAnGeraet(ziel, { beiTeilfehler: teilfehler })).toBe(1)
    expect(teilfehler).toHaveBeenCalled()

    // Der erste Teil kommt an und wird quittiert; der zweite fehlt.
    await zustellenAn('ziel-1')
    await zustellenAn('sender-1')
    expect(window.localStorage.getItem('msm:history_sent:3:ziel-1')).toBeNull()
    expect(paketeVomTyp(VERLAUF_DONE_TYP)).toHaveLength(0)

    const ersterVersuch = versuche
      .filter((auftrag) => auftrag.control_type === VERLAUF_SYNC_TYP)
      .map((auftrag) => ({
        roh: JSON.parse(atob(String(auftrag.ciphertext_envelope).slice(4))),
        huelle: auftrag.ciphertext_envelope,
      }))
      .find((eintrag) => Number(eintrag.roh.teil) === 1)!

    fehlschlag = null
    const vorher = postfach.length
    expect(await sendeVerlaufAnGeraet(ziel)).toBe(1)

    const neueTeile = postfach
      .slice(vorher)
      .filter((auftrag) => auftrag.control_type === VERLAUF_SYNC_TYP)
    expect(neueTeile).toHaveLength(1)
    const nachgeholt = await rohVon(neueTeile[0])
    expect(nachgeholt.transferId).toBe(ersterVersuch.roh.transferId)
    expect(nachgeholt.teil).toBe(1)
    expect(neueTeile[0].ciphertext_envelope).toBe(ersterVersuch.huelle)

    await zustellenAn('ziel-1')
    expect(speicher.get('ziel-1')?.get('chat-a')).toHaveLength(250)
    await zustellenAn('sender-1')
    expect(window.localStorage.getItem('msm:history_sent:3:ziel-1')).toBeTruthy()
  })

  it('übersteht einen Neustart, weil der Fortschritt in der Ablage liegt', async () => {
    frischeAblage({
      'chat-a': Array.from({ length: 250 }, (_, i) => nachricht(i + 1, `Nachricht ${i + 1}`)),
    })
    await sendeVerlaufAnGeraet(ziel)
    const manifest = await rohVon(paketeVomTyp(VERLAUF_MANIFEST_TYP)[0])
    const teile = await Promise.all(paketeVomTyp(VERLAUF_SYNC_TYP).map(rohVon))
    await zustellen(manifest, 'ziel-1')
    await zustellen(teile[0], 'ziel-1')

    const rohe = JSON.parse(String(window.localStorage.getItem('msm:chat_verlauf_empfang')))
    const transferId = String(manifest.transferId)
    expect(rohe[transferId]).toBeTruthy()
    // Zwei Teile, der erste ist angenommen.
    expect(rohe[transferId].teile['chat-a']).toBe('10')

    // Ein frischer Modulzustand: nichts im Arbeitsspeicher, alles auf der Platte.
    vi.resetModules()
    const frisch = await import('./verlaufsAbgleich')
    geraet.aktuell = 'ziel-1'
    expect(await frisch.verarbeiteVerlaufsAbgleich(teile[1], 3, entsiegle)).toBe(1)

    expect(speicher.get('ziel-1')?.get('chat-a')).toHaveLength(250)
    // Das doppelte Teil quittiert erneut — auch die Schlussquittung darf
    // wiederholt werden, bis die Quelle sie erhalten hat.
    expect(paketeVomTyp(VERLAUF_DONE_TYP).length).toBeGreaterThanOrEqual(1)
  })

  it('erzeugt aus doppelten Teilen keine doppelten Nachrichten', async () => {
    frischeAblage({
      'chat-a': Array.from({ length: 250 }, (_, i) => nachricht(i + 1, `Nachricht ${i + 1}`)),
    })
    await sendeVerlaufAnGeraet(ziel)
    const manifest = await rohVon(paketeVomTyp(VERLAUF_MANIFEST_TYP)[0])
    const teile = await Promise.all(paketeVomTyp(VERLAUF_SYNC_TYP).map(rohVon))

    expect(await zustellen(manifest, 'ziel-1')).toBe(1)
    expect(await zustellen(teile[0], 'ziel-1')).toBe(1)
    // Dasselbe Teil noch einmal: die Quittung geht erneut hinaus, der Verlauf
    // wächst nicht.
    expect(await zustellen(teile[0], 'ziel-1')).toBe(1)
    expect(speicher.get('ziel-1')?.get('chat-a')).toHaveLength(200)
    expect(await zustellen(teile[1], 'ziel-1')).toBe(1)
    expect(speicher.get('ziel-1')?.get('chat-a')).toHaveLength(250)
    expect(paketeVomTyp(VERLAUF_PART_ACK_TYP).length).toBe(3)
  })

  it('weist ein Teil ohne angenommenes Manifest ab', async () => {
    const teil = await baueTeil('t-ohne-manifest', 0, 1)

    expect(await zustellen(teil, 'ziel-1')).toBe(0)
    expect(speicher.get('ziel-1')?.get('chat-a')).toBeUndefined()
    expect(paketeVomTyp(VERLAUF_PART_ACK_TYP)).toHaveLength(0)
  })

  it('verwirft fremdes Ziel, falsche Unterschrift und abgelaufenes Fenster', async () => {
    // Fremdes Zielgerät: die äußere Prüfung greift vor jeder Entsiegelung.
    expect(await zustellen(await baueManifest({ zielDevice: 'fremd-1' }), 'ziel-1')).toBe(0)

    // Falsche Unterschrift.
    const gefaelscht = await baueManifest({ transferId: 't-gefaelscht' })
    gefaelscht.sig = 'sig:fremd-1'
    expect(await zustellen(gefaelscht, 'ziel-1')).toBe(0)

    // Abgelaufenes Manifest.
    expect(
      await zustellen(
        await baueManifest({ transferId: 't-alt', ablauf: Date.now() - 1_000 }),
        'ziel-1',
      ),
    ).toBe(0)

    // Ein gültiges Manifest, aber ein Teil aus einem alten Fenster.
    const gueltig = await baueManifest({ transferId: 't-fenster' })
    expect(await zustellen(gueltig, 'ziel-1')).toBe(1)
    const altesTeil = await baueTeil('t-fenster', 0, 1, { ablauf: Date.now() - 1_000 })
    expect(await zustellen(altesTeil, 'ziel-1')).toBe(0)
    expect(speicher.get('ziel-1')?.get('chat-a')).toBeUndefined()

    // Kein Fortschritt für das abgelaufene Fenster.
    const stand = JSON.parse(String(window.localStorage.getItem('msm:chat_verlauf_empfang')))
    expect(stand['t-alt']).toBeUndefined()
    expect(stand['t-gefaelscht']).toBeUndefined()
  })

  it('merkt den Verlauf erst, wenn Schlussquittung und alle Teile bestätigt sind', async () => {
    frischeAblage({ 'chat-a': [nachricht(1, 'Hallo')] })
    await sendeVerlaufAnGeraet(ziel)
    const plan = JSON.parse(String(window.localStorage.getItem('msm:chat_verlauf_transfer')))
    const transferId = plan['ziel-1'].transferId
    const done = {
      type: VERLAUF_DONE_TYP,
      v: 1,
      ziel: 'sender-1',
      geraet: 'ziel-1',
      von_konto: 3,
      von_geraet: 'ziel-1',
      sig: 'sig:ziel-1',
      transferId,
    }

    // Die Schlussquittung allein genügt nicht: ein Teil könnte unterwegs
    // verloren gegangen sein, ohne dass die Quelle es merkt.
    expect(await zustellen(done, 'sender-1')).toBe(0)
    expect(window.localStorage.getItem('msm:history_sent:3:ziel-1')).toBeNull()

    expect(
      await zustellen(
        {
          type: VERLAUF_PART_ACK_TYP,
          v: 1,
          ziel: 'sender-1',
          geraet: 'ziel-1',
          von_konto: 3,
          von_geraet: 'ziel-1',
          sig: 'sig:ziel-1',
          transferId,
          mailbox: 'chat-a',
          teil: 0,
          teile: 1,
        },
        'sender-1',
      ),
    ).toBe(1)
    expect(await zustellen(done, 'sender-1')).toBe(1)
    expect(window.localStorage.getItem('msm:history_sent:3:ziel-1')).toBeTruthy()
  })

  it('nimmt eine Quittung nur für den laufenden Plan dieses Ziels an', async () => {
    frischeAblage({ 'chat-a': [nachricht(1, 'Hallo')] })
    await sendeVerlaufAnGeraet(ziel)
    const plan = JSON.parse(String(window.localStorage.getItem('msm:chat_verlauf_transfer')))
    const transferId = plan['ziel-1'].transferId

    // Fremde Übertragungskennung: der Plan bleibt offen.
    expect(
      await zustellen(
        {
          type: VERLAUF_PART_ACK_TYP,
          ziel: 'sender-1',
          geraet: 'ziel-1',
          von_konto: 3,
          von_geraet: 'ziel-1',
          sig: 'sig:ziel-1',
          transferId: 't-falsch',
          mailbox: 'chat-a',
          teil: 0,
          teile: 1,
        },
        'sender-1',
      ),
    ).toBe(0)
    expect(
      JSON.parse(String(window.localStorage.getItem('msm:chat_verlauf_transfer')))[
        'ziel-1'
      ].transferId,
    ).toBe(transferId)

    // Die richtige Quittung nimmt ihn an, merkt aber noch nicht „gesendet".
    expect(
      await zustellen(
        {
          type: VERLAUF_PART_ACK_TYP,
          ziel: 'sender-1',
          geraet: 'ziel-1',
          von_konto: 3,
          von_geraet: 'ziel-1',
          sig: 'sig:ziel-1',
          transferId,
          mailbox: 'chat-a',
          teil: 0,
          teile: 1,
        },
        'sender-1',
      ),
    ).toBe(1)
    expect(window.localStorage.getItem('msm:history_sent:3:ziel-1')).toBeNull()
  })
})
