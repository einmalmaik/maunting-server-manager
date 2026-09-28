/**
 * Der Sende- und Lesepfad, ohne die Seite zu rendern.
 *
 * Genau das war vorher unmöglich: die Entscheidung, was ein Umschlag bedeutet,
 * stand in einer Renderfunktion mit 5.800 Zeilen. Um zu prüfen, ob eine fremde
 * Gerätekopie still übergangen wird, musste man den ganzen Messenger aufbauen,
 * inklusive Anrufen, Stories und Anhangvorschau.
 *
 * Die echte Krypto prüfen `ratchetSitzung.test.ts` und `gruppenSchluessel.test.ts`.
 * Hier geht es um die Schicht darüber: welche Mailbox, welches Verfahren, und
 * was mit dem Ergebnis geschieht.
 */

import { renderHook, waitFor, act } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { lesungen, geraete, gruppenLesungen, gerufen, klartextCache, abgelegt } = vi.hoisted(() => ({
  lesungen: new Map<string, any>(),
  geraete: [] as { device_id: string; public_key: string }[],
  gruppenLesungen: new Map<string, any>(),
  /** Der dauerhafte Klartextspeicher, nach Umschlagkennung. */
  abgelegt: new Map<number, string>(),
  /**
   * Der Zwischenspeicher schon geöffneter Umschläge, nach Umschlagkennung.
   * Die ist in der Datenbank der Primärschlüssel und damit über alle Mailboxen
   * hinweg eindeutig — in Testdaten nicht, deshalb wird er hier je Fall geleert.
   */
  klartextCache: new Map<number, { plain: string; ok: boolean }>(),
  gerufen: {
    fordereGruppenSchluessel: [] as string[],
    verwirfDrSitzung: [] as string[],
    verarbeiteGruppenSteuerung: [] as string[],
    holeGeraeteSteuerung: [] as number[],
    dmZiele: [] as string[],
    /** Aus welchen Mailboxen ein Durchlauf gelesen hat, in der Reihenfolge. */
    gelesen: [] as string[],
  },
}))

let umschlaege: any[] = []
/**
 * Was in den einzelnen Mailboxen liegt, wenn eine Gruppe umgezogen ist.
 *
 * Leer heisst: alle Kennungen liefern `umschlaege`. So bleiben die Fälle, die
 * den Umzug nicht betreffen, unverändert.
 */
const proMailbox = new Map<string, any[]>()

vi.mock('@/api/social', () => ({
  fetchE2eeEnvelopes: vi.fn(async (kennung: string) => {
    gerufen.gelesen.push(kennung)
    return proMailbox.get(kennung) ?? umschlaege
  }),
}))

vi.mock('@/services/e2eeCrypto', () => ({
  deriveBlindMailboxId: vi.fn(async (a: number, b: number) => `dm-${Math.min(a, b)}-${Math.max(a, b)}`),
  deriveGroupBlindMailboxId: vi.fn(async (g: number) => `gruppe-${g}`),
  getCachedBlindMailboxId: vi.fn(() => undefined),
  getCachedGroupBlindMailboxId: vi.fn(() => undefined),
  decryptE2eeHybridWithKeyring: vi.fn(async (umschlag: string) => {
    const wert = lesungen.get(umschlag)
    if (wert === undefined) throw new Error('nicht für dieses Gerät')
    return wert
  }),
  encryptE2eeHybrid: vi.fn(async (payload: string, pub: string) => `hybrid(${pub}):${payload}`),
  envelopePlaintextCache: klartextCache,
}))

vi.mock('@/services/e2eeGeraet', () => ({
  verlangeGeraeteVon: vi.fn(async () => {
    if (geraete.length === 0) throw new Error('kein Gerät')
    return geraete
  }),
}))

vi.mock('@/services/messengerLocalStore', () => ({
  ladeUmschlagKlartexte: vi.fn(async () => new Map<number, string>()),
  /** Die Ablage, wie sie `liesDrUmschlag` im Sitzungsschloss sieht. */
  leseUmschlagKlartext: vi.fn(async (_mid: string, envId: number) => abgelegt.get(envId) ?? null),
  speichereUmschlagKlartext: vi.fn(async (_mid: string, envId: number, text: string) => {
    abgelegt.set(envId, text)
  }),
}))

vi.mock('@/services/gruppenSchluessel', () => ({
  entschluesseleGruppenUmschlag: vi.fn(async (_g: number, umschlag: string) =>
    gruppenLesungen.get(umschlag) ?? { art: 'unbekannt' }
  ),
  fordereGruppenSchluessel: vi.fn(async (_k: any, keyId: string) => {
    gerufen.fordereGruppenSchluessel.push(keyId)
  }),
  verarbeiteGruppenSteuerung: vi.fn(async (_k: any, klartext: string) => {
    gerufen.verarbeiteGruppenSteuerung.push(klartext)
  }),
  // Die eigene Geräte-Mailbox. Sie steht vor dem Lesen der Gruppenmailbox und
  // bringt den Schlüssel mit; was sie im einzelnen tut, prüft
  // `gruppenSchluessel.test.ts`.
  holeGeraeteSteuerung: vi.fn(async (eigeneId: number) => {
    gerufen.holeGeraeteSteuerung.push(eigeneId)
    return 0
  }),
  verschluesseleFuerGruppe: vi.fn(async (k: any, payload: string) => `sv-e2ee-group-v1:${k.groupId}:${payload}`),
  // Wohin gesendet und woraus gelesen wird. Ohne gesetzten Umzug bleibt es bei
  // der alten Kennung — so verhalten sich alle Fälle wie vor Stufe 3d.
  gruppenZiele: vi.fn(async (_g: number, alt: string) => zieleFuer(alt)),
  // Dasselbe für den Direktchat. Der Hook fragt hier mit **beiden** Konten,
  // die Ziele hängen aber — wie beim echten Dienst — allein an der Kennung.
  dmZiele: vi.fn(async (eigeneId: number, peerId: number, alt: string) => {
    gerufen.dmZiele.push(`${eigeneId}->${peerId}:${alt}`)
    return zieleFuer(alt)
  }),
}))

