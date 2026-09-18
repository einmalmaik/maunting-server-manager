/**
 * Der Double Ratchet im Messenger: Sitzung aufbauen, senden, lesen.
 *
 * Eine Sitzung läuft zwischen zwei **Geräten**, nicht zwischen zwei Konten. Wer
 * an drei Geräten angemeldet ist, hat mit seinem Gegenüber an zwei Geräten sechs
 * Fäden, und jeder davon hat seinen eigenen Zustand. Deshalb geht jede Nachricht
 * aufgefächert raus: je Gerät der Gegenstelle **und** je eigenem Zweitgerät ein
 * eigener Umschlag. Das ist die Semantik, die Signal seit Jahren fährt.
 *
 * **Der Sitzungsaufbau.** DIS bringt bewusst kein X3DH mit, es nimmt 32
 * vereinbarte Bytes. Die vereinbart MSM über den Hybridumschlag gegen den
 * veröffentlichten Geräteschlüssel: wer eine Sitzung beginnt, erzeugt das
 * Geheimnis und das ECDH-Paar der Gegenstelle und schickt beides einmalig
 * versiegelt hinüber. Der Preis steht in den Restrisiken des Umbauplans — wer
 * den privaten Geräteschlüssel und diesen einen Umschlag hat, bekommt die
 * Sitzung. Ab der ersten Antwort greift die Erholung des Ratchets, weil die
 * Gegenstelle dann ihr eigenes Paar erzeugt hat.
 *
 * **Das Format.**
 *
 * ```
 * sv-e2ee-dr-v1:<vonKonto>.<vonGeraet>.<fuerGeraet>.<base64(sv-dr-msg-v1:…)>
 * ```
 *
 * Die drei Kopfteile stehen im Klartext, weil sie das Routing sind: ohne
 * `fuerGeraet` weiß kein Client, welche der aufgefächerten Kopien seine ist,
 * ohne `vonGeraet` findet er die Sitzung nicht, und ohne `vonKonto` könnte ein
 * zweites Konto eine Gerätekennung beanspruchen, die es gar nicht besitzt — die
 * Eindeutigkeit der Kennung gilt nur je Konto. Das Backend erfährt daraus nur,
 * dass Konten mehrere Geräte haben; die Mailbox kennt es ohnehin.
 *
 * Die DIS-Wire-Form wird base64 verpackt, weil sie sonst `"ciphertext":`
 * enthielte und an der Klartext-Leck-Erkennung des Backends hängenbliebe.
 *
 * **Die gebundenen Daten.** `associatedData` ist
 * `msm:dr:<kleineresKonto>:<groesseresKonto>:<GerätA>:<GerätB>`, beide
 * Gerätekennungen **sortiert**. Sortiert, weil dieselbe Sitzung in beide
 * Richtungen läuft, die Zeichenkette aber beim Aufbau festgeschrieben wird — mit
 * Absender zuerst bräche die erste Antwort. DIS bindet das in die AAD jeder
 * Nachricht: ein Umschlag lässt sich damit nicht in die Sitzung eines anderen
 * Paares oder eines anderen Geräts umhängen.
 *
 * **Was hier bewusst fehlt.** Kein Zwischenspeicher für Klartext. Ein
 * Ratchet-Nachrichtenschlüssel wird beim Entschlüsseln verbraucht; derselbe
 * Umschlag lässt sich kein zweites Mal öffnen. Deshalb nimmt `liesDrUmschlag`
 * eine Ablegefunktion entgegen und ruft sie **innerhalb** des Sitzungsschlosses
 * auf, bevor der fortgeschriebene Zustand gespeichert wird. Scheitert sie,
 * bleibt der Zustand stehen und der Umschlag beim nächsten Durchlauf lesbar.
 * Ohne diese Reihenfolge kostet ein Neuladen zur falschen Sekunde eine
 * Nachricht, endgültig.
 */

