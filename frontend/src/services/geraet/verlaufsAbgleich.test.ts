import { beforeEach, describe, expect, it, vi } from 'vitest'

const relay = vi.fn()
const encrypt = vi.fn(async (text: string) => `sv-e2ee-hybrid-v1:${btoa(text)}`)
const signiere = vi.fn(async (_mid: string, konto: number, roh: Record<string, unknown>) => ({
  ...roh,
  von_konto: konto,
  von_geraet: 'sender-1',
  sig: 'beleg',
}))
const pruefe = vi.fn(async () => ({ art: 'geprueft' as const, vonKonto: 3 }))
const dmMock = vi.hoisted(() => ({ nimm: vi.fn(async () => true) }))
const speicher = new Map<string, any[]>()

vi.mock('@/api/social', () => ({
  relayE2eeEnvelope: (auftrag: unknown) => {
    relay(auftrag)
    return Promise.resolve({ id: 1 })
  },
}))
vi.mock('@/lib/angemeldetesKonto', async (original) => {
  const echt = await original<typeof import('@/lib/angemeldetesKonto')>()
  return { ...echt, angemeldetesKonto: () => 3 }
})
vi.mock('../e2eeCrypto', () => ({
  deriveUserDeviceMailboxId: async (id: number) => `geraete-${id}`,
  deriveBlindMailboxId: async (a: number, b: number) => `alt-${a}-${b}`,
  encryptE2eeHybrid: (text: string) => encrypt(text),
}))
vi.mock('../gespraechsListe', () => ({
  ladeGespraeche: async () =>
    new Map([[4, { userId: 4, username: 'dave', blindMailboxId: 'chat-a', zuletzt: 0 }]]),
}))
vi.mock('../gruppen/gruppenAblage', () => ({
  ablage: {
    liesDmGeheimnis: async (peerId: number) =>
      peerId === 4 ? { peerId: 4, geheimnis: 'chat-geheimnis', verteiltAm: '2026-10-21' } : null,
  },
}))
vi.mock('../gruppen/dmGeheimnis', () => ({
  uebernimmDmGeheimnis: (peerId: number, geheimnis: string) => dmMock.nimm(peerId, geheimnis),
}))
vi.mock('./geraeteAblage', () => ({
  eigenesGeraet: async () => ({
    kennung: 'sender-1',
    paar: { publicKeyJwk: '{"kty":"RSA"}' },
    signaturPaar: { privateKeyJwk: '{}' },
  }),
}))
vi.mock('./geraeteVerzeichnis', () => ({
  geraeteVon: async () => [
    { device_id: 'sender-1', public_key: '{"kty":"RSA"}' },
    { device_id: 'ziel-1', public_key: '{"kty":"RSA"}' },
  ],
}))
vi.mock('../nutzlastSignatur', () => ({
  signiereNutzlast: (mid: string, konto: number, roh: Record<string, unknown>) => signiere(mid, konto, roh),
  pruefeNutzlast: (mid: string, roh: Record<string, unknown>) => pruefe(mid, roh),
}))
vi.mock('../messengerLocalStore', async (original) => {
  const echt = await original<typeof import('../messengerLocalStore')>()
  return {
    ...echt,
    listeLokaleMailboxen: async () => [...speicher.keys()],
    loadLocalMessages: async (mid: string) => speicher.get(mid) ?? [],
    saveLocalMessages: async (mid: string, nachrichten: any[]) => {
      speicher.set(mid, nachrichten)
    },
  }
})

import {
  VERLAUF_SYNC_TYP,
  sendeVerlaufAnGeraet,
  verarbeiteVerlaufsAbgleich,
} from './verlaufsAbgleich'

const ziel = {
  device_id: 'ziel-1',
  public_key: '{"kty":"RSA"}',
  signing_public_key: '',
  label: 'Neu',
  is_approved: true,
} as any

function nachricht(id: number, text: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    clientUuid: `u-${id}`,
    senderId: 3,
    text,
    createdAt: new Date(2026, 9, 1, 12, 0, id).toISOString(),
    isSelf: true,
    ...extra,
  }
}