/** Gesetzt heisst: dieses Gespräch ist in die neue Kennung umgezogen. */
const umzug = new Map<string, string>()

function zieleFuer(alt: string) {
  const neu = umzug.get(alt)
  return neu
    ? { senden: neu, lesen: [neu, alt], nachweis: 'token' }
    : { senden: alt, lesen: [alt], nachweis: null }
}

vi.mock('@/services/ratchetSitzung', () => ({
  baueZustellungen: vi.fn(async (_k: any, klartext: string, basisUuid: string) => [
    {
      empfaengerId: 2,
      zielGeraet: 'fremd-a',
      bootstrap: 'bootstrap-a',
      nachricht: `dr(fremd-a):${klartext}`,
      clientUuid: `${basisUuid}#aaa`,
      bootstrapClientUuid: `${basisUuid}#aaai`,
    },
    {
      empfaengerId: 1,
      zielGeraet: 'eigen-b',
      bootstrap: null,
      nachricht: `dr(eigen-b):${klartext}`,
      clientUuid: `${basisUuid}#bbb`,
      bootstrapClientUuid: `${basisUuid}#bbbi`,
    },
  ]),
  liesDrUmschlag: vi.fn(
    async (
      _k: any,
      umschlag: string,
      klartext: { lies(): Promise<string | null>; lege(t: string): Promise<void> },
      optionen: {
        zurueckstellen?: (vonKonto: number, vonGeraet: string) => boolean
        schonen?: (vonKonto: number, vonGeraet: string) => boolean
      } = {},
    ) => {
      const wert = lesungen.get(umschlag) ?? { art: 'unbekannt' }
      // Wie der echte Lesepfad: der Kopf nennt das Absendergerät, und dessen
      // Aufbau muss entschieden sein, bevor der Umschlag angefasst wird.
      if (wert.vonKonto !== undefined && optionen.zurueckstellen?.(wert.vonKonto, wert.vonGeraet)) {
        return { art: 'zurueckgestellt' }
      }
      // Ein Bruch von einem Gerät mit abgewiesenem Aufbau im Fenster gehört zu
      // diesem Aufbau, nicht zur Sitzung.
      if (wert.art === 'bruch' && optionen.schonen?.(wert.vonKonto, wert.vonGeraet)) {
        return { art: 'abgewiesen' }
      }
      // Der echte Lesepfad sieht im Sitzungsschloss zuerst nach, ob ein
      // anderer Durchlauf den Umschlag schon geöffnet hat.
      const schon = await klartext.lies()
      if (schon !== null) return { art: 'klartext', text: schon, vonKonto: 2, vonGeraet: 'fremd-a' }
      if (wert.art === 'klartext') await klartext.lege(wert.text)
      return wert
    },
  ),
  // `OFFEN` und `ABGELEHNT` im Klartext stehen für die beiden Ausgänge der
  // Unterschriftsprüfung; die Prüfung selbst steht in `ratchetSitzung.test.ts`.
  verarbeiteBootstrap: vi.fn(async (_k: any, klartext: string) =>
    klartext.startsWith('AUFBAU')
      ? {
          istAufbau: true,
          ersetzt: klartext.includes('ERSETZT'),
          vonKonto: 2,
          vonGeraet: 'fremd-a',
          ...(klartext.includes('OFFEN') ? { offen: true } : {}),
          ...(klartext.includes('ABGELEHNT') ? { abgelehnt: true } : {}),
          ...(klartext.includes('ABGEWIESEN') ? { abgewiesen: true } : {}),
        }
      : { istAufbau: false, ersetzt: false }
  ),
  verwirfDrSitzung: vi.fn(async (konto: number, geraet: string) => {
    gerufen.verwirfDrSitzung.push(`${konto}:${geraet}`)
  }),
  // Der Kopf eines Ratchet-Umschlags. Im Test steht er in `koepfe`.
  drUrheber: vi.fn((umschlag: string) => koepfe.get(umschlag) ?? null),
}))

/** Was der Kopf eines Test-Umschlags über seinen Absender sagt. */
const koepfe = new Map<string, { vonKonto: number; vonGeraet: string }>()

const { useKonversation } = await import('./useKonversation')
import type { GespraechsZiel } from './useKonversation'

const ICH = 1
const DU = 2

const identitaetRef = {
  current: {
    state: 'ready' as const,
    sendPair: { publicKeyJwk: 'mein-pub', privateKeyJwk: 'mein-priv' },
    decryptionKeys: ['mein-priv'],
  },
}

