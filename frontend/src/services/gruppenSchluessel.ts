/**
 * Der Gruppenschlüssel: erzeugen, zustellen, rotieren, nachfordern.
 *
 * Eine Gruppe kann keinen Double Ratchet fahren. Der Ratchet ist ein Faden
 * zwischen genau zwei Geräten; bei zwanzig Mitgliedern mit je drei Geräten wären
 * das je Nachricht sechzig Umschläge. Signal löst das mit Sender Keys, und genau
 * das steht hier: ein Zufallsschlüssel je Gruppe, verschlüsselt wird einmal,
 * zugestellt wird nur der **Schlüssel** — einzeln versiegelt gegen jedes Gerät
 * jedes Mitglieds.
 *
 * **Warum nicht abgeleitet.** Vorher war der Gruppenschlüssel
 * `sha256("msm:group:key:" + groupId)`. Die `groupId` steht in der Datenbank,
 * also konnte das Backend jede Gruppennachricht mitlesen. Das war keine
 * Ende-zu-Ende-Verschlüsselung, sondern eine, die genau den nicht aussperrt, den
 * sie aussperren soll. Dieser Absatz bleibt stehen, damit niemand sie neu
 * erfindet.
 *
 * **Die Kennung ist der Schlüssel.** `keyId = sha256Hex(schluessel).slice(0,16)`.
 * Das ist nicht bloß eine Nummer: wer einen Schlüssel zustellt, kann ihn nicht
 * unter der Kennung eines anderen unterschieben — beim Empfang wird die Kennung
 * nachgerechnet. Eine Nachricht nennt die Kennung im Klartext, damit jedes Gerät
 * weiß, welchen seiner Schlüssel es nehmen muss.
 *
 * **Zustellung über die Gruppenmailbox.** Die Umschläge sind gewöhnliche
 * `sv-e2ee-hybrid-v1:`, relayed als Steuerumschläge in dieselbe Mailbox wie die
 * Nachrichten. Das Backend braucht dafür nichts Neues, und es entstehen keine
 * Direktchat-Zeilen zwischen Mitgliedern, die einander gar nicht kennen. Jeder
 * lädt alle Umschläge, öffnen lässt sich nur der eigene.
 *
 * **Rotation.** Jeder Schlüssel merkt sich, für welche Mitgliedschaft er
 * gemünzt wurde. Vor dem Senden wird das mit der heutigen Mitgliederliste
 * verglichen; weicht sie ab, entsteht ein frischer Schlüssel. Wer die Gruppe
 * verlassen hat, liest damit nichts Neues mehr — die Nachrichten von vorher
 * bleiben ihm lesbar, denn seinen alten Schlüssel kann ihm niemand wegnehmen.
 *
 * Die Prüfung läuft beim Absender und nicht an einem zentralen Ereignis, weil
 * ein Ereignis jemanden braucht, der es mitbekommt: wer rauswirft und danach den
 * Tab schließt, hätte die Rotation nie ausgelöst. So entscheidet jedes Gerät für
 * sich, aus derselben Mitgliederliste, und es heilt sich von selbst.
 *
 * **Nachzügler.** Ein Gerät ohne passenden Schlüssel fragt nach. Beantwortet
 * wird nur der **aktuelle** Schlüssel, nie ein alter: sonst könnte sich jemand
 * hinauswerfen lassen, über einen offenen Einladungslink zurückkommen und den
 * gesamten Verlauf nachfordern.
 *
 * Eine Nachfrage wird **einmal** beantwortet. Sie ist ein Umschlag und bleibt
 * als solcher in der Mailbox liegen, und der Lesepfad holt bei jedem Abruf das
 * ganze Fenster neu — ohne Gedächtnis antwortete der Zuständige deshalb alle
 * fünf Sekunden erneut, mit einem Umschlag je Gerät des Fragenden. Am laufenden
 * System waren danach 99 von 100 Umschlägen der Gruppenmailbox
 * Schlüsselzustellungen: die Nachrichten fielen aus dem Fenster, und im Verlauf
 * stand nichts mehr ausser „Verschlüsselte Nachricht". Gemerkt wird je
 * fragendem Gerät und fehlender Schlüsselkennung — fragt dasselbe Gerät später
 * nach einer anderen Kennung, bekommt es wieder eine Antwort.
 */

