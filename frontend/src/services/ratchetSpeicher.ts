/**
 * Die Ablage der Ratchet-Sitzungen — und die eine Stelle, an der ihre
 * Persistenzzusage eingehalten wird.
 *
 * DIS gibt den Double Ratchet als reine Zustandsmaschine heraus: `encryptMessage`
 * und `decryptMessage` fassen ihren Eingangszustand nicht an, sondern liefern
 * einen neuen. Wer den alten weiterbenutzt, hat kein Problem — er hat zwei
 * Probleme, und beide fallen erst später auf:
 *
 * 1. **Der alte Zustand hält seine Kettenschlüssel am Leben.** Genau das soll
 *    der Ratchet verhindern. Deshalb `destroyRatchetState` auf den ersetzten
 *    Zustand, sobald der neue sicher liegt.
 * 2. **Zweimal aus demselben Zustand zu verschlüsseln erzeugt zwei Nachrichten
 *    auf derselben Kettenposition.** Die zweite entschlüsselt nie. Zwei
 *    Browser-Tabs teilen sich dieselbe IndexedDB, also ist das kein
 *    theoretischer Fall.
 *
 * `schritt()` ist die Antwort auf beides und der einzige Weg an einen Zustand.
 * Lesen, Rechnen und Schreiben passieren darin unter einem Schloss, statt an
 * jeder Aufrufstelle neu.
 *
 * **Zur Reihenfolge von Schreiben und Vernichten.** Sie ist hier folgenlos, und
 * das ist Absicht. `zustand` ist immer eine frische Deserialisierung, nie die
 * abgelegte Fassung selbst, und DIS kopiert in `cloneState` jeden Puffer tief —
 * der Nachfolger teilt sich mit dem Vorgänger nichts, `destroyRatchetState`
 * kann ihm also gar nichts antun. Die Gefahr steckte in der ursprünglich
 * geplanten Form `schreibeUndVerwerfe(alt, neu)`, bei der Aufrufer
 * Zustandsobjekte über längere Zeit im Speicher halten und der falsche davon
 * vernichtet werden kann. `schritt()` gibt keinen Zustand heraus und räumt die
 * Fehlerklasse damit weg, statt sie zu sortieren. Geschrieben wird trotzdem
 * zuerst; es kostet nichts und hält die Stelle gegen spätere Umbauten robust.
 *
 * **Achtung bei `destroyRatchetState`:** es nullt auch `dhSelf.publicKey` und
 * `associatedData`, nicht nur die Geheimnisse. Ein Zustand, der einmal durch
 * diese Funktion gegangen ist, ist vollständig tot. Serialisieren kommt immer
 * zuerst.
 *
 * **Ein Fehler aus `schritt` ist kein Grund, eine Sitzung wegzuwerfen.** Nur ein
 * `DisDecryptionError` bedeutet Sitzungsbruch. Scheitert dagegen das Schreiben,
 * bleibt die abgelegte Fassung unangetastet, und der Versuch lässt sich
 * wiederholen. Wer das verwechselt, macht aus einer vollen Platte einen
 * verlorenen Gesprächsfaden.
 */

import {
  deserializeRatchetState,
  destroyRatchetState,
  serializeRatchetState,
  type RatchetState,
} from '@msdis/shield/messaging'

import { entsiegleZeile, versiegleZeile } from './lokaleVersiegelung'
import i18n from '@/i18n'

const DB_NAME = 'msm_e2ee_ratchet'
const DB_VERSION = 1
const STORE = 'sessions'

/**
 * Die Sitzungskennung. Eine Sitzung gehört einem **Gerät**, nicht einem Konto:
 * dieselbe Person an zwei Geräten sind zwei Fäden, die nichts voneinander
 * wissen.
 *
 * Das gilt für beide Enden, und deshalb steht seit 09/2026 auch das eigene
 * Gerät im Namen. Vorher hieß eine Sitzung nur `<Konto>:<Gerät>` der
 * Gegenstelle. In einem Browser, in dem sich zwei Menschen nacheinander
 * anmelden, griffen beide auf denselben Platz: wer als zweiter sendete,
 * schaltete den Ratchet des ersten weiter, und die Gegenstelle suchte unter
 * einer anderen Absenderkennung, fand nichts und meldete einen Sitzungsbruch.
 *
 * Die eigene Gerätekennung und nicht die eigene Kontokennung, weil sie beides
 * leistet: sie unterscheidet die Konten ohnehin, und sie wechselt zusätzlich,
 * wenn die Schlüsselablage verloren geht. Zustände, die zu einem Schlüssel
 * gehören, den es nicht mehr gibt, verwaisen damit von selbst.
 */