function umschlag(id: number, ciphertext: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    blind_mailbox_id: 'dm-1-2',
    ciphertext_envelope: ciphertext,
    client_uuid: `uuid-${id}`,
    created_at: new Date(2026, 8, 18, 12, id).toISOString(),
    ...extra,
  }
}

async function baueHook(
  ziel: GespraechsZiel,
  meldeSitzungsbruch = vi.fn(),
  meldeAufbauAbgelehnt = vi.fn(),
) {
  const ergebnis = renderHook(() =>
    useKonversation({ ziel, eigeneId: ICH, identitaetRef, meldeSitzungsbruch, meldeAufbauAbgelehnt })
  )
  await waitFor(() => expect(ergebnis.result.current.blindMailboxId).not.toBe(''))
  return { ...ergebnis, meldeSitzungsbruch, meldeAufbauAbgelehnt }
}

describe('useKonversation', () => {
  beforeEach(() => {
    umschlaege = []
    lesungen.clear()
    gruppenLesungen.clear()
    geraete.length = 0
    gerufen.fordereGruppenSchluessel.length = 0
    gerufen.verwirfDrSitzung.length = 0
    gerufen.verarbeiteGruppenSteuerung.length = 0
    gerufen.holeGeraeteSteuerung.length = 0
    gerufen.dmZiele.length = 0
    gerufen.gelesen.length = 0
    umzug.clear()
    proMailbox.clear()
    klartextCache.clear()
    abgelegt.clear()
    koepfe.clear()
    identitaetRef.current = {
      state: 'ready',
      sendPair: { publicKeyJwk: 'mein-pub', privateKeyJwk: 'mein-priv' },
      decryptionKeys: ['mein-priv'],
    }
  })

  describe('Mailbox', () => {
    it('findet dieselbe Kennung, egal von welcher Seite', async () => {
      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      expect(result.current.blindMailboxId).toBe('dm-1-2')
    })

    it('nimmt für eine Gruppe die Gruppenmailbox', async () => {
      const { result } = await baueHook({ art: 'gruppe', groupId: 7, mitglieder: [ICH, DU] })
      expect(result.current.blindMailboxId).toBe('gruppe-7')
      expect(result.current.gruppenKontext).toEqual({
        groupId: 7,
        blindMailboxId: 'gruppe-7',
        eigeneId: ICH,
        mitglieder: [ICH, DU],
      })
    })

    it('münzt keinen Gruppenschlüssel, solange die Mitgliederliste fehlt', async () => {
      // Eine leere Liste heißt „noch nicht geladen". Daraus einen Schlüssel zu
      // bilden hieße, ihn an niemanden zu verteilen.
      const { result } = await baueHook({ art: 'gruppe', groupId: 7, mitglieder: [] })
      expect(result.current.gruppenKontext).toBeNull()
    })

    it('nimmt das eigene Konto in die Mitgliederliste auf', async () => {
      // Sonst bildet dieses Gerät eine andere Liste als alle anderen und münzt
      // bei jedem Senden einen neuen Schlüssel.
      const { result } = await baueHook({ art: 'gruppe', groupId: 7, mitglieder: [DU] })
      expect(result.current.gruppenKontext?.mitglieder).toContain(ICH)
    })

    it('bleibt ohne Gespräch leer', () => {
      const { result } = renderHook(() =>
        useKonversation({
          ziel: { art: 'keins' },
          eigeneId: ICH,
          identitaetRef,
          meldeSitzungsbruch: vi.fn(),
          meldeAufbauAbgelehnt: vi.fn(),
        })
      )
      expect(result.current.blindMailboxId).toBe('')
      expect(result.current.gruppenKontext).toBeNull()
    })
  })

  describe('Lesen im Direktchat', () => {
    it('gibt Klartext zurück und legt ihn vorher ab', async () => {
      umschlaege = [umschlag(1, 'dr-1')]
      lesungen.set('dr-1', { art: 'klartext', text: 'Hallo', vonKonto: DU, vonGeraet: 'fremd-a' })

      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen).toEqual([{ art: 'klartext', env: umschlaege[0], text: 'Hallo', vonKonto: DU, vonGeraet: 'fremd-a' }])
      const { speichereUmschlagKlartext } = await import('@/services/messengerLocalStore')
      expect(speichereUmschlagKlartext).toHaveBeenCalledWith('dm-1-2', 1, 'Hallo')
    })

    it('übergeht die eigene Nachricht und fremde Gerätekopien still', async () => {
      // Der wichtigste Fall. Beides als „Verschlüsselte Nachricht" anzuzeigen
      // machte den Verlauf bei zwei Geräten je Seite zur Hälfte zu Rauschen.
      umschlaege = [umschlag(1, 'dr-eigen'), umschlag(2, 'dr-fremd')]
      lesungen.set('dr-eigen', { art: 'eigen' })
      lesungen.set('dr-fremd', { art: 'fremd' })

      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen!.map((l) => l.art)).toEqual(['still', 'still'])
    })

    it('zeigt Altbestand als unlesbar, statt ihn zu entschlüsseln', async () => {
      // Der Weg zu den ableitbaren Kanalschlüsseln ist geschlossen, auch lesend.
      umschlaege = [umschlag(1, 'sv-e2ee-v1:altbestand')]
      lesungen.set('sv-e2ee-v1:altbestand', { art: 'unbekannt' })

      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen!.map((l) => l.art)).toEqual(['unlesbar'])
    })

    it('wirft eine gebrochene Sitzung weg und meldet sie sichtbar', async () => {
      // Eine still neu aufgebaute Sitzung ist genau das, was ein Angreifer sich
      // wünscht: er ersetzt die Gegenstelle und niemand sieht etwas.
      umschlaege = [umschlag(1, 'dr-bruch')]
      lesungen.set('dr-bruch', { art: 'bruch', vonKonto: DU, vonGeraet: 'fremd-a', grund: 'Tag' })

      const { result, meldeSitzungsbruch } = await baueHook({ art: 'direkt', peerId: DU })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen!.map((l) => l.art)).toEqual(['still'])
      expect(gerufen.verwirfDrSitzung).toEqual(['2:fremd-a'])
      expect(meldeSitzungsbruch).toHaveBeenCalledWith('fremd-a')
    })

    it('behält die Sitzung, wenn nur die lokale Ablage streikte', async () => {
      // Eine volle Platte darf nicht jeden Gesprächsfaden des Geräts abreißen.
      umschlaege = [umschlag(1, 'dr-fehler')]
      lesungen.set('dr-fehler', { art: 'fehler', grund: 'IndexedDB' })

      const { result, meldeSitzungsbruch } = await baueHook({ art: 'direkt', peerId: DU })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen!.map((l) => l.art)).toEqual(['unlesbar'])
      expect(gerufen.verwirfDrSitzung).toEqual([])
      expect(meldeSitzungsbruch).not.toHaveBeenCalled()
    })

    it('übergeht den Sitzungsaufbau still und meldet nur den Ersatz', async () => {
      umschlaege = [umschlag(1, 'sv-e2ee-hybrid-v1:a'), umschlag(2, 'sv-e2ee-hybrid-v1:b')]
      lesungen.set('sv-e2ee-hybrid-v1:a', 'AUFBAU')
      lesungen.set('sv-e2ee-hybrid-v1:b', 'AUFBAU ERSETZT')

      const { result, meldeSitzungsbruch } = await baueHook({ art: 'direkt', peerId: DU })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen!.map((l) => l.art)).toEqual(['still', 'still'])
      expect(meldeSitzungsbruch).toHaveBeenCalledTimes(1)
    })

    it('stellt die Nachrichten eines Geräts zurück, solange sein Aufbau offen ist', async () => {
      // Das Verzeichnis antwortet nicht: der Aufbau ist weder angenommen noch
      // abgewiesen. Liefe die Nachricht jetzt gegen eine Sitzung, die es noch
      // nicht gibt, wäre sie als Bruch gewertet und für immer verloren.
      umschlaege = [
        umschlag(1, 'sv-e2ee-hybrid-v1:aufbau'),
        umschlag(2, 'dr-wartet'),
        umschlag(3, 'dr-anderes-geraet'),
      ]
      lesungen.set('sv-e2ee-hybrid-v1:aufbau', 'AUFBAU OFFEN')
      lesungen.set('dr-wartet', { art: 'klartext', text: 'Hallo', vonKonto: DU, vonGeraet: 'fremd-a' })
      lesungen.set('dr-anderes-geraet', { art: 'klartext', text: 'Vom Tablet', vonKonto: DU, vonGeraet: 'fremd-b' })

      const { result, meldeSitzungsbruch, meldeAufbauAbgelehnt } = await baueHook({ art: 'direkt', peerId: DU })
      const zuerst = await result.current.liesUmschlaege()

      // Zurückgestellt wird nur das Gerät, dessen Aufbau offen ist.
      expect(zuerst!.map((l) => l.art)).toEqual(['still', 'still', 'klartext'])
      expect(abgelegt.has(2)).toBe(false)
      expect(klartextCache.has(2)).toBe(false)
      expect(meldeSitzungsbruch).not.toHaveBeenCalled()
      expect(meldeAufbauAbgelehnt).not.toHaveBeenCalled()

      // Das Verzeichnis ist wieder da, der Aufbau gilt: jetzt kommt sie durch.
      lesungen.set('sv-e2ee-hybrid-v1:aufbau', 'AUFBAU')
      const danach = await result.current.liesUmschlaege()

      expect(danach!.map((l) => l.art)).toEqual(['still', 'klartext', 'klartext'])
      expect(danach![1]).toMatchObject({ text: 'Hallo', vonGeraet: 'fremd-a' })
    })

    it('meldet einen abgewiesenen Aufbau, ohne ihn als Bruch zu zählen', async () => {
      // Abgewiesen heisst: angewandt wurde nichts, die laufende Sitzung steht
      // noch. Sie wegzuwerfen, wie bei einem Bruch, gäbe dem Fälscher, was er
      // wollte — einen Neuaufbau.
      umschlaege = [umschlag(1, 'sv-e2ee-hybrid-v1:falsch')]
      lesungen.set('sv-e2ee-hybrid-v1:falsch', 'AUFBAU ABGELEHNT')

      const { result, meldeSitzungsbruch, meldeAufbauAbgelehnt } = await baueHook({ art: 'direkt', peerId: DU })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen!.map((l) => l.art)).toEqual(['still'])
      expect(meldeAufbauAbgelehnt).toHaveBeenCalledWith('fremd-a')
      expect(meldeSitzungsbruch).not.toHaveBeenCalled()
      expect(gerufen.verwirfDrSitzung).toEqual([])
    })

    it('schont die Sitzung vor der Nachricht hinter einem abgewiesenen Aufbau', async () => {
      // Eine Fälschung kommt nicht allein: hinter dem Aufbau steht eine
      // Nachricht aus der gefälschten Sitzung. Gegen die echte geöffnet
      // scheitert sie — als Bruch gezählt, kippte sie die echte Sitzung doch
      // noch, und die Systemzeile behauptete das Gegenteil.
      umschlaege = [umschlag(1, 'sv-e2ee-hybrid-v1:falsch'), umschlag(2, 'dr-dahinter')]
      lesungen.set('sv-e2ee-hybrid-v1:falsch', 'AUFBAU ABGELEHNT ABGEWIESEN')
      lesungen.set('dr-dahinter', { art: 'bruch', vonKonto: DU, vonGeraet: 'fremd-a', grund: 'Tag' })

      const { result, meldeSitzungsbruch, meldeAufbauAbgelehnt } = await baueHook({ art: 'direkt', peerId: DU })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen!.map((l) => l.art)).toEqual(['still', 'still'])
      expect(meldeAufbauAbgelehnt).toHaveBeenCalledTimes(1)
      expect(meldeSitzungsbruch).not.toHaveBeenCalled()
      expect(gerufen.verwirfDrSitzung).toEqual([])

      // Beim nächsten Abruf ist der Aufbau bekannt: gemeldet wird nicht mehr,
      // geschont weiterhin.
      lesungen.set('sv-e2ee-hybrid-v1:falsch', 'AUFBAU ABGEWIESEN')
      await result.current.liesUmschlaege()
      expect(meldeAufbauAbgelehnt).toHaveBeenCalledTimes(1)
      expect(gerufen.verwirfDrSitzung).toEqual([])
    })

    it('nennt den Absender eines geöffneten Umschlags auch nach dem Neuladen', async () => {
      // Der Klartext kommt dann aus der Ablage, und der Zwischenspeicher, der
      // sich den Absender gemerkt hatte, ist leer. Ohne Absender griff die
      // Downgrade-Schranke im Messenger nicht mehr — eine Nachricht, die sie
      // beim ersten Sehen verworfen hatte, stand nach dem Neuladen im Verlauf.
      const { ladeUmschlagKlartexte } = await import('@/services/messengerLocalStore')
      vi.mocked(ladeUmschlagKlartexte).mockResolvedValueOnce(new Map([[1, 'schon offen']]))
      umschlaege = [umschlag(1, 'dr-schon-offen')]
      koepfe.set('dr-schon-offen', { vonKonto: DU, vonGeraet: 'fremd-a' })

      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen![0]).toMatchObject({
        art: 'klartext',
        text: 'schon offen',
        vonKonto: DU,
        vonGeraet: 'fremd-a',
      })
    })

    it('lässt Quittungen als Klartext durch', async () => {
      // Quittungen laufen bewusst auf dem Hybridumschlag, nicht durch den
      // Ratchet: eine verbrauchte Kettenposition kostet sonst eine Nachricht.
      umschlaege = [umschlag(1, 'sv-e2ee-hybrid-v1:q')]
      lesungen.set('sv-e2ee-hybrid-v1:q', '{"type":"read_receipt","read_up_to_id":5}')

      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen![0]).toMatchObject({ art: 'klartext' })
    })

    it('übergeht Hybridkopien für andere Geräte still', async () => {
      // Am laufenden System gefunden. Eine Nachricht geht je Zielgerät einmal
      // raus, Sitzungsaufbauten und Quittungen ebenso — alle Kopien liegen in
      // derselben Mailbox, und die meisten kann dieses Gerät nicht öffnen. Das
      // ist der Normalfall. Bis 09/2026 fiel jede davon in den äusseren
      // `catch` und wurde zu einer Zeile „Verschlüsselte Nachricht": nach einer
      // Stunde standen 98 unechte Nachrichten im Verlauf, und jeder Ladevorgang
      // quittierte sie, was neue Umschläge erzeugte.
      umschlaege = [
        // Kopie des Sitzungsaufbaus für ein anderes Gerät.
        umschlag(1, 'sv-e2ee-hybrid-v1:fremd-1', { client_uuid: 'basis-a#i9612fc45299e' }),
        // Quittung, die an ein anderes Gerät ging.
        umschlag(2, 'sv-e2ee-hybrid-v1:fremd-2', { client_uuid: 'deliv-2b9f' }),
        // Eine echte Nachricht, die dieses Gerät nicht öffnen kann: die muss
        // sichtbar bleiben, sonst verschwindet ein Schlüsselbruch lautlos.
        umschlag(3, 'sv-e2ee-hybrid-v1:kaputt'),
        umschlag(4, 'sv-e2ee-hybrid-v1:meins'),
      ]
      lesungen.set('sv-e2ee-hybrid-v1:meins', '{"type":"read_receipt","read_up_to_id":7}')

      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen!.map((l) => l.art)).toEqual(['still', 'still', 'unlesbar', 'klartext'])
    })

    it('lässt einen Durchlauf zur Zeit laufen und stapelt höchstens einen', async () => {
      // Der Messenger ruft aus fünf Quellen herein, und eine einzige Nachricht
      // löst mehrere davon fast gleichzeitig aus. Zwei überlappende Durchläufe
      // holten dasselbe Fenster aus hundert Umschlägen und entschlüsselten
      // jeden Hybridumschlag darin zweimal.
      //
      // Gestapelt wird nur einer: wer ruft, weil gerade ein Umschlag
      // eingetroffen ist, braucht einen Abruf **nach** diesem Umschlag — aber
      // drei Anrufer brauchen zusammen nur einen.
      umschlaege = [umschlag(1, 'dr-1')]
      lesungen.set('dr-1', { art: 'klartext', text: 'Hallo', vonKonto: DU, vonGeraet: 'fremd-a' })

      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      const { fetchE2eeEnvelopes } = await import('@/api/social')
      const vorher = vi.mocked(fetchE2eeEnvelopes).mock.calls.length

      // Der erste Durchlauf hängt am Abruf fest. Genau das ist das Fenster, in
      // dem am laufenden System die weiteren Aufrufe hereinkommen.
      let loslassen!: () => void
      const bremse = new Promise<void>((aufloesen) => {
        loslassen = aufloesen
      })
      vi.mocked(fetchE2eeEnvelopes).mockImplementationOnce(async () => {
        await bremse
        return umschlaege as any
      })

      const lauf1 = result.current.liesUmschlaege()
      await new Promise((r) => setTimeout(r, 0))
      const lauf2 = result.current.liesUmschlaege()
      const lauf3 = result.current.liesUmschlaege()

      // Zwei Nachzügler, ein gestapelter Durchlauf.
      expect(lauf2).toBe(lauf3)
      expect(lauf2).not.toBe(lauf1)

      loslassen()
      const alle = await Promise.all([lauf1, lauf2, lauf3])

      expect(vi.mocked(fetchE2eeEnvelopes).mock.calls.length - vorher).toBe(2)
      // Der Nachzügler liest denselben Umschlag ein zweites Mal — und bekommt
      // seinen Klartext aus der Ablage, statt einen verbrauchten
      // Nachrichtenschlüssel ein zweites Mal zu benutzen.
      for (const gelesen of alle) {
        expect(gelesen!.map((l) => l.art)).toEqual(['klartext'])
      }
    })

    it('liest nicht, solange der Schlüssel dieses Geräts nicht feststeht', async () => {
      // Ein Durchlauf davor schriebe sein Ergebnis in den Zwischenspeicher, und
      // der richtige Klartext käme danach nicht mehr durch.
      umschlaege = [umschlag(1, 'dr-1')]
      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      identitaetRef.current = { ...identitaetRef.current, state: 'loading' as any }

      expect(await result.current.liesUmschlaege()).toBeNull()
    })
  })

  describe('Lesen in der Gruppe', () => {
    it('behandelt jeden Hybridumschlag als Steuerpaket', async () => {
      // In einer Gruppenmailbox ist ein Hybridumschlag immer eine
      // Schlüsselzustellung oder eine Nachforderung, nie eine Nachricht.
      umschlaege = [umschlag(1, 'sv-e2ee-hybrid-v1:schluessel'), umschlag(2, 'sv-e2ee-hybrid-v1:fremd')]
      lesungen.set('sv-e2ee-hybrid-v1:schluessel', '{"typ":"group_key"}')

      const { result } = await baueHook({ art: 'gruppe', groupId: 7, mitglieder: [ICH, DU] })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen!.map((l) => l.art)).toEqual(['still', 'still'])
      expect(gerufen.verarbeiteGruppenSteuerung).toEqual(['{"typ":"group_key"}'])
    })

    it('fordert genau einen Schlüssel nach, und zwar den neuesten', async () => {
      // Ältere unlesbare Nachrichten stammen aus der Zeit vor dem Beitritt.
      // Nach ihnen zu fragen erzeugte bei jedem Ladevorgang neue Umschläge.
      umschlaege = [umschlag(1, 'g-alt'), umschlag(2, 'g-neu')]
      gruppenLesungen.set('g-alt', { art: 'kein-schluessel', keyId: 'aaaaaaaaaaaaaaaa' })
      gruppenLesungen.set('g-neu', { art: 'kein-schluessel', keyId: 'bbbbbbbbbbbbbbbb' })

      const { result } = await baueHook({ art: 'gruppe', groupId: 7, mitglieder: [ICH, DU] })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen!.map((l) => l.art)).toEqual(['unlesbar', 'unlesbar'])
      expect(gerufen.fordereGruppenSchluessel).toEqual(['bbbbbbbbbbbbbbbb'])
    })

    it('fragt nicht nach, wenn alles lesbar war', async () => {
      umschlaege = [umschlag(1, 'g-ok')]
      gruppenLesungen.set('g-ok', { art: 'klartext', text: 'Hallo Gruppe' })

      const { result } = await baueHook({ art: 'gruppe', groupId: 7, mitglieder: [ICH, DU] })
      await result.current.liesUmschlaege()

      expect(gerufen.fordereGruppenSchluessel).toEqual([])
    })

    it('sieht vor jedem Durchlauf in der eigenen Geräte-Mailbox nach', async () => {
      // Dort liegt seit 09/2026 der Gruppenschlüssel. Ohne diesen Griff stünde
      // ein Gerät ohne Schlüssel vor einer Mailbox voller „Verschlüsselte
      // Nachricht" — und bekäme ihn nie, weil er nirgends sonst ankommt.
      umschlaege = [umschlag(1, 'g-ok')]
      gruppenLesungen.set('g-ok', { art: 'klartext', text: 'Hallo Gruppe' })

      const { result } = await baueHook({ art: 'gruppe', groupId: 7, mitglieder: [ICH, DU] })
      await result.current.liesUmschlaege()

      expect(gerufen.holeGeraeteSteuerung).toEqual([ICH])
    })

    it('liest während des Umzugs aus beiden Mailboxen', async () => {
      /*
       * Der Fall, der sonst lautlos die Hälfte des Gesprächs verschluckt: die
       * Mitglieder bekommen das Gruppengeheimnis nicht gleichzeitig. Wer es
       * schon hat, sendet in die neue Mailbox; wer nicht, in die alte. Läse
       * jeder nur seine eigene, sähe keiner den anderen — und niemand bekäme
       * eine Fehlermeldung.
       */
      umzug.set('gruppe-7', 'geheime-mailbox')
      proMailbox.set('geheime-mailbox', [umschlag(2, 'g-neu')])
      proMailbox.set('gruppe-7', [umschlag(1, 'g-alt')])
      gruppenLesungen.set('g-neu', { art: 'klartext', text: 'aus der neuen' })
      gruppenLesungen.set('g-alt', { art: 'klartext', text: 'aus der alten' })

      const { result } = await baueHook({ art: 'gruppe', groupId: 7, mitglieder: [ICH, DU] })
      const gelesen = await result.current.liesUmschlaege()

      expect(gerufen.gelesen).toEqual(['geheime-mailbox', 'gruppe-7'])
      expect(gelesen?.map((l: any) => l.text)).toEqual(['aus der alten', 'aus der neuen'])
    })

    it('liest weiter, wenn eine der beiden Mailboxen streikt', async () => {
      // Die alte Kennung kann 404 werden, sobald der Bestand geräumt ist. Das
      // darf die neue nicht mitreissen — sonst nimmt ein aufgeräumter Server
      // dem Gespräch die Gegenwart.
      umzug.set('gruppe-7', 'geheime-mailbox')
      proMailbox.set('geheime-mailbox', [umschlag(2, 'g-neu')])
      gruppenLesungen.set('g-neu', { art: 'klartext', text: 'aus der neuen' })
      const echt = (await import('@/api/social')).fetchE2eeEnvelopes as any
      echt.mockImplementationOnce(async (k: string) => {
        gerufen.gelesen.push(k)
        return proMailbox.get(k) ?? []
      })
      echt.mockImplementationOnce(async () => {
        throw new Error('404')
      })

      const { result } = await baueHook({ art: 'gruppe', groupId: 7, mitglieder: [ICH, DU] })
      const gelesen = await result.current.liesUmschlaege()

      expect(gelesen?.map((l: any) => l.text)).toEqual(['aus der neuen'])
    })

    it('sieht auch beim Direktchat in der Geräte-Mailbox nach', async () => {
      /*
       * Bis 3d war das ausgelassen: sie trug nur Gruppenschlüssel, und ein
       * Direktchat hat keine. Seit 3e liegt dort auch sein **Chatgeheimnis** —
       * der Wert, aus dem seine Mailbox fällt. Bliebe der Griff aus, bekäme
       * die Gegenseite das Geheimnis nie und das Gespräch stünde für immer
       * auf der abgeleiteten Kennung, die das Backend selbst ausrechnen kann.
       */
      umschlaege = [umschlag(1, 'dr-ok')]
      lesungen.set('dr-ok', { art: 'klartext', text: 'Hallo' })

      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      await result.current.liesUmschlaege()

      expect(gerufen.holeGeraeteSteuerung).toEqual([ICH])
    })

    it('liest den Direktchat während des Umzugs aus beiden Mailboxen', async () => {
      // Dieselbe Gleichzeitigkeit wie bei der Gruppe: die Gegenseite bekommt
      // das Chatgeheimnis später und schreibt bis dahin in die alte Mailbox.
      umzug.set('dm-1-2', 'geheimer-chat')
      proMailbox.set('geheimer-chat', [umschlag(2, 'dr-neu')])
      proMailbox.set('dm-1-2', [umschlag(1, 'dr-alt')])
      lesungen.set('dr-neu', { art: 'klartext', text: 'aus der neuen' })
      lesungen.set('dr-alt', { art: 'klartext', text: 'aus der alten' })

      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      const gelesen = await result.current.liesUmschlaege()

      expect(gerufen.gelesen).toEqual(['geheimer-chat', 'dm-1-2'])
      expect(gelesen?.map((l: any) => l.text)).toEqual(['aus der alten', 'aus der neuen'])
    })
  })

  describe('Senden', () => {
    it('stellt den Sitzungsaufbau vor die Nachricht', async () => {
      // Reihenfolge ist bindend: ohne den Aufbau findet die Gegenstelle keine
      // Sitzung und läuft in den Sitzungsbruch.
      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      const auftraege = await result.current.baueVersand('Hallo', 'uuid-1')

      expect(auftraege.map((a) => [a.control_type ?? 'nachricht', a.client_uuid])).toEqual([
        ['dr-init', 'uuid-1#aaai'],
        ['nachricht', 'uuid-1#aaa'],
        ['nachricht', 'uuid-1#bbb'],
      ])
      // Und keiner nennt den Empfänger. Seit Stufe 4 nimmt das Relais keine
      // Kennung mehr entgegen; die Mailbox **ist** die Adresse.
      expect(auftraege.every((a) => !('recipient_id' in a))).toBe(true)
    })

    it('schickt in der Gruppe genau einen Umschlag', async () => {
      const { result } = await baueHook({ art: 'gruppe', groupId: 7, mitglieder: [ICH, DU] })
      const auftraege = await result.current.baueVersand('Hallo Gruppe', 'uuid-2')

      expect(auftraege).toHaveLength(1)
      expect(auftraege[0].ciphertext_envelope).toBe('sv-e2ee-group-v1:7:Hallo Gruppe')
      // Kein Empfänger: die Gruppenmailbox gehört allen.
      expect('recipient_id' in auftraege[0]).toBe(false)
    })

    it('sendet nicht, solange die Mitgliederliste der Gruppe fehlt', async () => {
      const { result } = await baueHook({ art: 'gruppe', groupId: 7, mitglieder: [] })
      expect(await result.current.baueVersand('Hallo', 'uuid-3')).toEqual([])
    })

    it('adressiert die Kennung aus dem Gruppengeheimnis, sobald es eine gibt', async () => {
      // Der Umzug aus Stufe 3d. Was der Server nicht ausrechnen kann, muss der
      // Client adressieren — sonst landet die Nachricht in der alten Mailbox,
      // die bald niemand mehr liest.
      umzug.set('gruppe-7', 'geheime-mailbox')

      const { result } = await baueHook({ art: 'gruppe', groupId: 7, mitglieder: [ICH, DU] })
      const auftraege = await result.current.baueVersand('Hallo Gruppe', 'uuid-4')

      expect(auftraege).toHaveLength(1)
      expect(auftraege[0].blind_mailbox_id).toBe('geheime-mailbox')
    })

    it('adressiert die Kennung aus dem Chatgeheimnis, sobald es eine gibt', async () => {
      // Dasselbe für den Direktchat — und zwar für **beide** Umschläge, den
      // Sitzungsaufbau eingeschlossen. Bliebe der Aufbau in der alten Mailbox
      // liegen, fände die Gegenstelle die Nachricht ohne die dazugehörige
      // Sitzung und liefe in den Sitzungsbruch.
      umzug.set('dm-1-2', 'geheimer-chat')

      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      const auftraege = await result.current.baueVersand('Hallo', 'uuid-5')

      expect(auftraege.map((a) => a.blind_mailbox_id)).toEqual([
        'geheimer-chat',
        'geheimer-chat',
        'geheimer-chat',
      ])
      expect(gerufen.dmZiele).toContain(`${ICH}->${DU}:dm-1-2`)
    })

    it('schickt auch die Quittung des Direktchats in die neue Mailbox', async () => {
      // Eine Quittung in der alten Mailbox erreicht niemanden mehr, sobald die
      // Gegenseite umgezogen ist — das Häkchen bliebe für immer aus.
      umzug.set('dm-1-2', 'geheimer-chat')
      geraete.push({ device_id: 'fremd-a', public_key: 'pub-a' })

      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      const auftraege = await result.current.baueSteuerversand('{}', 'ctrl-4', 'read_receipt')

      expect(auftraege.map((a) => a.blind_mailbox_id)).toEqual(['geheimer-chat'])
    })

    it('versiegelt eine Quittung je Gerät, mit eigener Kennung', async () => {
      // Ohne eigene Kennung gibt das Relais beim zweiten Aufruf still den
      // ersten Umschlag zurück und nur ein Gerät erfährt von der Quittung.
      geraete.push({ device_id: 'fremd-a', public_key: 'pub-a' })
      geraete.push({ device_id: 'fremd-b', public_key: 'pub-b' })

      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      const auftraege = await result.current.baueSteuerversand('{"type":"read_receipt"}', 'ctrl-1', 'read_receipt')

      expect(auftraege.map((a) => a.client_uuid)).toEqual(['ctrl-1#0', 'ctrl-1#1'])
      expect(auftraege.map((a) => a.ciphertext_envelope)).toEqual([
        'hybrid(pub-a):{"type":"read_receipt"}',
        'hybrid(pub-b):{"type":"read_receipt"}',
      ])
      expect(auftraege.every((a) => a.is_control && a.control_type === 'read_receipt')).toBe(true)
    })

    it('schickt die Gruppenquittung über den Gruppenschlüssel', async () => {
      const { result } = await baueHook({ art: 'gruppe', groupId: 7, mitglieder: [ICH, DU] })
      const auftraege = await result.current.baueSteuerversand('{"type":"read_receipt"}', 'ctrl-2', 'read_receipt')

      expect(auftraege).toHaveLength(1)
      expect(auftraege[0].is_control).toBe(true)
      expect(auftraege[0].client_uuid).toBe('ctrl-2')
    })

    it('sendet keine Quittung ohne Schlüssel dieses Geräts', async () => {
      geraete.push({ device_id: 'fremd-a', public_key: 'pub-a' })
      const { result } = await baueHook({ art: 'direkt', peerId: DU })
      await act(async () => {
        identitaetRef.current = { ...identitaetRef.current, sendPair: null as any }
      })

      expect(await result.current.baueSteuerversand('{}', 'ctrl-3', 'read_receipt')).toEqual([])
    })
  })
})