import { decryptString, encryptString, importAesGcmRawKey } from '@msdis/shield/aead'
import { base64ToBytes, bytesToBase64 } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'
import { randomBytes } from '@msdis/shield/random'

import { getGroupMembers, relayE2eeEnvelope } from '@/api/social'

import { encryptE2eeHybrid } from './e2eeCrypto'
import { eigenesGeraet, geraeteVon } from './e2eeGeraet'
import { entsiegleZeile, versiegleZeile } from './lokaleVersiegelung'
import { istSchluesselhalter } from './raumSchluessel'

export const GRUPPE_PREFIX = 'sv-e2ee-group-v1:'
export const GRUPPEN_SCHLUESSEL_TYP = 'group_key'
export const GRUPPEN_ANFRAGE_TYP = 'group_key_request'

/**
 * Der Gruppenschlüssel hat kein einziges Gerät erreicht.
 *
 * Nicht zu verwechseln mit einer unvollständigen Zustellung: die ist eingeplant,
 * die übergangenen Geräte fordern nach. Erreicht aber **niemand** den Schlüssel,
 * wäre jede folgende Nachricht für alle ausser dem Absender unlesbar.
 */
export class GruppenSchluesselNichtZugestelltError extends Error {
  constructor(
    public readonly groupId: number,
    public readonly ziele: number,
  ) {
    super('Der Gruppenschlüssel erreichte kein Gerät')
    this.name = 'GruppenSchluesselNichtZugestelltError'
  }
}

const SCHLUESSEL_BYTES = 32
const KEY_ID_LAENGE = 16
const KEY_ID_MUSTER = /^[0-9a-f]{16}$/

export interface GruppenKontext {
  groupId: number
  /** Die Mailbox der Gruppe. Dort liegen Nachrichten und Schlüsselumschläge. */
  blindMailboxId: string
  eigeneId: number
  /** Die Mitglieder, wie dieses Gerät sie gerade sieht. Quelle jeder Rotation. */
  mitglieder: readonly number[]
}

export type GruppenLesung =
  | { art: 'klartext'; text: string }
  /** Kein Schlüssel zu dieser Kennung. Der Aufrufer darf nachfordern. */
  | { art: 'kein-schluessel'; keyId: string }
  /** Kein Umschlag dieses Verfahrens — Altbestand aus der Zeit der Ableitung. */
  | { art: 'unbekannt' }
  /** Schlüssel vorhanden, Tag gescheitert: am Umschlag wurde gedreht. */
  | { art: 'bruch'; keyId: string; grund: string }

export type GruppenSteuerung =
  | { art: 'schluessel'; keyId: string }
  | { art: 'anfrage'; vonKonto: number; beantwortet: boolean }
  | { art: 'keine' }

// ==========================================
// Ablage
// ==========================================

export interface GruppenSchluesselEintrag {
  groupId: number
  keyId: string
  /** Base64 der 32 Schlüsselbytes. */
  schluessel: string
  /** Die Mitgliedschaft, für die dieser Schlüssel gemünzt wurde. Sortiert. */
  mitglieder: number[]
  erzeugtAm: string
}

export interface GruppenAblage {
  lies(groupId: number, keyId: string): Promise<GruppenSchluesselEintrag | null>
  liesAktuellen(groupId: number): Promise<GruppenSchluesselEintrag | null>
  /** Legt den Schlüssel ab und macht ihn zum aktuellen dieser Gruppe. */
  schreibe(eintrag: GruppenSchluesselEintrag): Promise<void>
  loescheGruppe(groupId: number): Promise<void>
  /** Wurde diese Nachfrage schon beantwortet? Siehe Kopf der Datei. */
  kennstAnfrage(groupId: number, kennung: string): Promise<boolean>
  /** Erst rufen, wenn die Antwort wirklich rausging. */
  merkeAnfrage(groupId: number, kennung: string): Promise<void>
}

const DB_NAME = 'msm_e2ee_gruppen'
/** Version 2: `beantwortet` kommt hinzu. */
const DB_VERSION = 2
const STORE_KEYS = 'keys'
const STORE_AKTUELL = 'aktuell'
const STORE_ANFRAGEN = 'beantwortet'

