/**
 * Das Geräteverzeichnis: Abruf, Zwischenspeicher und Belegprüfung.
 *
 * Die Liste eines Kontos ändert sich selten, der Abruf scheitert oft nur kurz.
 * Deshalb ein Zwischenspeicher — aber einer, in dem nur geprüfte Listen
 * liegen: wer hier liest, bekommt nie ein Gerät, das `vertrauteGeraete` nicht
 * durchgelassen hat.
 */

import { getE2eeGeraete, type E2eeGeraetItem } from '@/api/social'
import { pruefe } from '../absenderSignatur'
import { ramGeraetFuer } from './geraeteAblage'
import { setzeEigeneFreigabe, vertrauteGeraete } from './geraeteVertrauen'

const CACHE_FRIST_MS = 600000
/**
 * Wie alt die Liste höchstens sein darf, wenn eine Prüfung „frisch" verlangt.
 * Kurz genug für ein Gerät, das sich gerade angemeldet hat; lang genug, dass
 * eine Flut gefälschter Umschläge nicht in eine Flut von Abrufen übersetzt.
 */
const FRISCH_MS = 30_000

/**
 * Zwischenspeicher je Konto **und** Sicht.
 *
 * Die freigegebene Liste und die Liste mit wartenden Geräten sind zwei
 * Antworten auf zwei Fragen. Ein gemeinsamer Eintrag ließe die zweite die
 * erste überschreiben — und ein Gerät, das gerade wartet, wäre für den eigenen
 * Sendepfad plötzlich „nicht eingetragen".
 */
const geraeteCache = new Map<
  string,
  { geraete: E2eeGeraetItem[]; geholtAm: number }
>()

function cacheSchluessel(userId: number, mitWartenden: boolean, eigenes: boolean): string {
  // Die Sicht „mit dem eigenen Gerät" ist eine andere Antwort als die ohne:
  // die eigene wartende Zeile steht nur dort. Ein gemeinsamer Eintrag nähme
  // dem Sendepfad genau die Zeile, für die er herkommt.
  return `${userId}:${mitWartenden ? 'alle' : 'frei'}:${eigenes ? 'eigen' : 'fremd'}`
}

/**
 * Die Geräte eines Kontos, zehn Minuten gecacht.
 *
 * Die Frist ist der Grund für den Cache, nicht die Ersparnis: ohne sie hielte
 * ein lange offener Tab eine veraltete Liste für immer fest und verschlüsselte
 * weiter gegen ein Gerät, das es nicht mehr gibt — oder gar nicht erst gegen
 * ein neues. `vergessenGeraete` räumt denselben Eintrag, wenn sich beim Senden
 * herausstellt, dass er nicht mehr stimmt.
 */
export async function geraeteVon(userId: number): Promise<E2eeGeraetItem[]> {
  try {
    return await holeGeraete(userId)
  } catch {
    return []
  }
}

/**
 * Der veröffentlichte Signaturschlüssel eines fremden Geräts, oder `null`.
 *
 * `null` heißt „dieses Gerät hat noch keinen" und ist der Normalfall für
 * Installationen, die seit der Umstellung nicht neu gestartet wurden. Was ein
 * Leseweg daraus macht, entscheidet er selbst — hier steht keine Regel,
 * sondern nur die Auskunft.
 */
export async function signaturSchluesselVon(
  userId: number,
  deviceId: string,
): Promise<string | null> {
  const schluesselIn = (geraete: E2eeGeraetItem[]) =>
    geraete.find((g) => g.device_id === deviceId)?.signing_public_key || null

  const gemerkt = schluesselIn(await geraeteVon(userId))
  if (gemerkt) return gemerkt

  // Die gemerkte Liste darf bis zu zehn Minuten alt sein, ein Gerät kann
  // seitdem dazugekommen sein. Bis 09/2026 galt dann jede seiner Nachrichten
  // als gefälscht, bis die Liste ablief: die des eigenen, gerade angemeldeten
  // Geräts ebenso wie die vom neuen Telefon eines Freundes. Also einmal frisch
  // nachsehen, höchstens alle `FRISCH_MS` je Konto. Die frische Liste läuft
  // durch dieselbe Vertrauensprüfung, und die Unterschrift wird danach genauso
  // streng geprüft.
  try {
    return schluesselIn(await verzeichnisVon(userId, { frisch: true }))
  } catch {
    return null
  }
}

/**
 * Hat dieses Konto mindestens ein Gerät mit Signaturschlüssel?
 *
 * Die Frage hinter der Downgrade-Schranke: wer beglaubigen *kann*, muss es
 * auch. Ohne sie nähme ein Fälscher einfach die Signatur weg und stünde wieder
 * da, wo er vorher stand.
 *
 * Ein misslungener Abruf antwortet `false` — lieber eine Nachricht ungeprüft
 * anzeigen als bei jedem Netzwackler den halben Verlauf verschwinden lassen.
 * Die Liste kommt aus demselben zehn Minuten alten Zwischenspeicher, den der
 * Sendeweg ohnehin füllt.
 */
