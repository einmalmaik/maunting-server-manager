import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Versandauftrag } from '@/hooks/useKonversation'

/**
 * Die Schritte beim Senden, ohne die Seite drumherum.
 *
 * Upload und Relais sind Attrappen: ob ein Paket richtig verschlüsselt wird,
 * prüfen `medienKrypto.test.ts` und `circularVideoNotes.test.ts`. Hier geht es
 * darum, was mit dem Ergebnis passiert, und was, wenn es ausbleibt.
 */

vi.mock('@/api/social', () => ({
  ladeAnhangHoch: vi.fn(),
  relayE2eeEnvelope: vi.fn(),
}))

vi.mock('@/lib/offlineSync', () => ({
  enqueueMessageMutation: vi.fn(),
}))

import { ladeAnhangHoch, relayE2eeEnvelope } from '@/api/social'
import { enqueueMessageMutation } from '@/lib/offlineSync'
import { chatMediaBlobCache } from './klartextSpeicher'
import { baueNutzlast, ladeAnhaengeHoch, stelleZu, type Nutzlastangaben } from './nachrichtVersand'

const hochladen = vi.mocked(ladeAnhangHoch)
const relais = vi.mocked(relayE2eeEnvelope)
const einreihen = vi.mocked(enqueueMessageMutation)

const BINDUNG = { blindMailboxId: 'mb-gespraech', absenderId: 7, groupId: 3 }

let naechsterZeiger = 1
function zeiger() {
  const n = naechsterZeiger++
  return { mediaId: `medium-${n}`, paketSchluessel: `paket-${n}`, fileId: `datei-${n}` }
}

beforeEach(() => {
  vi.clearAllMocks()
  chatMediaBlobCache.clear()
  naechsterZeiger = 1
  hochladen.mockImplementation(async () => zeiger())
})

describe('baueNutzlast', () => {
  const grund: Nutzlastangaben = {
    clientUuid: 'u-1',
    absenderId: 7,
    absenderName: 'anna',
    text: 'hallo',
    zeitpunkt: '2026-09-24T10:00:00.000Z',
    erwaehnt: {},
  }

  it('setzt nur, was es gibt', () => {
    const nutzlast = baueNutzlast({
      ...grund,
      bezug: null,
      weitergeleitet: false,
      erwaehnt: { erwaehnungen: [] },
    })
    expect(nutzlast).toEqual({
      client_uuid: 'u-1',
      sender_id: 7,
      sender_name: 'anna',
      text: 'hallo',
      timestamp: '2026-09-24T10:00:00.000Z',
    })
    expect(Object.values(nutzlast)).not.toContain(undefined)
  })

  it('nimmt den Zeitpunkt, der übergeben wurde, nicht die Uhr beim Bauen', () => {
    // Zwischen Abschicken und Bauen liegt der Upload; der kann dauern.
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2030-01-01T00:00:00Z'))
    try {
      expect(baueNutzlast(grund).timestamp).toBe('2026-09-24T10:00:00.000Z')
    } finally {
      vi.useRealTimers()
    }
  })

  it('hält die Reihenfolge der Felder, in der sie immer standen', () => {
    const nutzlast = baueNutzlast({
      ...grund,
      bezug: { clientUuid: 'u-0', absenderId: 9, auszug: 'davor' },
      weitergeleitet: true,
      erwaehnt: { erwaehnungen: [9], erwaehntAlle: true },
      verfaelltAm: '2026-09-25T10:00:00.000Z',
      note: { title: 'n', content: 'c' },
      cal: { title: 't', start: 's', end: 'e' },
      sticker: { id: 's', label: 'l', svg: '<svg/>' },
      storyReply: { storyContent: 'story' },
      finalImg: { name: 'bild.png' },
      finalFile: { name: 'a.pdf', sizeBytes: 1, mimeType: 'application/pdf' },
      finalAudio: { ...zeiger(), durationSeconds: 2, mimeType: 'audio/webm' },
      finalVideoNote: { ...zeiger(), durationSeconds: 3, width: 1, height: 1, mimeType: 'video/webm' },
    })
    expect(Object.keys(nutzlast)).toEqual([
      'client_uuid',
      'sender_id',
      'sender_name',
      'text',
      'timestamp',
      'antwort_auf',
      'weitergeleitet',
      'erwaehnungen',
      'erwaehnt_alle',
      'verfaellt_am',
      'note_attachment',
      'calendar_attachment',
      'image_attachment',
      'audio_attachment',
      'file_attachment',
      'sticker_attachment',
      'story_reply',
      'video_note_attachment',
    ])
    expect(nutzlast.erwaehnungen).toEqual([9])
    expect(nutzlast.verfaellt_am).toBe('2026-09-25T10:00:00.000Z')
  })
})