function oeffneDatenbank(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      return reject(new Error('IndexedDB nicht verfügbar'))
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE_KEYS)) {
        db.createObjectStore(STORE_KEYS, { keyPath: ['groupId', 'keyId'] })
      }
      if (!db.objectStoreNames.contains(STORE_AKTUELL)) {
        db.createObjectStore(STORE_AKTUELL, { keyPath: 'groupId' })
      }
      if (!db.objectStoreNames.contains(STORE_ANFRAGEN)) {
        db.createObjectStore(STORE_ANFRAGEN, { keyPath: ['groupId', 'kennung'] })
      }
    }
    req.onsuccess = () => {
      // Ein zweiter Tab kann jederzeit eine neuere Version aufziehen. Ohne
      // diesen Abgang liefen laufende Zugriffe danach gegen eine Verbindung,
      // die der Browser nur noch mit Fehlern beantwortet.
      req.result.onversionchange = () => req.result.close()
      resolve(req.result)
    }
    req.onerror = () => reject(req.error)
    // Ein älterer Tab hält die Vorgängerversion offen. Ohne diesen Zweig
    // meldet der Browser weder Erfolg noch Fehler, und das Öffnen hinge
    // stillschweigend, bis der andere Tab zugeht.
    req.onblocked = () =>
      reject(new Error('Gruppenschlüssel-Datenbank blockiert: bitte andere Panel-Tabs schließen'))
  })
}

function hole<T>(store: string, key: IDBValidKey): Promise<T | null> {
  return oeffneDatenbank().then(
    (db) =>
      new Promise<T | null>((resolve, reject) => {
        const tx = db.transaction(store, 'readonly')
        const req = tx.objectStore(store).get(key)
        req.onsuccess = () => resolve((req.result as T) ?? null)
        req.onerror = () => reject(req.error)
      })
  )
}

/** Bindet einen Gruppenschlüssel an seinen Platz. Siehe `versiegleZeile`. */
function gruppenAad(groupId: number, keyId: string): string {
  return `msm-gruppenschluessel:${groupId}:${keyId}`
}

/**
 * Nur `keys` geht durchs Siegel. In `aktuell` steht ein Zeiger auf eine
 * Schlüsselkennung und in `beantwortet` eine Marke — beides sagt nichts über
 * Inhalte aus, und beides muss lesbar bleiben, damit die Zeiger ohne PIN noch
 * stimmen.
 */
async function liesSchluessel(
  groupId: number,
  keyId: string,
): Promise<GruppenSchluesselEintrag | null> {
  const roh = await hole<Record<string, any>>(STORE_KEYS, [groupId, keyId])
  const zeile = await entsiegleZeile<GruppenSchluesselEintrag>(roh, gruppenAad(groupId, keyId))
  return zeile?.schluessel ? zeile : null
}

const indexedDbAblage: GruppenAblage = {
  async lies(groupId, keyId) {
    return liesSchluessel(groupId, keyId)
  },
  async liesAktuellen(groupId) {
    const zeiger = await hole<{ groupId: number; keyId: string }>(STORE_AKTUELL, groupId)
    if (!zeiger?.keyId) return null
    return liesSchluessel(groupId, zeiger.keyId)
  },
  async schreibe(eintrag) {
    const db = await oeffneDatenbank()
    // Versiegeln vor der Transaktion — ein `await` mitten drin bricht sie ab.
    const zeile = await versiegleZeile(
      eintrag,
      ['groupId', 'keyId'],
      gruppenAad(eintrag.groupId, eintrag.keyId),
    )
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction([STORE_KEYS, STORE_AKTUELL], 'readwrite')
      tx.objectStore(STORE_KEYS).put(zeile)
      tx.objectStore(STORE_AKTUELL).put({ groupId: eintrag.groupId, keyId: eintrag.keyId })
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  },
  async loescheGruppe(groupId) {
    const db = await oeffneDatenbank()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction([STORE_KEYS, STORE_AKTUELL, STORE_ANFRAGEN], 'readwrite')
      // Der zusammengesetzte Schlüssel beginnt mit der Gruppenkennung, also
      // trifft ein Bereich über [groupId] genau deren Schlüssel.
      tx.objectStore(STORE_KEYS).delete(IDBKeyRange.bound([groupId], [groupId, []]))
      tx.objectStore(STORE_AKTUELL).delete(groupId)
      tx.objectStore(STORE_ANFRAGEN).delete(IDBKeyRange.bound([groupId], [groupId, []]))
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  },
  async kennstAnfrage(groupId, kennung) {
    return (await hole<unknown>(STORE_ANFRAGEN, [groupId, kennung])) !== null
  },
  async merkeAnfrage(groupId, kennung) {
    const db = await oeffneDatenbank()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_ANFRAGEN, 'readwrite')
      tx.objectStore(STORE_ANFRAGEN).put({ groupId, kennung, beantwortetAm: new Date().toISOString() })
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  },
}