import {
  decryptMessage,
  deserializeRatchetMessage,
  destroyRatchetState,
  encryptMessage,
  generateRatchetKeyPair,
  initReceiverState,
  initSenderState,
  serializeRatchetMessage,
  type RatchetDhKeyPair,
} from '@msdis/shield/messaging'
import { randomBytes } from '@msdis/shield/random'
import { base64ToBytes, bytesToBase64, bytesToUtf8, utf8ToBytes } from '@msdis/shield/core'

import { encryptE2eeHybrid } from './e2eeCrypto'
import { eigenesGeraet, geraeteVon, verlangeGeraeteVon } from './e2eeGeraet'
import {
  hatSitzung,
  kennstAufbau,
  merkeAufbau,
  schritt,
  sitzungsId,
  verwirfSitzung,
} from './ratchetSpeicher'

export const DR_PREFIX = 'sv-e2ee-dr-v1:'
export const DR_INIT_TYP = 'dr-init'

const GEHEIMNIS_BYTES = 32
/** Reicht für die `client_uuid`: 36 (UUID) + 1 + 12 = 49 von 64 erlaubten Zeichen. */
const KENNUNG_KURZ = 12

export interface DrKontext {
  /** Das eigene Konto. */
  eigeneId: number
  /** Das Konto der Gegenstelle in diesem Gespräch. */
  peerId: number
}

/**
 * Für kein einziges Zielgerät liess sich ein Umschlag bauen.
 *
 * Abzugrenzen von `E2eeKeinGeraetError`: dort ist niemand angemeldet, hier sind
 * Geräte da und das Verschlüsseln hat versagt. Der Unterschied gehört bis in
 * den Fehlertext, sonst sucht der Benutzer den Fehler bei der Gegenstelle.
 */
export class DrZustellungFehlgeschlagenError extends Error {
  constructor(public readonly ursachen: unknown[]) {
    super('Für kein Zielgerät liess sich ein Umschlag bauen')
    this.name = 'DrZustellungFehlgeschlagenError'
  }
}

/** Ein Umschlagpaar für genau ein Zielgerät. Reihenfolge beachten. */
export interface DrZustellung {
  empfaengerId: number
  zielGeraet: string
  /** Muss **vor** `nachricht` zugestellt werden, sonst findet die Gegenstelle keine Sitzung. */
  bootstrap: string | null
  nachricht: string
  clientUuid: string
  bootstrapClientUuid: string
}

export type DrLesung =
  | { art: 'klartext'; text: string; vonKonto: number; vonGeraet: string }
  /** Kopie für ein anderes Gerät. Nie anzeigen, auch nicht als „verschlüsselt". */
  | { art: 'fremd' }
  /** Von diesem Gerät gesendet. Der Absender kann sich selbst nicht lesen — so ist der Ratchet gebaut. */
  | { art: 'eigen' }
  /** Kein Umschlag dieses Verfahrens. */
  | { art: 'unbekannt' }
  /**
   * Die Sitzung trägt nicht mehr: die Gegenstelle hat neu installiert, oder
   * jemand hat am Umschlag gedreht. Die Sitzung gehört weg und der Mensch soll
   * es sehen.
   */
  | { art: 'bruch'; vonKonto: number; vonGeraet: string; grund: string }
  /**
   * Etwas anderes ging schief — meist die lokale Ablage. **Kein** Grund, eine
   * Sitzung wegzuwerfen: eine volle Platte darf nicht jeden Gesprächsfaden des
   * Geräts abreißen. Der Umschlag bleibt liegen und wird später erneut versucht.
   */
  | { art: 'fehler'; grund: string }

// ==========================================
// Format
// ==========================================

/**
 * Die gebundenen Daten einer Sitzung.
 *
 * Beide Gerätekennungen sortiert, damit Absender und Empfänger dieselbe
 * Zeichenkette bilden — die Sitzung trägt sie ab dem Aufbau unverändert, und
 * beide Richtungen laufen durch dieselbe Sitzung.
 */
function gebundeneDaten(kontext: DrKontext, geraetA: string, geraetB: string): Uint8Array {
  const kleiner = Math.min(kontext.eigeneId, kontext.peerId)
  const groesser = Math.max(kontext.eigeneId, kontext.peerId)
  const [erst, zweit] = [geraetA, geraetB].sort()
  return utf8ToBytes(`msm:dr:${kleiner}:${groesser}:${erst}:${zweit}`)
}

