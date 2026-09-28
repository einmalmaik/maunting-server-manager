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
 * **Der Aufbau trägt eine Unterschrift.** Der Hybridumschlag sagt nur, *für*
 * welches Gerät er ist, nie *von* welchem — den Geräteschlüssel des Empfängers
 * kann jeder benutzen. Bis 09/2026 genügte deshalb ein erfundener Aufbau, und
 * wer ihn schickte, hielt danach beide Enden der Sitzung: seine Nachrichten
 * erschienen unter fremdem Namen, auch unter dem eigenen, und was dieses Gerät
 * an das angebliche Gegenüber sandte, konnte er lesen. Jetzt unterschreibt das
 * sendende Gerät mit seinem Signaturpaar, und `verarbeiteBootstrap` wendet
 * einen Aufbau erst an, wenn die Unterschrift zum Verzeichnis passt
 * (`pruefeGeraeteBeleg`).
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
 * eine Klartextablage entgegen und benutzt sie **innerhalb** des
 * Sitzungsschlosses: erst nachsehen, ob der Umschlag schon offen ist, dann
 * entschlüsseln, dann ablegen, dann den Zustand fortschreiben. Scheitert das
 * Ablegen, bleibt der Zustand stehen und der Umschlag beim nächsten Durchlauf
 * lesbar. Ohne diese Reihenfolge kostet ein Neuladen zur falschen Sekunde eine
 * Nachricht, endgültig.
 *
 * **Warum die Abfrage im Schloss steht und nicht davor.** Der Messenger fragt
 * die Mailbox aus fünf Quellen ab — Takt, Sync-Ereignis, Sichtbarkeitswechsel,
 * Senden, Warteschlange — und eine einzige Nachricht löst mehrere davon
 * gleichzeitig aus. Zwei überlappende Durchläufe holen denselben Umschlag, und
 * beide halten ihn für ungeöffnet, weil der erste seinen Klartext noch nicht
 * abgelegt hat. Der zweite Versuch trifft dann auf einen verbrauchten
 * Nachrichtenschlüssel, und von aussen ist das von einer Fälschung nicht zu
 * unterscheiden: die Sitzung fliegt weg, im Verlauf steht „Die
 * Sicherheitssitzung wurde neu aufgebaut", und die Nachricht fehlt in genau dem
 * Durchlauf, der am Ende angezeigt wird. Dasselbe gilt über Tabs hinweg, die
 * sich Ablage und Geräteschlüssel teilen. Prüfen und Verbrauchen gehören
 * deshalb unter dasselbe Schloss.
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
import { sha256Hex } from '@msdis/shield/integrity'

import type { E2eeGeraetItem } from '@/api/social'
import i18n from '@/i18n'