/**
 * Schreibt jeden Gruppenschlüssel einmal neu — der Umstellungsdurchlauf.
 * Siehe `schreibeNachrichtenBestandNeu`.
 *
 * Läuft bewusst an `ablage` vorbei direkt auf die IndexedDB: die Schnittstelle
 * kennt kein „alle", und sie soll es auch nicht lernen. Alles auflisten ist
 * genau das, was im Alltag niemand tun soll.
 */
export async function schreibeGruppenBestandNeu(): Promise<number> {
  const db = await oeffneDatenbank()
  const rohzeilen = await new Promise<Record<string, any>[]>((resolve, reject) => {
    const tx = db.transaction(STORE_KEYS, 'readonly')
    const req = tx.objectStore(STORE_KEYS).getAll()
    req.onsuccess = () => resolve(req.result || [])
    req.onerror = () => reject(req.error)
  })

  let geschrieben = 0
  for (const roh of rohzeilen) {
    const groupId = Number(roh.groupId)
    const keyId = String(roh.keyId || '')
    if (!Number.isFinite(groupId) || !keyId) continue
    const aad = gruppenAad(groupId, keyId)
    const offen = await entsiegleZeile<GruppenSchluesselEintrag>(roh, aad)
    if (!offen?.schluessel) continue
    const zeile = await versiegleZeile(offen, ['groupId', 'keyId'], aad)
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_KEYS, 'readwrite')
      tx.objectStore(STORE_KEYS).put(zeile)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
    geschrieben++
  }
  return geschrieben
}

let ablage: GruppenAblage = indexedDbAblage

/** Hängt eine andere Ablage ein. Nur für Tests gedacht. */
export function setzeGruppenAblageFuerTest(neu: GruppenAblage | null): void {
  ablage = neu ?? indexedDbAblage
  gefragt.clear()
}

// ==========================================
// Format
// ==========================================

function gebundeneDaten(groupId: number, keyId: string): string {
  return `msm:group:aad:${groupId}:${keyId}`
}

/**
 * Führt eine Operation mit dem entpackten Schlüssel aus und nullt die Bytes
 * danach. Der CryptoKey selbst ist nicht auslesbar; die Rohbytes wären es.
 */
async function mitSchluessel<T>(
  eintrag: GruppenSchluesselEintrag,
  arbeit: (key: CryptoKey) => Promise<T>,
): Promise<T> {
  const bytes = base64ToBytes(eintrag.schluessel)
  try {
    return await arbeit(await importAesGcmRawKey(bytes, ['encrypt', 'decrypt']))
  } finally {
    bytes.fill(0)
  }
}

function lieseGruppenKopf(umschlag: string): { keyId: string; rumpf: string } | null {
  if (!umschlag.startsWith(GRUPPE_PREFIX)) return null
  const nutzlast = umschlag.slice(GRUPPE_PREFIX.length)
  const punkt = nutzlast.indexOf('.')
  if (punkt === -1) return null
  const keyId = nutzlast.slice(0, punkt)
  const rumpf = nutzlast.slice(punkt + 1)
  if (!KEY_ID_MUSTER.test(keyId) || !rumpf) return null
  return { keyId, rumpf }
}

/** Sortiert, entdoppelt, ohne Unsinn — damit zwei Geräte denselben Vergleich bilden. */
function normalisiereMitglieder(ids: readonly unknown[]): number[] {
  const sauber = new Set<number>()
  for (const roh of ids) {
    const id = Number(roh)
    if (Number.isInteger(id) && id > 0) sauber.add(id)
  }
  return [...sauber].sort((a, b) => a - b)
}

function gleicheMitglieder(a: readonly number[], b: readonly number[]): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i])
}

// ==========================================
// Schlüssel
// ==========================================

