// @vitest-environment node
/**
 * Der Gruppenschlüssel, geprüft an drei Mitgliedern mit je eigenen Geräten.
 *
 * Der Hybridumschlag ist hier ein Stellvertreter: er versiegelt gegen einen
 * benannten Schlüssel und öffnet nur mit demselben. Dass die echte Fassung
 * RSA-OAEP ist, prüft `e2eeCrypto.test.ts`; hier ginge sonst die Laufzeit für
 * Schlüsselerzeugung drauf und der eigentliche Gegenstand unter. Die
 * Gruppenverschlüsselung selbst ist echt — AES-256-GCM aus DIS.
 *
 * Node-Umgebung, nicht jsdom: DIS prüft Eingaben mit `instanceof Uint8Array`.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const HYBRID = 'sv-e2ee-hybrid-v1:'

// ---- Stellvertreter für den Hybridumschlag -------------------------------
vi.mock('./e2eeCrypto', () => ({
  encryptE2eeHybrid: async (nachricht: string, empfaengerOeffentlich: string) =>
    `${HYBRID}${empfaengerOeffentlich}.${Buffer.from(nachricht, 'utf-8').toString('base64')}`,
  decryptE2eeHybridWithKeyring: async () => {
    throw new Error('im Test nicht benutzt')
  },
  // Literal, nicht `HYBRID`: dieser Wert wird beim Bau der Factory gelesen,
  // und die läuft vor den Konstanten der Datei.
  E2EE_HYBRID_ENVELOPE_SPEC: { currentPrefix: 'sv-e2ee-hybrid-v1:' },
  // Die echten Ableitungen sind SHA-256; hier zählt nur, dass jede Mailbox
  // eine eigene Kennung hat und die der Gruppe dieselbe bleibt, die der
  // Kontext trägt (`MAILBOX`, weiter unten — hier als Literal, weil `vi.mock`
  // vor den Konstanten läuft).
  deriveUserDeviceMailboxId: async (uid: number) => `dev:${uid}`,
  deriveGroupBlindMailboxId: async (gid: number) =>
    gid === 42 ? 'a'.repeat(32) : `grp:${gid}`,
}))

// ---- Stellvertreter für die Geräteverwaltung ----------------------------
interface TestGeraet {
  kennung: string
  konto: number
  oeffentlich: string
  ablage: TestAblage
}

let aktuell: TestGeraet
const verzeichnis = new Map<number, { device_id: string; public_key: string; label: string }[]>()

vi.mock('./e2eeGeraet', () => ({
  eigenesGeraet: async () => aktuell,
  geraeteVon: async (uid: number) => verzeichnis.get(uid) ?? [],
  verlangeGeraeteVon: async (uid: number) => verzeichnis.get(uid) ?? [],
}))

// ---- Stellvertreter für Relais und Anrufe -------------------------------
interface Umschlag {
  id: number
  /** In welcher Mailbox er liegt. Seit 09/2026 der Gegenstand eigener Zusagen. */
  mailboxId: string
  ciphertext_envelope: string
  client_uuid: string | null
  control_type: string | null
}

let mailbox: Umschlag[] = []
let naechsteId = 1
/** Die Mitgliedschaft laut Server. `beforeEach` setzt sie je Test. */
let serverMitglieder: number[] = []
/** Lässt jeden Relais-Aufruf scheitern, für den Fall „erreicht niemanden". */
let relaisKaputt = false
/**
 * Jede Registrierung eines Besitznachweises, in der Reihenfolge.
 *
 * Erwartet wird genau eine, und zwar auf der Kennung **aus dem Geheimnis**.
 * Eine auf der alten Kennung wäre der Fehler, der den Messenger schon einmal
 * zerstört hat — siehe den Test dazu.
 */
const registrierungen: { mailboxId: string; authToken: string }[] = []
/** Gesetzt heisst: der Server nimmt den Nachweis nicht an. */
let registrierungFehlschlag = false

vi.mock('@/api/social', () => ({
  relayE2eeEnvelope: async (payload: {
    blind_mailbox_id: string
    ciphertext_envelope: string
    client_uuid?: string | null
    control_type?: string | null
  }) => {
    if (relaisKaputt) throw new Error('Relais nicht erreichbar')
    const umschlag: Umschlag = {
      id: naechsteId++,
      mailboxId: payload.blind_mailbox_id,
      ciphertext_envelope: payload.ciphertext_envelope,
      client_uuid: payload.client_uuid ?? null,
      control_type: payload.control_type ?? null,
    }
    mailbox.push(umschlag)
    return umschlag
  },
  fetchE2eeEnvelopes: async (mid: string, seit = 0) =>
    mailbox.filter((u) => u.mailboxId === mid && u.id > seit),
  // Die Mitgliederliste, wie der Server sie sieht. Absichtlich getrennt von der
  // Liste, die der Aufrufer im Kontext mitgibt: genau dieses Auseinanderlaufen
  // ist der Fall, den ein Nachzügler auslöst.
  getGroupMembers: async () => serverMitglieder.map((user_id) => ({ user_id })),
  registriereMailbox: async (mailboxId: string, authToken: string) => {
    if (registrierungFehlschlag) throw new Error('409')
    registrierungen.push({ mailboxId, authToken })
  },
}))

vi.mock('@/api/calls', () => ({ sendeRaumSchluessel: async () => undefined }))

import {
  GRUPPE_PREFIX,
  entschluesseleGruppenUmschlag,
  erzeugeGruppenSchluessel,
  fordereGruppenSchluessel,
  holeGeraeteSteuerung,
  gruppenZiele,
  leereGeraeteStand,
  leereUmzuege,
  setzeGruppenAblageFuerTest,
  umgezogeneMailbox,
  verarbeiteGruppenSteuerung,
  verschluesseleFuerGruppe,
  GruppenSchluesselNichtZugestelltError,
  verwirfGruppenSchluessel,
  mailboxAusGeheimnis,
  nachweisAusGeheimnis,
  type GruppenAblage,
  type GruppenGeheimnis,
  type GruppenKontext,
  type GruppenSchluesselEintrag,
} from './gruppenSchluessel'
import { leereMailboxNachweise, mailboxNachweis } from './mailboxNachweis'

const ALICE = 1
const BOB = 2
const CAROL = 9
const GRUPPE = 42
const MAILBOX = 'a'.repeat(32)

// ==========================================
// Gerüst
// ==========================================

interface TestAblage extends GruppenAblage {
  /** Wie viele Schlüssel dieses Gerät kennt. */
  anzahl(): number
}

