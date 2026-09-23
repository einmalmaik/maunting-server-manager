// @vitest-environment node
/**
 * Der Ratchet-Pfad im Messenger, geprüft an zwei Konten mit mehreren Geräten.
 *
 * Der Hybridumschlag ist hier ein Stellvertreter: er versiegelt gegen einen
 * benannten Schlüssel und öffnet nur mit demselben, mehr braucht dieser Test von
 * ihm nicht. Dass die echte Fassung RSA-OAEP-4096 ist, prüft
 * `e2eeCrypto.test.ts`; hier ginge sonst die meiste Laufzeit für
 * Schlüsselerzeugung drauf und der eigentliche Gegenstand unter.
 *
 * Node-Umgebung, nicht jsdom — DIS prüft Eingaben mit `instanceof Uint8Array`.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

// ---- Stellvertreter für den Hybridumschlag -------------------------------
vi.mock('./e2eeCrypto', () => ({
  encryptE2eeHybrid: async (nachricht: string, empfaengerOeffentlich: string) =>
    `sv-e2ee-hybrid-v1:${empfaengerOeffentlich}.${Buffer.from(nachricht, 'utf-8').toString('base64')}`,
}))

// ---- Stellvertreter für die Geräteverwaltung ----------------------------
//
// Nur zwei Dinge sind hier erfunden: welches Gerät gerade „dieses" ist, und
// was der Server auf die Frage nach einem Verzeichnis antwortet. Die Prüfung
// der Aufbau-Unterschrift läuft durch das echte `e2eeGeraet` — mit echten
// ECDSA-Paaren. Ein Stellvertreter für diese Prüfung bewiese nur, dass Test
// und Code dieselbe Annahme teilen.
interface TestGeraet {
  kennung: string
  paar: { publicKeyJwk: string; privateKeyJwk: string }
  signaturPaar: SignaturPaar
  konto: number
  ablage: RatchetAblage
}

let aktuell: TestGeraet
/** Jeder Aufruf von `geraetVeroeffentlichen`, mit seinen Argumenten. */
const anmeldungen: unknown[][] = []
const verzeichnis = new Map<
  number,
  { device_id: string; public_key: string; signing_public_key: string; label: string }[]
>()
/** Der Server ist nicht zu erreichen, solange das hier gesetzt ist. */
const netz = { aus: false, veroeffentlichenScheitert: false }

vi.mock('@/api/social', () => ({
  getE2eeGeraete: async (uid: number) => {
    if (netz.aus) throw new Error('Netzwerk weg')
    return [...(verzeichnis.get(uid) ?? [])]
  },
  putEigenesGeraet: async () => ({}),
}))

vi.mock('./e2eeGeraet', async (importOriginal) => {
  const echt = await importOriginal<typeof import('./e2eeGeraet')>()
  return {
    ...echt,
    eigenesGeraet: async () => aktuell,
    geraetVeroeffentlichen: async (...argumente: unknown[]) => {
      anmeldungen.push(argumente)
      if (netz.veroeffentlichenScheitert) throw new Error('Anmeldung gescheitert')
      return aktuell
    },
  }
})

import { erzeugeSignaturPaar, type SignaturPaar } from './absenderSignatur'
import { clearGeraeteMemory, geraeteVon, verlangeGeraeteVon } from './e2eeGeraet'
import { setzeAblageFuerTest, type RatchetAblage } from './ratchetSpeicher'
import {
  DR_PREFIX,
  DrGeraetNichtEingetragenError,
  DrZustellungFehlgeschlagenError,
  baueZustellungen,
  drUrheber,
  liesDrUmschlag,
  logischeUuid,
  verarbeiteBootstrap,
  verwirfDrSitzung,
  type DrKontext,
  type KlartextAblage,
} from './ratchetSitzung'

const ALICE = 1
const BOB = 2

function neueAblage(): RatchetAblage {
  const daten = new Map<string, string>()
  return {
    async lies(id) {
      return daten.get(id) ?? null
    },
    async schreibe(id, zustand) {
      daten.set(id, zustand)
    },
    async loesche(id) {
      daten.delete(id)
    },
    async alleIds() {
      return [...daten.keys()]
    },
  }
}

/**
 * Legt ein Gerät an und trägt es ins Verzeichnis seines Kontos ein.
 *
 * `altbestand` steht für ein Gerät aus der Zeit vor der Unterschrift: es hat
 * ein Signaturpaar (jeder heutige Stand erzeugt eines), aber das Verzeichnis
 * führt es nicht.
 */
async function geraet(
  konto: number,
  name: string,
  { altbestand = false }: { altbestand?: boolean } = {},
): Promise<TestGeraet> {
  const kennung = name.padEnd(32, '0')
  const g: TestGeraet = {
    kennung,
    paar: { publicKeyJwk: `pub-${name}`, privateKeyJwk: `priv-${name}` },
    signaturPaar: await erzeugeSignaturPaar(),
    konto,
    ablage: neueAblage(),
  }
  const liste = verzeichnis.get(konto) ?? []
  liste.push({
    device_id: kennung,
    public_key: g.paar.publicKeyJwk,
    signing_public_key: altbestand ? '' : g.signaturPaar.publicKeyJwk,
    label: name,
  })
  verzeichnis.set(konto, liste)
  return g
}

/** Versetzt den Prozess in die Rolle eines Geräts: eigene Identität, eigene Ablage. */
function aktiviere(g: TestGeraet): void {
  aktuell = g
  setzeAblageFuerTest(g.ablage)
}

/** Holt den Klartext aus dem Stellvertreter-Hybridumschlag. */
function oeffneHybrid(umschlag: string): string {
  const rumpf = umschlag.slice('sv-e2ee-hybrid-v1:'.length)
  return Buffer.from(rumpf.slice(rumpf.indexOf('.') + 1), 'base64').toString('utf-8')
}

const kontextVon = (eigene: number, peer: number): DrKontext => ({ eigeneId: eigene, peerId: peer })

/**
 * Das Klartextfach eines Umschlags, wie `useKonversation` es führt: je
 * Umschlagkennung eines, gelesen und geschrieben im Sitzungsschloss.
 */
function fach(): KlartextAblage {
  let inhalt: string | null = null
  return {
    async lies() {
      return inhalt
    },
    async lege(text) {
      inhalt = text
    },
  }
}

/** Ein Fach ohne Gedächtnis — für Fälle, in denen die Ablage nicht zur Sache gehört. */
const verwerfen: KlartextAblage = { lies: async () => null, lege: async () => {} }

