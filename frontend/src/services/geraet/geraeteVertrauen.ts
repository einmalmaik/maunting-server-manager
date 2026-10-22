/**
 * Das Vertrauen in Gerätelisten: Signaturkette, Belege, Sicherheitsnummer.
 *
 * `is_approved` ist ein Wort des Servers, und ein Server kann ein Gerät
 * eintragen und freigeben, wie er will. Hier steht, wem dieser Client glaubt,
 * und wann er warnt.
 */

import { utf8ToBytes } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import type { E2eeGeraetItem } from '@/api/social'
import { pruefe } from '../absenderSignatur'
import { ramGeraetFuer } from './geraeteAblage'

/**
 * Was ein freigegebenes Gerät unterschreibt, wenn es ein neues freigibt.
 * Dieselben Bytes wie `e2ee_device_service.freigabe_daten` im Backend.
 */
export async function freigabeDaten(
  userId: number,
  deviceId: string,
  publicKey: string,
  signingPublicKey: string,
): Promise<string> {
  const k = await sha256Hex(utf8ToBytes(publicKey))
  const s = await sha256Hex(utf8ToBytes(signingPublicKey || ''))
  return `msm:device-approval:v2:${userId}:${deviceId}:${k}:${s}`
}

/** Was ein freigegebenes Gerät unterschreibt, wenn es ein Gerät entfernt. */
export async function entfernenDaten(
  userId: number,
  deviceId: string,
  publicKey: string,
): Promise<string> {
  const k = await sha256Hex(utf8ToBytes(publicKey))
  return `msm:device-removal:v1:${userId}:${deviceId}:${k}`
}

/** Je Gerät: Hash des Verschlüsselungsschlüssels und der Signaturschlüssel. */
type VertrautesGeraet = { k: string; s: string }
type VertrauteListe = Record<string, VertrautesGeraet>

/**
 * Neues Präfix, nicht `msm_bekannte_geraete:`. Die alte Liste hielt nur fest,
 * was der Server je gezeigt hatte — auch Geräte, die er selbst eingetragen
 * haben könnte. Eine Liste mit diesem Präfix beginnt beim ersten Abruf nach
 * dem Update von vorn, und ab da zählt die Kette.
 */
const VERTRAUT_PRAEFIX = 'msm_vertraute_geraete:'
const vertrauteImRam = new Map<number, VertrauteListe>()

function liesVertraute(userId: number): VertrauteListe | null {
  try {
    if (typeof localStorage !== 'undefined') {
      const roh = localStorage.getItem(`${VERTRAUT_PRAEFIX}${userId}`)
      if (roh) {
        const wert = JSON.parse(roh)
        if (wert && typeof wert === 'object') return wert as VertrauteListe
      }
    }
  } catch {
    // Unlesbar heißt: wie Erstkontakt.
  }
  return vertrauteImRam.get(userId) ?? null
}

function schreibeVertraute(userId: number, liste: VertrauteListe): void {
  vertrauteImRam.set(userId, liste)
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(`${VERTRAUT_PRAEFIX}${userId}`, JSON.stringify(liste))
    }
  } catch {
    // Voller Speicher: der RAM hält es bis zum Neuladen.
  }
}

/** Vergisst die Vertrauensliste eines Kontos im Arbeitsspeicher. */
export function leereVertraute(): void {
  vertrauteImRam.clear()
}

/**
 * - `neues_geraet`: ein Gerät kam dazu, von einem vertrauten freigegeben.
 * - `unbestaetigt`: der Server nennt ein Gerät, das keine gültige Freigabe
 *   trägt — neu oder mit neuem Schlüssel. Es bekommt nichts.
 * - `freigabe_unbestaetigt`: ein Gerät trägt eine gültige Freigabe eines
 *   anderen, serverseitig freigegebenen Geräts, das dieser Client noch nicht
 *   verankert hat. Es bekommt Nachrichten, aber die Sicherheitsnummer ist
 *   noch nicht verglichen.
 * - `konto_neustart`: keines der bekannten Geräte ist mehr da. Das ist der
 *   Neustart nach Verlust aller Geräte — oder ein Server, der Geräte
 *   austauscht. Unterscheiden kann das nur der Mensch: Sicherheitsnummer.
 */
export type SchluesselWarnungTyp =
  | 'neues_geraet'
  | 'unbestaetigt'
  | 'freigabe_unbestaetigt'
  | 'konto_neustart'

export interface SchluesselWarnungEvent {
  userId: number
  typ: SchluesselWarnungTyp
  geraete: string[]
}