/**
 * Die Mitglieder, wie der Server sie in diesem Augenblick sieht.
 *
 * `kontext.mitglieder` kommt aus der Oberfläche und ist eine Momentaufnahme vom
 * Öffnen des Gesprächs: `activeGroup` wird in `Messenger.tsx` gesetzt und
 * danach nicht mehr aufgefrischt. Daran hingen bis 09/2026 beide Entscheidungen
 * über den Gruppenschlüssel, und beide gingen still schief, sobald jemand nach
 * dem Öffnen beitrat. Er stand in keiner Verteilung, und auf seine Nachfrage
 * antwortete niemand, weil er in der Momentaufnahme nicht vorkam. Am laufenden
 * System las ein frisch beigetretenes Mitglied dadurch überhaupt nichts.
 *
 * Wer über Schlüssel entscheidet, fragt deshalb den Server. Antwortet der
 * nicht, gilt die Momentaufnahme weiter: lieber gegen eine alte Liste
 * verteilen als gar nicht senden können.
 */
async function frischeMitglieder(kontext: GruppenKontext): Promise<number[]> {
  let roh: readonly number[] = kontext.mitglieder
  try {
    const liste = await getGroupMembers(kontext.groupId)
    if (liste.length > 0) roh = liste.map((m) => m.user_id)
  } catch {
    // Server nicht erreichbar — die Momentaufnahme muss reichen.
  }
  return normalisiereMitglieder([...roh, kontext.eigeneId])
}

export async function erzeugeGruppenSchluessel(): Promise<{
  keyId: string
  schluessel: Uint8Array
}> {
  const schluessel = randomBytes(SCHLUESSEL_BYTES)
  const keyId = (await sha256Hex(schluessel)).slice(0, KEY_ID_LAENGE)
  return { keyId, schluessel }
}

/**
 * Der Schlüssel, mit dem jetzt zu senden ist.
 *
 * Passt der aktuelle nicht mehr zur Mitgliedschaft, entsteht hier ein frischer
 * und geht an alle Geräte aller Mitglieder raus. Erreicht die Zustellung nicht
 * jedes Gerät, ist das kein Abbruch: die übergangenen Geräte sehen eine
 * unbekannte Kennung und fordern nach.
 */
async function schluesselZumSenden(kontext: GruppenKontext): Promise<GruppenSchluesselEintrag> {
  const jetzige = await frischeMitglieder(kontext)
  const vorhanden = await ablage.liesAktuellen(kontext.groupId)
  if (vorhanden && gleicheMitglieder(vorhanden.mitglieder, jetzige)) return vorhanden

  const { keyId, schluessel } = await erzeugeGruppenSchluessel()
  const eintrag: GruppenSchluesselEintrag = {
    groupId: kontext.groupId,
    keyId,
    schluessel: bytesToBase64(schluessel),
    mitglieder: jetzige,
    erzeugtAm: new Date().toISOString(),
  }
  schluessel.fill(0)

  // Erst ablegen, dann verteilen: ein Schlüssel, der schon unterwegs ist und
  // nirgends liegt, macht die eigenen Nachrichten unlesbar.
  await ablage.schreibe(eintrag)
  await verteileSchluessel(kontext, eintrag, jetzige)
  return eintrag
}

/** Versiegelt eine Nutzlast für jedes Gerät der genannten Konten und relayed sie. */
async function anJedesGeraet(
  kontext: GruppenKontext,
  empfaengerIds: readonly number[],
  nutzlast: string,
  kontrolltyp: string,
): Promise<number> {
  const meins = await eigenesGeraet()
  const basis =
    typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : `gk-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`

  let lfd = 0
  let zugestellt = 0
  let ziele = 0
  for (const empfaengerId of empfaengerIds) {
    const geraete = await geraeteVon(empfaengerId)
    for (const geraet of geraete) {
      // Das eigene Gerät hat den Schlüssel schon.
      if (empfaengerId === kontext.eigeneId && geraet.device_id === meins.kennung) continue
      ziele += 1
      const nummer = lfd++
      try {
        const umschlag = await encryptE2eeHybrid(nutzlast, geraet.public_key)
        await relayE2eeEnvelope({
          blind_mailbox_id: kontext.blindMailboxId,
          ciphertext_envelope: umschlag,
          // Je Gerät eine eigene Kennung: das Relais gibt beim zweiten Aufruf
          // mit derselben Kennung still den ersten Umschlag zurück.
          client_uuid: `${basis}#${nummer}`,
          is_control: true,
          control_type: kontrolltyp,
        })
        zugestellt += 1
      } catch {
        // Ein Gerät, das sich nicht erreichen lässt, hält die übrigen nicht auf.
        // Es sieht später eine unbekannte Kennung und fordert nach.
      }
    }
  }

  // Kein einziges Ziel erreicht, obwohl es welche gab. Das war bis 09/2026 eine
  // stille 0, die niemand auswertete: der Absender schrieb munter weiter mit
  // einem Schlüssel, den ausser ihm keiner hatte, und im Verlauf der anderen
  // stand nur „Verschlüsselte Nachricht".
  if (ziele > 0 && zugestellt === 0) {
    throw new GruppenSchluesselNichtZugestelltError(kontext.groupId, ziele)
  }
  return zugestellt
}