export function sitzungsId(
  meinGeraet: string,
  peerUserId: number,
  peerGeraeteId: string,
): string {
  return `${meinGeraet}:${peerUserId}:${peerGeraeteId}`
}

// ==========================================
// Ablage (austauschbar für Tests)
// ==========================================

export interface RatchetAblage {
  lies(id: string): Promise<string | null>
  /**
   * Legt `zustand` unter `id` ab. Ist `marke` gesetzt, liegt sie danach
   * ebenfalls da, in derselben Transaktion: alles oder keines.
   *
   * `begleiter` sind zusätzliche Zeilen derselben Transaktion. Sie tragen die
   * Schlüsselbindung einer Sitzung (`bindung:<id>`): der Abdruck des
   * öffentlichen Schlüssels, zu dem diese Sitzung gehört. Wechselt die
   * Gegenstelle ihren Schlüssel unter derselben Geräte-ID, ist die alte
   * Sitzung wertlos — und darf nicht weiterverwendet werden.
   */
  schreibe(
    id: string,
    zustand: string,
    marke?: string | string[],
    begleiter?: Record<string, string | null>,
  ): Promise<void>
  loesche(id: string): Promise<void>
  /** Alle abgelegten Kennungen. Nur die einmalige Umbenennung braucht das. */
  alleIds(): Promise<string[]>
}

function oeffneDatenbank(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      return reject(new Error(i18n.t('chat.errors.indexedDbUnavailable')))
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'sitzungsId' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

/** Bindet einen Sitzungszustand an seinen Platz. Siehe `versiegleZeile`. */
function ratchetAad(id: string): string {
  return `msm-ratchet:${id}`
}

const indexedDbAblage: RatchetAblage = {
  async lies(id) {
    const db = await oeffneDatenbank()
    const roh = await new Promise<Record<string, any> | null>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly')
      const req = tx.objectStore(STORE).get(id)
      req.onsuccess = () => resolve(req.result ?? null)
      req.onerror = () => reject(req.error)
    })
    const zeile = await entsiegleZeile<{ zustand?: unknown }>(roh, ratchetAad(id))
    return typeof zeile?.zustand === 'string' ? zeile.zustand : null
  },
  async schreibe(id, zustand, marke, begleiter) {
    const db = await oeffneDatenbank()
    const jetzt = new Date().toISOString()
    // Versiegeln vor der Transaktion — ein `await` mitten drin bricht sie ab.
    const versiegle = (platz: string, wert: string) =>
      versiegleZeile(
        { sitzungsId: platz, zustand: wert, aktualisiertAm: jetzt },
        ['sitzungsId'],
        ratchetAad(platz),
      )
    const zeilen = [await versiegle(id, zustand)]
    const marken = marke === undefined ? [] : Array.isArray(marke) ? marke : [marke]
    for (const eine of marken) zeilen.push(await versiegle(eine, MARKE_WERT))
    const zuLoeschen: string[] = []
    for (const [platz, wert] of Object.entries(begleiter ?? {})) {
      if (wert === null) zuLoeschen.push(platz)
      else zeilen.push(await versiegle(platz, wert))
    }
    // Fertig erst mit `oncomplete`: bis dahin kann die Transaktion noch
    // abbrechen (Kontingent), und dann liegt keine der Zeilen da. Ein
    // gescheitertes `put` bricht sie ebenfalls ab und landet in `onabort`.
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite')
      const store = tx.objectStore(STORE)
      for (const zeile of zeilen) store.put(zeile)
      for (const platz of zuLoeschen) store.delete(platz)
      tx.oncomplete = () => resolve()
      tx.onabort = () => reject(tx.error ?? new Error('Transaktion abgebrochen'))
    })
  },
  async loesche(id) {
    const db = await oeffneDatenbank()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite')
      const req = tx.objectStore(STORE).delete(id)
      req.onsuccess = () => resolve()
      req.onerror = () => reject(req.error)
    })
  },
  async alleIds() {
    const db = await oeffneDatenbank()
    return new Promise<string[]>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly')
      const req = tx.objectStore(STORE).getAllKeys()
      req.onsuccess = () => resolve((req.result ?? []).map((k) => String(k)))
      req.onerror = () => reject(req.error)
    })
  },
}