function neueAblage(): TestAblage {
  const keys = new Map<string, GruppenSchluesselEintrag>()
  const aktuelle = new Map<number, string>()
  const beantwortet = new Set<string>()
  const geheimnisse = new Map<number, GruppenGeheimnis>()
  return {
    async liesGeheimnis(groupId) {
      return geheimnisse.get(groupId) ?? null
    },
    async schreibeGeheimnis(eintrag) {
      // Wie die echte Ablage: wer zuerst da ist, bleibt stehen.
      if (geheimnisse.has(eintrag.groupId)) return
      geheimnisse.set(eintrag.groupId, eintrag)
    },
    async lies(groupId, keyId) {
      return keys.get(`${groupId}:${keyId}`) ?? null
    },
    async liesAktuellen(groupId) {
      const keyId = aktuelle.get(groupId)
      return keyId ? (keys.get(`${groupId}:${keyId}`) ?? null) : null
    },
    async schreibe(eintrag) {
      keys.set(`${eintrag.groupId}:${eintrag.keyId}`, eintrag)
      aktuelle.set(eintrag.groupId, eintrag.keyId)
    },
    async schreibeNebenher(eintrag) {
      keys.set(`${eintrag.groupId}:${eintrag.keyId}`, eintrag)
    },
    async kennstAnfrage(groupId, kennung) {
      return beantwortet.has(`${groupId}:${kennung}`)
    },
    async merkeAnfrage(groupId, kennung) {
      beantwortet.add(`${groupId}:${kennung}`)
    },
    async loescheGruppe(groupId) {
      for (const schluessel of [...keys.keys()]) {
        if (schluessel.startsWith(`${groupId}:`)) keys.delete(schluessel)
      }
      aktuelle.delete(groupId)
      geheimnisse.delete(groupId)
      for (const marke of [...beantwortet]) {
        if (marke.startsWith(`${groupId}:`)) beantwortet.delete(marke)
      }
    },
    anzahl: () => keys.size,
  }
}

function geraet(konto: number, name: string): TestGeraet {
  const g: TestGeraet = {
    kennung: name.padEnd(32, '0'),
    konto,
    oeffentlich: `pub-${name}`,
    ablage: neueAblage(),
  }
  const liste = verzeichnis.get(konto) ?? []
  liste.push({ device_id: g.kennung, public_key: g.oeffentlich, label: name })
  verzeichnis.set(konto, liste)
  return g
}

/** Versetzt den Prozess in die Rolle eines Geräts: eigene Identität, eigene Ablage. */
function aktiviere(g: TestGeraet): void {
  aktuell = g
  setzeGruppenAblageFuerTest(g.ablage)
}

function kontext(g: TestGeraet, mitglieder: number[], istEigentuemer = false): GruppenKontext {
  return {
    groupId: GRUPPE,
    blindMailboxId: MAILBOX,
    eigeneId: g.konto,
    mitglieder,
    istEigentuemer,
  }
}

/** Öffnet einen Stellvertreter-Hybridumschlag, wenn er für dieses Gerät ist. */
function oeffneHybrid(g: TestGeraet, umschlag: string): string | null {
  if (!umschlag.startsWith(HYBRID)) return null
  const rumpf = umschlag.slice(HYBRID.length)
  const punkt = rumpf.indexOf('.')
  if (rumpf.slice(0, punkt) !== g.oeffentlich) return null
  return Buffer.from(rumpf.slice(punkt + 1), 'base64').toString('utf-8')
}

/**
 * Lässt ein Gerät einen Durchlauf machen: erst die eigene Geräte-Mailbox, dann
 * die der Gruppe. Genau die Reihenfolge aus `einDurchlauf` — und sie ist keine
 * Geschmacksfrage: die Schlüssel liegen in der ersten, die Nachrichten in der
 * zweiten. Andersherum wäre der erste Durchlauf immer der blinde.
 */
async function lies(
  g: TestGeraet,
  mitglieder: number[],
  ab = 0,
): Promise<{ texte: string[]; unlesbar: number }> {
  aktiviere(g)

  await holeGeraeteSteuerung(g.konto, async (umschlag) => {
    const klartext = oeffneHybrid(g, umschlag)
    if (klartext === null) throw new Error('nicht für dieses Gerät')
    return klartext
  })

  const texte: string[] = []
  let unlesbar = 0
  for (const umschlag of mailbox.filter((u) => u.mailboxId === MAILBOX && u.id > ab)) {
    if (umschlag.ciphertext_envelope.startsWith(HYBRID)) {
      // Altbestand: bis 09/2026 lagen die Steuerumschläge hier. Der Lesepfad
      // muss sie weiter verstehen, sonst verlöre ein Gerät seinen Schlüssel.
      const klartext = oeffneHybrid(g, umschlag.ciphertext_envelope)
      if (klartext !== null) {
        await verarbeiteGruppenSteuerung(kontext(g, mitglieder), klartext)
      }
      continue
    }
    const lesung = await entschluesseleGruppenUmschlag(GRUPPE, umschlag.ciphertext_envelope)
    if (lesung.art === 'klartext') texte.push(lesung.text)
    else unlesbar += 1
  }
  return { texte, unlesbar }
}

/** Wie `sende`, aber als Eigentuemer — nur der darf ein Gruppengeheimnis erzeugen. */
async function sendeAlsEigentuemer(
  g: TestGeraet,
  mitglieder: number[],
  text: string,
): Promise<string> {
  aktiviere(g)
  const umschlag = await verschluesseleFuerGruppe(kontext(g, mitglieder, true), text)
  mailbox.push({
    id: naechsteId++,
    mailboxId: MAILBOX,
    ciphertext_envelope: umschlag,
    client_uuid: null,
    control_type: null,
  })
  return umschlag
}

async function sende(g: TestGeraet, mitglieder: number[], text: string): Promise<string> {
  aktiviere(g)
  const umschlag = await verschluesseleFuerGruppe(kontext(g, mitglieder), text)
  mailbox.push({
    id: naechsteId++,
    mailboxId: MAILBOX,
    ciphertext_envelope: umschlag,
    client_uuid: null,
    control_type: null,
  })
  return umschlag
}

// ==========================================
// Zusagen
// ==========================================

