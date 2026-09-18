// @vitest-environment node
/**
 * Der Verlaufsumzug auf ein frisch gekoppeltes Gerät.
 *
 * Die zwei Zusagen, an denen alles hängt: der Blob ist ohne den privaten
 * Schlüssel des Zielgeräts nichts, und **Schlüssel wandern nicht mit**. Wer
 * das zweite bricht, hebt die ganze Umstellung von 09/2026 hintenherum wieder
 * auf: dann hätten wieder zwei Geräte dasselbe Material, und der Ratchet
 * verklemmt.
 *
 * Node-Umgebung: DIS prüft Eingaben mit `instanceof Uint8Array`, und unter
 * jsdom liefert `TextEncoder` ein Array aus der falschen Realm.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { generateLocalE2eeKeyPair } from './e2eeCrypto'

const { lokal, abgelegt } = vi.hoisted(() => ({
  lokal: new Map<string, any[]>(),
  abgelegt: { blob: null as string | null, pfad: '' },
}))

vi.mock('@/api/client', () => ({
  api: vi.fn(async (pfad: string, opt?: { method?: string; body?: string }) => {
    if (opt?.method === 'PUT') {
      abgelegt.blob = JSON.parse(opt.body || '{}').blob
      abgelegt.pfad = pfad
      return { ok: true }
    }
    const blob = abgelegt.blob
    abgelegt.blob = null // Einmal abholen, dann ist er weg.
    return { blob }
  }),
}))

vi.mock('./messengerLocalStore', async () => {
  const echt = await vi.importActual<typeof import('./messengerLocalStore')>('./messengerLocalStore')
  return {
    ...echt,
    listeLokaleMailboxen: vi.fn(async () => [...lokal.keys()]),
    loadLocalMessages: vi.fn(async (mid: string) => lokal.get(mid) ?? []),
    saveLocalMessages: vi.fn(async (mid: string, msgs: any[]) => {
      lokal.set(mid, msgs)
    }),
  }
})

const { holeVerlaufAb, packeUndVersiegele, uebergebeVerlauf, uebernimmVerlauf } = await import(
  './verlaufsUebergabe'
)

const MAILBOX = 'dm-1-2'

function nachricht(id: number, text: string, isSelf = false) {
  return {
    id,
    clientUuid: `uuid-${id}`,
    senderId: isSelf ? 1 : 2,
    text,
    createdAt: new Date(2026, 8, 18, 12, id).toISOString(),
    isSelf,
  }
}

describe('Verlaufs-Erstabgleich', () => {
  let neuesGeraet: { publicKeyJwk: string; privateKeyJwk: string }
  let fremdesGeraet: { publicKeyJwk: string; privateKeyJwk: string }

  beforeEach(async () => {
    lokal.clear()
    abgelegt.blob = null
    abgelegt.pfad = ''
    if (!neuesGeraet) neuesGeraet = await generateLocalE2eeKeyPair()
    if (!fremdesGeraet) fremdesGeraet = await generateLocalE2eeKeyPair()
  }, 60_000)

  it('trägt den Verlauf vom alten auf das neue Gerät', async () => {
    lokal.set(MAILBOX, [nachricht(1, 'Hallo'), nachricht(2, 'Antwort', true)])
    lokal.set('dm-1-3', [nachricht(5, 'Andere Mailbox')])

    const blob = await packeUndVersiegele([
      { device_id: 'neu', public_key: neuesGeraet.publicKeyJwk },
    ])
    expect(blob).not.toBeNull()

    // Beim neuen Gerät ist der lokale Speicher leer.
    lokal.clear()
    const anzahl = await uebernimmVerlauf(blob!, neuesGeraet.privateKeyJwk)

    expect(anzahl).toBe(3)
    expect(lokal.get(MAILBOX)!.map((m) => m.text)).toEqual(['Hallo', 'Antwort'])
    expect(lokal.get('dm-1-3')!.map((m) => m.text)).toEqual(['Andere Mailbox'])
  }, 60_000)

  it('ist für ein fremdes Gerät binäres Rauschen', async () => {
    lokal.set(MAILBOX, [nachricht(1, 'Streng vertraulich')])

    const blob = await packeUndVersiegele([
      { device_id: 'neu', public_key: neuesGeraet.publicKeyJwk },
    ])

    // Der Server hält diesen Text in der Hand. Er darf nichts daraus lesen.
    expect(blob).not.toContain('Streng vertraulich')
    expect(blob).not.toContain(MAILBOX)

    lokal.clear()
    expect(await uebernimmVerlauf(blob!, fremdesGeraet.privateKeyJwk)).toBe(0)
    expect(lokal.size).toBe(0)
  }, 60_000)

  it('gibt keinen privaten Schlüssel weiter', async () => {
    // Übertragen wird der gelesene Verlauf, nicht die Fähigkeit, ihn zu lesen.
    // Zwei Geräte mit demselben Material verklemmen jede Ratchet-Sitzung.
    lokal.set(MAILBOX, [nachricht(1, 'Hallo')])
    const blob = await packeUndVersiegele([
      { device_id: 'neu', public_key: neuesGeraet.publicKeyJwk },
    ])

    lokal.clear()
    await uebernimmVerlauf(blob!, neuesGeraet.privateKeyJwk)

    const alles = JSON.stringify([...lokal.entries()])
    expect(alles).not.toContain('"d":')
    expect(alles).not.toContain(neuesGeraet.privateKeyJwk)
    expect(alles).not.toContain(fremdesGeraet.privateKeyJwk)
  }, 60_000)

  it('versiegelt für mehrere neue Geräte, jedes öffnet nur seines', async () => {
    lokal.set(MAILBOX, [nachricht(1, 'Für beide')])

    const blob = await packeUndVersiegele([
      { device_id: 'a', public_key: neuesGeraet.publicKeyJwk },
      { device_id: 'b', public_key: fremdesGeraet.publicKeyJwk },
    ])

    lokal.clear()
    expect(await uebernimmVerlauf(blob!, neuesGeraet.privateKeyJwk)).toBe(1)
    lokal.clear()
    expect(await uebernimmVerlauf(blob!, fremdesGeraet.privateKeyJwk)).toBe(1)
  }, 60_000)

  it('mischt beim Empfänger, statt zu ersetzen', async () => {
    // Zwischen Kopplung und Abholung kann das neue Gerät längst selbst etwas
    // gesehen haben, und das ist der aktuellere Stand.
    lokal.set(MAILBOX, [nachricht(1, 'Von früher')])
    const blob = await packeUndVersiegele([
      { device_id: 'neu', public_key: neuesGeraet.publicKeyJwk },
    ])

    lokal.set(MAILBOX, [nachricht(9, 'Schon selbst gesehen')])
    await uebernimmVerlauf(blob!, neuesGeraet.privateKeyJwk)

    const texte = lokal.get(MAILBOX)!.map((m) => m.text)
    expect(texte).toContain('Von früher')
    expect(texte).toContain('Schon selbst gesehen')
  }, 60_000)

  it('übergibt nichts, wenn es nichts zu übergeben gibt', async () => {
    expect(await packeUndVersiegele([{ device_id: 'neu', public_key: neuesGeraet.publicKeyJwk }])).toBeNull()
    lokal.set(MAILBOX, [])
    expect(await packeUndVersiegele([{ device_id: 'neu', public_key: neuesGeraet.publicKeyJwk }])).toBeNull()
    // Und ohne Zielgerät erst recht nicht: ein Blob ohne Abnehmer.
    lokal.set(MAILBOX, [nachricht(1, 'Hallo')])
    expect(await packeUndVersiegele([])).toBeNull()
  }, 60_000)

  it('kürzt auf die jüngsten Nachrichten je Mailbox', async () => {
    // 200 ist die oberste Stufe; alles darüber bleibt zurück, und zwar das
    // Älteste.
    const viele = Array.from({ length: 260 }, (_, i) => nachricht(i + 1, `N${i + 1}`))
    lokal.set(MAILBOX, viele)

    const blob = await packeUndVersiegele([
      { device_id: 'neu', public_key: neuesGeraet.publicKeyJwk },
    ])
    lokal.clear()
    await uebernimmVerlauf(blob!, neuesGeraet.privateKeyJwk)

    const angekommen = lokal.get(MAILBOX)!
    expect(angekommen).toHaveLength(200)
    expect(angekommen[0].text).toBe('N61')
    expect(angekommen[angekommen.length - 1].text).toBe('N260')
  }, 60_000)

  it('geht den ganzen Weg über den Kopplungscode', async () => {
    lokal.set(MAILBOX, [nachricht(1, 'Hallo vom alten Gerät')])

    const abgegeben = await uebergebeVerlauf('ABCD-EFGH-JKLM', [
      { device_id: 'neu', public_key: neuesGeraet.publicKeyJwk },
    ])
    expect(abgegeben).toBe(true)
    expect(abgelegt.pfad).toBe('/auth/devices/pairing/ABCD-EFGH-JKLM/verlauf')

    lokal.clear()
    const anzahl = await holeVerlaufAb('ABCD-EFGH-JKLM', neuesGeraet.privateKeyJwk, 5_000, 10)
    expect(anzahl).toBe(1)
    expect(lokal.get(MAILBOX)!.map((m) => m.text)).toEqual(['Hallo vom alten Gerät'])
  }, 60_000)

  it('gibt auf, wenn nichts abgelegt wird', async () => {
    // Das neue Gerät soll benutzbar sein, auch wenn der Umzug ausbleibt.
    const anzahl = await holeVerlaufAb('ABCD-EFGH-JKLM', neuesGeraet.privateKeyJwk, 120, 40)
    expect(anzahl).toBe(0)
  }, 60_000)

  it('verschluckt sich nicht an beschädigten Blobs', async () => {
    for (const kaputt of ['', 'kein-json', '{}', '[]', '["nur-müll"]']) {
      expect(await uebernimmVerlauf(kaputt, neuesGeraet.privateKeyJwk)).toBe(0)
    }
  }, 60_000)
})