let ablage: RatchetAblage = indexedDbAblage

/**
 * Hängt eine andere Ablage ein. Nur für Tests gedacht — und dafür der Grund,
 * warum hier kein `fake-indexeddb` als Abhängigkeit steht: eine schmale
 * Schnittstelle ist leichter zu fälschen als eine Datenbank nachzubauen.
 */
export function setzeAblageFuerTest(neu: RatchetAblage | null): void {
  ablage = neu ?? indexedDbAblage
  schloesser.clear()
}

/**
 * Benennt die Sitzungen aus der Zeit vor der eigenen Gerätekennung um.
 *
 * Aufgerufen genau einmal, aus der Übernahme des alten Geräteschlüssels in
 * `e2eeGeraet.ts`: erst dort steht fest, welchem Gerät der Altbestand gehört.
 * Ohne diesen Schritt fände der Messenger nach dem Update keine einzige seiner
 * Sitzungen wieder und meldete jedem Gesprächspartner einen Bruch.
 *
 * Erkannt wird das alte Format an zwei Feldern, deren erstes eine Kontokennung
 * ist. Die Zählung allein reicht nicht: in **demselben** Store liegen die
 * Marken, und `aufbau:<base64>` hat ebenfalls zwei Felder — base64 kennt keinen
 * Doppelpunkt. Eine umbenannte Marke wäre für immer unauffindbar, und der
 * zugehörige Sitzungsaufbau liefe noch einmal. Ziffern am Anfang schliessen
 * `aufbau` und `bruch` sicher aus.
 *
 * Bereits umbenannte Sitzungen bleiben unangetastet, und ein Platz, der im
 * neuen Format schon belegt ist, wird nicht überschrieben.
 */
export async function uebernehmeAltbestand(meinGeraet: string): Promise<number> {
  const ids = await ablage.alleIds()
  let umbenannt = 0
  for (const alt of ids) {
    if (!/^\d+:[A-Za-z0-9_-]+$/.test(alt)) continue
    const neu = `${meinGeraet}:${alt}`
    if (await ablage.lies(neu)) continue
    const zustand = await ablage.lies(alt)
    if (!zustand) continue
    await ablage.schreibe(neu, zustand)
    await ablage.loesche(alt)
    umbenannt += 1
  }
  return umbenannt
}

// ==========================================
// Schloss
// ==========================================

/** Ersatzschloss, wo `navigator.locks` fehlt (Node-Tests, ältere WebViews). */
const schloesser = new Map<string, Promise<unknown>>()

async function imSchloss<T>(id: string, arbeit: () => Promise<T>): Promise<T> {
  const web = typeof navigator !== 'undefined' ? navigator.locks : undefined
  if (web?.request) {
    // Gilt über alle Tabs derselben Herkunft hinweg — genau die Reichweite, die
    // eine geteilte IndexedDB braucht.
    return web.request(`msm-ratchet:${id}`, () => arbeit()) as Promise<T>
  }

  // Ohne Web Locks reicht es für einen einzelnen Kontext, die Aufrufe
  // hintereinander zu hängen. `then(arbeit, arbeit)` startet den eigenen
  // Schritt in beiden Ausgängen des Vorgängers: ein gescheiterter Schritt darf
  // die Warteschlange nicht mitreißen.
  const vorgaenger = schloesser.get(id) ?? Promise.resolve()
  const lauf = vorgaenger.then(arbeit, arbeit)
  const kette = lauf.then(
    () => undefined,
    () => undefined
  )
  schloesser.set(id, kette)
  try {
    return await lauf
  } finally {
    // Nur aufräumen, wenn seither niemand weiter angehängt hat.
    if (schloesser.get(id) === kette) schloesser.delete(id)
  }
}

// ==========================================
// Der eine Weg an einen Zustand
// ==========================================