interface DrKopf {
  vonKonto: number
  vonGeraet: string
  fuerGeraet: string
  rumpf: string
}

function baueUmschlag(kopf: DrKopf): string {
  return `${DR_PREFIX}${kopf.vonKonto}.${kopf.vonGeraet}.${kopf.fuerGeraet}.${kopf.rumpf}`
}

function lieseKopf(umschlag: string): DrKopf | null {
  if (!umschlag.startsWith(DR_PREFIX)) return null
  const teile = umschlag.slice(DR_PREFIX.length).split('.')
  if (teile.length !== 4) return null
  const [kontoRoh, vonGeraet, fuerGeraet, rumpf] = teile
  const vonKonto = Number(kontoRoh)
  if (!Number.isInteger(vonKonto) || vonKonto <= 0) return null
  if (!vonGeraet || !fuerGeraet || !rumpf) return null
  return { vonKonto, vonGeraet, fuerGeraet, rumpf }
}

/**
 * Schneidet den Gerätezusatz von der Kennung ab.
 *
 * Dieselbe Nachricht liegt je Zielgerät einmal in der Mailbox. Damit alle Geräte
 * denselben Verlauf führen, zählt für die Anzeige und den lokalen Speicher die
 * Kennung ohne Zusatz.
 */
export function logischeUuid(clientUuid: string | null | undefined): string | undefined {
  if (!clientUuid) return undefined
  const schnitt = clientUuid.indexOf('#')
  return schnitt === -1 ? clientUuid : clientUuid.slice(0, schnitt)
}

// ==========================================
// Sitzungsaufbau
// ==========================================

interface BootstrapInhalt {
  typ: string
  v: number
  vonKonto: number
  vonGeraet: string
  fuerGeraet: string
  geheimnis: string
  paar: { algorithm: string; publicKey: string; privateKey: string }
}

/**
 * Erzeugt Geheimnis und Gegenpaar, versiegelt beides gegen den Geräteschlüssel
 * der Gegenstelle und liefert den Absenderzustand zurück.
 *
 * Das Geheimnis wird von `initSenderState` genullt, das private Gegenstück hier
 * — beide haben nach diesem Aufruf nur noch beim Empfänger eine Fassung.
 */
async function beginneSitzung(
  kontext: DrKontext,
  meinGeraet: string,
  meinOeffentlicher: string,
  zielGeraet: string,
  zielOeffentlicher: string,
) {
  const paar: RatchetDhKeyPair = await generateRatchetKeyPair()
  const geheimnis = randomBytes(GEHEIMNIS_BYTES)

  const inhalt: BootstrapInhalt = {
    typ: DR_INIT_TYP,
    v: 1,
    vonKonto: kontext.eigeneId,
    vonGeraet: meinGeraet,
    fuerGeraet: zielGeraet,
    geheimnis: bytesToBase64(geheimnis),
    paar: {
      algorithm: paar.algorithm,
      publicKey: bytesToBase64(paar.publicKey),
      privateKey: bytesToBase64(paar.privateKey),
    },
  }
  const umschlag = await encryptE2eeHybrid(
    JSON.stringify(inhalt),
    zielOeffentlicher,
    meinOeffentlicher,
  )
  paar.privateKey.fill(0)

  const zustand = await initSenderState({
    sharedSecret: geheimnis,
    remotePublicKey: paar.publicKey,
    associatedData: gebundeneDaten(kontext, meinGeraet, zielGeraet),
  })
  return { umschlag, zustand }
}

/**
 * Nimmt einen entschlüsselten Hybrid-Klartext entgegen und richtet die Sitzung
 * ein, falls es ein Sitzungsaufbau ist.
 *
 * Meldet `false`, wenn der Klartext etwas anderes war — dann gehört er in den
 * normalen Lesepfad. Ein Aufbau für ein fremdes Gerät wird verworfen: er ist
 * eine der aufgefächerten Kopien und nicht für dieses Gerät bestimmt.
 *
 * Kommt ein Aufbau, obwohl schon eine Sitzung steht, ersetzt er sie. Die
 * Gegenstelle hat dann ihren Zustand verloren, und ihr altes Gegenstück liegt
 * nur noch im Weg. `ersetzt: true` sagt dem Aufrufer, dass er das sichtbar
 * machen soll.
 */