export type SchluesselWarnungListener = (event: SchluesselWarnungEvent) => void
const schluesselWarnungListeners = new Set<SchluesselWarnungListener>()

export function onSchluesselWarnung(listener: SchluesselWarnungListener): () => void {
  schluesselWarnungListeners.add(listener)
  return () => {
    schluesselWarnungListeners.delete(listener)
  }
}

function melde(event: SchluesselWarnungEvent): void {
  for (const listener of schluesselWarnungListeners) {
    try {
      listener(event)
    } catch {
      // Ein kaputter Zuhörer hält die Prüfung nicht auf.
    }
  }
}

/**
 * Die Geräte eines Kontos, denen dieser Client glaubt.
 *
 * `is_approved` ist ein Wort des Servers, und ein Server kann ein Gerät
 * eintragen und freigeben, wie er will. Geglaubt wird deshalb nur:
 *
 * 0. dem **eigenen** Gerät, wenn Kennung und öffentlicher Schlüssel zu dem
 *    passen, das hier liegt — auch wartend. Ein anderes wartendes Gerät des
 *    eigenen Kontos ist kein Ziel (`is_approved === false`); es hat nie etwas
 *    bekommen, und der Server entscheidet nicht, wer mithört;
 * 1. beim ersten Kontakt allem, was er zeigt (anders geht es nicht — das ist
 *    der Moment für die Sicherheitsnummer);
 * 2. danach jedem Gerät mit denselben Schlüsseln wie beim letzten Mal;
 * 3. jedem Gerät, das ein vertrautes Gerät über genau diese Schlüssel
 *    freigegeben hat (`approved_by` + `approval_signature`), auch in Ketten.
 *
 * Der Rest bekommt nichts und löst eine Warnung aus. Sind gar keine bekannten
 * Geräte mehr da, beginnt die Liste mit Warnung neu — sonst wäre ein Konto
 * nach Verlust aller Geräte für immer stumm.
 *
 * Das eigene Gerät zählt immer: seine Schlüssel liegen hier.
 *
 * `eigenes` ist dieses Gerät, wie der Aufrufer es eben veröffentlicht hat.
 * Ohne die Angabe wird die Ablage gefragt. Der Sendepfad übergibt es, weil er
 * die Kennung in der Hand hat und nicht davon abhängen soll, ob der
 * Arbeitsspeicher der Ablage gerade gefüllt ist.
 */