describe('ladeAnhaengeHoch', () => {
  it('lädt nichts hoch, wenn es nichts gibt', async () => {
    expect(await ladeAnhaengeHoch({}, BINDUNG)).toEqual({
      finalImg: undefined,
      finalFile: undefined,
      finalAudio: undefined,
      finalVideoNote: undefined,
    })
    expect(hochladen).not.toHaveBeenCalled()
  })

  it('bindet jeden Upload an Gespräch, Absender und Gruppe', async () => {
    await ladeAnhaengeHoch({ img: { dataUrl: 'data:image/jpeg;base64,AAAA', name: 'foto.jpg' } }, BINDUNG)
    expect(hochladen).toHaveBeenCalledWith({
      klartext: 'data:image/jpeg;base64,AAAA',
      dateiname: 'foto.jpg',
      mimeType: 'image/jpeg',
      blindMailboxId: 'mb-gespraech',
      absenderId: 7,
      groupId: 3,
    })
  })

  it('schickt ein Bild nur als Zeiger weiter, nie mit seinem Inhalt', async () => {
    const { finalImg } = await ladeAnhaengeHoch(
      { img: { dataUrl: 'data:image/png;base64,AAAA', name: 'bild.png' } },
      BINDUNG,
    )
    expect(finalImg).toEqual({ mediaId: 'medium-1', paketSchluessel: 'paket-1', fileId: 'datei-1', name: 'bild.png' })
    expect(chatMediaBlobCache.get('medium-1')).toBe('data:image/png;base64,AAAA')
  })

  it('lädt ein schon hochgeladenes Bild nicht noch einmal hoch', async () => {
    const { finalImg } = await ladeAnhaengeHoch(
      {
        img: {
          mediaId: 'alt',
          paketSchluessel: 'paket-alt',
          fileId: 'datei-alt',
          name: 'weiter.png',
          dataUrl: 'data:image/png;base64,AAAA',
        },
      },
      BINDUNG,
    )
    expect(hochladen).not.toHaveBeenCalled()
    expect(finalImg).toEqual({ mediaId: 'alt', paketSchluessel: 'paket-alt', fileId: 'datei-alt', name: 'weiter.png' })
  })

  it('schickt Bild und Datei nur mit ihrem Namen, wenn der Upload scheitert', async () => {
    hochladen.mockRejectedValue(new Error('offline'))
    const { finalImg, finalFile } = await ladeAnhaengeHoch(
      {
        img: { dataUrl: 'data:image/png;base64,AAAA', name: 'bild.png' },
        file: { dataUrl: 'data:application/pdf;base64,AAAA', name: 'a.pdf', sizeBytes: 3, mimeType: 'application/pdf' },
      },
      BINDUNG,
    )
    expect(finalImg).toEqual({ name: 'bild.png' })
    expect(finalFile).toEqual({ name: 'a.pdf', sizeBytes: 3, mimeType: 'application/pdf' })
    expect(chatMediaBlobCache.size).toBe(0)
  })

  it('bricht bei einer Sprachnachricht ab, wenn der Upload scheitert', async () => {
    hochladen.mockRejectedValue(new Error('offline'))
    await expect(
      ladeAnhaengeHoch(
        { audio: { dataUrl: 'data:audio/webm;base64,AAAA', durationSeconds: 2, mimeType: 'audio/webm' } },
        BINDUNG,
      ),
    ).rejects.toThrow('offline')
  })

  it('bricht bei einer Videonotiz ab, wenn der Upload scheitert', async () => {
    hochladen.mockRejectedValue(new Error('offline'))
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: 'video/webm' })
    await expect(
      ladeAnhaengeHoch(
        { videoNote: { blob, durationSeconds: 3, width: 240, height: 240, mimeType: 'video/webm' } },
        BINDUNG,
      ),
    ).rejects.toThrow('offline')
  })

  it('lädt die Videonotiz als Data-URL hoch und gibt ihre Masse mit', async () => {
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: 'video/webm' })
    const { finalVideoNote } = await ladeAnhaengeHoch(
      { videoNote: { blob, durationSeconds: 3, width: 240, height: 320, mimeType: 'video/webm' } },
      BINDUNG,
    )
    const aufruf = hochladen.mock.calls[0][0]
    expect(aufruf.klartext).toBe('data:video/webm;base64,AQID')
    expect(aufruf.dateiname).toBe('videonotiz.webm')
    expect(finalVideoNote).toEqual({
      mediaId: 'medium-1',
      paketSchluessel: 'paket-1',
      fileId: 'datei-1',
      durationSeconds: 3,
      width: 240,
      height: 320,
      mimeType: 'video/webm',
    })
    expect(chatMediaBlobCache.get('medium-1')).toBe('data:video/webm;base64,AQID')
  })
})