export async function verarbeiteBootstrap(
  kontext: DrKontext,
  klartext: string,
): Promise<{ istAufbau: boolean; ersetzt: boolean; vonGeraet?: string }> {
  let inhalt: BootstrapInhalt
  try {
    const roh = JSON.parse(klartext)
    if (!roh || typeof roh !== 'object' || roh.typ !== DR_INIT_TYP) {
      return { istAufbau: false, ersetzt: false }
    }
    inhalt = roh as BootstrapInhalt
  } catch {
    return { istAufbau: false, ersetzt: false }
  }

  const meins = await eigenesGeraet()
  if (inhalt.fuerGeraet !== meins.kennung) {
    // Kopie für ein anderes Gerät desselben Kontos.
    return { istAufbau: true, ersetzt: false }
  }
  // Nur die beiden Konten dieser Mailbox dürfen hier eine Sitzung setzen. Ohne
  // diese Prüfung könnte ein Absender eine fremde Kontokennung behaupten und
  // damit eine Sitzung überschreiben, die einem anderen Gespräch gehört.
  if (inhalt.vonKonto !== kontext.eigeneId && inhalt.vonKonto !== kontext.peerId) {
    return { istAufbau: true, ersetzt: false }
  }

  // Derselbe Umschlag beim nächsten Abruf ist kein Neuaufbau. Der öffentliche
  // Teil des mitgereisten Paares wird je Aufbau frisch erzeugt und ist damit
  // die Kennung dieses einen Aufbaus — anders als die Gerätekennung, die über
  // alle Aufbauten hinweg dieselbe bleibt.
  const aufbauKennung = inhalt.paar.publicKey
  if (await kennstAufbau(aufbauKennung)) {
    return { istAufbau: true, ersetzt: false, vonGeraet: inhalt.vonGeraet }
  }

  const id = sitzungsId(inhalt.vonKonto, inhalt.vonGeraet)
  const stand = await hatSitzung(id)

  const paar: RatchetDhKeyPair = {
    algorithm: inhalt.paar.algorithm as RatchetDhKeyPair['algorithm'],
    publicKey: base64ToBytes(inhalt.paar.publicKey),
    privateKey: base64ToBytes(inhalt.paar.privateKey),
  }
  const geheimnis = base64ToBytes(inhalt.geheimnis)

  await schritt(id, async (alterZustand) => {
    const neu = await initReceiverState({
      sharedSecret: geheimnis,
      dhKeyPair: paar,
      associatedData: gebundeneDaten(kontext, inhalt.vonGeraet, meins.kennung),
    })
    // `alterZustand` räumt `schritt` selbst ab.
    void alterZustand
    return { naechster: neu, ergebnis: null }
  })
  paar.privateKey.fill(0)
  // Erst nach dem Anwenden. Scheitert `schritt`, bleibt der Aufbau ungemerkt
  // und der nächste Abruf nimmt den Faden wieder auf.
  await merkeAufbau(aufbauKennung)

  return { istAufbau: true, ersetzt: stand, vonGeraet: inhalt.vonGeraet }
}

// ==========================================
// Senden
// ==========================================

/**
 * Baut für jedes Zielgerät einen Umschlag — und, wo nötig, den Sitzungsaufbau
 * davor.
 *
 * Der Aufrufer muss je Zustellung **erst** `bootstrap` und **dann** `nachricht`
 * relayen. Andersherum trifft die Nachricht auf eine Gegenstelle ohne Sitzung
 * und läuft in den Sitzungsbruch.
 *
 * Ziele sind alle Geräte der Gegenstelle und alle eigenen außer diesem. Das
 * eigene Gerät bleibt außen vor: es hat den Klartext schon und könnte seine
 * eigene Ratchet-Nachricht ohnehin nicht öffnen.
 */