/**
 * Eine Versandabsicht, die **mit dem Ratchet-Schritt** entsteht.
 *
 * Der Nachfolgezustand und der fertige Umschlag gehören zusammen: wer den
 * Zustand fortschreibt, ohne die Bytes zu behalten, hat eine Kettenposition
 * verbraucht und nichts, was er noch senden könnte. Beides in einer
 * Transaktion — der Umschlag ist danach die einzige Fassung dieser Nachricht,
 * die dieses Gerät je sieht.
 *
 * Die Absicht bleibt liegen, bis sie nachweislich hinaus ist. Ein Retry sendet
 * **dieselben Bytes unter derselben Kennung**; der Server erkennt die
 * Wiederholung, und die Gegenstelle bekommt keine zweite Kopie.
 */
export interface AusstehendeZustellung {
  /** Die logische Kennung samt Gerätezusatz (`<basis>#<geraet>`). */
  clientUuid: string
  /** Wem die Absicht gehört. Ein anderes Konto auf diesem Gerät fasst sie nicht an. */
  eigeneId: number
  peerId: number
  /** Die Mailbox, in die der Umschlag gehört. */
  mailbox: string
  /** Die exakten Umschlagbytes. Nie neu verschlüsseln, immer diese senden. */
  ciphertext: string
  isControl?: boolean
  controlType?: string
  /** Die Zustelladresse des Zielgeräts, falls es den neuen Weg kennt. */
  zielInbox?: string | null
  /**
   * Schon vergebene Kennungen der Geräte-Inbox.
   *
   * Sie werden **vor** dem HTTP-Aufruf festgehalten: ging die Antwort verloren,
   * wiederholt der nächste Lauf den Auftrag mit denselben Kennungen, und der
   * Server gibt den angenommenen Auftrag zurück, statt einen zweiten anzulegen.
   */
  zustellung?: { payloadId: string; deliveryId: string } | null
  /** Die Inbox hat abgelehnt — nur noch über die geteilte Mailbox. */
  nurMailbox?: boolean
  /** Wann die Absicht entstand (ISO), für die Reihenfolge des Wiederaufnehmers. */
  angelegtAm?: string
  /** Position im selben Schritt: 0 ist der Aufbau, 1 die Nachricht. */
  reihenfolge?: number
}

export interface SchrittErgebnis<T> {
  /** Der Nachfolgezustand. Fehlt er, bleibt der gespeicherte unverändert. */
  naechster?: RatchetState
  /**
   * Eine Marke, die mit `naechster` in derselben Transaktion abgelegt wird.
   * Scheitert das Ablegen, fehlen beide, und der nächste Versuch findet die
   * Arbeit noch offen. Ohne `naechster` wird sie nicht geschrieben.
   */
  marke?: { bereich: Markenbereich; kennung: string }
  /**
   * Weitere Marken derselben Transaktion. Ein angewandter Sitzungsaufbau setzt
   * `aufbau` (der Umschlag selbst ist erledigt) — mehr als eine Marke war bis
   * 10/2026 nicht vorgesehen, und zwei getrennte Schreibvorgänge rissen bei
   * einem Abbruch dazwischen auseinander.
   */
  marken?: { bereich: Markenbereich; kennung: string }[]
  /**
   * Der Abdruck des Gegenstellen-Schlüssels, zu dem dieser Zustand gehört.
   * Wird als `bindung:<id>` in derselben Transaktion geschrieben; `null`
   * löscht die alte Bindung, wenn der Aufbau keinen Schlüssel zum Vergleichen
   * hatte.
   */
  bindung?: string | null
  /**
   * Versandabsichten dieser Transaktion. Sie liegen danach unter
   * `versand:<clientUuid>` und werden erst nach belegtem Erfolg entfernt.
   */
  versand?: AusstehendeZustellung[]
  ergebnis: T
}

/**
 * Führt einen Schritt auf einer Sitzung aus: laden, rechnen, ablegen.
 *
 * `arbeit` bekommt den gespeicherten Zustand (oder `null`, wenn es noch keinen
 * gibt) und liefert zurück, was daraus geworden ist. Alles dazwischen läuft
 * unter dem Schloss dieser Sitzung, damit zwei gleichzeitige Sendevorgänge
 * nicht dieselbe Kettenposition zweimal verbrauchen.
 *
 * Scheitert `arbeit`, bleibt der gespeicherte Zustand unangetastet. Das ist
 * kein Zufall, sondern trägt eine Zusage von DIS weiter: eine gefälschte oder
 * wiedereingespielte Nachricht lässt den Zustand byteweise unverändert und kann
 * eine Sitzung damit weder vorspulen noch verklemmen.
 */