describe('stelleZu', () => {
  function auftrag(clientUuid: string, extra: Partial<Versandauftrag> = {}): Versandauftrag {
    return { blind_mailbox_id: 'mb', ciphertext_envelope: `inhalt:${clientUuid}`, client_uuid: clientUuid, ...extra }
  }
  const aufbau = (geraet: string) => auftrag(`u#i${geraet}`, { is_control: true, control_type: 'dr-init' })
  const nachricht = (geraet: string) => auftrag(`u#${geraet}`)
  const gesendet = () => relais.mock.calls.map(([a]) => a.client_uuid)
  const eingereiht = () => einreihen.mock.calls.map(([a]) => a.client_uuid)

  it('nennt die niedrigste Kennung der Nachricht, nicht die eines Steuerumschlags', async () => {
    const ids: Record<string, number> = { 'u#iA': 1, 'u#A': 12, 'u#B': 10 }
    relais.mockImplementation(async (a) => ({ id: ids[a.client_uuid as string] }) as never)
    const ergebnis = await stelleZu([aufbau('A'), nachricht('A'), nachricht('B')])
    expect(ergebnis).toEqual({ niedrigsteId: 10, verbindungsfehler: false })
    expect(einreihen).not.toHaveBeenCalled()
  })

  it('zählt eine Antwort ohne Kennung nicht mit', async () => {
    relais.mockResolvedValue({} as never)
    expect(await stelleZu([nachricht('A')])).toEqual({ niedrigsteId: 0, verbindungsfehler: false })
  })

  it('schickt die Nachricht nicht ohne ihren Sitzungsaufbau, sondern reiht beide ein', async () => {
    // Ohne den Aufbau findet das Zielgerät keine Sitzung und läuft in den
    // Sitzungsbruch. Beide kommen später in dieser Reihenfolge aus der Outbox.
    relais.mockImplementation(async (a) => {
      if (a.client_uuid === 'u#iA') throw new Error('offline')
      return { id: 5 } as never
    })
    const ergebnis = await stelleZu([aufbau('A'), nachricht('A'), aufbau('B'), nachricht('B')])
    expect(gesendet()).toEqual(['u#iA', 'u#iB', 'u#B'])
    expect(eingereiht()).toEqual(['u#iA', 'u#A'])
    expect(ergebnis).toEqual({ niedrigsteId: 5, verbindungsfehler: true })
  })

  it('hält die Nachricht auch zurück, wenn ihr Aufbau keine Gerätemarke trägt', async () => {
    // Heute tragen Aufbau und Nachricht dieselbe Marke (`#i<geraet>` und
    // `#<geraet>`), und die Geräteliste fängt den Fall schon ab. Die Paarung
    // darf aber nicht an diesem Format hängen.
    relais.mockImplementation(async (a) => {
      if (a.client_uuid === 'aufbau') throw new Error('offline')
      return { id: 5 } as never
    })
    await stelleZu([
      auftrag('aufbau', { is_control: true, control_type: 'dr-init' }),
      auftrag('nachricht'),
      auftrag('danach'),
    ])
    expect(gesendet()).toEqual(['aufbau', 'danach'])
    expect(eingereiht()).toEqual(['aufbau', 'nachricht'])
  })

  it('schickt einem Gerät nichts mehr, sobald ein Umschlag an es gescheitert ist', async () => {
    relais.mockImplementation(async (a) => {
      if (a.client_uuid === 'u#A') throw new Error('offline')
      return { id: 5 } as never
    })
    await stelleZu([nachricht('A'), auftrag('v#A'), nachricht('B')])
    expect(gesendet()).toEqual(['u#A', 'u#B'])
    expect(eingereiht()).toEqual(['u#A', 'v#A'])
  })

  it('reiht alles in der Reihenfolge ein, wenn nichts durchgeht', async () => {
    relais.mockRejectedValue(new Error('offline'))
    const ergebnis = await stelleZu([aufbau('A'), nachricht('A'), aufbau('B'), nachricht('B')])
    expect(eingereiht()).toEqual(['u#iA', 'u#A', 'u#iB', 'u#B'])
    expect(ergebnis).toEqual({ niedrigsteId: 0, verbindungsfehler: true })
  })

  it('lässt einen Steuerumschlag nach einem gescheiterten Aufbau durch', async () => {
    // Nur die Nachricht gehört zum Aufbau; ein Gruppenschlüssel an ein
    // anderes Gerät hat damit nichts zu tun.
    relais.mockImplementation(async (a) => {
      if (a.client_uuid === 'u#iA') throw new Error('offline')
      return { id: 5 } as never
    })
    await stelleZu([aufbau('A'), auftrag('g#1', { is_control: true, control_type: 'gruppenschluessel' })])
    expect(gesendet()).toEqual(['u#iA', 'g#1'])
    expect(eingereiht()).toEqual(['u#iA'])
  })
})