function schluesselNutzlast(eintrag: GruppenSchluesselEintrag): string {
  return JSON.stringify({
    typ: GRUPPEN_SCHLUESSEL_TYP,
    v: 1,
    groupId: eintrag.groupId,
    keyId: eintrag.keyId,
    schluessel: eintrag.schluessel,
    mitglieder: eintrag.mitglieder,
  })
}

async function verteileSchluessel(
  kontext: GruppenKontext,
  eintrag: GruppenSchluesselEintrag,
  empfaenger: readonly number[],
): Promise<number> {
  return anJedesGeraet(kontext, empfaenger, schluesselNutzlast(eintrag), GRUPPEN_SCHLUESSEL_TYP)
}

// ==========================================
// Nachrichten
// ==========================================

export async function verschluesseleFuerGruppe(
  kontext: GruppenKontext,
  klartext: string,
): Promise<string> {
  const eintrag = await schluesselZumSenden(kontext)
  const chiffre = await mitSchluessel(eintrag, (key) =>
    encryptString(klartext, key, gebundeneDaten(kontext.groupId, eintrag.keyId))
  )
  return `${GRUPPE_PREFIX}${eintrag.keyId}.${chiffre}`
}

export async function entschluesseleGruppenUmschlag(
  groupId: number,
  umschlag: string,
): Promise<GruppenLesung> {
  const kopf = lieseGruppenKopf(umschlag)
  if (!kopf) return { art: 'unbekannt' }

  const eintrag = await ablage.lies(groupId, kopf.keyId)
  if (!eintrag) return { art: 'kein-schluessel', keyId: kopf.keyId }

  try {
    const text = await mitSchluessel(eintrag, (key) =>
      decryptString(kopf.rumpf, key, gebundeneDaten(groupId, kopf.keyId))
    )
    return { art: 'klartext', text }
  } catch (fehler) {
    const grund = fehler instanceof Error ? fehler.name : 'unbekannt'
    return { art: 'bruch', keyId: kopf.keyId, grund }
  }
}

// ==========================================
// Steuerumschläge
// ==========================================

/**
 * Nimmt einen entschlüsselten Hybrid-Klartext aus einer Gruppenmailbox entgegen.
 *
 * Meldet `keine`, wenn es keiner der beiden Steuertypen war — dann gehört der
 * Klartext in den normalen Lesepfad.
 */
export async function verarbeiteGruppenSteuerung(
  kontext: GruppenKontext,
  klartext: string,
): Promise<GruppenSteuerung> {
  let roh: Record<string, unknown>
  try {
    const geparst = JSON.parse(klartext)
    if (!geparst || typeof geparst !== 'object') return { art: 'keine' }
    roh = geparst as Record<string, unknown>
  } catch {
    return { art: 'keine' }
  }

  // Ein Umschlag aus einer fremden Gruppe darf hier nichts setzen. Ohne diese
  // Prüfung könnte ein Mitglied von A einen Schlüssel für B unterschieben.
  if (Number(roh.groupId) !== kontext.groupId) return { art: 'keine' }

  if (roh.typ === GRUPPEN_SCHLUESSEL_TYP) {
    return nimmSchluessel(kontext, roh)
  }
  if (roh.typ === GRUPPEN_ANFRAGE_TYP) {
    return beantworteAnfrage(kontext, roh)
  }
  return { art: 'keine' }
}