export async function schritt<T>(
  id: string,
  arbeit: (zustand: RatchetState | null) => Promise<SchrittErgebnis<T>>
): Promise<T> {
  return imSchloss(id, async () => {
    const roh = await ablage.lies(id)
    const zustand = roh ? deserializeRatchetState(roh) : null

    let ausgang: SchrittErgebnis<T>
    try {
      ausgang = await arbeit(zustand)
    } catch (fehler) {
      if (zustand) destroyRatchetState(zustand)
      throw fehler
    }

    if (ausgang.naechster) {
      try {
        const marken = [
          ...(ausgang.marke ? [ausgang.marke] : []),
          ...(ausgang.marken ?? []),
        ].map((m) => `${m.bereich}:${m.kennung}`)
        const begleiter =
          ausgang.bindung === undefined
            ? undefined
            : { [bindungSchluessel(id)]: ausgang.bindung }
        /*
         * Die Versandabsichten gehören in dieselbe Transaktion wie der
         * Zustand. Zwei getrennte Schreibvorgänge hätten genau die Lücke, gegen
         * die sie da sind: ein Abbruch dazwischen liesse eine verbrauchte
         * Kettenposition ohne Umschlag zurück.
         */
        const mitVersand: Record<string, string | null> = { ...(begleiter ?? {}) }
        for (const absicht of ausgang.versand ?? []) {
          mitVersand[versandSchluessel(absicht.clientUuid)] = JSON.stringify(absicht)
        }
        await ablage.schreibe(
          id,
          serializeRatchetState(ausgang.naechster),
          marken,
          Object.keys(mitVersand).length > 0 ? mitVersand : undefined,
        )
      } catch (fehler) {
        // Der Nachfolger liegt jetzt nirgends und wird nie gebraucht, der
        // Vorgänger liegt unverändert in der Ablage. Beide RAM-Kopien können
        // weg; die Sitzung überlebt in der Form, die vor diesem Schritt galt.
        destroyRatchetState(ausgang.naechster)
        if (zustand) destroyRatchetState(zustand)
        throw fehler
      }
    }
    if (zustand && zustand !== ausgang.naechster) {
      destroyRatchetState(zustand)
    }
    return ausgang.ergebnis
  })
}

/** Meldet, ob für diese Gegenstelle schon eine Sitzung liegt. */
export async function hatSitzung(id: string): Promise<boolean> {
  return (await ablage.lies(id)) !== null
}

/** Wo der abgelegte Schlüsselabdruck einer Sitzung liegt. */
export function bindungSchluessel(id: string): string {
  return `bindung:${id}`
}

/**
 * Wo eine Versandabsicht liegt. Das Präfix aus Buchstaben schliesst eine
 * Verwechslung mit einer `sitzungsId` (`<geraet>:<konto>:<geraet>`) aus.
 */
export function versandSchluessel(clientUuid: string): string {
  return `versand:${clientUuid}`
}

/**
 * Der Abdruck des Gegenstellen-Schlüssels, zu dem diese Sitzung gehört, oder
 * `null`.
 *
 * `null` heißt „nicht bekannt" — etwa bei einer Sitzung aus der Zeit vor
 * dieser Bindung. Dann wird nichts verworfen: eine fehlende Angabe ist kein
 * Befund.
 */
export async function leseBindung(id: string): Promise<string | null> {
  return ablage.lies(bindungSchluessel(id))
}

/**
 * Wirft eine Sitzung weg.
 *
 * Gebraucht beim Sitzungsbruch: die Gegenstelle hat neu installiert, ihre Seite
 * des Fadens gibt es nicht mehr, und ein Zustand, zu dem niemand mehr das
 * Gegenstück hält, ist nur noch im Weg.
 */
export async function verwirfSitzung(id: string): Promise<void> {
  await imSchloss(id, async () => {
    await ablage.loesche(id)
    // Die Bindungszeile und die Marke „war schon einmal etabliert" gehen mit.
    // Sonst gälte ein neuer Aufbau unter derselben Kennung als Wiederaufbau
    // einer Sitzung, die es nicht mehr gibt.
    await ablage.loesche(bindungSchluessel(id)).catch(() => {})
    await ablage.loesche(`etabliert:${id}`).catch(() => {})
  })
}