describe('gruppenSchluessel', () => {
  let alice: TestGeraet
  let bob: TestGeraet
  let carol: TestGeraet
  let alle: number[]

  beforeEach(() => {
    verzeichnis.clear()
    mailbox = []
    naechsteId = 1
    alice = geraet(ALICE, 'alice-laptop')
    bob = geraet(BOB, 'bob-handy')
    carol = geraet(CAROL, 'carol-tablet')
    alle = [ALICE, BOB, CAROL]
    serverMitglieder = [...alle]
    relaisKaputt = false
    registrierungen.length = 0
    leereGeraeteStand()
  })

  it('erreicht ein Mitglied, das erst nach dem Öffnen des Gesprächs beitritt', async () => {
    // Am laufenden System gefunden, und die Frage, die dahinter steht: eine
    // Gruppe läuft schon, jemand kommt dazu — liest der die nächsten
    // Nachrichten? Bis 09/2026 nicht. `activeGroup` ist in `Messenger.tsx` eine
    // Momentaufnahme vom Öffnen des Gesprächs und wird nie aufgefrischt. Alice
    // verteilte weiter an ihre alte Liste, und als Carol nachfragte, wies Alice
    // sie ab, weil Carol in der Momentaufnahme nicht vorkam. Still, ohne
    // Meldung, bei jedem Versuch aufs Neue.
    const ohneCarol = [ALICE, BOB]
    serverMitglieder = [...ohneCarol]

    await sende(alice, ohneCarol, 'vor Carols Beitritt')
    await lies(bob, ohneCarol)

    // Carol tritt bei. Der Server weiss es, Alices geöffnetes Fenster nicht.
    serverMitglieder = [ALICE, BOB, CAROL]

    const stand = naechsteId
    await sende(alice, ohneCarol, 'nach Carols Beitritt')

    // Carol liest mit der Liste, die sie beim Beitritt bekommen hat.
    const beiCarol = await lies(carol, alle, stand - 1)
    expect(beiCarol.texte).toEqual(['nach Carols Beitritt'])

    // Und die Nachricht von vorher bleibt ihr verschlossen. Das ist die Zusage,
    // kein Versehen: sonst holte sich ein Hinausgeworfener über einen offenen
    // Einladungslink den ganzen Verlauf zurück.
    const vonAnfang = await lies(carol, alle, 0)
    expect(vonAnfang.texte).toEqual(['nach Carols Beitritt'])
    expect(vonAnfang.unlesbar).toBe(1)
  })

  it('meldet es, wenn der Gruppenschlüssel kein einziges Gerät erreicht', async () => {
    // Eine unvollständige Zustellung ist eingeplant, die Übergangenen fordern
    // nach. Erreicht der Schlüssel aber niemanden, wäre jede folgende Nachricht
    // für alle ausser dem Absender unlesbar — und das lief bis 09/2026 als
    // stille 0 durch, die niemand auswertete.
    relaisKaputt = true
    try {
      await expect(sende(alice, alle, 'hört mich jemand?')).rejects.toThrow(
        GruppenSchluesselNichtZugestelltError
      )
    } finally {
      relaisKaputt = false
    }
  })

  it('stellt den Schlüssel an jedes Gerät zu, und jeder öffnet genau seinen Umschlag', async () => {
    await sende(alice, alle, 'Treffen um acht?')

    const schluesselUmschlaege = mailbox.filter((u) => u.control_type === 'group_key')
    expect(schluesselUmschlaege).toHaveLength(2) // Bob und Carol, nicht Alice selbst
    // Eigene Kennung je Umschlag: sonst gäbe das Relais beim zweiten Aufruf
    // still den ersten zurück und nur ein Gerät bekäme den Schlüssel.
    const kennungen = new Set(schluesselUmschlaege.map((u) => u.client_uuid))
    expect(kennungen.size).toBe(2)

    for (const empfaenger of [bob, carol]) {
      const meine = schluesselUmschlaege.filter(
        (u) => oeffneHybrid(empfaenger, u.ciphertext_envelope) !== null,
      )
      expect(meine).toHaveLength(1)
    }

    expect((await lies(bob, alle)).texte).toEqual(['Treffen um acht?'])
    expect((await lies(carol, alle)).texte).toEqual(['Treffen um acht?'])
  })

  it('trägt im Nachrichtenumschlag keine Spur des Klartexts', async () => {
    const umschlag = await sende(alice, alle, 'Das Passwort lautet Sonnenblume')

    expect(umschlag.startsWith(GRUPPE_PREFIX)).toBe(true)
    expect(umschlag).not.toContain('Sonnenblume')
    expect(umschlag).not.toContain('Passwort')
    // Die Kennung steht im Klartext — sie ist der Hash des Schlüssels und sagt
    // ohne ihn nichts.
    expect(umschlag.slice(GRUPPE_PREFIX.length).split('.')[0]).toMatch(/^[0-9a-f]{16}$/)
  })

  it('bleibt ohne Schlüssel unlesbar', async () => {
    const umschlag = await sende(alice, alle, 'Nur für die Gruppe')

    // Dave ist nirgends Mitglied und hat nie einen Schlüssel gesehen.
    const dave = geraet(77, 'dave-pc')
    aktiviere(dave)
    const lesung = await entschluesseleGruppenUmschlag(GRUPPE, umschlag)
    expect(lesung.art).toBe('kein-schluessel')
  })

  it('rotiert beim Rauswurf: die nächste Nachricht bleibt draußen, die vorherige nicht', async () => {
    await sende(alice, alle, 'Vor dem Rauswurf')
    await lies(carol, alle)
    const standCarol = naechsteId

    // Der Rauswurf passiert beim Server. Die Liste im Kontext ist nur noch die
    // Momentaufnahme der Oberfläche; entscheidend ist, was der Server sagt.
    const ohneCarol = [ALICE, BOB]
    serverMitglieder = [...ohneCarol]
    await sende(alice, ohneCarol, 'Nach dem Rauswurf')

    // Bob bekommt den frischen Schlüssel und liest beides.
    const beiBob = await lies(bob, ohneCarol)
    expect(beiBob.texte).toEqual(['Vor dem Rauswurf', 'Nach dem Rauswurf'])

    // Carol bekommt nichts Neues zugestellt und liest nur das Alte weiter.
    const beiCarol = await lies(carol, alle, standCarol - 1)
    expect(beiCarol.texte).toEqual([])
    expect(beiCarol.unlesbar).toBe(1)
    expect((await lies(carol, alle, 0)).texte).toEqual(['Vor dem Rauswurf'])
  })

  it('münzt keinen neuen Schlüssel, solange die Mitgliedschaft gleich bleibt', async () => {
    await sende(alice, alle, 'eins')
    const nachErster = mailbox.filter((u) => u.control_type === 'group_key').length
    await sende(alice, alle, 'zwei')
    await sende(alice, [...alle].reverse(), 'drei') // dieselbe Menge, andere Reihenfolge

    expect(mailbox.filter((u) => u.control_type === 'group_key')).toHaveLength(nachErster)
    expect((await lies(bob, alle)).texte).toEqual(['eins', 'zwei', 'drei'])
  })

  it('weist eine Zustellung ab, deren Kennung nicht zum Schlüssel passt', async () => {
    await sende(alice, alle, 'egal')
    const zustellung = mailbox.find((u) => u.control_type === 'group_key' && oeffneHybrid(bob, u.ciphertext_envelope))!
    const inhalt = JSON.parse(oeffneHybrid(bob, zustellung.ciphertext_envelope)!)

    // Derselbe Name, anderer Schlüssel: so ließe sich ein untergeschobener
    // Schlüssel als der bekannte ausgeben.
    const gefaelscht = JSON.stringify({ ...inhalt, schluessel: Buffer.alloc(32, 7).toString('base64') })

    aktiviere(bob)
    expect((await verarbeiteGruppenSteuerung(kontext(bob, alle), gefaelscht)).art).toBe('keine')
    expect(bob.ablage.anzahl()).toBe(0)
  })

  it('weist einen Schlüssel ab, der eine andere Gruppe nennt', async () => {
    await sende(alice, alle, 'egal')
    const zustellung = mailbox.find((u) => u.control_type === 'group_key' && oeffneHybrid(bob, u.ciphertext_envelope))!
    const inhalt = JSON.parse(oeffneHybrid(bob, zustellung.ciphertext_envelope)!)
    const fremd = JSON.stringify({ ...inhalt, groupId: GRUPPE + 1 })

    aktiviere(bob)
    expect((await verarbeiteGruppenSteuerung(kontext(bob, alle), fremd)).art).toBe('keine')
    expect(bob.ablage.anzahl()).toBe(0)
  })

  it('meldet einen verdrehten Umschlag als Bruch statt als fehlenden Schlüssel', async () => {
    const umschlag = await sende(alice, alle, 'unverfälscht')
    await lies(bob, alle)

    const [keyId, rumpf] = umschlag.slice(GRUPPE_PREFIX.length).split('.')
    const bytes = Buffer.from(rumpf, 'base64')
    bytes[bytes.length - 1] ^= 0x01
    const verdreht = `${GRUPPE_PREFIX}${keyId}.${bytes.toString('base64')}`

    aktiviere(bob)
    const lesung = await entschluesseleGruppenUmschlag(GRUPPE, verdreht)
    expect(lesung.art).toBe('bruch')
  })

  it('weist einen Umschlag ab, dessen Kennung auf eine andere Generation zeigt', async () => {
    const ersterUmschlag = await sende(alice, alle, 'erste Generation')
    await lies(bob, alle)
    serverMitglieder = [ALICE, BOB]
    await sende(alice, [ALICE, BOB], 'zweite Generation')
    await lies(bob, [ALICE, BOB])

    // Der Chiffretext der ersten Generation unter der Kennung der zweiten: die
    // gebundenen Daten nennen die Kennung, also scheitert der Tag.
    const zweiteKeyId = mailbox
      .map((u) => u.ciphertext_envelope)
      .filter((e) => e.startsWith(GRUPPE_PREFIX))
      .pop()!
      .slice(GRUPPE_PREFIX.length)
      .split('.')[0]
    const umgehaengt = `${GRUPPE_PREFIX}${zweiteKeyId}.${ersterUmschlag.split('.')[1]}`

    aktiviere(bob)
    expect((await entschluesseleGruppenUmschlag(GRUPPE, umgehaengt)).art).toBe('bruch')
  })

  it('erkennt den Altbestand nicht als Gruppenumschlag', async () => {
    aktiviere(bob)
    // So sah ein Umschlag aus, als sich der Schlüssel aus der Gruppenkennung
    // ableiten ließ: kein Punkt, keine Schlüsselkennung.
    const alt = `${GRUPPE_PREFIX}${Buffer.alloc(40, 3).toString('base64')}`
    expect((await entschluesseleGruppenUmschlag(GRUPPE, alt)).art).toBe('unbekannt')
  })

  describe('Nachforderung', () => {
    it('beschafft dem Nachzügler den aktuellen Schlüssel', async () => {
      await sende(alice, alle, 'lief schon')

      // Bobs zweites Gerät: frisch gekoppelt, kennt keinen Schlüssel.
      const bobZwei = geraet(BOB, 'bob-laptop')
      aktiviere(bobZwei)
      const gestellt = await fordereGruppenSchluessel(kontext(bobZwei, alle), 'ffffffffffffffff')
      expect(gestellt).toBe(true)

      const anfragen = mailbox.filter((u) => u.control_type === 'group_key_request')
      expect(anfragen.length).toBeGreaterThan(0)

      // Alice ist die kleinste Mitgliedskennung und damit zuständig.
      aktiviere(alice)
      for (const anfrage of anfragen) {
        const klartext = oeffneHybrid(alice, anfrage.ciphertext_envelope)
        if (klartext) {
          const ergebnis = await verarbeiteGruppenSteuerung(kontext(alice, alle), klartext)
          expect(ergebnis).toMatchObject({ art: 'anfrage', beantwortet: true })
        }
      }

      // **Ein** Durchlauf reicht. Die Antwort liegt in Bobs Geräte-Mailbox,
      // und die geht `lies` vor der Gruppenmailbox durch — der Schlüssel ist
      // also da, wenn die Nachricht an der Reihe ist.
      //
      // Bis 09/2026 brauchte es hier zwei Ladevorgänge: die Antwort lag in
      // derselben Mailbox *hinter* der Nachricht, die sie lesbar macht. Wer
      // nachforderte, sah beim ersten Mal „Verschlüsselte Nachricht" und musste
      // auf den nächsten Durchlauf warten.
      const ersterDurchlauf = await lies(bobZwei, alle)
      expect(ersterDurchlauf.texte).toEqual(['lief schon'])
      expect(ersterDurchlauf.unlesbar).toBe(0)
    })

    it('antwortet nicht, wer nicht zuständig ist', async () => {
      await sende(alice, alle, 'lief schon')
      aktiviere(carol)
      const anfrage = JSON.stringify({
        typ: 'group_key_request',
        v: 1,
        groupId: GRUPPE,
        vonKonto: BOB,
        vonGeraet: 'x'.repeat(32),
      })
      // Zuständig wäre Alice (kleinste Kennung), nicht Carol.
      expect(await verarbeiteGruppenSteuerung(kontext(carol, alle), anfrage)).toMatchObject({
        art: 'anfrage',
        beantwortet: false,
      })
    })

    it('gibt keinen Schlüssel an Fremde heraus', async () => {
      await sende(alice, alle, 'lief schon')
      const vorher = mailbox.length

      aktiviere(alice)
      const anfrage = JSON.stringify({
        typ: 'group_key_request',
        v: 1,
        groupId: GRUPPE,
        vonKonto: 77, // kein Mitglied
        vonGeraet: 'x'.repeat(32),
      })
      expect((await verarbeiteGruppenSteuerung(kontext(alice, alle), anfrage)).art).toBe('keine')
      expect(mailbox.length).toBe(vorher)
    })

    it('gibt nur den aktuellen Schlüssel heraus, nie einen alten', async () => {
      await sende(alice, alle, 'alte Generation')
      await lies(bob, alle)
      await sende(alice, [ALICE, BOB], 'neue Generation')

      const neueKeyId = mailbox
        .map((u) => u.ciphertext_envelope)
        .filter((e) => e.startsWith(GRUPPE_PREFIX))
        .pop()!
        .slice(GRUPPE_PREFIX.length)
        .split('.')[0]
      const vorher = mailbox.length

      aktiviere(alice)
      await verarbeiteGruppenSteuerung(
        kontext(alice, [ALICE, BOB]),
        JSON.stringify({
          typ: 'group_key_request',
          v: 1,
          groupId: GRUPPE,
          vonKonto: BOB,
          vonGeraet: 'x'.repeat(32),
          keyId: '0'.repeat(16),
        }),
      )

      const antworten = mailbox.slice(vorher)
      expect(antworten.length).toBeGreaterThan(0)
      for (const antwort of antworten) {
        const klartext = oeffneHybrid(bob, antwort.ciphertext_envelope)
        if (klartext) expect(JSON.parse(klartext).keyId).toBe(neueKeyId)
      }
    })

    it('fragt nach derselben Kennung nur einmal, nach einer anderen wieder', async () => {
      aktiviere(bob)
      expect(await fordereGruppenSchluessel(kontext(bob, alle), 'a'.repeat(16))).toBe(true)
      const nachErster = mailbox.length
      expect(await fordereGruppenSchluessel(kontext(bob, alle), 'a'.repeat(16))).toBe(false)
      expect(mailbox.length).toBe(nachErster)

      expect(await fordereGruppenSchluessel(kontext(bob, alle), 'b'.repeat(16))).toBe(true)
      expect(mailbox.length).toBeGreaterThan(nachErster)
    })

    // Gescheitert heißt: es liegt nichts in der Mailbox, das jemand später
    // beantworten könnte. Bliebe die Marke stehen, wäre dieses Gerät wegen
    // eines Netzfehlers bis zum Neuladen ohne Schlüssel.
    it('fragt erneut, wenn die Nachfrage niemanden erreicht hat', async () => {
      aktiviere(bob)
      relaisKaputt = true
      try {
        expect(await fordereGruppenSchluessel(kontext(bob, alle), 'c'.repeat(16))).toBe(false)
      } finally {
        relaisKaputt = false
      }
      expect(await fordereGruppenSchluessel(kontext(bob, alle), 'c'.repeat(16))).toBe(true)
    })

    // Eine Nachfrage bleibt als Umschlag liegen und wird bei jedem Abruf wieder
    // gelesen. Wer sie jedes Mal neu beantwortet, füllt die Mailbox mit
    // Zustellungen, bis die echten Nachrichten aus dem Abruffenster fallen — am
    // laufenden System waren 99 von 100 Umschlägen Schlüsselzustellungen.
    it('beantwortet dieselbe Nachfrage nur einmal', async () => {
      await sende(alice, alle, 'lief schon')
      const anfrage = JSON.stringify({
        typ: 'group_key_request',
        v: 1,
        groupId: GRUPPE,
        vonKonto: BOB,
        vonGeraet: 'x'.repeat(32),
        keyId: '0'.repeat(16),
      })

      aktiviere(alice)
      const vorher = mailbox.length
      expect(await verarbeiteGruppenSteuerung(kontext(alice, alle), anfrage)).toMatchObject({
        art: 'anfrage',
        beantwortet: true,
      })
      const nachAntwort = mailbox.length
      expect(nachAntwort).toBeGreaterThan(vorher)

      for (let i = 0; i < 5; i++) {
        expect(await verarbeiteGruppenSteuerung(kontext(alice, alle), anfrage)).toMatchObject({
          art: 'anfrage',
          beantwortet: false,
        })
      }
      expect(mailbox.length).toBe(nachAntwort)
    })

    // Sonst bliebe ein Gerät, dem eine Rotation entgangen ist, für immer
    // draußen: es fragt nach der neuen Kennung, und niemand antwortet.
    it('beantwortet eine Nachfrage nach einer anderen Kennung erneut', async () => {
      await sende(alice, alle, 'lief schon')
      const anfrage = (keyId: string) =>
        JSON.stringify({
          typ: 'group_key_request',
          v: 1,
          groupId: GRUPPE,
          vonKonto: BOB,
          vonGeraet: 'x'.repeat(32),
          keyId,
        })

      aktiviere(alice)
      await verarbeiteGruppenSteuerung(kontext(alice, alle), anfrage('0'.repeat(16)))
      const nachErster = mailbox.length
      expect(
        await verarbeiteGruppenSteuerung(kontext(alice, alle), anfrage('1'.repeat(16))),
      ).toMatchObject({ art: 'anfrage', beantwortet: true })
      expect(mailbox.length).toBeGreaterThan(nachErster)
    })
  })

  it('überlebt einen Neustart der Anwendung', async () => {
    await sende(alice, alle, 'vor dem Neustart')
    await lies(bob, alle)

    // Neustart: derselbe Gerätespeicher, alles andere frisch. Die Mailbox
    // liefert nichts Neues, der Schlüssel muss aus der Ablage kommen.
    const alterUmschlag = mailbox.find((u) => u.ciphertext_envelope.startsWith(GRUPPE_PREFIX))!
    mailbox = []
    aktiviere(bob)
    const lesung = await entschluesseleGruppenUmschlag(GRUPPE, alterUmschlag.ciphertext_envelope)
    expect(lesung).toEqual({ art: 'klartext', text: 'vor dem Neustart' })
  })

  it('räumt die Schlüssel weg, wenn die Gruppe verlassen wird', async () => {
    const umschlag = await sende(alice, alle, 'noch drin')
    await lies(carol, alle)
    expect(carol.ablage.anzahl()).toBe(1)

    aktiviere(carol)
    await verwirfGruppenSchluessel(GRUPPE)

    expect(carol.ablage.anzahl()).toBe(0)
    expect((await entschluesseleGruppenUmschlag(GRUPPE, umschlag)).art).toBe('kein-schluessel')
  })

  /**
   * Ankommen ja, verdrängen nein.
   *
   * Bis 09/2026 gewann der zuletzt eingetroffene Schlüssel. Carol konnte Alice
   * damit einen unterschieben, den nur sie kennt: Alice verschlüsselte ab da
   * gegen ihn, Bob las nur noch „Verschlüsselte Nachricht", und für Alice sah
   * alles richtig aus — ihr eigener Tab konnte ja alles lesen.
   */
  it('lässt einen zugestellten Schlüssel den aktuellen nicht verdrängen', async () => {
    const ersterUmschlag = await sende(alice, alle, 'von Alice')
    const alicesKeyId = ersterUmschlag.slice(GRUPPE_PREFIX.length).split('.')[0]
    await lies(bob, alle)

    // Carol münzt einen eigenen und stellt ihn ausschliesslich Alice zu.
    const { keyId, schluessel } = await erzeugeGruppenSchluessel()
    const untergeschoben = JSON.stringify({
      typ: 'group_key',
      v: 1,
      groupId: GRUPPE,
      keyId,
      schluessel: Buffer.from(schluessel).toString('base64'),
      // Dieselbe Mitgliedschaft wie Alices Schlüssel — sonst rotierte Alice
      // beim nächsten Senden ohnehin von selbst.
      mitglieder: alle,
    })

    aktiviere(alice)
    await verarbeiteGruppenSteuerung(kontext(alice, alle), untergeschoben)

    // Alice schreibt weiter mit ihrem eigenen Schlüssel. Ohne Rotation geht
    // dabei nur ein Umschlag raus, und der bekommt genau `naechsteId` — der
    // Lesestand muss also eins davor liegen.
    const stand = naechsteId - 1
    const zweiterUmschlag = await sende(alice, alle, 'weiter von Alice')
    expect(zweiterUmschlag.slice(GRUPPE_PREFIX.length).split('.')[0]).toBe(alicesKeyId)

    // Und Bob liest sie, ohne nachfordern zu müssen.
    expect(await lies(bob, alle, stand)).toEqual({ texte: ['weiter von Alice'], unlesbar: 0 })

    // Abgelegt ist Carols Schlüssel trotzdem: käme eine Nachricht damit, wäre
    // sie lesbar. Verworfen wird nur sein Anspruch, der aktuelle zu sein.
    expect(await alice.ablage.lies(GRUPPE, keyId)).not.toBeNull()
  })
})