export async function vertrauteGeraete(
  userId: number,
  liste: E2eeGeraetItem[],
  eigenes?: { kennung: string; publicKeyJwk: string },
): Promise<E2eeGeraetItem[]> {
  const lokal = ramGeraetFuer(userId)
  const eigenesGeraet =
    eigenes ?? (lokal ? { kennung: lokal.kennung, publicKeyJwk: lokal.paar.publicKeyJwk } : null)
  const eigenesKonto = angemeldetesKonto()

  /**
   * Ein wartendes Gerät des **eigenen** Kontos ist kein Ziel — außer diesem.
   *
   * `is_approved` ist ein Wort des Servers; wer es für ein Ziel hält, schickt
   * seine Nachrichten an einen Schlüssel, den der Server selbst eingetragen
   * haben kann. Ausgenommen ist allein das Gerät, dessen privater Schlüssel
   * hier liegt: der Vergleich von Kennung **und** öffentlichem Schlüssel mit
   * der örtlichen Ablage ist der Nachweis, den nur dieses Gerät führen kann.
   *
   * Für fremde Konten kommt `mitWartenden` gar nicht erst an — der Server gibt
   * Dritten nur freigegebene Geräte heraus.
   */
  const wartend: string[] = []
  const sicht =
    eigenesKonto === null || userId !== eigenesKonto
      ? liste
      : liste.filter((g) => {
          if (g.is_approved !== false) return true
          const istDieses =
            eigenesGeraet?.kennung === g.device_id &&
            eigenesGeraet.publicKeyJwk === g.public_key
          if (!istDieses) wartend.push(g.device_id)
          return istDieses
        })
  if (wartend.length > 0) melde({ userId, typ: 'unbestaetigt', geraete: wartend })

  const hashes = await Promise.all(sicht.map((g) => sha256Hex(utf8ToBytes(g.public_key))))
  const fingerabdruck = (i: number): VertrautesGeraet => ({
    k: hashes[i],
    s: sicht[i].signing_public_key || '',
  })
  const bekannt = liesVertraute(userId)

  if (bekannt === null) {
    const neu: VertrauteListe = {}
    sicht.forEach((g, i) => {
      neu[g.device_id] = fingerabdruck(i)
    })
    schreibeVertraute(userId, neu)
    return sicht
  }

  const vertraut = new Map<string, VertrautesGeraet>()
  const offen: number[] = []
  sicht.forEach((g, i) => {
    const fp = fingerabdruck(i)
    const alt = bekannt[g.device_id]
    const istEigenes =
      eigenesGeraet?.kennung === g.device_id && eigenesGeraet.publicKeyJwk === g.public_key
    // Ein Signaturschlüssel, der zu einem bekannten Gerät nachkommt, ist die
    // Nachrüstung des Bestands und kein Austausch.
    const gleich = alt && alt.k === fp.k && (alt.s === fp.s || alt.s === '')
    if (istEigenes || gleich) vertraut.set(g.device_id, fp)
    else offen.push(i)
  })

  const neuFreigegeben: string[] = []
  let weiter = true
  while (weiter && offen.length > 0) {
    weiter = false
    for (let n = 0; n < offen.length; n += 1) {
      const i = offen[n]
      const g = sicht[i]
      const von = g.approved_by
      if (!von || von === g.device_id || !g.approval_signature) continue
      const schluessel = vertraut.get(von)?.s || bekannt[von]?.s
      if (!schluessel) continue
      const daten = await freigabeDaten(userId, g.device_id, g.public_key, g.signing_public_key)
      if (!(await pruefe(daten, g.approval_signature, schluessel))) continue
      vertraut.set(g.device_id, fingerabdruck(i))
      neuFreigegeben.push(g.device_id)
      offen.splice(n, 1)
      n -= 1
      weiter = true
    }
  }

  /**
   * Die Brücke für fremde Konten.
   *
   * Ein Gerät, das ein anderes, serverseitig freigegebenes Gerät signiert hat,
   * ist nicht bloß ein Wort des Servers: Die Unterschrift deckt Kennung und
   * beide Schlüssel. Fehlt nur die lokale Verankerung des Freigebers, wird das
   * Gerät trotzdem als Ziel aufgenommen und sichtbar gewarnt. Ohne diese
   * Brücke fiel ein auf dem Zweitgerät freigegebenes Pontis still aus jeder
   * Auffächerung — der Browser sendete nie dorthin, während Pontis den Browser
   * wegen seines frischen Vertrauensstands erreichte.
   *
   * Nicht überbrückt werden: das eigene Konto (dort bleibt die Kette strikt),
   * eine Selbstfreigabe (`approved_by === device_id`), eine fehlende oder
   * falsche Unterschrift und ein Freigeber, der selbst nicht freigegeben ist.
   * Brücken-Geräte werden nicht in `bekannt` geschrieben; die Warnung kommt
   * bei jedem Abruf erneut, bis der Mensch die Sicherheitsnummer verglichen
   * oder ein verankertes Gerät die Kette fortgesetzt hat.
   */
  const bruecke = new Set<string>()
  if (eigenesKonto !== null && userId !== eigenesKonto) {
    for (let n = 0; n < offen.length; n += 1) {
      const g = sicht[offen[n]]
      const von = g.approved_by
      if (!von || von === g.device_id || !g.approval_signature) continue
      const freigeber = sicht.find(
        (kandidat) => kandidat.device_id === von && kandidat.is_approved !== false,
      )
      if (!freigeber?.signing_public_key) continue
      const daten = await freigabeDaten(userId, g.device_id, g.public_key, g.signing_public_key)
      if (!(await pruefe(daten, g.approval_signature, freigeber.signing_public_key))) continue
      bruecke.add(g.device_id)
      offen.splice(n, 1)
      n -= 1
    }
  }
  if (bruecke.size > 0) {
    melde({ userId, typ: 'freigabe_unbestaetigt', geraete: [...bruecke] })
  }

  if (vertraut.size === 0 && bruecke.size === 0 && sicht.length > 0) {
    const neu: VertrauteListe = {}
    sicht.forEach((g, i) => {
      neu[g.device_id] = fingerabdruck(i)
    })
    schreibeVertraute(userId, neu)
    melde({ userId, typ: 'konto_neustart', geraete: sicht.map((g) => g.device_id) })
    return sicht
  }

  // Entfernte Geräte bleiben in der Liste: ihr Signaturschlüssel beglaubigt
  // weiter, was sie freigegeben haben, bevor sie gingen. Brücken-Geräte
  // bleiben bewusst draußen, damit die Warnung nicht still verschwindet.
  schreibeVertraute(userId, { ...bekannt, ...Object.fromEntries(vertraut) })
  if (neuFreigegeben.length > 0) melde({ userId, typ: 'neues_geraet', geraete: neuFreigegeben })
  if (offen.length > 0) {
    melde({ userId, typ: 'unbestaetigt', geraete: offen.map((i) => sicht[i].device_id) })
  }
  return sicht.filter((g) => vertraut.has(g.device_id) || bruecke.has(g.device_id))
}

