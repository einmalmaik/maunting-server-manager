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

const DB_NAME = 'msm_e2ee_ratchet'
const DB_VERSION = 1
const STORE = 'sessions'

/**
 * Die Sitzungskennung. Eine Sitzung gehört einem **Gerät**, nicht einem Konto:
 * dieselbe Person an zwei Geräten sind zwei Fäden, die nichts voneinander
 * wissen.
 */
export function sitzungsId(peerUserId: number, peerGeraeteId: string): string {
  return `${peerUserId}:${peerGeraeteId}`
}

// ==========================================
// Ablage (austauschbar für Tests)
// ==========================================

export interface RatchetAblage {
  lies(id: string): Promise<string | null>
  schreibe(id: string, zustand: string): Promise<void>
  loesche(id: string): Promise<void>
}

function oeffneDatenbank(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      return reject(new Error('IndexedDB nicht verfügbar'))
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

const indexedDbAblage: RatchetAblage = {
  async lies(id) {
    const db = await oeffneDatenbank()
    return new Promise<string | null>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly')
      const req = tx.objectStore(STORE).get(id)
      req.onsuccess = () => resolve(req.result?.zustand ?? null)
      req.onerror = () => reject(req.error)
    })
  },
  async schreibe(id, zustand) {
    const db = await oeffneDatenbank()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite')
      const req = tx
        .objectStore(STORE)
        .put({ sitzungsId: id, zustand, aktualisiertAm: new Date().toISOString() })
      req.onsuccess = () => resolve()
      req.onerror = () => reject(req.error)
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
}

let ablage: RatchetAblage = indexedDbAblage

/**
 * Hängt eine andere Ablage ein. Nur für Tests gedacht — und dafür der Grund,
 * warum hier kein `fake-indexeddb` als Abhängigkeit steht: eine Schnittstelle
 * mit drei Methoden ist leichter zu fälschen als eine Datenbank nachzubauen.
 */
export function setzeAblageFuerTest(neu: RatchetAblage | null): void {
  ablage = neu ?? indexedDbAblage
  schloesser.clear()
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

export interface SchrittErgebnis<T> {
  /** Der Nachfolgezustand. Fehlt er, bleibt der gespeicherte unverändert. */
  naechster?: RatchetState
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
        await ablage.schreibe(id, serializeRatchetState(ausgang.naechster))
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

/**
 * Wirft eine Sitzung weg.
 *
 * Gebraucht beim Sitzungsbruch: die Gegenstelle hat neu installiert, ihre Seite
 * des Fadens gibt es nicht mehr, und ein Zustand, zu dem niemand mehr das
 * Gegenstück hält, ist nur noch im Weg.
 */
export async function verwirfSitzung(id: string): Promise<void> {
  await imSchloss(id, () => ablage.loesche(id))
}

// ==========================================
// Schon angewandte Sitzungsaufbauten
// ==========================================

/**
 * Neben den Sitzungen liegen in derselben Ablage Marken: „diesen Aufbau habe
 * ich schon angewandt". Sie tragen ein Präfix aus Buchstaben und können
 * deshalb nie mit einer `sitzungsId` (`<Konto>:<Gerätekennung>`) kollidieren.
 */
const AUFBAU_PRAEFIX = 'aufbau:'

/**
 * Ob dieser Sitzungsaufbau schon einmal angewandt wurde.
 *
 * Ein Aufbau bleibt als Umschlag in der Mailbox liegen, und der Lesepfad holt
 * das ganze Fenster bei jedem Abruf neu — abgelegt wird nur Klartext, ein
 * Aufbau also nie. Ohne diese Marke baute derselbe Umschlag die Sitzung bei
 * jedem Durchlauf erneut auf: der Ratchet fiel auf den Anfangszustand zurück,
 * und weil dabei eine Sitzung vorgefunden wurde, meldete der Verlauf einen
 * Neuaufbau, den niemand ausgelöst hatte.
 */
export async function kennstAufbau(kennung: string): Promise<boolean> {
  return (await ablage.lies(AUFBAU_PRAEFIX + kennung)) !== null
}

/** Hält fest, dass dieser Aufbau angewandt ist. Erst nach dem Anwenden rufen. */
export async function merkeAufbau(kennung: string): Promise<void> {
  await ablage.schreibe(AUFBAU_PRAEFIX + kennung, '1')
}