// ==========================================
// Schon erledigte Umschläge
// ==========================================

/**
 * Neben den Sitzungen liegen in derselben Ablage Marken: „das hier habe ich
 * schon erledigt". Sie tragen ein Präfix aus Buchstaben und können deshalb nie
 * mit einer `sitzungsId` (`<Konto>:<Gerätekennung>`) kollidieren.
 *
 * Gebraucht werden sie, weil ein Umschlag in der Mailbox liegen bleibt und der
 * Lesepfad bei jedem Abruf das ganze Fenster neu holt. Abgelegt wird nur
 * Klartext — alles andere käme ohne Marke bei jedem Durchlauf wieder, und beide
 * Fälle richten dabei Schaden an:
 *
 * - `aufbau`: derselbe Sitzungsaufbau baute die Sitzung erneut auf, der Ratchet
 *   fiel auf den Anfangszustand zurück, und der Verlauf meldete einen Neuaufbau,
 *   den niemand ausgelöst hatte.
 * - `bruch`: ein Umschlag, der sich nicht öffnen lässt, wurde bei jedem Abruf
 *   erneut als Sitzungsbruch gewertet — und ein Bruch wirft die Sitzung weg.
 *   Ein einziger alter Umschlag zerstörte so bei jedem Öffnen des Messengers
 *   die gerade funktionierende Sitzung, und ab da kam nichts mehr an.
 * - `abgewiesen`: ein Sitzungsaufbau, dessen Unterschrift nicht trug (Kennung:
 *   sein öffentlicher Teil), und jede Nachricht, die zu ihm gehört (Kennung wie
 *   bei `bruch`). Beides kommt bei jedem Abruf wieder; ohne die Marke wüsste der
 *   nächste Durchlauf nicht mehr, dass eine unlesbare Nachricht dieses Geräts
 *   zu einer Fälschung gehört — und würfe ihretwegen die echte Sitzung weg.
 */
export type Markenbereich = 'aufbau' | 'bruch' | 'abgewiesen' | 'etabliert'

const MARKE_WERT = '1'

/**
 * Schreibt jede Zeile dieser Ablage einmal neu — der Umstellungsdurchlauf für
 * Sitzungen und Marken. Siehe `schreibeNachrichtenBestandNeu`.
 */
export async function schreibeRatchetBestandNeu(): Promise<number> {
  const ids = await ablage.alleIds()
  let geschrieben = 0
  for (const id of ids) {
    const zustand = await ablage.lies(id)
    if (zustand === null) continue
    await ablage.schreibe(id, zustand)
    geschrieben++
  }
  return geschrieben
}

export async function kennstMarke(bereich: Markenbereich, kennung: string): Promise<boolean> {
  return (await ablage.lies(`${bereich}:${kennung}`)) !== null
}

/** Hält die Marke fest. Erst rufen, wenn das Erledigen geglückt ist. */
export async function merkeMarke(bereich: Markenbereich, kennung: string): Promise<void> {
  await ablage.schreibe(`${bereich}:${kennung}`, MARKE_WERT)
}

// ==========================================
// Versandabsichten
// ==========================================