/**
 * Hat ein Gerät aus `liste` dieses freigegeben — belegt durch seine Unterschrift?
 *
 * `liste` muss eine von `vertrauteGeraete` geprüfte sein (`verzeichnisVon`,
 * `geraeteVon`): dann ist der Freigeber selbst vertraut, und die Unterschrift
 * kann nur von ihm stammen. `approved_by` allein ist das Wort des Servers und
 * zählt nicht.
 */
export async function freigabeBelegt(
  userId: number,
  geraet: E2eeGeraetItem,
  liste: E2eeGeraetItem[],
): Promise<boolean> {
  const von = geraet.approved_by
  if (!von || von === geraet.device_id || !geraet.approval_signature) return false
  const freigeber = liste.find((g) => g.device_id === von)
  if (!freigeber?.signing_public_key) return false
  try {
    const daten = await freigabeDaten(
      userId,
      geraet.device_id,
      geraet.public_key,
      geraet.signing_public_key,
    )
    return await pruefe(daten, geraet.approval_signature, freigeber.signing_public_key)
  } catch {
    return false
  }
}

/** Ob der Server dieses Gerät zuletzt als freigegeben gemeldet hat. `null`: noch nicht gemeldet. */
let eigeneFreigabe: boolean | null = null
const eigeneFreigabeListeners = new Set<(freigegeben: boolean | null) => void>()

export function eigenesGeraetFreigegeben(): boolean | null {
  return eigeneFreigabe
}

/** Meldet jede Änderung von `eigenesGeraetFreigegeben` — für den Hinweis im Messenger. */
export function onEigeneFreigabe(listener: (freigegeben: boolean | null) => void): () => void {
  eigeneFreigabeListeners.add(listener)
  return () => {
    eigeneFreigabeListeners.delete(listener)
  }
}

export function setzeEigeneFreigabe(wert: boolean | null): void {
  if (eigeneFreigabe === wert) return
  eigeneFreigabe = wert
  for (const listener of eigeneFreigabeListeners) {
    try {
      listener(wert)
    } catch {
      // Ein kaputter Zuhörer ändert nichts am Stand.
    }
  }
}

/**
 * Die Sicherheitsnummer eines Geräteschlüssels: zwanzig Ziffern in vier Gruppen.
 *
 * Zum Vergleichen mit dem Auge. Beim Koppeln zeigt das Panel die Nummer des
 * Geräts, das sich gerade gemeldet hat, und die App ihre eigene. Stimmen beide
 * überein, ist das Gerät, das den Verlauf bekommt, dasjenige, das man in der
 * Hand hält — und nicht eines, das jemand im selben Moment untergeschoben hat.
 *
 * Gerechnet wird über die **kanonische** Form (`e`, `kty`, `n`), nicht über den
 * JWK-String: der Server gibt den Schlüssel so zurück, wie er ihn bekam, aber
 * eine andere Feldreihenfolge dürfte nie eine andere Nummer ergeben.
 */
export async function sicherheitsnummer(publicKeyJwk: string): Promise<string> {
  let kanonisch = publicKeyJwk
  try {
    const jwk = JSON.parse(publicKeyJwk)
    if (jwk && typeof jwk.n === 'string' && typeof jwk.e === 'string') {
      kanonisch = JSON.stringify({ e: jwk.e, kty: jwk.kty, n: jwk.n })
    }
  } catch {
    // Kein JSON — dann eben über den Wert, wie er ist.
  }
  const hex = await sha256Hex(utf8ToBytes(`msm:sicherheitsnummer:v1:${kanonisch}`))
  const gruppen: string[] = []
  // Je Gruppe fünf Bytes (40 Bit, sicher unter 2^53), auf fünf Ziffern
  // gefaltet: zusammen rund 66 Bit. Das reicht, weil ein Angreifer keine
  // Kollision irgendwelcher zwei Schlüssel braucht, sondern einen, der genau
  // diese eine angezeigte Nummer trifft.
  for (let i = 0; i < 4; i += 1) {
    const wert = parseInt(hex.slice(i * 10, i * 10 + 10), 16) % 100_000
    gruppen.push(String(wert).padStart(5, '0'))
  }
  return gruppen.join(' ')
}
