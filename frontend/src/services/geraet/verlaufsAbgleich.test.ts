import 'fake-indexeddb/auto'
import { utf8ToBytes } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { setzeSiegelAktiv } from '../lokaleVersiegelung'

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
  verzeichnisVon: async () => [
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

import { leseMedium, speichereMedium } from '../messengerLocalStore'
import {
  VERLAUF_SYNC_TYP,
  merkeVerlaufGesendet,
  sendeVerlaufAnGeraet,
  verarbeiteVerlaufsAbgleich,
} from './verlaufsAbgleich'
import { VERLAUF_MEDIA_ACK_TYP, VERLAUF_MEDIA_TYP } from './verlaufsMedien'

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
    window.localStorage.clear()
    setzeSiegelAktiv(false)
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

  it('packt weder Platzhalter noch dieselbe Nachricht zweimal ins Paket', async () => {
    speicher.set('chat-a', [
      nachricht(1, 'echt'),
      nachricht(2, i18n.t('messenger.encryptedMessage'), { isSelf: false, senderId: 4 }),
      // Dieselbe logische Kennung zweimal — etwa die Kopie des Gegenübers.
      nachricht(3, 'doppelt'),
      nachricht(3, 'doppelt'),
    ])

    await sendeVerlaufAnGeraet(ziel)

    const aussen = encrypt.mock.calls.map(([text]) => JSON.parse(text))
    const paket = JSON.parse(
      atob(aussen.find((p) => p.type === VERLAUF_SYNC_TYP)!.paket.replace('sv-e2ee-hybrid-v1:', '')),
    )
    expect(paket.nachrichten.map((m: any) => m.text)).toEqual(['echt', 'doppelt'])
    expect(paket.art).toBe('direkt')
  })

  it('misst die äußere Hülle und halbiert, statt das Paket zu verlieren', async () => {
    // Das Relais nimmt 1 MiB an. Bis 10/2026 wurde nur der **innere** Umschlag
    // geprüft; Signatur und Hybridverschlüsselung legten ihre eigene Hülle
    // darum, und das Paket kam als Ganzes nicht an — der Verlauf riss an genau
    // dieser Stelle ab, ohne Meldung.
    speicher.set(
      'chat-a',
      Array.from({ length: 120 }, (_, i) => nachricht(i + 1, `Nachricht ${i + 1}`)),
    )
    const original = encrypt.getMockImplementation()!
    encrypt.mockImplementation(async (text: string) => {
      const b64 = `sv-e2ee-hybrid-v1:${btoa(text)}`
      // Die äußere Hülle (der signierte Umschlag mit `paket`) ist zu groß,
      // solange sie mehr als sechzig Nachrichten trägt.
      if (text.includes('"paket"')) {
        const innen = atob(JSON.parse(text).paket.replace('sv-e2ee-hybrid-v1:', ''))
        const anzahl = (innen.match(/"createdAt"/g) ?? []).length
        return anzahl > 60 ? 'x'.repeat(1_000_001) : b64
      }
      return b64
    })
    try {
      await sendeVerlaufAnGeraet(ziel)
    } finally {
      encrypt.mockImplementation(original)
    }

    expect(relay).toHaveBeenCalled()
    // Was wirklich hinausging, nicht was der erste Versuch war: die erste
    // Hülle war zu groß und wurde verworfen.
    const gesendet = relay.mock.calls[0][0].ciphertext_envelope
    const aussen = JSON.parse(atob(gesendet.replace('sv-e2ee-hybrid-v1:', '')))
    const paket = JSON.parse(atob(aussen.paket.replace('sv-e2ee-hybrid-v1:', '')))
    expect(paket.nachrichten.length).toBe(60)
  })

  it('merkt eine vollständige Übergabe und lässt einen Teilfehler offen', async () => {
    window.localStorage.clear()
    speicher.set('chat-a', [nachricht(1, 'Hallo')])

    merkeVerlaufGesendet('ziel-1')
    expect(window.localStorage.getItem('msm:history_sent:3:ziel-1')).toBeTruthy()

    // Ein Teilfehler darf nicht als erledigt gelten: sonst bliebe die Lücke
    // für immer, weil der nächste Start den Merker sieht.
    window.localStorage.clear()
    const teilfehler = vi.fn()
    const original = speicher.get.bind(speicher)
    speicher.get = (() => {
      throw new Error('Ablage kaputt')
    }) as typeof speicher.get
    try {
      expect(await sendeVerlaufAnGeraet(ziel, { beiTeilfehler: teilfehler })).toBe(0)
    } finally {
      speicher.get = original
    }
    expect(teilfehler).toHaveBeenCalled()
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

  it('führt ein Gruppenpaket mit Absender zusammen', async () => {
    speicher.set('chat-a', [])
    const paket = {
      v: 1,
      mailbox: 'chat-a',
      art: 'gruppe',
      nachrichten: [
        { ...nachricht(1, 'von A'), senderId: 1, clientUuid: 'gleiche-uuid' },
        { ...nachricht(2, 'von B'), senderId: 2, clientUuid: 'gleiche-uuid' },
      ],
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

    expect(await verarbeiteVerlaufsAbgleich(roh, 3, async () => JSON.stringify(paket))).toBe(2)
    expect(speicher.get('chat-a')?.map((m) => m.text)).toEqual(['von A', 'von B'])
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

  it('überträgt mehr als 5000 Nachrichten, statt den Rest fallenzulassen', async () => {
    speicher.set(
      'chat-a',
      Array.from({ length: 5001 }, (_, i) => nachricht(i + 1, `Nachricht ${i + 1}`)),
    )

    const anzahl = await sendeVerlaufAnGeraet(ziel)

    // 5001 Nachrichten in 200er-Teilen = 26 Umschläge. Bis 10/2026 waren bei
    // 25 (5000 Nachrichten) Schluss, und der Rest fiel still weg.
    expect(anzahl).toBe(26)
  })

  it('nimmt eine empfangene Postfach-Nachricht mit, auch wenn sie lokal queued heißt', async () => {
    // Über das Gerätepostfach empfangene Zeilen tragen eine negative Kennung
    // und lokal `status: 'queued'`. Bis 23.10.2026 hielt der Export sie für
    // nie gesendete eigene Nachrichten und ließ sie aus jeder Übergabe weg.
    speicher.set('chat-a', [
      {
        ...nachricht(1, 'angekommen', { isSelf: false, senderId: 4 }),
        id: -7,
        status: 'queued',
      },
    ])

    await sendeVerlaufAnGeraet(ziel)

    const aussen = encrypt.mock.calls.map(([text]) => JSON.parse(text))
    const paket = JSON.parse(
      atob(aussen.find((p) => p.type === VERLAUF_SYNC_TYP)!.paket.replace('sv-e2ee-hybrid-v1:', '')),
    )
    expect(paket.nachrichten.map((m: any) => m.text)).toEqual(['angekommen'])
  })

  it('nimmt abgelaufene Nachrichten nicht in die Übergabe', async () => {
    speicher.set('chat-a', [
      nachricht(1, 'alt', { verfaelltAm: '2020-01-01T00:00:00.000Z' }),
      nachricht(2, 'neu'),
    ])

    await sendeVerlaufAnGeraet(ziel)

    const aussen = encrypt.mock.calls.map(([text]) => JSON.parse(text))
    const paket = JSON.parse(
      atob(aussen.find((p) => p.type === VERLAUF_SYNC_TYP)!.paket.replace('sv-e2ee-hybrid-v1:', '')),
    )
    expect(paket.nachrichten.map((m: any) => m.text)).toEqual(['neu'])
  })

  it('schickt einen Anhang in geprüften Teilen statt als Serververweis', async () => {
    const inhalt = 'data:image/png;base64,' + 'A'.repeat(400_000)
    await speichereMedium('m-bild', inhalt, 'bild.png')
    speicher.set('chat-a', [
      nachricht(1, 'Bild', {
        imageAttachment: {
          mediaId: 'm-bild',
          paketSchluessel: 'paket',
          fileId: 'datei',
          name: 'bild.png',
        },
      }),
    ])

    await sendeVerlaufAnGeraet(ziel)

    const medien = relay.mock.calls
      .map(([auftrag]) => auftrag)
      .filter((auftrag) => auftrag.control_type === VERLAUF_MEDIA_TYP)
    expect(medien.length).toBe(3)
    const teile = medien.map((auftrag) => {
      const aussen = JSON.parse(
        atob(auftrag.ciphertext_envelope.replace('sv-e2ee-hybrid-v1:', '')),
      )
      return JSON.parse(atob(aussen.paket.replace('sv-e2ee-hybrid-v1:', '')))
    })
    expect(new Set(teile.map((p) => p.transferId)).size).toBe(1)
    expect(teile.map((p) => p.teil)).toEqual([0, 1, 2])
    expect(teile.every((p) => p.teile === 3 && p.mediaId === 'm-bild')).toBe(true)
    expect(teile.every((p) => /^[a-f0-9]{64}$/.test(p.sha256))).toBe(true)
    expect(teile.map((p) => p.daten).join('')).toBe(inhalt)
  })

  it('setzt einen Anhang aus den Teilen zusammen und bestätigt ihn', async () => {
    const daten = 'data:image/png;base64,' + 'B'.repeat(250_000)
    const sha256 = await sha256Hex(utf8ToBytes(daten))
    const bauen = (teil: number) => {
      const paket = JSON.stringify({
        v: 1,
        transferId: 't-empfang',
        mediaId: 'm-empfang',
        name: 'empfang.png',
        teil,
        teile: 2,
        sha256,
        groesse: daten.length,
        daten: daten.slice(teil * 160_000, (teil + 1) * 160_000),
        ablauf: Date.now() + 60_000,
      })
      return {
        type: VERLAUF_MEDIA_TYP,
        ziel: 'sender-1',
        geraet: 'ziel-1',
        von_konto: 3,
        sig: 'beleg',
        paket: `sv-e2ee-hybrid-v1:${btoa(paket)}`,
      }
    }
    const entsiegle = async (umschlag: string) =>
      atob(umschlag.replace('sv-e2ee-hybrid-v1:', ''))

    expect(await verarbeiteVerlaufsAbgleich(bauen(0), 3, entsiegle)).toBe(0)
    expect(await leseMedium('m-empfang')).toBeNull()
    expect(await verarbeiteVerlaufsAbgleich(bauen(1), 3, entsiegle)).toBe(1)
    expect(await leseMedium('m-empfang')).toBe(daten)
    const ack = relay.mock.calls
      .map(([auftrag]) => auftrag)
      .find((auftrag) => auftrag.control_type === VERLAUF_MEDIA_ACK_TYP)
    expect(ack).toBeTruthy()
  })

  it('übernimmt einen Anhang mit falscher Prüfsumme nicht', async () => {
    const daten = 'data:image/png;base64,' + 'C'.repeat(10_000)
    const paket = JSON.stringify({
      v: 1,
      transferId: 't-kaputt',
      mediaId: 'm-kaputt',
      name: null,
      teil: 0,
      teile: 1,
      sha256: 'a'.repeat(64),
      groesse: daten.length,
      daten,
      ablauf: Date.now() + 60_000,
    })
    const roh = {
      type: VERLAUF_MEDIA_TYP,
      ziel: 'sender-1',
      geraet: 'ziel-1',
      von_konto: 3,
      sig: 'beleg',
      paket: `sv-e2ee-hybrid-v1:${btoa(paket)}`,
    }

    expect(
      await verarbeiteVerlaufsAbgleich(roh, 3, async (u) =>
        atob(u.replace('sv-e2ee-hybrid-v1:', '')),
      ),
    ).toBe(0)
    expect(await leseMedium('m-kaputt')).toBeNull()
    expect(
      relay.mock.calls.some(([a]) => a.control_type === VERLAUF_MEDIA_ACK_TYP),
    ).toBe(false)
  })

  it('verwirft ein Medienpaket nach dem Übertragungsfenster', async () => {
    const paket = JSON.stringify({
      v: 1,
      transferId: 't-alt',
      mediaId: 'm-alt',
      name: null,
      teil: 0,
      teile: 1,
      sha256: 'a'.repeat(64),
      groesse: 4,
      daten: 'AAAA',
      ablauf: Date.now() - 1,
    })
    const roh = {
      type: VERLAUF_MEDIA_TYP,
      ziel: 'sender-1',
      geraet: 'ziel-1',
      von_konto: 3,
      sig: 'beleg',
      paket: `sv-e2ee-hybrid-v1:${btoa(paket)}`,
    }
    expect(
      await verarbeiteVerlaufsAbgleich(roh, 3, async (u) =>
        atob(u.replace('sv-e2ee-hybrid-v1:', '')),
      ),
    ).toBe(0)
    expect(await leseMedium('m-alt')).toBeNull()
  })

  it('überspringt einen Anhang, den das Ziel bestätigt hat', async () => {
    await speichereMedium('m-bestaetigt', 'data:image/png;base64,AAAA', 'a.png')
    speicher.set('chat-a', [
      nachricht(1, 'Bild', {
        imageAttachment: { mediaId: 'm-bestaetigt', paketSchluessel: 'k', fileId: 'f' },
      }),
    ])
    const ack = {
      type: VERLAUF_MEDIA_ACK_TYP,
      ziel: 'sender-1',
      geraet: 'ziel-1',
      von_konto: 3,
      sig: 'beleg',
      mediaId: 'm-bestaetigt',
      transferId: 't-bestaetigt',
    }
    expect(await verarbeiteVerlaufsAbgleich(ack, 3, async () => '')).toBe(1)

    await sendeVerlaufAnGeraet(ziel)

    expect(
      relay.mock.calls.filter(([a]) => a.control_type === VERLAUF_MEDIA_TYP),
    ).toHaveLength(0)
  })
})