export async function kontoNutztSignaturen(userId: number): Promise<boolean> {
  return (await geraeteVon(userId)).some((g) => Boolean(g.signing_public_key))
}

/**
 * Was eine Unterschrift über einen Schlüsselumschlag sagt.
 *
 * - `echt` — das Verzeichnis führt dieses Gerät unter diesem Konto, und die
 *   Unterschrift passt zu seinem Signaturschlüssel.
 * - `altbestand` — ohne Unterschrift, von einem Gerät im Verzeichnis, dessen
 *   Konto **nirgends** einen Signaturschlüssel führt. Es kann es nicht besser.
 * - `falsch` — widerlegt: die Unterschrift passt nicht zum Schlüssel im
 *   Verzeichnis, oder sie fehlt bei einem Konto, das unterschreiben könnte
 *   (die Downgrade-Schranke).
 * - `unbekannt` — das Verzeichnis führt dieses Gerät nicht, oder noch ohne
 *   Signaturschlüssel. Das ist kein Nein: ein Gerät, das sich gerade eben
 *   gemeldet hat, steht auch nach dem frischen Abruf womöglich noch nicht in
 *   einer Liste, die vor ein paar Sekunden geholt wurde.
 * - `offen` — das Verzeichnis war nicht zu erreichen. Weder ja noch nein; der
 *   Aufrufer versucht es später wieder, statt etwas zu entscheiden.
 */
export type GeraeteBeleg = 'echt' | 'altbestand' | 'falsch' | 'unbekannt' | 'offen'

/**
 * Prüft, ob ein Umschlag von dem Gerät stammt, das er nennt.
 *
 * Für Umschläge, die **Schlüsselmaterial** tragen: den Sitzungsaufbau des
 * Double Ratchet und die Übergabe des Notizschlüssels. Beide sind hybrid gegen
 * den Geräteschlüssel des Empfängers versiegelt, und das beweist nur, *für*
 * wen sie sind — nie, *von* wem. Der Geräteschlüssel steht im Verzeichnis und
 * kann von jedem benutzt werden, vom Server ohnehin, und in die eigene Geräte-Mailbox
 * darf jeder Freund, jedes Gruppenmitglied und jedes Gegenüber eines
 * Direktchats Steuerumschläge legen.
 *
 * Anders als `pruefeNutzlast` trifft diese Funktion die Downgrade-Entscheidung
 * selbst: für Schlüsselmaterial gibt es keinen Leseweg, der sie besser wüsste.
 *
 * Ein Nein aus der zehn Minuten alten Liste ist noch keins. Ein Gerät, das sich
 * eben erst angemeldet hat, steht dort nicht — also wird vor jedem `falsch`
 * und `unbekannt` einmal frisch nachgesehen. `FRISCH_MS` hält das in Grenzen:
 * wer mit gefälschten Umschlägen um sich wirft, erzwingt höchstens einen Abruf
 * je halbe Minute.
 */
export async function pruefeGeraeteBeleg(
  userId: number,
  deviceId: string,
  daten: string,
  signatur: unknown,
): Promise<GeraeteBeleg> {
  const urteil = async (frisch: boolean): Promise<GeraeteBeleg> => {
    const liste = await holeGeraete(userId, frisch ? FRISCH_MS : CACHE_FRIST_MS)
    const eintrag = liste.find((g) => g.device_id === deviceId)
    if (!eintrag) return 'unbekannt'
    if (typeof signatur === 'string' && signatur) {
      if (!eintrag.signing_public_key) return 'unbekannt'
      return (await pruefe(daten, signatur, eintrag.signing_public_key)) ? 'echt' : 'falsch'
    }
    return liste.some((g) => Boolean(g.signing_public_key)) ? 'falsch' : 'altbestand'
  }

  try {
    const erst = await urteil(false)
    // `altbestand` ebenso: die Nachsicht für Konten ohne Signaturschlüssel darf
    // nicht aus der alten Liste kommen. Unterschreibt das Konto seit eben, wäre
    // sie sonst bis zu zehn Minuten lang der Weg an der Downgrade-Schranke
    // vorbei (Durchsicht vom 23.09.).
    return erst === 'falsch' || erst === 'unbekannt' || erst === 'altbestand'
      ? await urteil(true)
      : erst
  } catch {
    return 'offen'
  }
}

/**
 * Derselbe Abruf, aber ohne die leere Liste als Notausgang.
 *
 * Wer nur anzeigt, kommt mit `geraeteVon` und einer leeren Liste zurecht. Wer
 * verschlüsselt, darf einen misslungenen Abruf nicht für „niemand angemeldet"
 * halten: ohne Antwort weiss niemand, ob ein Gerät da ist, und der Benutzer
 * bekäme eine Aussage über die Gegenstelle zu lesen, die gar nicht geprüft
 * wurde.
 *
 * `frist` ist das Höchstalter der gecachten Liste. Wer eine kürzere verlangt
 * (`pruefeGeraeteBeleg` vor einem Nein), bekommt bei einem Ausfall **keine**
 * alte Liste zurück, sondern den Fehler: er fragt ja gerade, weil er der alten
 * nicht traut.
 */