/**
 * Die echte Ablage — eine Datenbank je Konto.
 *
 * Die Tests oben spritzen eine Attrappe ein und berühren `oeffneDatenbank`
 * nie. Genau deshalb fiel jahrelang nicht auf, dass `msm_e2ee_gruppen` als
 * einzige der vier E2EE-Ablagen an gar nichts hing: der Geräteschlüssel liegt
 * unter `konto:<id>`, der Ratchet unter einer Kennung mit dem eigenen Gerät —
 * die Gruppenschlüssel unter nichts. Zwei Konten in einem Browserprofil
 * teilten sie sich.
 */
describe('Schlüsselablage je Konto', () => {
  const geoeffnet: string[] = []
  const geloescht: string[] = []

  function installiereIndexedDb() {
    geoeffnet.length = 0
    geloescht.length = 0
    const db: any = {
      close: () => {},
      objectStoreNames: { contains: () => true },
      createObjectStore: () => ({ createIndex: () => {} }),
      transaction: () => {
        const tx: any = {
          objectStore: () => ({
            delete: () => {
              const r: any = { onsuccess: null, onerror: null }
              queueMicrotask(() => r.onsuccess?.())
              return r
            },
            get: () => {
              const r: any = { onsuccess: null, onerror: null, result: null }
              queueMicrotask(() => r.onsuccess?.())
              return r
            },
            put: () => {
              const r: any = { onsuccess: null, onerror: null }
              queueMicrotask(() => r.onsuccess?.())
              return r
            },
          }),
          oncomplete: null,
          onerror: null,
        }
        // `loescheGruppe` wartet auf den Abschluss der Transaktion. Ohne
        // dieses Signal liefe der Test in die Zeitgrenze statt in eine
        // Aussage.
        queueMicrotask(() => tx.oncomplete?.())
        return tx
      },
    }
    ;(globalThis as any).IDBKeyRange = {
      bound: (lower: any, upper: any) => ({ lower, upper }),
      only: (v: any) => v,
    }
    ;(globalThis as any).indexedDB = {
      open: (name: string) => {
        geoeffnet.push(name)
        const req: any = { onsuccess: null, onerror: null, onupgradeneeded: null, onblocked: null, result: db }
        queueMicrotask(() => {
          req.onupgradeneeded?.()
          req.onsuccess?.()
        })
        return req
      },
      deleteDatabase: (name: string) => {
        geloescht.push(name)
        const r: any = { onsuccess: null, onerror: null }
        queueMicrotask(() => r.onsuccess?.())
        return r
      },
    }
  }

  beforeEach(() => {
    // Die echte Ablage zurückholen — die Attrappe der anderen Tests ginge
    // sonst an `oeffneDatenbank` vorbei, und dieser Test prüfte nichts.
    setzeGruppenAblageFuerTest(null)
    installiereIndexedDb()
  })

  it('öffnet für jedes Konto eine eigene Datenbank und nie die alte', async () => {
    const { setzeAngemeldetesKonto } = await import('@/lib/angemeldetesKonto')

    setzeAngemeldetesKonto(1)
    await verwirfGruppenSchluessel(GRUPPE)
    setzeAngemeldetesKonto(7)
    await verwirfGruppenSchluessel(GRUPPE)

    expect(geoeffnet).toEqual(['msm_e2ee_gruppen:konto:1', 'msm_e2ee_gruppen:konto:7'])
    expect(geloescht).toContain('msm_e2ee_gruppen')
    setzeAngemeldetesKonto(null)
  })

  it('gibt ohne angemeldetes Konto keine Schlüsselablage her', async () => {
    const { setzeAngemeldetesKonto } = await import('@/lib/angemeldetesKonto')
    setzeAngemeldetesKonto(null)

    // `verwirfGruppenSchluessel` schluckt Fehler nicht — ohne Konto muss der
    // Versuch scheitern, statt auf eine gemeinsame Ablage auszuweichen.
    await expect(verwirfGruppenSchluessel(GRUPPE)).rejects.toThrow()
    expect(geoeffnet).toEqual([])
  })
})