export async function baueZustellungen(
  kontext: DrKontext,
  klartext: string,
  basisUuid: string,
): Promise<DrZustellung[]> {
  const meins = await eigenesGeraet()
  const fremde = await verlangeGeraeteVon(kontext.peerId)
  const eigene = (await geraeteVon(kontext.eigeneId)).filter((g) => g.device_id !== meins.kennung)

  const ziele = [
    ...fremde.map((g) => ({ geraet: g, konto: kontext.peerId })),
    ...eigene.map((g) => ({ geraet: g, konto: kontext.eigeneId })),
  ]

  const zustellungen: DrZustellung[] = []
  const gescheitert: unknown[] = []
  for (const ziel of ziele) {
    const zielGeraet = ziel.geraet.device_id
    const id = sitzungsId(ziel.konto, zielGeraet)
    try {
      const gebaut = await schritt<{ bootstrap: string | null; nachricht: string }>(
        id,
        async (zustand) => {
          let arbeitszustand = zustand
          let bootstrap: string | null = null
          let selbstErzeugt = false

          // `sendingChainKey === null` heisst: dieser Zustand hat noch nie
          // etwas entschlüsselt und kann deshalb nicht senden — so kommt jeder
          // Empfängerzustand aus `initReceiverState`. Im Normalfall ist das
          // eine Momentaufnahme: die erste eingehende Nachricht dreht den
          // Ratchet und legt die Sendekette an. Bleibt er so liegen, weil ein
          // zweiter Sitzungsaufbau den arbeitenden Zustand ersetzt hat, wäre
          // dieses Gerät dauerhaft stumm und käme aus eigener Kraft nie wieder
          // heraus. Deshalb zählt er hier wie „keine Sitzung".
          if (!arbeitszustand || arbeitszustand.sendingChainKey === null) {
            const begonnen = await beginneSitzung(
              kontext,
              meins.kennung,
              meins.paar.publicKeyJwk,
              zielGeraet,
              ziel.geraet.public_key,
            )
            bootstrap = begonnen.umschlag
            arbeitszustand = begonnen.zustand
            selbstErzeugt = true
          }

          const { nextState, message } = await encryptMessage(
            arbeitszustand,
            utf8ToBytes(klartext),
          )
          // Ein gerade erzeugter Anfangszustand kam nicht aus der Ablage;
          // `schritt` räumt nur den geladenen ab.
          if (selbstErzeugt) destroyRatchetState(arbeitszustand)

          return {
            naechster: nextState,
            ergebnis: {
              bootstrap,
              nachricht: baueUmschlag({
                vonKonto: kontext.eigeneId,
                vonGeraet: meins.kennung,
                fuerGeraet: zielGeraet,
                rumpf: bytesToBase64(utf8ToBytes(serializeRatchetMessage(message))),
              }),
            },
          }
        },
      )

      zustellungen.push({
        empfaengerId: kontext.peerId,
        zielGeraet,
        bootstrap: gebaut.bootstrap,
        nachricht: gebaut.nachricht,
        clientUuid: `${basisUuid}#${zielGeraet.slice(0, KENNUNG_KURZ)}`,
        bootstrapClientUuid: `${basisUuid}#i${zielGeraet.slice(0, KENNUNG_KURZ)}`,
      })
    } catch (fehler) {
      // Ein Gerät, für das sich kein Umschlag bauen lässt, hält die übrigen
      // nicht auf. Der Mensch bekommt seine Nachricht auf den anderen.
      gescheitert.push(fehler)
    }
  }

  // Kein einziges Ziel hat funktioniert. Das als leere Liste zurückzugeben sähe
  // für den Aufrufer genauso aus wie „die Gegenstelle hat kein Gerät
  // angemeldet" — und genau das stand dann im Fehlertext, obwohl die Geräte da
  // waren und nur das Verschlüsseln scheiterte. Eine Verschlüsselung, die
  // stillschweigend nichts liefert, ist schlimmer als eine, die scheitert.
  if (zustellungen.length === 0 && ziele.length > 0) {
    throw new DrZustellungFehlgeschlagenError(gescheitert)
  }

  return zustellungen
}