describe('ratchetSitzung', () => {
  let alice: TestGeraet
  let bob: TestGeraet

  beforeEach(async () => {
    verzeichnis.clear()
    // Der Zwischenspeicher des Verzeichnisses lebt im Modul und damit über
    // alle Tests hinweg — ohne das hier sähe jeder Test die Geräte des vorigen.
    clearGeraeteMemory()
    netz.aus = false
    netz.veroeffentlichenScheitert = false
    anmeldungen.length = 0
    alice = await geraet(ALICE, 'alice-laptop')
    bob = await geraet(BOB, 'bob-handy')
  })

  /** Stellt eine Zustellung zu: erst der Aufbau, dann die Nachricht. */
  async function zustellen(
    empfaenger: TestGeraet,
    kontext: DrKontext,
    z: { bootstrap: string | null; nachricht: string },
    klartext: KlartextAblage = verwerfen,
  ) {
    aktiviere(empfaenger)
    if (z.bootstrap) {
      await verarbeiteBootstrap(kontext, oeffneHybrid(z.bootstrap))
    }
    return liesDrUmschlag(kontext, z.nachricht, klartext)
  }

  it('trägt eine Nachricht über einen frisch aufgebauten Faden', async () => {
    aktiviere(alice)
    const [z] = await baueZustellungen(kontextVon(ALICE, BOB), 'Kommst du heute?', 'uuid-1')

    expect(z.bootstrap).not.toBeNull()
    expect(z.nachricht.startsWith(DR_PREFIX)).toBe(true)
    // Der Rumpf ist base64 und enthält nichts Lesbares aus der Nachricht.
    expect(z.nachricht).not.toContain('Kommst')
    expect(z.nachricht).not.toContain('ciphertext')

    const gelesen = await zustellen(bob, kontextVon(BOB, ALICE), z)
    expect(gelesen).toEqual({
      art: 'klartext',
      text: 'Kommst du heute?',
      vonKonto: ALICE,
      vonGeraet: alice.kennung,
    })
  })

  it('hält zwei Konten in einem Browser auseinander', async () => {
    // Der gemeldete Fehler. Zwei Menschen melden sich nacheinander in
    // demselben Browser an und teilen sich damit eine IndexedDB. Solange die
    // Sitzungskennung nur die Gegenstelle nannte, griffen beide auf denselben
    // Platz: wer als zweiter sendete, schaltete den Ratchet des ersten weiter,
    // und der Empfänger las danach von jeder Nachricht des ersten nur noch
    // „Die Sicherheitssitzung wurde neu aufgebaut".
    const BERT = 3
    verzeichnis.clear()
    const geteilteAblage = neueAblage()
    const annaBrowser = { ...(await geraet(ALICE, 'anna-browser')), ablage: geteilteAblage }
    const bertBrowser = { ...(await geraet(BERT, 'bert-browser')), ablage: geteilteAblage }
    const empfaenger = await geraet(BOB, 'bob-handy')

    aktiviere(annaBrowser)
    const [vonAnna] = await baueZustellungen(kontextVon(ALICE, BOB), 'von Anna', 'u1')
    expect((await zustellen(empfaenger, kontextVon(BOB, ALICE), vonAnna)).art).toBe('klartext')

    aktiviere(bertBrowser)
    const [vonBert] = await baueZustellungen(kontextVon(BERT, BOB), 'von Bert', 'u2')
    expect((await zustellen(empfaenger, kontextVon(BOB, BERT), vonBert)).art).toBe('klartext')

    // Und jetzt wieder Anna, aus derselben Ablage heraus.
    aktiviere(annaBrowser)
    const [nochmalAnna] = await baueZustellungen(kontextVon(ALICE, BOB), 'wieder Anna', 'u3')
    const gelesen = await zustellen(empfaenger, kontextVon(BOB, ALICE), nochmalAnna)
    expect(gelesen).toMatchObject({ art: 'klartext', text: 'wieder Anna' })
  })

  it('führt das Gespräch in beide Richtungen fort', async () => {
    const kA = kontextVon(ALICE, BOB)
    const kB = kontextVon(BOB, ALICE)

    aktiviere(alice)
    const [erste] = await baueZustellungen(kA, 'eins', 'u1')
    expect((await zustellen(bob, kB, erste)).art).toBe('klartext')

    // Bob antwortet. Erst ab hier hat er eine eigene Sendekette, und Alices
    // Sitzung muss den Kettenwechsel mitmachen.
    aktiviere(bob)
    const [antwort] = await baueZustellungen(kB, 'zwei', 'u2')
    expect(antwort.bootstrap).toBeNull()
    expect(await zustellen(alice, kA, antwort)).toMatchObject({ art: 'klartext', text: 'zwei' })

    aktiviere(alice)
    const [dritte] = await baueZustellungen(kA, 'drei', 'u3')
    expect(await zustellen(bob, kB, dritte)).toMatchObject({ art: 'klartext', text: 'drei' })

    aktiviere(bob)
    const [vierte] = await baueZustellungen(kB, 'vier', 'u4')
    expect(await zustellen(alice, kA, vierte)).toMatchObject({ art: 'klartext', text: 'vier' })
  })

  it('trägt Bytes unverändert durch, auch die unangenehmen', async () => {
    const inhalt = JSON.stringify({
      text: 'Grüße aus Zürich 🎉\u0000\u001b[31m',
      anhang: 'a'.repeat(5000),
    })
    aktiviere(alice)
    const [z] = await baueZustellungen(kontextVon(ALICE, BOB), inhalt, 'u1')
    const gelesen = await zustellen(bob, kontextVon(BOB, ALICE), z)
    expect(gelesen).toMatchObject({ art: 'klartext', text: inhalt })
  })

  it('fächert an jedes Gerät der Gegenstelle auf', async () => {
    const bobTablet = await geraet(BOB, 'bob-tablet')

    aktiviere(alice)
    const zustellungen = await baueZustellungen(kontextVon(ALICE, BOB), 'an alle', 'u1')
    expect(zustellungen).toHaveLength(2)
    expect(zustellungen.map((z) => z.zielGeraet).sort()).toEqual(
      [bob.kennung, bobTablet.kennung].sort(),
    )

    const fuerHandy = zustellungen.find((z) => z.zielGeraet === bob.kennung)!
    const fuerTablet = zustellungen.find((z) => z.zielGeraet === bobTablet.kennung)!
    const kB = kontextVon(BOB, ALICE)

    expect(await zustellen(bob, kB, fuerHandy)).toMatchObject({ art: 'klartext', text: 'an alle' })
    expect(await zustellen(bobTablet, kB, fuerTablet)).toMatchObject({
      art: 'klartext',
      text: 'an alle',
    })

    // Die Kopie des jeweils anderen Geräts ist kein Fehler und keine
    // „verschlüsselte Nachricht", sondern schlicht nicht gemeint.
    aktiviere(bob)
    expect(await liesDrUmschlag(kB, fuerTablet.nachricht, verwerfen)).toEqual({ art: 'fremd' })
  })

  it('schickt eine Kopie an das eigene Zweitgerät, aber nicht an sich selbst', async () => {
    const alicePc = await geraet(ALICE, 'alice-pc')

    aktiviere(alice)
    const zustellungen = await baueZustellungen(kontextVon(ALICE, BOB), 'auch für mich', 'u1')
    expect(zustellungen.map((z) => z.zielGeraet).sort()).toEqual(
      [bob.kennung, alicePc.kennung].sort(),
    )
    expect(zustellungen.some((z) => z.zielGeraet === alice.kennung)).toBe(false)

    const fuerPc = zustellungen.find((z) => z.zielGeraet === alicePc.kennung)!
    // Das Zweitgerät steht im selben Gespräch: sein Gegenüber ist Bob.
    const gelesen = await zustellen(alicePc, kontextVon(ALICE, BOB), fuerPc)
    expect(gelesen).toMatchObject({ art: 'klartext', text: 'auch für mich', vonKonto: ALICE })
  })

  it('erkennt die eigene Nachricht und versucht gar nicht erst zu öffnen', async () => {
    aktiviere(alice)
    const [z] = await baueZustellungen(kontextVon(ALICE, BOB), 'hallo', 'u1')
    // Alice sieht ihren eigenen Umschlag beim nächsten Abruf wieder. Der
    // Absender kann eine Ratchet-Nachricht nicht öffnen — das ist der Sinn der
    // Sache, kein Fehler, und darf nie als Fehler aussehen.
    expect(await liesDrUmschlag(kontextVon(ALICE, BOB), z.nachricht, verwerfen)).toEqual({
      art: 'eigen',
    })
  })

  it('scheitert geschlossen, wenn ein Byte gekippt wurde', async () => {
    const kB = kontextVon(BOB, ALICE)
    aktiviere(alice)
    const [erste] = await baueZustellungen(kontextVon(ALICE, BOB), 'echt', 'u1')
    const [zweite] = await baueZustellungen(kontextVon(ALICE, BOB), 'auch echt', 'u2')

    aktiviere(bob)
    await verarbeiteBootstrap(kB, oeffneHybrid(erste.bootstrap!))

    const teile = erste.nachricht.split('.')
    const roh = Buffer.from(teile[3], 'base64')
    roh[roh.length - 5] ^= 0x01
    const gefaelscht = [teile[0], teile[1], teile[2], roh.toString('base64')].join('.')

    const abgelegt: string[] = []
    const gescheitert = await liesDrUmschlag(kB, gefaelscht, {
      lies: async () => null,
      lege: async (t) => {
        abgelegt.push(t)
      },
    })
    // Hier dagegen schon: ein gescheiterter Tag heißt, dass die Sitzung nicht
    // mehr trägt oder jemand daran gedreht hat.
    expect(gescheitert.art).toBe('bruch')
    // Nichts darf durchgerutscht sein.
    expect(abgelegt).toEqual([])

    // Und die Sitzung darf die Fälschung nicht verklemmt haben: die echte
    // Nachricht danach muss weiterhin aufgehen.
    expect(await liesDrUmschlag(kB, zweite.nachricht, verwerfen)).toMatchObject({
      art: 'klartext',
      text: 'auch echt',
    })
  })

  it('meldet Bruch, wenn der abgelegte Zustand nicht mehr lesbar ist', async () => {
    const kB = kontextVon(BOB, ALICE)
    aktiviere(alice)
    const [z] = await baueZustellungen(kontextVon(ALICE, BOB), 'hallo', 'u1')

    aktiviere(bob)
    await verarbeiteBootstrap(kB, oeffneHybrid(z.bootstrap!))

    // Ein beschädigter Zustand ist ein Sitzungsproblem, kein vorübergehendes.
    // Als 'fehler' gemeldet bliebe das Gespräch ohne sichtbaren Grund stehen
    // und jeder Abruf liefe erneut in denselben Fehler.
    const echt = bob.ablage.lies
    bob.ablage.lies = async (k) => (k.endsWith(alice.kennung) ? 'sv-dr-state-v1:unsinn' : echt(k))
    const gelesen = await liesDrUmschlag(kB, z.nachricht, verwerfen)
    bob.ablage.lies = echt
    expect(gelesen.art).toBe('bruch')
  })

  it('meldet Bruch statt Stille, wenn die Sitzung fehlt', async () => {
    const kB = kontextVon(BOB, ALICE)
    aktiviere(alice)
    const [z] = await baueZustellungen(kontextVon(ALICE, BOB), 'hallo', 'u1')

    // Bobs Gerät hat den Aufbau nie gesehen — etwa weil es damals nicht
    // existierte oder seine Ablage verloren hat.
    aktiviere(bob)
    const gelesen = await liesDrUmschlag(kB, z.nachricht, verwerfen)
    expect(gelesen).toMatchObject({ art: 'bruch', vonKonto: ALICE, vonGeraet: alice.kennung })
  })

  it('verliert keine Nachricht, wenn das Ablegen scheitert', async () => {
    const kB = kontextVon(BOB, ALICE)
    aktiviere(alice)
    const [z] = await baueZustellungen(kontextVon(ALICE, BOB), 'wichtig', 'u1')

    aktiviere(bob)
    await verarbeiteBootstrap(kB, oeffneHybrid(z.bootstrap!))

    const gescheitert = await liesDrUmschlag(kB, z.nachricht, {
      lies: async () => null,
      lege: async () => {
        throw new Error('IndexedDB voll')
      },
    })
    // Ausdrücklich **kein** Sitzungsbruch. Eine volle Platte ist vorübergehend;
    // würde sie als Bruch gemeldet, risse ein einziger Ablagefehler jeden
    // Gesprächsfaden dieses Geräts ab und setzte sie alle neu auf.
    expect(gescheitert.art).toBe('fehler')

    // Der Nachrichtenschlüssel darf dabei nicht verbraucht worden sein.
    // Andernfalls wäre diese Nachricht endgültig weg, ohne dass es jemand
    // merkt — der teuerste aller stillen Fehler.
    const zweiterVersuch = await liesDrUmschlag(kB, z.nachricht, verwerfen)
    expect(zweiterVersuch).toMatchObject({ art: 'klartext', text: 'wichtig' })
  })

  it('setzt eine Sitzung neu auf, wenn die Gegenstelle ihren Zustand verloren hat', async () => {
    const kA = kontextVon(ALICE, BOB)
    const kB = kontextVon(BOB, ALICE)

    aktiviere(alice)
    const [erste] = await baueZustellungen(kA, 'vorher', 'u1')
    await zustellen(bob, kB, erste)

    // Alice installiert neu: ihre Sitzung ist fort, Bobs nicht.
    aktiviere(alice)
    await verwirfDrSitzung(BOB, bob.kennung)
    const [neue] = await baueZustellungen(kA, 'nachher', 'u2')
    expect(neue.bootstrap).not.toBeNull()

    aktiviere(bob)
    const aufbau = await verarbeiteBootstrap(kB, oeffneHybrid(neue.bootstrap!))
    // `ersetzt` ist das Signal für die sichtbare Systemzeile im Verlauf. Eine
    // still neu aufgebaute Sitzung wäre genau das, was ein Angreifer will.
    expect(aufbau).toMatchObject({ istAufbau: true, ersetzt: true, vonGeraet: alice.kennung })

    expect(await liesDrUmschlag(kB, neue.nachricht, verwerfen)).toMatchObject({
      art: 'klartext',
      text: 'nachher',
    })
  })

  it('lässt einen alten unlesbaren Umschlag nicht jede neue Sitzung töten', async () => {
    // Vom Betreiber gemeldet: die Systemzeile zum Sitzungsbruch kam bei fast
    // jeder Nachricht, und seine Nachrichten kamen nicht mehr an.
    //
    // Ein Umschlag bleibt in der Mailbox liegen, und der Lesepfad holt bei
    // jedem Abruf das ganze Fenster. Liess er sich nicht öffnen, wurde er
    // jedes Mal erneut als Bruch gewertet — und ein Bruch wirft die Sitzung
    // weg. Ein einziger alter Umschlag zerstörte so bei jedem Öffnen des
    // Gesprächs die frisch aufgebaute Sitzung, was den nächsten Aufbau nötig
    // machte, was drüben wieder ersetzte. Die Schleife hielt sich selbst am
    // Leben.
    const kA = kontextVon(ALICE, BOB)
    const kB = kontextVon(BOB, ALICE)

    // Ein Umschlag aus einer Sitzung, die es nicht mehr gibt.
    aktiviere(alice)
    const [alt] = await baueZustellungen(kA, 'aus einem früheren Leben', 'u-alt')
    aktiviere(bob)
    expect((await liesDrUmschlag(kB, alt.nachricht, verwerfen)).art).toBe('bruch')

    // Danach läuft ein frisches Gespräch an.
    aktiviere(alice)
    await verwirfDrSitzung(BOB, bob.kennung)
    const [neu] = await baueZustellungen(kA, 'frisch aufgebaut', 'u-neu')
    expect(await zustellen(bob, kB, neu)).toMatchObject({ art: 'klartext', text: 'frisch aufgebaut' })

    // Der alte Umschlag liegt weiter in der Mailbox und wird wieder gelesen.
    // Er bleibt unlesbar, darf die laufende Sitzung aber nicht mehr kosten.
    aktiviere(bob)
    expect((await liesDrUmschlag(kB, alt.nachricht, verwerfen)).art).toBe('beurteilt')

    // Der Beweis, dass die Sitzung noch steht: die nächste Nachricht kommt an.
    aktiviere(alice)
    const [weiter] = await baueZustellungen(kA, 'und es geht weiter', 'u-weiter')
    expect(weiter.bootstrap).toBeNull()
    aktiviere(bob)
    expect(await liesDrUmschlag(kB, weiter.nachricht, verwerfen)).toMatchObject({
      art: 'klartext',
      text: 'und es geht weiter',
    })
  })

  it('wendet denselben Sitzungsaufbau kein zweites Mal an', async () => {
    // Am laufenden System gefunden, in einem brandneuen Gespräch nach einer
    // Freundschaftsanfrage. Ein Aufbau bleibt als Umschlag in der Mailbox
    // liegen, und der Lesepfad holt das ganze Fenster bei jedem Abruf neu —
    // gespeichert wird nur Klartext, also nie ein Aufbau. Ohne Gedächtnis
    // baute derselbe Aufbau die Sitzung bei jedem Durchlauf erneut auf: der
    // Ratchet fiel auf Anfang zurück, und in den Verlauf schrieb sich
    // „Die Sicherheitssitzung mit diesem Gerät wurde neu aufgebaut", obwohl
    // niemand etwas neu aufgebaut hatte.
    const kA = kontextVon(ALICE, BOB)
    const kB = kontextVon(BOB, ALICE)

    aktiviere(alice)
    const [z] = await baueZustellungen(kA, 'Erste', 'u1')

    aktiviere(bob)
    expect(await verarbeiteBootstrap(kB, oeffneHybrid(z.bootstrap!))).toMatchObject({
      istAufbau: true,
      ersetzt: false,
    })
    await liesDrUmschlag(kB, z.nachricht, verwerfen)

    // Derselbe Umschlag beim nächsten Abruf. Kein Neuaufbau, also auch keine
    // Meldung darüber.
    const nochmal = await verarbeiteBootstrap(kB, oeffneHybrid(z.bootstrap!))
    expect(nochmal.ersetzt).toBe(false)

    // Der eigentliche Schaden sitzt tiefer als die Zeile: ein zurückgesetzter
    // Zustand hat keine Sendekette mehr, und Bobs Antwort schleppte einen
    // neuen Aufbau mit — die Sitzung schaukelte sich bei jedem Abruf neu auf.
    const [antwort] = await baueZustellungen(kB, 'Zweite', 'u2')
    expect(antwort.bootstrap).toBeNull()
  })

  it('öffnet denselben Umschlag zweimal, ohne die Sitzung dafür zu töten', async () => {
    // Vom Betreiber gemeldet: jede gesendete Nachricht wurde beim Gegenüber zu
    // „Die Sicherheitssitzung mit diesem Gerät wurde neu aufgebaut."
    //
    // Der Messenger fragt die Mailbox aus fünf Quellen ab, und eine einzige
    // Nachricht löst mehrere davon gleichzeitig aus. Zwei überlappende
    // Durchläufe holten denselben Umschlag, und beide hielten ihn für
    // ungeöffnet, weil der erste seinen Klartext noch nicht abgelegt hatte. Der
    // zweite Versuch traf auf einen verbrauchten Nachrichtenschlüssel — von
    // aussen nicht von einer Fälschung zu unterscheiden. Die Sitzung flog weg,
    // der Verlauf meldete einen Bruch, und die Nachricht fehlte in genau dem
    // Durchlauf, der angezeigt wurde.
    const kB = kontextVon(BOB, ALICE)
    aktiviere(alice)
    const [erste] = await baueZustellungen(kontextVon(ALICE, BOB), 'hallo', 'u1')

    aktiviere(bob)
    await verarbeiteBootstrap(kB, oeffneHybrid(erste.bootstrap!))

    // Beide Durchläufe teilen sich das Fach dieses Umschlags — in der Anwendung
    // ist das die Ablage unter `[Mailbox, Umschlagkennung]`.
    const gemeinsam = fach()
    const [a, b] = await Promise.all([
      liesDrUmschlag(kB, erste.nachricht, gemeinsam),
      liesDrUmschlag(kB, erste.nachricht, gemeinsam),
    ])
    expect(a).toMatchObject({ art: 'klartext', text: 'hallo' })
    expect(b).toMatchObject({ art: 'klartext', text: 'hallo' })

    // Der Beweis, dass die Sitzung das überstanden hat: die nächste Nachricht
    // kommt ohne neuen Aufbau an.
    aktiviere(alice)
    const [zweite] = await baueZustellungen(kontextVon(ALICE, BOB), 'und weiter', 'u2')
    expect(zweite.bootstrap).toBeNull()
    aktiviere(bob)
    expect(await liesDrUmschlag(kB, zweite.nachricht, fach())).toMatchObject({
      art: 'klartext',
      text: 'und weiter',
    })
  })

  it('wendet einen Aufbau auch dann nur einmal an, wenn zwei Durchläufe ihn sehen', async () => {
    // Dieselbe Überlappung von der anderen Seite. Prüfen und Merken standen
    // ausserhalb des Sitzungsschlosses: beide Durchläufe fanden die Marke noch
    // nicht und bauten die Sitzung auf. Der zweite setzte damit einen Zustand
    // zurück, der schon Nachrichten getragen hatte — der Faden verlor seine
    // Sendekette, beide Seiten bauten neu auf, und im Verlauf erschien ein
    // Sitzungsbruch, den niemand ausgelöst hatte.
    aktiviere(alice)
    const [z] = await baueZustellungen(kontextVon(ALICE, BOB), 'hallo', 'u1')

    aktiviere(bob)
    const geschrieben: string[] = []
    const echt = bob.ablage.schreibe
    bob.ablage.schreibe = async (id, zustand) => {
      geschrieben.push(id)
      return echt(id, zustand)
    }

    const klartext = oeffneHybrid(z.bootstrap!)
    const beide = await Promise.all([
      verarbeiteBootstrap(kontextVon(BOB, ALICE), klartext),
      verarbeiteBootstrap(kontextVon(BOB, ALICE), klartext),
    ])
    bob.ablage.schreibe = echt

    // Genau ein Sitzungszustand geschrieben; der Rest sind Marken.
    expect(geschrieben.filter((id) => !id.startsWith('aufbau:'))).toHaveLength(1)
    // Und keiner der beiden meldet einen Ersatz: es gab keinen.
    expect(beide.map((b) => b.ersetzt)).toEqual([false, false])
  })

  it('bleibt nicht stumm, wenn ein zweiter Aufbau den arbeitenden Zustand ersetzt', async () => {
    // Am laufenden System gefunden. Ein Empfängerzustand aus `initReceiverState`
    // hat `sendingChainKey === null` und kann nicht senden; erst die erste
    // entschlüsselte Nachricht dreht den Ratchet. `verarbeiteBootstrap` ersetzt
    // aber jede bestehende Sitzung durch genau so einen frischen Zustand. Trifft
    // ein zweiter Aufbau ein, nachdem Bob schon gelesen hat, verliert er seine
    // Sendekette — und kam aus eigener Kraft nie wieder heraus: `encryptMessage`
    // warf, das leere `catch` schluckte es, `baueZustellungen` lieferte eine
    // leere Liste, und der Benutzer las „Alice ist mit keinem Gerät angemeldet".
    // Falsch in jedem einzelnen Teil der Aussage.
    const kA = kontextVon(ALICE, BOB)
    const kB = kontextVon(BOB, ALICE)

    aktiviere(alice)
    const [erste] = await baueZustellungen(kA, 'hallo', 'u1')
    await zustellen(bob, kB, erste)

    // Alice verliert ihren Zustand und baut neu auf. Ihr zweiter Aufbau ist ein
    // anderer als der erste — dass derselbe nichts anrichtet, prüft der Test
    // darüber. Bob wendet ihn an, kommt aber vor der zugehörigen Nachricht zum
    // Schreiben: genau dann steht er mit einem Zustand ohne Sendekette da.
    aktiviere(alice)
    await verwirfDrSitzung(BOB, bob.kennung)
    const [zweite] = await baueZustellungen(kA, 'bist du noch da?', 'u2')

    aktiviere(bob)
    expect(await verarbeiteBootstrap(kB, oeffneHybrid(zweite.bootstrap!))).toMatchObject({
      ersetzt: true,
    })

    const [antwort] = await baueZustellungen(kB, 'ja, gegen sieben', 'u3')
    // Bob setzt neu auf, statt zu verstummen: mit Aufbau davor, sonst stünde
    // Alice vor einer Nachricht, zu der sie keine Sitzung hat.
    expect(antwort.bootstrap).not.toBeNull()

    expect(await zustellen(alice, kA, antwort)).toMatchObject({
      art: 'klartext',
      text: 'ja, gegen sieben',
    })
  })

  it('meldet einen gescheiterten Versand, statt ihn wie „kein Gerät" aussehen zu lassen', async () => {
    // Die Unterscheidung trägt den Fehlertext: „niemand angemeldet" ist eine
    // Aussage über die Gegenstelle, „liess sich nicht verschlüsseln" eine über
    // uns. Wer beides gleich behandelt, schickt den Benutzer in die falsche
    // Richtung — und bei einer leeren Rückgabe merkt es überhaupt niemand.
    aktiviere(alice)
    const kaputt: RatchetAblage = {
      ...alice.ablage,
      async schreibe() {
        throw new Error('Ablage voll')
      },
    }
    setzeAblageFuerTest(kaputt)

    await expect(baueZustellungen(kontextVon(ALICE, BOB), 'geht nicht', 'u1')).rejects.toThrow(
      DrZustellungFehlgeschlagenError
    )
  })

  it('lässt kein fremdes Gespräch die Sitzung mit Alice überschreiben', async () => {
    const EVE = 3
    const eve = await geraet(EVE, 'eve-geraet')
    const kAliceSeite = kontextVon(ALICE, BOB)
    const kBobMitAlice = kontextVon(BOB, ALICE)
    const kBobMitEve = kontextVon(BOB, EVE)

    // Bob und Alice sprechen bereits.
    aktiviere(alice)
    const [echt] = await baueZustellungen(kAliceSeite, 'von Alice', 'u1')
    await zustellen(bob, kBobMitAlice, echt)

    // Eve schreibt in ihre eigene Mailbox mit Bob — dorthin darf sie — und
    // behauptet dabei, Alices Gerät zu sein. Griffe der Aufbau durch, hätte Eve
    // Bobs Faden zu Alice gekapert, ohne je deren Mailbox berührt zu haben.
    aktiviere(eve)
    const [untergeschoben] = await baueZustellungen(kontextVon(EVE, BOB), 'nicht von Alice', 'u2')
    const gefaelscht = JSON.parse(oeffneHybrid(untergeschoben.bootstrap!))
    gefaelscht.vonKonto = ALICE
    gefaelscht.vonGeraet = alice.kennung

    aktiviere(bob)
    const abgewiesen = await verarbeiteBootstrap(kBobMitEve, JSON.stringify(gefaelscht))
    expect(abgewiesen).toMatchObject({ istAufbau: true, ersetzt: false })

    // Bobs Sitzung mit Alice muss unberührt weiterlaufen.
    aktiviere(alice)
    const [weitere] = await baueZustellungen(kAliceSeite, 'immer noch Alice', 'u3')
    aktiviere(bob)
    expect(await liesDrUmschlag(kBobMitAlice, weitere.nachricht, verwerfen)).toMatchObject({
      art: 'klartext',
      text: 'immer noch Alice',
    })
  })

  it('hält die Kennungen innerhalb der Feldgrenze und faltet sie wieder zusammen', async () => {
    aktiviere(alice)
    const basis = '123e4567-e89b-12d3-a456-426614174000'
    const [z] = await baueZustellungen(kontextVon(ALICE, BOB), 'hallo', basis)

    // `client_uuid` ist im Backend auf 64 Zeichen begrenzt.
    expect(z.clientUuid.length).toBeLessThanOrEqual(64)
    expect(z.bootstrapClientUuid.length).toBeLessThanOrEqual(64)
    expect(z.clientUuid).not.toBe(z.bootstrapClientUuid)

    // Alle Kopien derselben Nachricht müssen auf dieselbe logische Kennung
    // zurückfallen, sonst führen zwei Geräte zwei Verläufe.
    expect(logischeUuid(z.clientUuid)).toBe(basis)
    expect(logischeUuid(basis)).toBe(basis)
    expect(logischeUuid(null)).toBeUndefined()
  })

  it('überlebt einen Neustart auf beiden Seiten', async () => {
    const kA = kontextVon(ALICE, BOB)
    const kB = kontextVon(BOB, ALICE)

    aktiviere(alice)
    const [erste] = await baueZustellungen(kA, 'vor dem Neustart', 'u1')
    await zustellen(bob, kB, erste)

    // Nur die Ablagen überleben — die Fassung im Speicher ist fort. Genau das
    // passiert bei F5, beim Neustart der App und beim Neustart des Backends.
    setzeAblageFuerTest(null)
    setzeAblageFuerTest(alice.ablage)

    aktiviere(alice)
    const [zweite] = await baueZustellungen(kA, 'nach dem Neustart', 'u2')
    expect(zweite.bootstrap).toBeNull()
    expect(await zustellen(bob, kB, zweite)).toMatchObject({
      art: 'klartext',
      text: 'nach dem Neustart',
    })
  })

  it('erkennt Umschläge anderer Verfahren, ohne sie anzufassen', async () => {
    aktiviere(bob)
    const k = kontextVon(BOB, ALICE)
    expect(await liesDrUmschlag(k, 'sv-e2ee-hybrid-v1:abc.def', verwerfen)).toEqual({
      art: 'unbekannt',
    })
    expect(await liesDrUmschlag(k, `${DR_PREFIX}1.zuwenig.teile`, verwerfen)).toEqual({
      art: 'unbekannt',
    })
    expect(await liesDrUmschlag(k, 'Klartext', verwerfen)).toEqual({ art: 'unbekannt' })
  })

  it('erkennt Nicht-Aufbauten im Hybridklartext', async () => {
    aktiviere(bob)
    const k = kontextVon(BOB, ALICE)
    expect(await verarbeiteBootstrap(k, JSON.stringify({ type: 'read_receipt' }))).toEqual({
      istAufbau: false,
      ersetzt: false,
    })
    expect(await verarbeiteBootstrap(k, 'kein JSON')).toEqual({
      istAufbau: false,
      ersetzt: false,
    })
  })

  describe('Die Unterschrift am Sitzungsaufbau', () => {
    /** Der Klartext eines Aufbaus, zum Verbiegen. */
    async function aufbauVon(
      sender: TestGeraet,
      kontext: DrKontext,
      zielGeraet: string,
    ): Promise<Record<string, any>> {
      aktiviere(sender)
      const zustellungen = await baueZustellungen(kontext, 'egal', `u-${Math.random()}`)
      const z = zustellungen.find((x) => x.zielGeraet === zielGeraet)!
      return JSON.parse(oeffneHybrid(z.bootstrap!))
    }

    it('lässt das Gegenüber keine Sitzung als eigenes Zweitgerät unterschieben', async () => {
      // Der Befund vom 23.09.2026. Bob schreibt in die Mailbox, die er mit
      // Alice teilt — das darf er —, und behauptet im Aufbau, Alices zweites
      // Gerät zu sein. Das Konto gehört zu diesem Gespräch, also ging es
      // durch: Alices Laptop ersetzte seine Sitzung mit dem eigenen PC durch
      // eine, deren beide Enden Bob kannte. Seine Nachrichten standen danach
      // als „von dir" im Verlauf, und was der Laptop an den PC schickte — in
      // jedem Gespräch, denn diese Sitzung gilt für alle —, konnte er lesen.
      const alicePc = await geraet(ALICE, 'alice-pc')
      const kAlice = kontextVon(ALICE, BOB)

      // Die echte Sitzung zwischen Alices Geräten steht.
      aktiviere(alicePc)
      const vomPc = await baueZustellungen(kAlice, 'vom PC', 'u-pc')
      const anLaptop = vomPc.find((z) => z.zielGeraet === alice.kennung)!
      expect(await zustellen(alice, kAlice, anLaptop)).toMatchObject({ art: 'klartext' })

      // Bob baut einen Aufbau an Alices Laptop und gibt sich als ihr PC aus.
      const gefaelscht = await aufbauVon(bob, kontextVon(BOB, ALICE), alice.kennung)
      gefaelscht.vonKonto = ALICE
      gefaelscht.vonGeraet = alicePc.kennung

      aktiviere(alice)
      const ergebnis = await verarbeiteBootstrap(kAlice, JSON.stringify(gefaelscht))
      expect(ergebnis).toMatchObject({ istAufbau: true, ersetzt: false, abgelehnt: true })

      // Die echte Sitzung lebt weiter: die nächste Nachricht vom PC geht auf,
      // ohne neuen Aufbau.
      aktiviere(alicePc)
      const weiter = (await baueZustellungen(kAlice, 'immer noch der PC', 'u-pc2')).find(
        (z) => z.zielGeraet === alice.kennung,
      )!
      expect(weiter.bootstrap).toBeNull()
      aktiviere(alice)
      expect(await liesDrUmschlag(kAlice, weiter.nachricht, verwerfen)).toMatchObject({
        art: 'klartext',
        text: 'immer noch der PC',
        vonKonto: ALICE,
        vonGeraet: alicePc.kennung,
      })
    })

    it('weist einen Aufbau ohne Unterschrift ab, wenn das Konto unterschreiben kann', async () => {
      // Die Downgrade-Schranke. Ohne sie nähme ein Fälscher die Unterschrift
      // einfach weg und stünde wieder am Anfang.
      const ohne = await aufbauVon(alice, kontextVon(ALICE, BOB), bob.kennung)
      delete ohne.sig

      aktiviere(bob)
      expect(await verarbeiteBootstrap(kontextVon(BOB, ALICE), JSON.stringify(ohne))).toMatchObject({
        istAufbau: true,
        ersetzt: false,
        abgelehnt: true,
        vonKonto: ALICE,
        vonGeraet: alice.kennung,
      })
    })

    it('weist einen Aufbau ab, an dem nach dem Unterschreiben gedreht wurde', async () => {
      const aufbau = await aufbauVon(alice, kontextVon(ALICE, BOB), bob.kennung)
      // Ein anderes Geheimnis — wer es einsetzt, kennt die Sitzung.
      aufbau.geheimnis = Buffer.alloc(32, 7).toString('base64')

      aktiviere(bob)
      expect(await verarbeiteBootstrap(kontextVon(BOB, ALICE), JSON.stringify(aufbau))).toMatchObject({
        abgelehnt: true,
      })
    })

    it('meldet einen abgewiesenen Aufbau genau einmal', async () => {
      // Er bleibt in der Mailbox liegen und kommt bei jedem Abruf wieder. Jedes
      // Mal eine neue Meldung wäre Rauschen, das die echte übertönt.
      const aufbau = await aufbauVon(alice, kontextVon(ALICE, BOB), bob.kennung)
      delete aufbau.sig
      const klartext = JSON.stringify(aufbau)

      aktiviere(bob)
      expect((await verarbeiteBootstrap(kontextVon(BOB, ALICE), klartext)).abgelehnt).toBe(true)
      const nochmal = await verarbeiteBootstrap(kontextVon(BOB, ALICE), klartext)
      expect(nochmal.istAufbau).toBe(true)
      expect(nochmal.abgelehnt).toBeUndefined()
    })

    it('nimmt Altbestand ohne Unterschrift, solange das Konto nirgends unterschreibt', async () => {
      // Ein Konto, dessen Geräte alle noch vor der Unterschrift stehen, kann es
      // nicht besser. Es auszusperren hiesse, seine Gespräche zu beenden.
      verzeichnis.clear()
      clearGeraeteMemory()
      const altesGeraet = await geraet(ALICE, 'alice-alt', { altbestand: true })
      const empfaenger = await geraet(BOB, 'bob-neu')
      aktiviere(altesGeraet)
      const [z] = await baueZustellungen(kontextVon(ALICE, BOB), 'von früher', 'u-alt')
      const ohne = JSON.parse(oeffneHybrid(z.bootstrap!))
      delete ohne.sig

      aktiviere(empfaenger)
      expect(
        await verarbeiteBootstrap(kontextVon(BOB, ALICE), JSON.stringify(ohne)),
      ).toMatchObject({ istAufbau: true, ersetzt: false })
      expect(await liesDrUmschlag(kontextVon(BOB, ALICE), z.nachricht, verwerfen)).toMatchObject({
        art: 'klartext',
        text: 'von früher',
      })
    })

    it('entscheidet nichts, solange das Verzeichnis nicht antwortet', async () => {
      // Ein Netzfehler ist kein Nein. Abgewiesen und gemerkt wäre der Aufbau
      // für immer verloren, und mit ihm jede Nachricht, die auf ihm aufbaut.
      const kB = kontextVon(BOB, ALICE)
      aktiviere(alice)
      const [z] = await baueZustellungen(kontextVon(ALICE, BOB), 'trotz Funkloch', 'u-netz')

      clearGeraeteMemory()
      netz.aus = true
      aktiviere(bob)
      const aufbau = await verarbeiteBootstrap(kB, oeffneHybrid(z.bootstrap!))
      expect(aufbau).toMatchObject({ istAufbau: true, offen: true, vonKonto: ALICE })

      // Die Nachricht dahinter wird in diesem Durchlauf nicht angefasst ...
      const offen = new Set([`${ALICE}:${alice.kennung}`])
      const zurueckstellen = (konto: number, geraet: string) => offen.has(`${konto}:${geraet}`)
      expect(await liesDrUmschlag(kB, z.nachricht, verwerfen, { zurueckstellen })).toEqual({
        art: 'zurueckgestellt',
      })

      // ... und geht auf, sobald das Verzeichnis wieder antwortet.
      netz.aus = false
      expect(await verarbeiteBootstrap(kB, oeffneHybrid(z.bootstrap!))).toMatchObject({
        istAufbau: true,
        ersetzt: false,
      })
      expect(await liesDrUmschlag(kB, z.nachricht, verwerfen)).toMatchObject({
        art: 'klartext',
        text: 'trotz Funkloch',
      })
    })

    it('hält ein gerade erst angemeldetes Gerät nicht für eine Fälschung', async () => {
      // Bobs Liste von Alices Geräten ist wenige Sekunden alt, als Alices
      // neues Handy sich anmeldet und sofort schreibt. Ein Nein an dieser
      // Stelle wäre endgültig; ein „noch nicht" löst sich beim nächsten Abruf.
      const kB = kontextVon(BOB, ALICE)
      const neuesHandy = await geraet(ALICE, 'alice-handy')
      aktiviere(neuesHandy)
      const [z] = (await baueZustellungen(kontextVon(ALICE, BOB), 'mein neues Handy', 'u-neu')).filter(
        (x) => x.zielGeraet === bob.kennung,
      )

      // Bobs Liste stammt von kurz vor der Anmeldung. Im Test teilen sich alle
      // Geräte einen Zwischenspeicher, und das Handy hat seine eigene Liste
      // eben frisch geholt — Bobs wird deshalb von Hand auf den alten Stand
      // gebracht.
      const handyEintrag = verzeichnis.get(ALICE)!.find((g) => g.device_id === neuesHandy.kennung)!
      verzeichnis.set(ALICE, verzeichnis.get(ALICE)!.filter((g) => g !== handyEintrag))
      clearGeraeteMemory()
      aktiviere(bob)
      await baueZustellungen(kB, 'Bob füllt seinen Zwischenspeicher', 'u-vorher')
      verzeichnis.set(ALICE, [...verzeichnis.get(ALICE)!, handyEintrag])

      expect(await verarbeiteBootstrap(kB, oeffneHybrid(z.bootstrap!))).toMatchObject({ offen: true })

      // Eine halbe Minute später darf frisch gefragt werden.
      const echtesJetzt = Date.now
      Date.now = () => echtesJetzt() + 31_000
      try {
        expect(await verarbeiteBootstrap(kB, oeffneHybrid(z.bootstrap!))).toMatchObject({
          istAufbau: true,
          ersetzt: false,
        })
        expect((await verarbeiteBootstrap(kB, oeffneHybrid(z.bootstrap!))).abgelehnt).toBeUndefined()
      } finally {
        Date.now = echtesJetzt
      }
      expect(await liesDrUmschlag(kB, z.nachricht, verwerfen)).toMatchObject({
        art: 'klartext',
        text: 'mein neues Handy',
        vonGeraet: neuesHandy.kennung,
      })
    })

    it('sendet nicht, solange dieses Gerät nicht im Verzeichnis steht', async () => {
      // Drüben prüft jeder den Aufbau gegen das Verzeichnis. Ein Gerät, dessen
      // Anmeldung gescheitert ist, schickte sonst Aufbauten, die niemand prüfen
      // kann — hier sähe alles zugestellt aus, drüben käme nichts an.
      netz.veroeffentlichenScheitert = true
      aktiviere(alice)
      await expect(baueZustellungen(kontextVon(ALICE, BOB), 'hallo', 'u1')).rejects.toThrow(
        /Anmeldung gescheitert/,
      )
    })

    it('sendet nicht, wenn das Verzeichnis dieses Gerät nicht mehr führt', async () => {
      // In einem anderen Tab aus der Geräteliste entfernt, dieser hier blieb
      // offen. Die Anmeldung beim Start war längst erledigt und kostet danach
      // nichts mehr — seine Aufbauten gingen drüben als „unbekannt" ins Leere,
      // und hier sähe alles zugestellt aus. Sich still neu einzutragen nähme
      // dem Entfernen, was der Dialog verspricht; also scheitert das Senden.
      const zweites = await geraet(ALICE, 'alice-tablet')
      verzeichnis.set(ALICE, verzeichnis.get(ALICE)!.filter((g) => g.device_id !== alice.kennung))
      clearGeraeteMemory()
      aktiviere(alice)
      await expect(baueZustellungen(kontextVon(ALICE, BOB), 'hallo', 'u-weg')).rejects.toBeInstanceOf(
        DrGeraetNichtEingetragenError,
      )
      // Nichts angelegt: kein Zustand, der später ohne Aufbau weitersendete.
      expect(await alice.ablage.alleIds()).toEqual([])
      expect(anmeldungen).toHaveLength(1)

      // Das zweite Gerät steht drin und sendet wie immer — an Bob, und nicht
      // mehr an das entfernte Gerät.
      aktiviere(zweites)
      const vomTablet = await baueZustellungen(kontextVon(ALICE, BOB), 'vom Tablet', 'u-da')
      expect(vomTablet.map((z) => z.zielGeraet)).toEqual([bob.kennung])
    })

    it('hält eine Liste von vor der eigenen Anmeldung nicht für einen Befund', async () => {
      // Der Zwischenspeicher kann älter sein als die Anmeldung dieses Geräts —
      // beim Start fragt mehr als eine Stelle nach den eigenen Geräten. Erst
      // ein Abruf ohne Zwischenspeicher entscheidet.
      const eintrag = verzeichnis.get(ALICE)!.find((g) => g.device_id === alice.kennung)!
      verzeichnis.set(ALICE, verzeichnis.get(ALICE)!.filter((g) => g !== eintrag))
      clearGeraeteMemory()
      expect(await geraeteVon(ALICE)).toEqual([])
      verzeichnis.set(ALICE, [...verzeichnis.get(ALICE)!, eintrag])

      aktiviere(alice)
      expect(await baueZustellungen(kontextVon(ALICE, BOB), 'hallo', 'u-alt')).toHaveLength(1)
    })

    it('sendet trotzdem, wenn das eigene Verzeichnis nicht antwortet', async () => {
      // Keine Antwort ist kein Befund. Ob das Relais erreichbar ist, entscheidet
      // der Versand selbst.
      aktiviere(alice)
      await baueZustellungen(kontextVon(ALICE, BOB), 'vorher', 'u-vorher')
      clearGeraeteMemory()
      await verlangeGeraeteVon(BOB)
      netz.aus = true
      try {
        expect(await baueZustellungen(kontextVon(ALICE, BOB), 'ohne Netz', 'u-ohne')).toHaveLength(1)
      } finally {
        netz.aus = false
      }
    })

    it('hält die Sitzung, wenn hinter einem abgewiesenen Aufbau eine Nachricht kommt', async () => {
      // Der Befund der Durchsicht vom 23.09.2026: eine Fälschung kommt nicht
      // allein. Hinter dem Aufbau steht eine Nachricht aus der gefälschten
      // Sitzung — gegen die echte geöffnet scheiterte sie, galt als Bruch, und
      // der Bruch warf die echte Sitzung weg. „Die bestehende Sitzung bleibt,
      // wie sie ist" hielt dann genau bis zur nächsten Zeile.
      const kB = kontextVon(BOB, ALICE)
      aktiviere(alice)
      const [echt] = await baueZustellungen(kontextVon(ALICE, BOB), 'echt', 'u-echt')
      expect(await zustellen(bob, kB, echt)).toMatchObject({ art: 'klartext', text: 'echt' })

      // Alices Kennung, aber ein Signaturschlüssel, den das Verzeichnis nicht kennt.
      const faelscher: TestGeraet = {
        ...alice,
        signaturPaar: await erzeugeSignaturPaar(),
        ablage: neueAblage(),
      }
      aktiviere(faelscher)
      const [f] = await baueZustellungen(kontextVon(ALICE, BOB), 'gefälscht', 'u-falsch')

      aktiviere(bob)
      expect(await verarbeiteBootstrap(kB, oeffneHybrid(f.bootstrap!))).toMatchObject({
        abgelehnt: true,
        abgewiesen: true,
        vonKonto: ALICE,
        vonGeraet: alice.kennung,
      })
      const geschont = new Set([`${ALICE}:${alice.kennung}`])
      const schonen = (konto: number, g: string) => geschont.has(`${konto}:${g}`)
      expect(await liesDrUmschlag(kB, f.nachricht, verwerfen, { schonen })).toEqual({ art: 'abgewiesen' })

      // Beim nächsten Abruf liegt beides wieder im Fenster: gemeldet wird nichts
      // mehr, geschont weiterhin — und die Nachricht bleibt still.
      const nochmal = await verarbeiteBootstrap(kB, oeffneHybrid(f.bootstrap!))
      expect(nochmal).toMatchObject({ istAufbau: true, abgewiesen: true, vonKonto: ALICE })
      expect(nochmal.abgelehnt).toBeUndefined()
      expect(await liesDrUmschlag(kB, f.nachricht, verwerfen)).toEqual({ art: 'abgewiesen' })

      // Die echte Sitzung trägt weiter, ohne neuen Aufbau.
      aktiviere(alice)
      const [weiter] = await baueZustellungen(kontextVon(ALICE, BOB), 'immer noch echt', 'u-weiter')
      expect(weiter.bootstrap).toBeNull()
      aktiviere(bob)
      expect(await liesDrUmschlag(kB, weiter.nachricht, verwerfen, { schonen })).toMatchObject({
        art: 'klartext',
        text: 'immer noch echt',
      })
    })
  })

  it('nennt den Absender aus dem Kopf eines Umschlags', async () => {
    // Der Kopf wählt die Sitzung, gegen die entschlüsselt wird. Was sich damit
    // öffnen liess, kam von diesem Gerät — auch dann noch, wenn der Klartext
    // nach einem Neuladen aus der Ablage kommt und kein Zwischenspeicher mehr
    // weiss, von wem.
    aktiviere(alice)
    const [z] = await baueZustellungen(kontextVon(ALICE, BOB), 'x', 'u-kopf')
    expect(drUrheber(z.nachricht)).toEqual({ vonKonto: ALICE, vonGeraet: alice.kennung })
    expect(drUrheber('sv-e2ee-hybrid-v1:abc')).toBeNull()
    expect(drUrheber(`${DR_PREFIX}0.a.b.c`)).toBeNull()
  })
})