/**
 * Das Gruppengeheimnis: erzeugen, mitreisen, den Besitznachweis daraus.
 *
 * Es ist nicht der Nachrichtenschlüssel. **Der** rotiert bei jedem
 * Mitgliederwechsel; das Geheimnis darf das nicht, denn aus ihm entsteht die
 * Kennung der Mailbox — rotierte es mit, zöge die Gruppe bei jedem Beitritt
 * um und liesse ihr Fenster stehen.
 */
describe('Gruppengeheimnis', () => {
  // Eigene Geraete: die der oberen Suite leben in deren Gueltigkeitsbereich.
  let alice: TestGeraet
  let bob: TestGeraet
  let carol: TestGeraet
  let alle: number[]

  beforeEach(() => {
    verzeichnis.clear()
    mailbox = []
    naechsteId = 1
    alice = geraet(ALICE, 'alice-laptop')
    bob = geraet(BOB, 'bob-handy')
    carol = geraet(CAROL, 'carol-tablet')
    alle = [ALICE, BOB, CAROL]
    serverMitglieder = [...alle]
    relaisKaputt = false
    registrierungen.length = 0
    registrierungFehlschlag = false
    leereMailboxNachweise()
    leereGeraeteStand()
    leereUmzuege()
  })

  it('trennt Mailbox-Kennung und Besitznachweis', async () => {
    // Die Kennung steht in jedem Aufruf. Wäre der Nachweis derselbe Wert,
    // hätte ihn jeder, der die Kennung sieht — das Schloss wäre ein Türschild.
    const geheimnis = 'Z'.repeat(44)
    const mid = await mailboxAusGeheimnis(geheimnis)
    const token = await nachweisAusGeheimnis(geheimnis)

    expect(mid).toMatch(/^[0-9a-f]{64}$/)
    expect(token).toMatch(/^[0-9a-f]{64}$/)
    expect(mid).not.toBe(token)
  })

  it('leitet aus verschiedenen Geheimnissen verschiedene Kennungen ab', async () => {
    expect(await mailboxAusGeheimnis('A'.repeat(44))).not.toBe(
      await mailboxAusGeheimnis('B'.repeat(44)),
    )
  })

  it('wird nur vom Eigentümer erzeugt', async () => {
    // Erzeugten zwei Mitglieder gleichzeitig eines, hätte die Gruppe zwei
    // Mailboxen und zerfiele in zwei Hälften, die einander nicht mehr sehen.
    await sende(bob, alle, 'Bob ist nicht Eigentümer')

    expect(await bob.ablage.liesGeheimnis(GRUPPE)).toBeNull()
    expect(registrierungen).toEqual([])
  })

  it('entsteht beim ersten Senden des Eigentümers', async () => {
    await sendeAlsEigentuemer(alice, alle, 'erste Nachricht')

    const geheimnis = await alice.ablage.liesGeheimnis(GRUPPE)
    expect(geheimnis).not.toBeNull()
    // Der Nachweis liegt danach im Speicher und geht bei jedem Aufruf mit —
    // auf der Kennung **aus dem Geheimnis**, nicht auf der alten aus der
    // `group_id`. Die alte bleibt offen und trägt den Bestand.
    const neu = await mailboxAusGeheimnis(geheimnis!.geheimnis)
    expect(mailboxNachweis(neu)).toBe(await nachweisAusGeheimnis(geheimnis!.geheimnis))
    expect(mailboxNachweis(MAILBOX)).toBeNull()
  })

  it('reist mit dem Schlüssel zu den anderen Geräten', async () => {
    const stand = naechsteId
    await sendeAlsEigentuemer(alice, alle, 'mit Geheimnis')
    // `stand - 1`: `lies` filtert `id > ab`, und der erste Umschlag dieses
    // Sendevorgangs traegt genau `stand`.
    await lies(bob, alle, stand - 1)

    const beiAlice = await alice.ablage.liesGeheimnis(GRUPPE)
    const beiBob = await bob.ablage.liesGeheimnis(GRUPPE)
    expect(beiBob?.geheimnis).toBe(beiAlice?.geheimnis)
  })

  it('wird beim Empfänger nie überschrieben', async () => {
    // Der Fall, der die Gruppe spalten würde: ein zweites Geheimnis von
    // irgendwo. Wer schon eines hat, behält es.
    await sendeAlsEigentuemer(alice, alle, 'erst')
    await lies(bob, alle)
    const erstes = (await bob.ablage.liesGeheimnis(GRUPPE))?.geheimnis

    aktiviere(bob)
    await verarbeiteGruppenSteuerung(
      kontext(bob, alle),
      JSON.stringify({
        typ: 'group_key',
        v: 1,
        groupId: GRUPPE,
        keyId: 'ffffffffffffffff',
        schluessel: 'x',
        mitglieder: alle,
        geheimnis: 'UNTERGESCHOBEN'.padEnd(44, 'x'),
      }),
    )

    expect((await bob.ablage.liesGeheimnis(GRUPPE))?.geheimnis).toBe(erstes)
  })

  it('erzwingt genau einmal eine Rotation und danach nicht mehr', async () => {
    // Ohne Mitgliederwechsel rotiert nichts — ein frisch erzeugtes Geheimnis
    // muss die Rotation deshalb selbst auslösen, sonst käme es nie zu den
    // anderen. Beim zweiten Senden darf das nicht noch einmal passieren.
    await sendeAlsEigentuemer(alice, alle, 'erste')
    const nachErster = alice.ablage.anzahl()

    await sendeAlsEigentuemer(alice, alle, 'zweite')

    expect(alice.ablage.anzahl()).toBe(nachErster)
  })

  it('verschliesst die neue Kennung und lässt die alte offen', async () => {
    /*
     * Der Test zu einem gemessenen Fehler, nicht zu einer Vermutung — und zu
     * seiner Auflösung.
     *
     * Ein früherer Entwurf registrierte den Nachweis auf der **alten**
     * Kennung. Am laufenden System war die Gruppe danach tot: die
     * Schlüsselzustellung lief damals durch genau diese Mailbox, und ein
     * Mitglied ohne Geheimnis bekam dort 403 — also nie den Schlüssel, aus dem
     * das Geheimnis gekommen wäre. Es sah die Nachricht nicht einmal als
     * „Verschlüsselte Nachricht"; die Mailbox lieferte gar nichts mehr.
     *
     * Jetzt geht es, weil der Schlüssel einen anderen Weg nimmt: in die
     * Geräte-Mailbox des Empfängers. Durch die neue Gruppenmailbox kommt
     * nichts, was man zum Öffnen der neuen Gruppenmailbox bräuchte.
     *
     * Beide Hälften stehen hier zusammen, weil die eine ohne die andere in die
     * Irre führt: „registriert" allein wäre die Zusage, die den Messenger
     * schon einmal zerstört hat.
     */
    const stand = naechsteId
    await sendeAlsEigentuemer(alice, alle, 'erste')

    const geheimnis = (await alice.ablage.liesGeheimnis(GRUPPE))!.geheimnis
    const neu = await mailboxAusGeheimnis(geheimnis)
    expect(registrierungen).toEqual([
      { mailboxId: neu, authToken: await nachweisAusGeheimnis(geheimnis) },
    ])
    // Die alte bleibt unverschlossen: sie trägt den Bestand und die Mitglieder,
    // deren Geheimnis noch unterwegs ist.
    expect(registrierungen.some((r) => r.mailboxId === MAILBOX)).toBe(false)

    // Und die Gegenprobe, die zählt: das Mitglied kommt an den Schlüssel.
    // Von Anfang an gelesen, denn die Zustellung liegt beim ersten Senden —
    // danach rotiert nichts mehr.
    await sendeAlsEigentuemer(alice, alle, 'zweite')
    expect(await lies(bob, alle, stand - 1)).toEqual({
      texte: ['erste', 'zweite'],
      unlesbar: 0,
    })
  })

  it('bleibt sendefähig, wenn das Registrieren scheitert', async () => {
    // Ein 409 hiesse „schon registriert, mit anderem Token", alles andere ist
    // ein Netzfehler. Beides darf die Nachricht nicht aufhalten: der Nachweis
    // ist jederzeit nachholbar, eine nicht gesendete Nachricht nicht.
    registrierungFehlschlag = true
    const stand = naechsteId

    await sendeAlsEigentuemer(alice, alle, 'trotzdem')

    expect(await lies(bob, alle, stand - 1)).toEqual({ texte: ['trotzdem'], unlesbar: 0 })
  })

  it('zieht Senden und Lesen in die Kennung aus dem Geheimnis um', async () => {
    // Der Umzug selbst: `gruppenZiele` ist die eine Stelle, die ihn kennt.
    await sendeAlsEigentuemer(alice, alle, 'erste')
    const geheimnis = (await alice.ablage.liesGeheimnis(GRUPPE))!.geheimnis
    const neu = await mailboxAusGeheimnis(geheimnis)

    aktiviere(alice)
    const ziele = await gruppenZiele(GRUPPE, MAILBOX)

    expect(ziele.senden).toBe(neu)
    // Beide, und die neue zuerst: der Bestand liegt noch in der alten, und die
    // Mitglieder ohne Geheimnis senden weiter dorthin.
    expect(ziele.lesen).toEqual([neu, MAILBOX])
    expect(ziele.nachweis).toBe(await nachweisAusGeheimnis(geheimnis))
    // Und wohin die alte Kennung zeigt — das braucht das Löschen, das nur sie hat.
    expect(umgezogeneMailbox(MAILBOX)).toBe(neu)
  })

  it('bleibt bei der alten Kennung, solange es kein Geheimnis gibt', async () => {
    // Bestandsgruppe, oder das Geheimnis ist noch unterwegs. Ein Umzug auf
    // Verdacht wäre eine Mailbox, die niemand sonst liest.
    aktiviere(bob)
    const ziele = await gruppenZiele(GRUPPE, MAILBOX)

    expect(ziele).toEqual({ senden: MAILBOX, lesen: [MAILBOX], nachweis: null })
    expect(umgezogeneMailbox(MAILBOX)).toBeNull()
  })
})