import { signiere } from './absenderSignatur'
import { encryptE2eeHybrid } from './e2eeCrypto'
import {
  eigenesGeraet,
  geraeteVon,
  geraetVeroeffentlichen,
  pruefeGeraeteBeleg,
  verlangeGeraeteVon,
  vergessenGeraete,
  verzeichnisVon,
  type EigenesGeraet,
} from './e2eeGeraet'
import {
  hatSitzung,
  kennstMarke,
  merkeMarke,
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

/**
 * Dieses Gerät steht nicht mehr im Verzeichnis des eigenen Kontos.
 *
 * Jemand hat es in der Geräteliste entfernt, während dieser Tab offen war.
 * Senden hiesse Aufbauten, die drüben niemand prüfen kann; sich still neu
 * einzutragen hiesse, dem Entfernen zu nehmen, was der Dialog dort verspricht.
 * Der Text sagt deshalb beides: warum nicht, und dass ein Neuladen es wieder
 * einträgt — so, wie es der Dialog angekündigt hat.
 */
export class DrGeraetNichtEingetragenError extends Error {
  constructor() {
    super(i18n.t('messenger.deviceNotListed'))
    this.name = 'DrGeraetNichtEingetragenError'
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
   * Dieser Umschlag ist schon einmal als Bruch gewertet worden.
   *
   * Anzeigen als „nicht lesbar", aber nichts mehr daraus folgern. Er bleibt bis
   * zum Ende seiner Aufbewahrung in der Mailbox liegen, und der Lesepfad holt
   * bei jedem Abruf das ganze Fenster — ohne diese Unterscheidung warf ein
   * einziger alter Umschlag bei jedem Öffnen des Gesprächs die gerade
   * funktionierende Sitzung weg.
   */
  | { art: 'beurteilt' }
  /**
   * Etwas anderes ging schief — meist die lokale Ablage. **Kein** Grund, eine
   * Sitzung wegzuwerfen: eine volle Platte darf nicht jeden Gesprächsfaden des
   * Geräts abreißen. Der Umschlag bleibt liegen und wird später erneut versucht.
   */
  | { art: 'fehler'; grund: string }
  /**
   * Noch nicht angefasst, weil der Sitzungsaufbau dieses Geräts im selben
   * Durchlauf nicht entschieden werden konnte. Nicht anzeigen; der nächste
   * Durchlauf versucht es erneut. Siehe `DrLeseOptionen.zurueckstellen`.
   */
  | { art: 'zurueckgestellt' }
  /**
   * Gehört zu einem abgewiesenen Sitzungsaufbau: das Gerät hat einen im
   * Fenster, und diese Nachricht öffnet sich gegen die bestehende Sitzung
   * nicht. Nicht anzeigen, und — der Grund für diese Art — **keine** Sitzung
   * verwerfen. Siehe `DrLeseOptionen.schonen`.
   */
  | { art: 'abgewiesen' }

/** Was `verarbeiteBootstrap` über einen Hybrid-Klartext herausgefunden hat. */
export interface AufbauErgebnis {
  /** Es war ein Sitzungsaufbau, gleich was daraus wurde. Gehört nie in den Verlauf. */
  istAufbau: boolean
  /** Eine bestehende Sitzung wurde ersetzt. Der Aufrufer macht das sichtbar. */
  ersetzt: boolean
  vonKonto?: number
  vonGeraet?: string
  /**
   * Noch nicht entschieden: das Verzeichnis antwortete nicht, oder es führt
   * das Gerät noch nicht. Nichts angewandt, nichts gemerkt — der nächste
   * Durchlauf versucht es erneut.
   *
   * Nachrichten dieses Geräts, die im selben Durchlauf folgen, gehören
   * zurückgestellt. Liefen sie gegen eine Sitzung, die es noch nicht gibt,
   * wären sie als Bruch gewertet und damit für immer verloren.
   */
  offen?: boolean
  /**
   * Abgewiesen und gemerkt: die Unterschrift passt nicht zum Verzeichnis, sie
   * fehlt bei einem Konto, das unterschreiben kann, oder der Aufbau nennt ein
   * Konto, das nicht zu diesem Gespräch gehört. Angewandt wurde nichts; der
   * Aufrufer macht es sichtbar.
   *
   * Nur beim ersten Sehen — gemeldet wird einmal.
   */
  abgelehnt?: boolean
  /**
   * Ein abgewiesener Aufbau, beim ersten Sehen und bei jedem weiteren. Solange
   * er im Fenster liegt, gehören unlesbare Nachrichten seines Geräts zu ihm und
   * nicht zur bestehenden Sitzung: `DrLeseOptionen.schonen`.
   */
  abgewiesen?: boolean
}

export interface DrLeseOptionen {
  /**
   * Liefert `true` für ein Gerät, dessen Sitzungsaufbau in diesem Durchlauf
   * offen geblieben ist (`AufbauErgebnis.offen`). Seine Nachrichten werden
   * dann nicht geöffnet, sondern als `zurueckgestellt` gemeldet.
   */
  zurueckstellen?: (vonKonto: number, vonGeraet: string) => boolean
  /**
   * Liefert `true` für ein Gerät, von dem ein abgewiesener Aufbau im Fenster
   * liegt (`AufbauErgebnis.abgewiesen`). Öffnet sich eine seiner Nachrichten
   * gegen die bestehende Sitzung nicht, ist das kein Bruch, sondern die
   * Nachricht hinter der Fälschung: `abgewiesen`, und die Sitzung bleibt.
   * Ohne das kippte ein gefälschter Aufbau mit einer einzigen Nachricht
   * dahinter die laufende Sitzung doch noch.
   */
  schonen?: (vonKonto: number, vonGeraet: string) => boolean
}

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
 * Konto und Gerät aus dem Kopf eines Ratchet-Umschlags, oder `null`.
 *
 * Der Kopf wählt die Sitzung, gegen die entschlüsselt wird — was sich damit
 * öffnen liess, kam von diesem Gerät. Für Klartext aus der Ablage ist das die
 * einzige Quelle: der Zwischenspeicher, der sich den Absender beim Öffnen
 * gemerkt hat, lebt nur bis zum Neuladen. Ohne Absender griffe die
 * Downgrade-Schranke im Messenger nicht, und eine Nachricht, die sie beim
 * ersten Sehen verworfen hat, stünde nach dem Neuladen im Verlauf.
 *
 * Nur für Umschläge, die dieses Gerät schon geöffnet hat. Einem fremden Kopf
 * glaubt hier niemand etwas.
 */
export function drUrheber(umschlag: string): { vonKonto: number; vonGeraet: string } | null {
  const kopf = lieseKopf(umschlag)
  return kopf ? { vonKonto: kopf.vonKonto, vonGeraet: kopf.vonGeraet } : null
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
  /**
   * Die Unterschrift des sendenden Geräts über `aufbauDaten`. Fehlt sie, gilt
   * die Downgrade-Schranke aus `pruefeGeraeteBeleg`: durch kommt dann nur, wer
   * gar nicht unterschreiben *kann*.
   */
  sig?: string
}

/**
 * Hat der Klartext die Form eines Aufbaus?
 *
 * Nicht bloß Hygiene: ein Aufbau mit fehlendem Konto oder Gerät liefe sonst
 * bis in die Verzeichnisabfrage, bliebe dort `offen` und kostete bei jedem
 * Abruf eine Anfrage, die nie beantwortet werden kann.
 */
function istAufbauInhalt(roh: any): roh is BootstrapInhalt {
  return (
    Number.isInteger(roh.vonKonto) &&
    roh.vonKonto > 0 &&
    typeof roh.vonGeraet === 'string' &&
    roh.vonGeraet.length > 0 &&
    typeof roh.fuerGeraet === 'string' &&
    typeof roh.geheimnis === 'string' &&
    Boolean(roh.paar) &&
    typeof roh.paar.algorithm === 'string' &&
    typeof roh.paar.publicKey === 'string' &&
    typeof roh.paar.privateKey === 'string'
  )
}

/**
 * Die Zeichenkette, über die ein Sitzungsaufbau unterschrieben wird.
 *
 * Alles, was die Sitzung ausmacht: von wem, für wen, in welchem Gespräch, und
 * das Material selbst. Das Gespräch steht als Kontenpaar darin und nicht als
 * Mailbox — die Mailbox zieht mit dem Chatgeheimnis um, das Gespräch nicht.
 *
 * Ein JSON-Array statt einer Feldliste: Reihenfolge und Grenzen sind damit
 * eindeutig, ohne dass jemand ein Trennzeichen maskieren muss. Der Vorsatz
 * hält diese Unterschrift von denen über Nutzlasten (`msm:nutzlast:v1:`)
 * fern, die mit demselben Schlüssel entstehen.
 */
function aufbauDaten(kontext: DrKontext, inhalt: BootstrapInhalt): string {
  return JSON.stringify([
    'msm:dr-init:v1',
    Math.min(kontext.eigeneId, kontext.peerId),
    Math.max(kontext.eigeneId, kontext.peerId),
    inhalt.vonKonto,
    inhalt.vonGeraet,
    inhalt.fuerGeraet,
    inhalt.geheimnis,
    inhalt.paar.algorithm,
    inhalt.paar.publicKey,
    inhalt.paar.privateKey,
  ])
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
  meins: EigenesGeraet,
  zielGeraet: string,
  zielOeffentlicher: string,
) {
  const paar: RatchetDhKeyPair = await generateRatchetKeyPair()
  const geheimnis = randomBytes(GEHEIMNIS_BYTES)

  const inhalt: BootstrapInhalt = {
    typ: DR_INIT_TYP,
    v: 1,
    vonKonto: kontext.eigeneId,
    vonGeraet: meins.kennung,
    fuerGeraet: zielGeraet,
    geheimnis: bytesToBase64(geheimnis),
    paar: {
      algorithm: paar.algorithm,
      publicKey: bytesToBase64(paar.publicKey),
      privateKey: bytesToBase64(paar.privateKey),
    },
  }
  // Scheitert das, scheitert der Aufbau für dieses Ziel. Ein unsignierter
  // Aufbau aus diesem Stand wäre genau die Lücke, die die Unterschrift schließt.
  inhalt.sig = await signiere(aufbauDaten(kontext, inhalt), meins.signaturPaar.privateKeyJwk)
  const umschlag = await encryptE2eeHybrid(
    JSON.stringify(inhalt),
    zielOeffentlicher,
    meins.paar.publicKeyJwk,
  )
  paar.privateKey.fill(0)

  const zustand = await initSenderState({
    sharedSecret: geheimnis,
    remotePublicKey: paar.publicKey,
    associatedData: gebundeneDaten(kontext, meins.kennung, zielGeraet),
  })
  return { umschlag, zustand }
}

/**
 * Nimmt einen entschlüsselten Hybrid-Klartext entgegen und richtet die Sitzung
 * ein, falls es ein Sitzungsaufbau ist.
 *
 * Meldet `istAufbau: false`, wenn der Klartext etwas anderes war — dann gehört
 * er in den normalen Lesepfad. Ein Aufbau für ein fremdes Gerät wird
 * übergangen: er ist eine der aufgefächerten Kopien und nicht für dieses Gerät
 * bestimmt.
 *
 * Kommt ein Aufbau, obwohl schon eine Sitzung steht, ersetzt er sie. Die
 * Gegenstelle hat dann ihren Zustand verloren, und ihr altes Gegenstück liegt
 * nur noch im Weg. `ersetzt: true` sagt dem Aufrufer, dass er das sichtbar
 * machen soll.
 *
 * Angewandt wird nur, was das nennende Gerät unterschrieben hat — oder was von
 * einem Konto kommt, das nirgends unterschreiben kann. Siehe `AufbauErgebnis`
 * für die beiden anderen Ausgänge.
 */
export async function verarbeiteBootstrap(
  kontext: DrKontext,
  klartext: string,
): Promise<AufbauErgebnis> {
  let inhalt: BootstrapInhalt
  try {
    const roh = JSON.parse(klartext)
    if (!roh || typeof roh !== 'object' || roh.typ !== DR_INIT_TYP) {
      return { istAufbau: false, ersetzt: false }
    }
    if (!istAufbauInhalt(roh)) return { istAufbau: true, ersetzt: false }
    inhalt = roh
  } catch {
    return { istAufbau: false, ersetzt: false }
  }

  const meins = await eigenesGeraet()
  if (inhalt.fuerGeraet !== meins.kennung) {
    // Kopie für ein anderes Gerät desselben Kontos.
    return { istAufbau: true, ersetzt: false }
  }

  // Derselbe Umschlag beim nächsten Abruf ist kein Neuaufbau. Der öffentliche
  // Teil des mitgereisten Paares wird je Aufbau frisch erzeugt und ist damit
  // die Kennung dieses einen Aufbaus — anders als die Gerätekennung, die über
  // alle Aufbauten hinweg dieselbe bleibt.
  const aufbauKennung = inhalt.paar.publicKey
  const herkunft = { vonKonto: inhalt.vonKonto, vonGeraet: inhalt.vonGeraet }
  // Der übliche Fall, und er soll billig bleiben: ein längst angewandter — oder
  // abgewiesener — Aufbau liegt bei jedem Abruf wieder im Fenster und braucht
  // weder Verzeichnis noch Schloss. Die verbindliche Prüfung steht unten.
  if (await kennstMarke('aufbau', aufbauKennung)) {
    // Ein abgewiesener meldet sich nicht noch einmal, schont aber weiter: die
    // Nachricht hinter ihm liegt ebenso wieder im Fenster.
    if (await kennstMarke('abgewiesen', aufbauKennung)) {
      return { istAufbau: true, ersetzt: false, abgewiesen: true, ...herkunft }
    }
    return { istAufbau: true, ersetzt: false, vonGeraet: inhalt.vonGeraet }
  }

  /**
   * Abweisen und merken, in dieser Reihenfolge.
   *
   * `abgewiesen` zuerst: bricht der Vorgang zwischen beiden Marken ab, prüft
   * der nächste Durchlauf den Aufbau eben noch einmal und meldet ihn ein
   * zweites Mal. Andersherum stünde er als angewandt da, und die Nachricht
   * hinter ihm kostete die laufende Sitzung doch noch.
   */
  const weiseAb = async (): Promise<AufbauErgebnis> => {
    await merkeMarke('abgewiesen', aufbauKennung)
    await merkeMarke('aufbau', aufbauKennung)
    return { istAufbau: true, ersetzt: false, abgelehnt: true, abgewiesen: true, ...herkunft }
  }

  // Nur die beiden Konten dieses Gesprächs dürfen hier eine Sitzung setzen.
  // Ohne diese Prüfung könnte ein Absender eine fremde Kontokennung behaupten
  // und damit eine Sitzung überschreiben, die einem anderen Gespräch gehört.
  if (inhalt.vonKonto !== kontext.eigeneId && inhalt.vonKonto !== kontext.peerId) {
    return weiseAb()
  }

  // Von wem ist er? Die Kontenprüfung oben begrenzt das auf zwei Verzeichnisse,
  // die der Sendepfad ohnehin schon abfragt.
  const beleg = await pruefeGeraeteBeleg(
    inhalt.vonKonto,
    inhalt.vonGeraet,
    aufbauDaten(kontext, inhalt),
    inhalt.sig,
  )
  if (beleg === 'offen' || beleg === 'unbekannt') {
    // Nichts merken: der nächste Durchlauf fragt noch einmal. Ein Gerät, das
    // sich eben erst gemeldet hat, steht dann im Verzeichnis.
    return { istAufbau: true, ersetzt: false, offen: true, ...herkunft }
  }
  if (beleg === 'falsch') {
    // Gemerkt, damit derselbe Umschlag nicht bei jedem Abruf eine neue Meldung
    // auslöst. Angewandt wird nichts — die laufende Sitzung bleibt, wie sie ist,
    // auch gegen die Nachricht dahinter (`DrLeseOptionen.schonen`).
    return weiseAb()
  }

  const id = sitzungsId(meins.kennung, inhalt.vonKonto, inhalt.vonGeraet)

  const paar: RatchetDhKeyPair = {
    algorithm: inhalt.paar.algorithm as RatchetDhKeyPair['algorithm'],
    publicKey: base64ToBytes(inhalt.paar.publicKey),
    privateKey: base64ToBytes(inhalt.paar.privateKey),
  }
  const geheimnis = base64ToBytes(inhalt.geheimnis)

  /**
   * Prüfen, anwenden und merken gehören unter dasselbe Schloss.
   *
   * Die Prüfung oben allein reicht nicht: zwei überlappende Lesedurchläufe
   * sehen denselben Aufbau, beide finden die Marke noch nicht, und beide
   * wenden ihn an. Der zweite setzt damit einen Zustand zurück, der
   * inzwischen schon Nachrichten getragen hat — der Faden verliert seine
   * Sendekette, beide Seiten bauen neu auf, und im Verlauf erscheint ein
   * Sitzungsbruch, den niemand ausgelöst hat.
   *
   * Gemerkt wird **vor** dem Fortschreiben, weil nur so beides im selben
   * Schloss liegt. Scheitert das Schreiben des Zustands danach, bleibt eine
   * Marke ohne Sitzung zurück: die nächste Nachricht dieses Geräts meldet dann
   * einen sichtbaren Bruch, und der nächste Sendevorgang der Gegenstelle baut
   * mit einem neuen Aufbau — also einer neuen Marke — wieder auf. Sichtbar und
   * selbstheilend; die umgekehrte Reihenfolge wäre still und dauerhaft.
   */
  const ersetzt = await schritt<boolean | null>(id, async (alterZustand) => {
    if (await kennstMarke('aufbau', aufbauKennung)) return { ergebnis: null }
    const neu = await initReceiverState({
      sharedSecret: geheimnis,
      dhKeyPair: paar,
      associatedData: gebundeneDaten(kontext, inhalt.vonGeraet, meins.kennung),
    })
    await merkeMarke('aufbau', aufbauKennung)
    // `alterZustand` räumt `schritt` selbst ab; hier zählt nur, ob es ihn gab.
    return { naechster: neu, ergebnis: alterZustand !== null }
  })
  paar.privateKey.fill(0)

  return { istAufbau: true, ersetzt: ersetzt === true, ...herkunft }
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
 *
 * **Wer sendet, muss im Verzeichnis stehen.** Die Gegenstelle prüft jeden
 * Aufbau gegen den Signaturschlüssel, den das Verzeichnis für dieses Gerät
 * führt. Ein Gerät, dessen Anmeldung beim Start gescheitert ist, schickte
 * sonst Aufbauten, die drüben niemand prüfen kann — seine Nachrichten blieben
 * dort still liegen, und hier sähe alles zugestellt aus. `geraetVeroeffentlichen`
 * kostet nach dem ersten Erfolg nichts mehr; scheitert es, scheitert das Senden
 * mit einem Fehler, der sagt, woran. Dass das Gerät seither nicht entfernt
 * wurde, prüft `eigenesVerzeichnis`.
 */
export async function baueZustellungen(
  kontext: DrKontext,
  klartext: string,
  basisUuid: string,
): Promise<DrZustellung[]> {
  const meins = await geraetVeroeffentlichen()
  const eigene = (await eigenesVerzeichnis(kontext.eigeneId, meins.kennung)).filter(
    (g) => g.device_id !== meins.kennung,
  )
  const fremde = await verlangeGeraeteVon(kontext.peerId)

  const ziele = [
    ...fremde.map((g) => ({ geraet: g, konto: kontext.peerId })),
    ...eigene.map((g) => ({ geraet: g, konto: kontext.eigeneId })),
  ]

  const zustellungen: DrZustellung[] = []
  const gescheitert: unknown[] = []
  for (const ziel of ziele) {
    const zielGeraet = ziel.geraet.device_id
    const id = sitzungsId(meins.kennung, ziel.konto, zielGeraet)
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
              meins,
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

/**
 * Die eigenen Geräte — und die Gewissheit, dass dieses darunter ist.
 *
 * `geraetVeroeffentlichen` meldet sich je Sitzung einmal. Wird das Gerät
 * danach in der Geräteliste entfernt, bleibt dieser Tab offen und sendete
 * weiter Aufbauten, die drüben niemand prüfen kann: sie blieben als
 * „unbekannt" zurückgestellt, bis sie aus dem Fenster rutschen. Sich neu
 * einzutragen wäre die bequeme Antwort und nähme dem Entfernen, was der
 * Dialog verspricht — also `DrGeraetNichtEingetragenError`.
 *
 * Frisch gefragt, weil eine zehn Minuten alte Liste das Gerät noch führen
 * kann, wenn es längst entfernt ist; gedrosselt ist das über `FRISCH_MS`.
 * Fehlt es, wird noch einmal ohne Zwischenspeicher nachgesehen: eine Liste
 * von vor der eigenen Anmeldung ist kein Befund. Keine Antwort auch nicht —
 * ob das Relais erreichbar ist, entscheidet der Versand selbst.
 */
async function eigenesVerzeichnis(eigeneId: number, kennung: string): Promise<E2eeGeraetItem[]> {
  const fuehrt = (liste: E2eeGeraetItem[]) => liste.some((g) => g.device_id === kennung)
  try {
    const liste = await verzeichnisVon(eigeneId, { frisch: true })
    if (fuehrt(liste)) return liste
    vergessenGeraete(eigeneId)
    const nochmal = await verzeichnisVon(eigeneId, { frisch: true })
    if (fuehrt(nochmal)) return nochmal
  } catch {
    return geraeteVon(eigeneId)
  }
  throw new DrGeraetNichtEingetragenError()
}

// ==========================================
// Lesen
// ==========================================

/**
 * Der Klartextspeicher genau dieses Umschlags.
 *
 * Beide Hälften zusammen, weil nur das Paar trägt: gelesen und geschrieben wird
 * innerhalb des Sitzungsschlosses, und zwischen beidem darf nichts passieren.
 * Eine `lies`, die ihren Fehler als `null` ausgibt, macht die Klammer wertlos —
 * „Ablage kaputt" sieht dann aus wie „noch nicht geöffnet".
 */
export interface KlartextAblage {
  /** Der abgelegte Klartext, oder `null`. Wirft, wenn die Ablage nicht antwortet. */
  lies(): Promise<string | null>
  lege(text: string): Promise<void>
}

/**
 * Öffnet einen Umschlag dieses Verfahrens.
 *
 * Alles am Zustand passiert innerhalb des Sitzungsschlosses und in dieser
 * Reihenfolge: nachsehen, ob ein anderer Durchlauf den Umschlag schon geöffnet
 * hat; entschlüsseln; ablegen; fortschreiben. Wirft `lege`, bleibt der Zustand
 * stehen und derselbe Umschlag ist beim nächsten Durchlauf erneut lesbar. Das
 * ist der einzige Schutz gegen einen verbrauchten Nachrichtenschlüssel ohne
 * abgelegten Klartext.
 */
export async function liesDrUmschlag(
  kontext: DrKontext,
  umschlag: string,
  klartext: KlartextAblage,
  optionen: DrLeseOptionen = {},
): Promise<DrLesung> {
  const kopf = lieseKopf(umschlag)
  if (!kopf) return { art: 'unbekannt' }

  const meins = await eigenesGeraet()
  if (kopf.vonGeraet === meins.kennung) return { art: 'eigen' }
  if (kopf.fuerGeraet !== meins.kennung) return { art: 'fremd' }
  if (kopf.vonKonto !== kontext.eigeneId && kopf.vonKonto !== kontext.peerId) {
    return { art: 'fremd' }
  }

  // Ein Umschlag wird genau einmal beurteilt. Er bleibt bis zum Ende seiner
  // Aufbewahrung in der Mailbox liegen, und der Lesepfad holt bei jedem Abruf
  // das ganze Fenster neu; abgelegt wird nur Klartext, ein unlesbarer Umschlag
  // also nie. Ohne diese Marke wertete ihn jeder Durchlauf erneut als Bruch —
  // und ein Bruch wirft die Sitzung weg. Am laufenden System hiess das: bei
  // jedem Öffnen des Gesprächs starb die gerade funktionierende Sitzung, die
  // Systemzeile erschien wieder, und die eigenen Nachrichten kamen nicht mehr
  // an. Erkannt wird er am Rumpf, der je Nachricht ein anderer ist.
  const rumpfHash = (await sha256Hex(utf8ToBytes(kopf.rumpf))).slice(0, 32)
  const marke = `${kopf.vonKonto}:${kopf.vonGeraet}:${rumpfHash}`
  if (await kennstMarke('bruch', marke)) return { art: 'beurteilt' }
  if (await kennstMarke('abgewiesen', marke)) return { art: 'abgewiesen' }

  // Der Aufbau davor ist noch nicht entschieden. Jetzt zu öffnen hiesse, gegen
  // die alte Sitzung oder gegen gar keine zu laufen — und das endete als Bruch
  // mit Marke, die Nachricht wäre für immer weg.
  if (optionen.zurueckstellen?.(kopf.vonKonto, kopf.vonGeraet)) return { art: 'zurueckgestellt' }

  /**
   * Was sich nicht öffnen lässt, ist ein Bruch — ausser, von diesem Gerät liegt
   * ein abgewiesener Aufbau im Fenster.
   *
   * Dann ist es die Nachricht aus der gefälschten Sitzung, und als Bruch
   * gezählt kippte sie die echte doch noch. Gemerkt wird sie trotzdem, unter
   * eigener Marke: beim nächsten Abruf bleibt sie still, auch wenn der Aufbau
   * bis dahin aus dem Fenster gerutscht ist.
   */
  const unlesbar = async (grund: string): Promise<DrLesung> => {
    if (optionen.schonen?.(kopf.vonKonto, kopf.vonGeraet)) {
      await merkeMarke('abgewiesen', marke)
      return { art: 'abgewiesen' }
    }
    await merkeMarke('bruch', marke)
    return { art: 'bruch', vonKonto: kopf.vonKonto, vonGeraet: kopf.vonGeraet, grund }
  }

  const id = sitzungsId(meins.kennung, kopf.vonKonto, kopf.vonGeraet)
  if (!(await hatSitzung(id))) return unlesbar('keine Sitzung')

  let nachricht
  try {
    nachricht = deserializeRatchetMessage(bytesToUtf8(base64ToBytes(kopf.rumpf)))
  } catch {
    return { art: 'unbekannt' }
  }

  try {
    const text = await schritt<string>(id, async (zustand) => {
      // Hat ihn ein anderer Durchlauf in der Zwischenzeit geöffnet? Dann ist
      // sein Nachrichtenschlüssel verbraucht, und ein zweiter Versuch wäre
      // nicht bloß vergeblich: er sähe aus wie eine Fälschung und kostete die
      // Sitzung. Ohne `naechster` bleibt der abgelegte Zustand unangetastet.
      const schon = await klartext.lies()
      if (schon !== null) return { ergebnis: schon }

      if (!zustand) throw new Error('Sitzung verschwunden')
      const { nextState, plaintext } = await decryptMessage(zustand, nachricht)
      const text = bytesToUtf8(plaintext)
      // Erst ablegen, dann den Zustand fortschreiben: ein verbrauchter
      // Nachrichtenschlüssel ohne abgelegten Klartext wäre eine verlorene
      // Nachricht, und zwar ohne jede Meldung.
      await klartext.lege(text)
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
    return unlesbar(name)
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
  const meins = await eigenesGeraet()
  await verwirfSitzung(sitzungsId(meins.kennung, vonKonto, vonGeraet))
}