async function nimmSchluessel(
  kontext: GruppenKontext,
  roh: Record<string, unknown>,
): Promise<GruppenSteuerung> {
  const keyId = String(roh.keyId ?? '')
  const schluessel = String(roh.schluessel ?? '')
  if (!KEY_ID_MUSTER.test(keyId) || !schluessel) return { art: 'keine' }

  let bytes: Uint8Array
  try {
    bytes = base64ToBytes(schluessel)
  } catch {
    return { art: 'keine' }
  }
  try {
    if (bytes.length !== SCHLUESSEL_BYTES) return { art: 'keine' }
    // Die Kennung wird nachgerechnet. Sie ist der Hash des Schlüssels, also
    // lässt sich unter einer bekannten Kennung kein anderer Schlüssel
    // unterschieben — und ein Gerät, das denselben Schlüssel zweimal bekommt,
    // schreibt zweimal dasselbe.
    const gerechnet = (await sha256Hex(bytes)).slice(0, KEY_ID_LAENGE)
    if (gerechnet !== keyId) return { art: 'keine' }
  } finally {
    bytes.fill(0)
  }

  // Die mitgeschickte Mitgliederliste entscheidet nur, wann *dieses* Gerät das
  // nächste Mal rotiert. Eine falsche Angabe verzögert die Rotation bis zum
  // nächsten eigenen Sendevorgang — mehr kann sie nicht, denn wer den Schlüssel
  // münzt, hat ihn ohnehin.
  await ablage.schreibe({
    groupId: kontext.groupId,
    keyId,
    schluessel,
    mitglieder: normalisiereMitglieder(Array.isArray(roh.mitglieder) ? roh.mitglieder : []),
    erzeugtAm: new Date().toISOString(),
  })
  return { art: 'schluessel', keyId }
}

/**
 * Wer einem Nachzügler antwortet, entscheidet jedes Gerät für sich aus der
 * Mitgliederliste — dieselbe Regel wie beim Raumschlüssel eines Anrufs.
 *
 * Dazu kommt ein Fall, den ein Anruf nicht kennt: fragt ein **zweites Gerät
 * desselben Kontos**, antworten die eigenen Geräte immer. `istSchluesselhalter`
 * schließt das fragende Konto aus den Kandidaten aus, und ein frisch gekoppeltes
 * Gerät in einer Gruppe, deren übrige Mitglieder gerade offline sind, bekäme
 * sonst nie einen Schlüssel.
 */
async function beantworteAnfrage(
  kontext: GruppenKontext,
  roh: Record<string, unknown>,
): Promise<GruppenSteuerung> {
  const anfragerId = Number(roh.vonKonto)
  if (!Number.isInteger(anfragerId) || anfragerId <= 0) return { art: 'keine' }
  // Nur Mitglieder bekommen Schlüssel. Und die Geräteschlüssel kommen aus dem
  // Verzeichnis, nie aus der Anfrage — sonst bestimmte der Fragende selbst,
  // wogegen versiegelt wird.
  //
  // Die Liste kommt vom Server und nicht aus `kontext.mitglieder`: wer gerade
  // erst beigetreten ist, steht in der Momentaufnahme der Oberfläche noch
  // nicht, und genau der fragt hier. Mit der alten Liste wurde er abgewiesen,
  // ohne dass irgendwo etwas davon stand.
  const mitglieder = await frischeMitglieder(kontext)
  if (!mitglieder.includes(anfragerId)) return { art: 'keine' }

  const eigenesKonto = anfragerId === kontext.eigeneId
  const zustaendig =
    eigenesKonto || istSchluesselhalter(kontext.eigeneId, mitglieder, anfragerId)
  if (!zustaendig) return { art: 'anfrage', vonKonto: anfragerId, beantwortet: false }

  // Diese Nachfrage liegt als Umschlag in der Mailbox und kommt bei jedem
  // Abruf wieder. Einmal beantworten reicht; siehe Kopf der Datei.
  const kennung = `${anfragerId}:${String(roh.vonGeraet ?? '')}:${String(roh.keyId ?? '')}`
  if (await ablage.kennstAnfrage(kontext.groupId, kennung)) {
    return { art: 'anfrage', vonKonto: anfragerId, beantwortet: false }
  }

  const eintrag = await ablage.liesAktuellen(kontext.groupId)
  if (!eintrag) return { art: 'anfrage', vonKonto: anfragerId, beantwortet: false }

  // Nur der aktuelle Schlüssel. Ältere bleiben hier, sonst holte sich ein
  // Zurückgekehrter über eine Anfrage den ganzen Verlauf.
  //
  // Hier darf nichts fliegen: das läuft mitten im Lesen der Mailbox, und eine
  // gescheiterte Antwort darf nicht den Verlauf des Antwortenden abreissen. Der
  // Fragende versucht es in einer Minute erneut.
  let zugestellt = 0
  try {
    zugestellt = await anJedesGeraet(
      kontext,
      [anfragerId],
      schluesselNutzlast(eintrag),
      GRUPPEN_SCHLUESSEL_TYP,
    )
  } catch {
    zugestellt = 0
  }
  // Erst merken, wenn wirklich etwas rausging. Eine gescheiterte Antwort soll
  // der nächste Durchlauf erneut versuchen — sonst bliebe der Fragende ohne
  // Schlüssel und niemand käme je darauf zurück.
  if (zugestellt > 0) {
    await ablage.merkeAnfrage(kontext.groupId, kennung).catch(() => {})
  }
  return { art: 'anfrage', vonKonto: anfragerId, beantwortet: zugestellt > 0 }
}