describe('Verlaufsübergabe an ein freigegebenes Gerät', () => {
  beforeEach(() => {
    relay.mockClear()
    encrypt.mockClear()
    pruefe.mockClear()
    dmMock.nimm.mockClear()
    speicher.clear()
  })

  it('schickt je Mailbox einen Umschlag an die eigene Geräte-Mailbox', async () => {
    speicher.set('chat-a', [
      nachricht(1, 'Hallo', { imageAttachment: { name: 'a.png', dataUrl: 'data:image/png;base64,AAAA' } }),
      nachricht(2, 'Welt'),
      nachricht(3, 'Geheim', {
        isDeleted: true,
        deletedAt: '2026-10-21T13:00:00.000Z',
        originalText: 'Geheim',
        noteAttachment: { title: 'Notiz', content: 'Inhalt' },
      }),
      nachricht(4, 'Noch unterwegs', { status: 'queued' }),
    ])
    speicher.set('chat-b', [nachricht(5, 'Anders')])

    const anzahl = await sendeVerlaufAnGeraet(ziel)

    expect(anzahl).toBe(2)
    expect(relay).toHaveBeenCalledTimes(2)
    for (const [auftrag] of relay.mock.calls) {
      expect(auftrag.blind_mailbox_id).toBe('geraete-3')
      expect(auftrag.is_control).toBe(true)
      expect(auftrag.control_type).toBe(VERLAUF_SYNC_TYP)
    }
    const entschluesselt = encrypt.mock.calls.map(([text]) => JSON.parse(text))
    const a = entschluesselt.find((p) => p.type === VERLAUF_SYNC_TYP && p.ziel === 'ziel-1')
    expect(a?.geraet).toBe('sender-1')
    // Der eingebettete Anhang bleibt als Verweis erhalten, nicht als Datenblock.
    const paket = JSON.parse(atob(a!.paket.replace('sv-e2ee-hybrid-v1:', '')))
    expect(paket.mailbox).toBe('chat-a')
    expect(paket.nachrichten[0].imageAttachment.dataUrl).toBeUndefined()
    expect(paket.nachrichten[0].imageAttachment.name).toBe('a.png')
    // Das Chatgeheimnis reist mit: sonst liest das neue Gerät die alte Mailbox
    // und sieht keine neuen Nachrichten, bis ein Geschwistergerät antwortet.
    expect(paket.dmGeheimnis).toEqual({ peerId: 4, geheimnis: 'chat-geheimnis' })
    // Gelöscht bleibt gelöscht, und was nie hinausging, reist nicht mit.
    const geloescht = paket.nachrichten.find((m: any) => m.id === 3)
    expect(geloescht.text).toBe('')
    expect(geloescht.originalText).toBeUndefined()
    expect(geloescht.noteAttachment).toBeUndefined()
    expect(paket.nachrichten.some((m: any) => m.id === 4)).toBe(false)
  })

  it('teilt einen langen Verlauf in mehrere Pakete, statt bei 200 abzubrechen', async () => {
    speicher.set(
      'chat-a',
      Array.from({ length: 250 }, (_, i) => nachricht(i + 1, `Nachricht ${i + 1}`)),
    )

    const anzahl = await sendeVerlaufAnGeraet(ziel)

    expect(anzahl).toBe(2)
    expect(relay).toHaveBeenCalledTimes(2)
  })

  it('nimmt ein Verlaufspaket für dieses Gerät an und mischt es', async () => {
    speicher.set('chat-a', [nachricht(1, 'lokal')])
    const paket = {
      v: 1,
      mailbox: 'chat-a',
      nachrichten: [nachricht(1, 'lokal'), nachricht(2, 'vom anderen Gerät')],
      dmGeheimnis: { peerId: 4, geheimnis: 'chat-geheimnis' },
    }
    const roh = {
      type: VERLAUF_SYNC_TYP,
      ziel: 'sender-1',
      geraet: 'ziel-1',
      von_konto: 3,
      von_geraet: 'ziel-1',
      sig: 'beleg',
      paket: 'sv-e2ee-hybrid-v1:egal',
    }

    const angenommen = await verarbeiteVerlaufsAbgleich(roh, 3, async () => JSON.stringify(paket))

    expect(angenommen).toBe(2)
    expect(speicher.get('chat-a')?.map((m) => m.text)).toEqual(['lokal', 'vom anderen Gerät'])
    expect(dmMock.nimm.mock.calls[0]?.slice(0, 2)).toEqual([4, 'chat-geheimnis'])
  })

  it('verwirft ein Paket für ein anderes Gerät', async () => {
    const angenommen = await verarbeiteVerlaufsAbgleich(
      {
        type: VERLAUF_SYNC_TYP,
        ziel: 'anderes-gerät',
        geraet: 'ziel-1',
        von_konto: 3,
        von_geraet: 'ziel-1',
        sig: 'beleg',
        paket: 'egal',
      },
      3,
      async () => JSON.stringify({ mailbox: 'chat-a', nachrichten: [nachricht(1, 'x')] }),
    )
    expect(angenommen).toBe(0)
  })

  it('verwirft ein Paket ohne gültige Unterschrift', async () => {
    pruefe.mockResolvedValueOnce({ art: 'gefaelscht', behauptet: 3 } as any)
    const angenommen = await verarbeiteVerlaufsAbgleich(
      {
        type: VERLAUF_SYNC_TYP,
        ziel: 'sender-1',
        geraet: 'fremdes-gerät',
        von_konto: 3,
        von_geraet: 'fremdes-gerät',
        sig: 'beleg',
        paket: 'egal',
      },
      3,
      async () => JSON.stringify({ mailbox: 'chat-a', nachrichten: [nachricht(1, 'x')] }),
    )
    expect(angenommen).toBe(0)
  })
})