async function holeGeraete(
  userId: number,
  frist = CACHE_FRIST_MS,
  {
    mitWartenden = false,
    eigenes,
  }: {
    mitWartenden?: boolean
    /** Dieses Gerät, wie der Aufrufer es kennt. Siehe `vertrauteGeraete`. */
    eigenes?: { kennung: string; publicKeyJwk: string }
  } = {},
): Promise<E2eeGeraetItem[]> {
  const schluessel = cacheSchluessel(userId, mitWartenden, Boolean(eigenes))
  const cached = geraeteCache.get(schluessel)
  if (cached && Date.now() - cached.geholtAm < frist) {
    return cached.geraete
  }
  try {
    // Gecacht wird die geprüfte Liste: wer aus dem Cache liest, bekommt nie
    // ein Gerät, das die Prüfung nicht bestanden hat.
    //
    // `include_unapproved` darf nur der Kontoinhaber selbst; der Sendepfad
    // fragt danach für das **eigene** Konto, um ein wartendes Gerät nicht mit
    // einem entfernten zu verwechseln. Fremde Konten sehen weiter nur die
    // freigegebenen.
    const roh = await getE2eeGeraete(userId, mitWartenden)
    // Steht dieses Gerät in der freigegebenen Liste, hat ein anderes es
    // inzwischen freigegeben — der Hinweis kann weg. Ein wartendes zählt hier
    // nicht.
    const lokal = ramGeraetFuer(userId)
    if (lokal && roh.some((g) => g.device_id === lokal.kennung && g.is_approved !== false)) {
      setzeEigeneFreigabe(true)
    }
    const geraete = await vertrauteGeraete(userId, roh, eigenes)
    geraeteCache.set(schluessel, { geraete, geholtAm: Date.now() })
    return geraete
  } catch (fehler) {
    // Ein abgelaufener Eintrag ist immer noch besser als gar keiner: die
    // Geräteliste ändert sich selten, der Abruf scheitert oft nur kurz.
    if (cached && frist === CACHE_FRIST_MS) return cached.geraete
    throw fehler
  }
}

/**
 * Die Geräte eines Kontos, und bei einem Ausfall ein Fehler statt einer leeren
 * Liste.
 *
 * Für Prüfwege, die „kein Gerät" von „keine Antwort" unterscheiden müssen —
 * etwa, bevor der Notizschlüssel an ein eigenes Gerät geht.
 *
 * `frisch` verlangt eine Liste, die höchstens `FRISCH_MS` alt ist: für ein
 * Gerät, das sich gerade erst gemeldet haben könnte. Gedrosselt ist das
 * trotzdem — öfter als einmal je halbe Minute fragt niemand nach.
 */
export async function verzeichnisVon(
  userId: number,
  {
    frisch = false,
    mitWartenden = false,
    eigenes,
  }: {
    frisch?: boolean
    mitWartenden?: boolean
    /** Dieses Gerät, wie der Aufrufer es kennt. Siehe `vertrauteGeraete`. */
    eigenes?: { kennung: string; publicKeyJwk: string }
  } = {},
): Promise<E2eeGeraetItem[]> {
  return holeGeraete(userId, frisch ? FRISCH_MS : CACHE_FRIST_MS, { mitWartenden, eigenes })
}

export function vergessenGeraete(userId: number): void {
  for (const mitWartenden of [false, true]) {
    for (const eigenes of [false, true]) {
      geraeteCache.delete(cacheSchluessel(userId, mitWartenden, eigenes))
    }
  }
}

/** Leert den gesamten Zwischenspeicher im Arbeitsspeicher. */
export function leereVerzeichnisRam(): void {
  geraeteCache.clear()
}

/** Der Empfänger hat noch kein Gerät angemeldet — es gibt nichts, wogegen verschlüsselt werden kann. */
export class E2eeKeinGeraetError extends Error {
  constructor(public readonly userId: number) {
    super('Für diesen Empfänger ist kein Gerät angemeldet')
    this.name = 'E2eeKeinGeraetError'
  }
}

/**
 * Wie `geraeteVon`, wirft aber statt eine leere Liste zu liefern.
 *
 * Der Sendepfad braucht diese Variante. Früher gab es an dieser Stelle einen
 * symmetrischen Notweg, dessen Schlüssel sich allein aus den beiden
 * Benutzerkennungen ergab — für den Server also nachbaubar. Lieber nicht senden
 * und es sagen, als eine Zusage brechen, die niemand nachprüfen kann.
 */
export async function verlangeGeraeteVon(userId: number): Promise<E2eeGeraetItem[]> {
  const geraete = await holeGeraete(userId)
  if (geraete.length === 0) throw new E2eeKeinGeraetError(userId)
  return geraete
}