// ==========================================
// Lesen
// ==========================================

/**
 * Öffnet einen Umschlag dieses Verfahrens.
 *
 * `ablegen` läuft innerhalb des Sitzungsschlosses, nachdem der Klartext vorliegt
 * und bevor der fortgeschriebene Zustand gespeichert wird. Wirft sie, bleibt der
 * Zustand stehen und derselbe Umschlag ist beim nächsten Durchlauf erneut
 * lesbar. Das ist der einzige Schutz gegen einen verbrauchten Nachrichten-
 * schlüssel ohne abgelegten Klartext.
 */
export async function liesDrUmschlag(
  kontext: DrKontext,
  umschlag: string,
  ablegen: (text: string) => Promise<void>,
): Promise<DrLesung> {
  const kopf = lieseKopf(umschlag)
  if (!kopf) return { art: 'unbekannt' }

  const meins = await eigenesGeraet()
  if (kopf.vonGeraet === meins.kennung) return { art: 'eigen' }
  if (kopf.fuerGeraet !== meins.kennung) return { art: 'fremd' }
  if (kopf.vonKonto !== kontext.eigeneId && kopf.vonKonto !== kontext.peerId) {
    return { art: 'fremd' }
  }

  const id = sitzungsId(kopf.vonKonto, kopf.vonGeraet)
  if (!(await hatSitzung(id))) {
    return {
      art: 'bruch',
      vonKonto: kopf.vonKonto,
      vonGeraet: kopf.vonGeraet,
      grund: 'keine Sitzung',
    }
  }

  let nachricht
  try {
    nachricht = deserializeRatchetMessage(bytesToUtf8(base64ToBytes(kopf.rumpf)))
  } catch {
    return { art: 'unbekannt' }
  }

  try {
    const text = await schritt<string>(id, async (zustand) => {
      if (!zustand) throw new Error('Sitzung verschwunden')
      const { nextState, plaintext } = await decryptMessage(zustand, nachricht)
      const text = bytesToUtf8(plaintext)
      // Erst ablegen, dann den Zustand fortschreiben: ein verbrauchter
      // Nachrichtenschlüssel ohne abgelegten Klartext wäre eine verlorene
      // Nachricht, und zwar ohne jede Meldung.
      await ablegen(text)
      return { naechster: nextState, ergebnis: text }
    })
    return { art: 'klartext', text, vonKonto: kopf.vonKonto, vonGeraet: kopf.vonGeraet }
  } catch (fehler) {
    // Was DIS hier werfen kann, betrifft immer die Sitzung: ein gescheiterter
    // Tag (`DisDecryptionError`) oder ein Zustand, der sich nicht mehr lesen
    // lässt (`DisInvalidArgumentError` aus `deserializeRatchetState`). Beides
    // heißt, dass dieser Faden nicht mehr trägt. Die Nachricht selbst ist schon
    // vor dem Schloss geprüft worden.
    //
    // Alles andere — eine volle Platte, eine geschlossene Datenbank, ein
    // abgebrochener Schreibvorgang — ist vorübergehend und darf die Sitzung
    // nicht kosten.
    const name = fehler instanceof Error ? fehler.name : 'unbekannt'
    if (!name.startsWith('Dis')) {
      return { art: 'fehler', grund: name }
    }
    return {
      art: 'bruch',
      vonKonto: kopf.vonKonto,
      vonGeraet: kopf.vonGeraet,
      grund: name,
    }
  }
}

/**
 * Wirft die Sitzung zu einem Gerät weg.
 *
 * Danach baut der nächste Sendevorgang eine frische auf. Der Aufrufer meldet das
 * sichtbar im Verlauf — eine stillschweigend neu aufgebaute Sitzung wäre genau
 * das, was ein Angreifer sich wünscht.
 */
export async function verwirfDrSitzung(vonKonto: number, vonGeraet: string): Promise<void> {
  await verwirfSitzung(sitzungsId(vonKonto, vonGeraet))
}