/** Wonach dieses Gerät seit dem Laden der Seite schon gefragt hat. */
const gefragt = new Set<string>()

/**
 * Fordert den aktuellen Gruppenschlüssel an.
 *
 * Gerichtet an den zuständigen Halter und an die eigenen Zweitgeräte, statt an
 * alle Mitglieder: antworten würde ohnehin nur dieser eine Kreis, und eine
 * Rundsendung an jedes Gerät jedes Mitglieds kostet in großen Gruppen ein
 * Vielfaches.
 *
 * **Einmal je fehlender Kennung**, nicht einmal je Minute. Eine Nachfrage ist
 * ein Umschlag und bleibt liegen: wer erst in einer Stunde online geht, liest
 * sie dann und antwortet. Ein zweites Mal zu fragen bringt deshalb nichts und
 * kostet je Frage einen Umschlag pro Gerät des Gefragten — bei einer alten
 * Nachricht, deren Schlüssel niemand mehr herausgibt, jede Minute aufs Neue,
 * für immer. Der Aufrufer ruft das ohnehin nur für die **neueste** unlesbare
 * Kennung auf.
 *
 * Das Gedächtnis hält bis zum Neuladen. Das ist der Wiederholungsweg für den
 * seltenen Fall, dass die Nachfrage aus dem Abruffenster gefallen ist, bevor
 * sie jemand gesehen hat.
 */
export async function fordereGruppenSchluessel(
  kontext: GruppenKontext,
  fehlendeKeyId: string,
): Promise<boolean> {
  const marke = `${kontext.groupId}:${fehlendeKeyId}`
  if (gefragt.has(marke)) return false
  // Sofort merken, damit zwei überlappende Durchläufe nicht beide fragen.
  gefragt.add(marke)

  try {
    // Auch hier die Liste vom Server: der Fragende ist oft gerade erst
    // beigetreten, und wen er fragt, muss zur heutigen Gruppe passen.
    const mitglieder = await frischeMitglieder(kontext)
    const andere = mitglieder.filter((id) => id !== kontext.eigeneId)
    const ziele = andere.length > 0 ? [Math.min(...andere), kontext.eigeneId] : [kontext.eigeneId]

    const meins = await eigenesGeraet()
    const nutzlast = JSON.stringify({
      typ: GRUPPEN_ANFRAGE_TYP,
      v: 1,
      groupId: kontext.groupId,
      vonKonto: kontext.eigeneId,
      vonGeraet: meins.kennung,
      keyId: fehlendeKeyId,
    })

    const erreicht = await anJedesGeraet(kontext, ziele, nutzlast, GRUPPEN_ANFRAGE_TYP)
    // Nichts rausgegangen heißt: es liegt auch nichts in der Mailbox, das
    // jemand später noch beantworten könnte. Also Marke zurück.
    if (erreicht === 0) gefragt.delete(marke)
    return erreicht > 0
  } catch {
    // Auch das läuft im Lesepfad: eine Nachfrage, die niemanden erreicht, ist
    // ein „noch nicht", kein Grund, die Anzeige abzubrechen. Ein Netzfehler
    // darf dieses Gerät aber nicht bis zum Neuladen ohne Schlüssel lassen.
    gefragt.delete(marke)
    return false
  }
}

/** Wirft die Schlüssel einer Gruppe weg. Beim Verlassen und beim Löschen fällig. */
export async function verwirfGruppenSchluessel(groupId: number): Promise<void> {
  for (const marke of gefragt) {
    if (marke.startsWith(`${groupId}:`)) gefragt.delete(marke)
  }
  await ablage.loescheGruppe(groupId)
}