/** Prüft, was aus einer Zeile gelesen wurde, bevor es weiterverwendet wird. */
function alsZustellung(roh: string | null): AusstehendeZustellung | null {
  if (!roh) return null
  try {
    const wert = JSON.parse(roh) as Partial<AusstehendeZustellung> | null
    if (!wert || typeof wert !== 'object') return null
    if (typeof wert.clientUuid !== 'string' || !wert.clientUuid) return null
    if (typeof wert.mailbox !== 'string' || !wert.mailbox) return null
    if (typeof wert.ciphertext !== 'string' || !wert.ciphertext) return null
    if (!Number.isInteger(wert.eigeneId) || !Number.isInteger(wert.peerId)) return null
    const zustellung =
      wert.zustellung &&
      typeof wert.zustellung.payloadId === 'string' &&
      typeof wert.zustellung.deliveryId === 'string'
        ? { payloadId: wert.zustellung.payloadId, deliveryId: wert.zustellung.deliveryId }
        : undefined
    return {
      clientUuid: wert.clientUuid,
      eigeneId: Number(wert.eigeneId),
      peerId: Number(wert.peerId),
      mailbox: wert.mailbox,
      ciphertext: wert.ciphertext,
      ...(wert.isControl ? { isControl: true } : {}),
      ...(typeof wert.controlType === 'string' ? { controlType: wert.controlType } : {}),
      ...(typeof wert.zielInbox === 'string' && wert.zielInbox
        ? { zielInbox: wert.zielInbox }
        : {}),
      ...(zustellung ? { zustellung } : {}),
      ...(wert.nurMailbox ? { nurMailbox: true } : {}),
      ...(typeof wert.angelegtAm === 'string' ? { angelegtAm: wert.angelegtAm } : {}),
      ...(Number.isInteger(wert.reihenfolge) ? { reihenfolge: Number(wert.reihenfolge) } : {}),
    }
  } catch {
    // Eine Zeile, die nicht aufgeht, ist keine Absicht. Sie bleibt liegen und
    // wird beim nächsten Durchlauf übersprungen — sie kostet nie eine Nachricht,
    // die woanders noch liegt.
    return null
  }
}

/**
 * Alle offenen Versandabsichten — für den Wiederaufnehmer.
 *
 * `eigeneId` grenzt auf das angemeldete Konto ein: auf diesem Rechner können
 * nacheinander mehrere Konten liegen, und eine Absicht gehört genau einem.
 * Sortiert nach Anlage, innerhalb eines Schritts Aufbau vor Nachricht.
 */
export async function ausstehendeZustellungen(
  eigeneId?: number,
): Promise<AusstehendeZustellung[]> {
  const ids = (await ablage.alleIds()).filter((id) => id.startsWith('versand:'))
  const gefunden: AusstehendeZustellung[] = []
  for (const id of ids) {
    const absicht = alsZustellung(await ablage.lies(id))
    if (!absicht) continue
    if (eigeneId !== undefined && absicht.eigeneId !== eigeneId) continue
    gefunden.push(absicht)
  }
  return gefunden.sort(
    (a, b) =>
      String(a.angelegtAm ?? '').localeCompare(String(b.angelegtAm ?? '')) ||
      (a.reihenfolge ?? 0) - (b.reihenfolge ?? 0),
  )
}

/** Meldet, ob zu dieser Kennung noch eine Absicht liegt. */
export async function hatZustellung(clientUuid: string): Promise<boolean> {
  return (await ablage.lies(versandSchluessel(clientUuid))) !== null
}

/**
 * Schreibt den Zustand einer Absicht fort — Kennungen der Geräte-Inbox oder
 * die Umstellung auf den Mailbox-Weg.
 *
 * Unter demselben Schloss wie der Versand dieser einen Kennung: zwei Tabs
 * dürfen nicht die eine Hälfte der Kennungen des anderen überschreiben.
 * Fehlt die Absicht (schon gesendet), ist das kein Fehler.
 */
export async function merkeZustellstand(
  clientUuid: string,
  aenderung: { zustellung?: { payloadId: string; deliveryId: string } | null; nurMailbox?: boolean },
): Promise<void> {
  await imSchloss(versandSchluessel(clientUuid), async () => {
    const absicht = alsZustellung(await ablage.lies(versandSchluessel(clientUuid)))
    if (!absicht) return
    if (aenderung.zustellung !== undefined) {
      if (aenderung.zustellung) absicht.zustellung = aenderung.zustellung
      else delete absicht.zustellung
    }
    if (aenderung.nurMailbox !== undefined) {
      if (aenderung.nurMailbox) absicht.nurMailbox = true
      else delete absicht.nurMailbox
    }
    await ablage.schreibe(versandSchluessel(clientUuid), JSON.stringify(absicht))
  })
}

/**
 * Nimmt die Absicht weg — erst, wenn der Umschlag nachweislich angenommen ist.
 *
 * Scheitert das Löschen, bleibt die Absicht liegen und wird noch einmal
 * gesendet. Das ist der harmlose Ausgang: dieselben Bytes unter derselben
 * Kennung sind beim Server eine Wiederholung, keine zweite Nachricht.
 */
export async function entferneZustellung(clientUuid: string): Promise<void> {
  await imSchloss(versandSchluessel(clientUuid), () =>
    ablage.loesche(versandSchluessel(clientUuid)),
  )
}