// ==========================================
// Die Geräte-Mailbox
// ==========================================

describe('Zustellung über die Geräte-Mailbox', () => {
  let alice: TestGeraet
  let bob: TestGeraet
  let alle: number[]

  /** Öffnet einen Umschlag für dieses Gerät — sonst wirft es, wie im Betrieb. */
  const entsiegleFuer = (g: TestGeraet) => async (umschlag: string) => {
    const klartext = oeffneHybrid(g, umschlag)
    if (klartext === null) throw new Error('nicht für dieses Gerät')
    return klartext
  }

  beforeEach(() => {
    verzeichnis.clear()
    mailbox = []
    naechsteId = 1
    alice = geraet(ALICE, 'alice-laptop')
    bob = geraet(BOB, 'bob-handy')
    alle = [ALICE, BOB]
    serverMitglieder = [...alle]
    relaisKaputt = false
    registrierungen.length = 0
    leereMailboxNachweise()
    leereGeraeteStand()
  })

  it('legt keinen Steuerumschlag mehr in die Gruppenmailbox', async () => {
    /*
     * Die Zusage, an der die ganze Scheibe hängt.
     *
     * Solange der Schlüssel durch die Gruppenmailbox läuft, lässt sie sich
     * nicht verschliessen — der Schlüssel läge hinter dem Schloss, das er
     * aufsperren soll. Und das Lesefenster verhungerte: 99 von 100 Umschlägen
     * waren Zustellungen für fremde Geräte.
     */
    await sendeAlsEigentuemer(alice, alle, 'eine Nachricht')

    const inDerGruppe = mailbox.filter((u) => u.mailboxId === MAILBOX)
    expect(inDerGruppe.every((u) => u.control_type === null)).toBe(true)
    expect(inDerGruppe).toHaveLength(1)

    // Und das Gegenstück: die Zustellung liegt bei Bob, nicht bei Alice.
    const beiBob = mailbox.filter((u) => u.mailboxId === 'dev:2')
    expect(beiBob).toHaveLength(1)
    expect(beiBob[0].control_type).toBe('group_key')
  })

  it('holt den Schlüssel, ohne die Gruppe zu kennen', async () => {
    // Der Bootstrap: Bobs Gerät weiss von der Gruppe nichts — keine groupId,
    // keine Mitgliederliste, kein Kontext. Der Umschlag bringt alles mit.
    await sendeAlsEigentuemer(alice, alle, 'für Bob')

    aktiviere(bob)
    expect(await bob.ablage.liesAktuellen(GRUPPE)).toBeNull()

    const verarbeitet = await holeGeraeteSteuerung(BOB, entsiegleFuer(bob))

    expect(verarbeitet).toBe(1)
    expect(await bob.ablage.liesAktuellen(GRUPPE)).not.toBeNull()
    expect((await bob.ablage.liesGeheimnis(GRUPPE))?.geheimnis).toBe(
      (await alice.ablage.liesGeheimnis(GRUPPE))?.geheimnis,
    )
  })

  it('liest jeden Umschlag nur einmal', async () => {
    // Ohne Lesestand liefe jeder Durchlauf über den ganzen Bestand. Das ist
    // nicht nur langsam: `beantworteAnfrage` würde jede Nachfrage erneut
    // beantworten und dabei neue Umschläge erzeugen.
    await sendeAlsEigentuemer(alice, alle, 'einmal')

    aktiviere(bob)
    expect(await holeGeraeteSteuerung(BOB, entsiegleFuer(bob))).toBe(1)
    expect(await holeGeraeteSteuerung(BOB, entsiegleFuer(bob))).toBe(0)
  })

  it('geht über fremden Inhalt derselben Mailbox hinweg', async () => {
    /*
     * Die Geräte-Mailbox gehört nicht dem Messenger allein — Notizen und
     * Kalender legen dort ihre Schlüssel ab. Deren Umschläge sind kein JSON
     * mit `groupId`, und ein Fehler daran dürfte den Schlüssel dahinter nicht
     * aufhalten.
     */
    mailbox.push({
      id: naechsteId++,
      mailboxId: 'dev:2',
      ciphertext_envelope: `${HYBRID}pub-bob-handy.${Buffer.from('kein JSON', 'utf-8').toString('base64')}`,
      client_uuid: null,
      control_type: 'notes_key',
    })
    await sendeAlsEigentuemer(alice, alle, 'dahinter')

    aktiviere(bob)
    expect(await holeGeraeteSteuerung(BOB, entsiegleFuer(bob))).toBe(1)
    expect(await bob.ablage.liesAktuellen(GRUPPE)).not.toBeNull()
  })

  it('macht aus einem eingehenden Umschlag keinen Eigentümer', async () => {
    /*
     * Der Missbrauchsfall: wer über die Geräte-Mailbox etwas bekommt, darf
     * daraus keine Rechte ableiten. Ein erzeugtes Geheimnis wäre das
     * schlimmste — es spaltete die Gruppe in zwei Mailboxen.
     *
     * Geprüft wird über die Wirkung: Bob bekommt einen Schlüssel **ohne**
     * Geheimnis. Legte der Lesepfad ihm dabei den Eigentümerrang bei, stünde
     * danach eines in seiner Ablage.
     */
    // Alice sendet *nicht* als Eigentümerin: ihr Schlüssel geht raus, ein
    // Geheimnis entsteht dabei nicht.
    await sende(alice, alle, 'ohne Geheimnis')
    expect(await alice.ablage.liesGeheimnis(GRUPPE)).toBeNull()

    aktiviere(bob)
    expect(await holeGeraeteSteuerung(BOB, entsiegleFuer(bob))).toBe(1)

    expect(await bob.ablage.liesAktuellen(GRUPPE)).not.toBeNull()
    expect(await bob.ablage.liesGeheimnis(GRUPPE)).toBeNull()
  })

  it('versteht Altbestand aus der Gruppenmailbox weiter', async () => {
    // Was vor 09/2026 zugestellt wurde, liegt dort. Ginge es beim Umstieg
    // verloren, verlöre jedes Gerät seinen laufenden Schlüssel.
    await sende(alice, alle, 'aus der alten Zeit')
    // Die Zustellung dorthin zurücklegen, wo sie vor 09/2026 lag.
    for (const u of mailbox) {
      if (u.mailboxId === 'dev:2') u.mailboxId = MAILBOX
    }

    expect(await lies(bob, alle)).toEqual({ texte: ['aus der alten Zeit'], unlesbar: 0 })
  })
})
