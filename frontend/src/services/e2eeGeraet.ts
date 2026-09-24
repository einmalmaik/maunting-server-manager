/**
 * Die E2EE-Identität dieses Geräts — und es gibt nur diese eine.
 *
 * Vorher hing sie am Konto: ein RSA-Paar, dessen privater Teil mit einem
 * Argon2id-Schlüssel aus einem abgetippten Wiederherstellungsschlüssel verpackt
 * beim Server lag, damit jedes Gerät ihn holen konnte. Das war selbst schon die
 * zweite Reparatur eines Mehrgeräteproblems — davor überschrieb jedes neue
 * Gerät den Schlüssel des vorigen.
 *
 * Mit dem Double Ratchet ist ein geteilter privater Schlüssel nicht bloß
 * unnötig, sondern falsch. Der Ratchet ist eine lineare Kette: wer eine
 * Nachricht entschlüsselt, vernichtet dabei den Schlüssel, der sie geöffnet
 * hat. Zwei Geräte mit demselben privaten Schlüssel laufen darum unweigerlich
 * auseinander und verklemmen die Sitzung.
 *
 * Also gehört der Schlüssel jetzt dem Gerät. Er entsteht hier, er bleibt hier,
 * und veröffentlicht wird nur der öffentliche Teil. Kein Dialog, kein Code,
 * nichts zum Aufschreiben: ein Browser bekommt seine Identität nach dem
 * normalen Login, die Tauri-App und die APK nach dem Kopplungscode, den sie
 * ohnehin zum Anmelden einlösen.
 *
 * Was das kostet, steht bewusst hier: **ein Gerät, das seine IndexedDB
 * verliert, verliert seinen Gesprächsfaden.** Es meldet sich mit einem neuen
 * Schlüssel, die Gegenstellen bauen neue Sitzungen auf, und alles davor bleibt
 * unlesbar. Genau dafür gibt es den Verlaufs-Erstabgleich über die Kopplung.
 *
 * **Ein Schlüssel je Gerät *und* Konto.** Bis 09/2026 lag hier genau ein Paar
 * unter dem festen Namen `self`, mit der Begründung, ein Gerät sei ein Gerät,
 * egal wer sich anmelde. Das war falsch, und zwar zweifach: der private
 * Schlüssel öffnete danach die Post beider Konten. Dazu stritten beide Konten
 * um dieselben Ratchet-Sitzungen. Ein Browser, in dem sich zwei Menschen
 * nacheinander anmelden, ist kein seltener Fall, und `clearSession` räumt die
 * IndexedDB nicht mit ab.
 *
 * **Zwei Paare, zwei Aufgaben.** Neben dem RSA-OAEP-Paar, gegen das
 * verschlüsselt wird, hält ein Gerät seit 09/2026 ein ECDSA-Paar, mit dem es
 * seine Nutzlasten unterschreibt. Warum ein Gruppenschlüssel das nicht leisten
 * kann, steht im Kopf von `nutzlastSignatur.ts`. Keines der beiden darf die
 * Aufgabe des anderen übernehmen; das Backend prüft beide getrennt.
 */

import { randomBytes } from '@msdis/shield/random'
import { bytesToHex, utf8ToBytes } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'
import i18n from '@/i18n'

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { erzeugeSignaturPaar, pruefe, signiere, type SignaturPaar } from './absenderSignatur'
import { generateLocalE2eeKeyPair, type LocalE2eeKeyPair } from './e2eeCrypto'
import {
  MessengerVerschlossenError,
  entsiegleZeile,
  istOffen,
  versiegleZeile,
} from './lokaleVersiegelung'
import { uebernehmeAltbestand } from './ratchetSpeicher'
import {
  approveEigenesGeraet,
  getE2eeGeraete,
  putEigenesGeraet,
  removeEigenesGeraet,
  resetEigeneGeraete,
  type E2eeGeraetItem,
} from '@/api/social'

/** 16 Bytes hex. Bedeutungsfrei — die Kennung steht im Klartext in jedem Umschlag. */
const KENNUNG_BYTES = 16

const IDB_DB_NAME = 'msm_e2ee_keystore'
const IDB_GERAETE_STORE = 'devices'

export interface EigenesGeraet {
  kennung: string
  paar: LocalE2eeKeyPair
  /**
   * Das zweite Paar: ECDSA, und es verschlüsselt nichts.
   *
   * Es beglaubigt den Absender einer Gruppennachricht — die Begründung steht
   * im Kopf von `absenderSignatur.ts`. Der Bestand aus der Zeit davor trägt es
   * noch nicht; `lies` rüstet es beim ersten Zugriff nach, damit es nicht an
   * zwei Stellen „vielleicht da" heißen muss.
   */
  signaturPaar: SignaturPaar
}

// ==========================================
// Lokale Ablage
// ==========================================

let geraetImRam: { konto: number; geraet: EigenesGeraet } | null = null
/**
 * Ein einziger Aufbau-Lauf, auch wenn mehrere Aufrufer gleichzeitig fragen.
 * Ohne diese Klammer erzeugten zwei parallele `eigenesGeraet()` zwei Paare, und
 * das zweite überschriebe das erste — mitsamt allen Sitzungen, die schon gegen
 * das erste laufen.
 *
 * Das Konto steht daneben, weil ein laufender Aufbau für Konto A die Antwort
 * für Konto B nicht sein darf.
 */
let aufbau: { konto: number; lauf: Promise<EigenesGeraet> } | null = null

/** Der Ablageschlüssel dieses Kontos. Vor 09/2026 hieß er für alle `self`. */
function ablageSchluessel(kontoId: number): string {
  return `konto:${kontoId}`
}

function oeffneDatenbank(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      return reject(new Error(i18n.t('chat.errors.indexedDbUnavailable')))
    }
    // Version 3: Store `devices` kommt hinzu. `keys` und `keyring` stammen aus
    // der Zeit des Kontoschlüssels und bleiben unangetastet — dort liegt das
    // Material, mit dem `altbestandUebernahme` den alten Verlauf einmal rettet.
    const req = indexedDB.open(IDB_DB_NAME, 3)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains('keys')) {
        db.createObjectStore('keys', { keyPath: 'userId' })
      }
      if (!db.objectStoreNames.contains('keyring')) {
        db.createObjectStore('keyring', { keyPath: 'userId' })
      }
      if (!db.objectStoreNames.contains(IDB_GERAETE_STORE)) {
        // Ein Eintrag je Konto, Schlüssel `konto:<id>`. Der Altbestand steht
        // unter `self` und wird von `uebernimmAltbestand` einmalig umgehängt.
        db.createObjectStore(IDB_GERAETE_STORE, { keyPath: 'id' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

/**
 * Eine abgelegte Zeile.
 *
 * `sitzungenUebernommen` ist der Haken hinter der einmaligen Umbenennung der
 * Ratchet-Sitzungen. Er steht hier und nicht in einer zweiten Ablage, weil er
 * genau dann etwas bedeutet, wenn es diese Zeile gibt. Fehlt er, gilt die
 * Umbenennung als offen: Zeilen aus der Übernahme tragen ihn erst, nachdem sie
 * gelaufen ist, und frisch erzeugte tragen ihn sofort, weil es bei ihnen nichts
 * zu übernehmen gibt.
 */
interface GeraeteZeile {
  kennung: string
  paar: LocalE2eeKeyPair
  /** `null` heißt: abgelegt vor 09/2026, das Signaturpaar fehlt noch. */
  signaturPaar: SignaturPaar | null
  sitzungenUebernommen: boolean
}

function ausZeile(zeile: any): GeraeteZeile | null {
  if (!zeile?.kennung || !zeile?.publicKeyJwk || !zeile?.privateKeyJwk) return null
  return {
    kennung: zeile.kennung,
    paar: { publicKeyJwk: zeile.publicKeyJwk, privateKeyJwk: zeile.privateKeyJwk },
    signaturPaar:
      zeile.signPublicKeyJwk && zeile.signPrivateKeyJwk
        ? { publicKeyJwk: zeile.signPublicKeyJwk, privateKeyJwk: zeile.signPrivateKeyJwk }
        : null,
    sitzungenUebernommen: zeile.sitzungenUebernommen === true,
  }
}

/** Bindet die Zeile eines Kontos an ihren Platz. Siehe `versiegleZeile`. */
function geraetAad(kontoId: number): string {
  return `msm-geraet:${ablageSchluessel(kontoId)}`
}

/**
 * Baut die Zeile und macht sie zu, falls ein Messenger-PIN eingerichtet ist.
 *
 * Nur `id` bleibt lesbar, das ist der Ablageschlüssel. Der private Teil des
 * Geräteausweises lag hier bis 09/2026 als JWK im Klartext — wer das Profil
 * kopierte, konnte sich als dieses Gerät ausgeben und mitlesen.
 */
async function zuZeile(
  kontoId: number,
  geraet: EigenesGeraet,
  sitzungenUebernommen: boolean,
): Promise<Record<string, unknown>> {
  return await versiegleZeile(
    {
      id: ablageSchluessel(kontoId),
      kennung: geraet.kennung,
      publicKeyJwk: geraet.paar.publicKeyJwk,
      privateKeyJwk: geraet.paar.privateKeyJwk,
      // Derselbe Schutz wie für den privaten Geräteschlüssel: wer ihn hat,
      // kann als dieses Gerät signieren und in jeder Gruppe als sein Mensch
      // auftreten.
      signPublicKeyJwk: geraet.signaturPaar.publicKeyJwk,
      signPrivateKeyJwk: geraet.signaturPaar.privateKeyJwk,
      sitzungenUebernommen,
    },
    ['id'],
    geraetAad(kontoId),
  )
}

async function lies(kontoId: number): Promise<GeraeteZeile | null> {
  try {
    const db = await oeffneDatenbank()
    const roh = await new Promise<Record<string, any> | null>((resolve, reject) => {
      const tx = db.transaction(IDB_GERAETE_STORE, 'readonly')
      const req = tx.objectStore(IDB_GERAETE_STORE).get(ablageSchluessel(kontoId))
      req.onsuccess = () => resolve(req.result ?? null)
      req.onerror = () => reject(req.error)
    })
    return ausZeile(await entsiegleZeile(roh, geraetAad(kontoId)))
  } catch {
    return null
  }
}

async function schreibe(
  kontoId: number,
  geraet: EigenesGeraet,
  sitzungenUebernommen: boolean,
): Promise<void> {
  const db = await oeffneDatenbank()
  const zeile = await zuZeile(kontoId, geraet, sitzungenUebernommen)
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(IDB_GERAETE_STORE, 'readwrite')
    const req = tx.objectStore(IDB_GERAETE_STORE).put(zeile)
    req.onsuccess = () => resolve()
    req.onerror = () => reject(req.error)
  })
}

/**
 * Schreibt die Zeile dieses Kontos einmal neu — der Umstellungsdurchlauf für
 * den Geräteausweis. Siehe `schreibeNachrichtenBestandNeu`.
 */
export async function schreibeGeraetBestandNeu(kontoId: number): Promise<boolean> {
  const vorhanden = await lies(kontoId)
  if (!vorhanden) return false
  await schreibe(kontoId, await vollstaendig(vorhanden), vorhanden.sitzungenUebernommen)
  return true
}

/**
 * Macht aus einer abgelegten Zeile eine vollständige Identität.
 *
 * Der einzige Ort, an dem ein Signaturpaar nachwächst. Es steht hier und nicht
 * bei der Neuanlage, weil der Bestand aus der Zeit davor genau denselben Weg
 * nimmt: lesen, feststellen dass es fehlt, eines münzen, weitermachen. Wer es
 * münzt, muss die Zeile danach schreiben — sonst wäre es bei jedem Start ein
 * anderes und niemand könnte die Signatur prüfen, die er gerade gelesen hat.
 */
async function vollstaendig(zeile: GeraeteZeile): Promise<EigenesGeraet> {
  return {
    kennung: zeile.kennung,
    paar: zeile.paar,
    signaturPaar: zeile.signaturPaar ?? (await erzeugeSignaturPaar()),
  }
}

/**
 * Hängt den alten `self`-Eintrag an das Konto, das sich als erstes meldet.
 *
 * Ohne diesen Schritt verlöre jede bestehende Installation beim Update ihren
 * Schlüssel und damit jede laufende Sitzung. Welchem Konto der Altbestand
 * gehört, steht nirgends — er hat nie eines getragen. Das erste Konto nach dem
 * Update ist die einzige verfügbare Antwort und fast immer die richtige: es ist
 * dasselbe, das den Browser vorher benutzt hat.
 *
 * Der Anspruch selbst liegt in **einer** Transaktion: zwei Tabs, die
 * gleichzeitig starten, sollen nicht beide denselben Altbestand übernehmen und
 * anschließend verschiedene Meinungen darüber haben, wem er gehört. Nur wer
 * `self` beim Zugreifen noch vorfindet, gewinnt.
 *
 * Das Versiegeln passiert davor, außerhalb der Transaktion — es ist asynchron
 * und eine IndexedDB-Transaktion überlebt kein `await`. Der Altbestand selbst
 * stammt aus der Zeit vor dem Siegel und liegt deshalb immer im Klartext.
 */
async function uebernimmAltbestand(kontoId: number): Promise<GeraeteZeile | null> {
  try {
    const db = await oeffneDatenbank()

    const roh = await new Promise<Record<string, any> | null>((resolve, reject) => {
      const tx = db.transaction(IDB_GERAETE_STORE, 'readonly')
      const req = tx.objectStore(IDB_GERAETE_STORE).get('self')
      req.onsuccess = () => resolve(req.result ?? null)
      req.onerror = () => reject(req.error)
    })
    const alt = ausZeile(await entsiegleZeile(roh, geraetAad(kontoId)))
    if (!alt) return null

    // Das Signaturpaar wächst hier nach und wird mit derselben Zeile
    // geschrieben. Einmal gemünzt, einmal abgelegt, einmal zurückgegeben —
    // münzte der Aufrufer danach ein zweites, wäre das geschriebene tot.
    const geraet = await vollstaendig(alt)

    // Der Haken steht bewusst auf `false`: die Sitzungen sind noch nicht
    // umbenannt, und wenn das gleich scheitert, muss der nächste Start es
    // erneut versuchen dürfen.
    const zeile = await zuZeile(kontoId, geraet, false)

    const gewonnen = await new Promise<boolean>((resolve, reject) => {
      const tx = db.transaction(IDB_GERAETE_STORE, 'readwrite')
      const store = tx.objectStore(IDB_GERAETE_STORE)
      let anspruch = false
      const req = store.get('self')
      req.onsuccess = () => {
        if (!req.result) return
        anspruch = true
        store.put(zeile)
        store.delete('self')
      }
      req.onerror = () => reject(req.error)
      tx.oncomplete = () => resolve(anspruch)
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })

    return gewonnen ? { ...alt, signaturPaar: geraet.signaturPaar } : null
  } catch {
    return null
  }
}

/**
 * Das angemeldete Konto. Kommt aus `lib/angemeldetesKonto`, nicht aus dem
 * Store: der holt sich `clearGeraeteMemory` von hier, ein Rückimport wäre ein
 * Zyklus.
 */
function meinKonto(): number {
  const id = angemeldetesKonto()
  if (id === null) {
    throw new Error(i18n.t('chat.errors.noE2eeIdentity'))
  }
  return id
}

/**
 * Die Identität dieses Geräts für das angemeldete Konto, bei Bedarf erzeugt.
 *
 * Ohne IndexedDB wirft das: ein Gerät, dessen Schlüssel jeden Neustart nicht
 * überlebt, kann keine Sitzung halten. Ein stiller Rückfall auf reinen
 * RAM-Betrieb sähe eine Minute lang aus wie ein funktionierender Messenger und
 * würde danach jedes Gespräch verlieren.
 *
 * Ohne angemeldetes Konto wirft es ebenfalls. Ein Rückfall auf einen über alle
 * Konten geteilten Schlüssel ist genau der Zustand, den diese Datei seit
 * 09/2026 nicht mehr herstellt.
 */
export async function eigenesGeraet(): Promise<EigenesGeraet> {
  const konto = meinKonto()

  /*
   * Bei gesperrtem Messenger hört das hier auf, und zwar bevor irgendetwas
   * gelesen wird.
   *
   * Der Grund steht ein paar Zeilen tiefer: findet `lies` nichts, gilt das als
   * „dieses Gerät hat noch keine Identität" und der Weg endet bei einem frisch
   * erzeugten Schlüsselpaar. Gesperrt findet `lies` aber **nie** etwas — die
   * Ablage gibt ohne Schlüssel nichts heraus. Ohne diese Schranke liefe jeder
   * Zugriff im gesperrten Zustand auf den Neuanlage-Zweig zu und versuchte, die
   * bestehende Identität zu überschreiben. Das Schreiben scheitert zwar
   * seinerseits am Siegel, aber der Fehler käme dann aus der Ablage und hiesse
   * irgendetwas — hier heisst er, was er ist.
   */
  if (!istOffen()) throw new MessengerVerschlossenError()

  if (geraetImRam?.konto === konto) return geraetImRam.geraet
  if (aufbau?.konto === konto) return aufbau.lauf

  const lauf = (async () => {
    let zeile = await lies(konto)
    if (!zeile) zeile = await uebernimmAltbestand(konto)
    if (zeile) {
      const geraet = await vollstaendig(zeile)
      // Ein nachgewachsenes Signaturpaar muss auf die Platte, bevor irgendwer
      // damit signiert: sonst prüfte die Gegenstelle gegen einen Schlüssel,
      // den der nächste Start nicht mehr kennt.
      if (!zeile.signaturPaar) {
        await schreibe(konto, geraet, zeile.sitzungenUebernommen)
      }
      if (!zeile.sitzungenUebernommen) await holeSitzungenNach(konto, geraet)
      geraetImRam = { konto, geraet }
      return geraet
    }
    const frisch: EigenesGeraet = {
      kennung: bytesToHex(randomBytes(KENNUNG_BYTES)),
      paar: await generateLocalE2eeKeyPair(),
      signaturPaar: await erzeugeSignaturPaar(),
    }
    // Ein frisches Gerät hat nichts zu übernehmen: der Haken steht sofort.
    // Wichtig für ein **zweites** Konto in diesem Browser — die Sitzungen des
    // ersten gehören ihm nicht.
    await schreibe(konto, frisch, true)
    geraetImRam = { konto, geraet: frisch }
    return frisch
  })()
  aufbau = { konto, lauf }

  try {
    return await lauf
  } finally {
    aufbau = null
  }
}

/**
 * Benennt die Sitzungen um, die an dem übernommenen Schlüssel hängen.
 *
 * Beides gehört zusammen: die Ratchet-Zustände tragen seit 09/2026 die eigene
 * Gerätekennung im Namen, und die steht erst fest, wenn der Altbestand ein
 * Konto gefunden hat.
 *
 * Der Haken fällt erst, wenn das Umbenennen geglückt ist, und genau darum geht
 * es hier. In der Erstfassung lief die Umbenennung **einmal**, im Augenblick der
 * Übernahme. Genau das ging am laufenden System schief: der Schlüssel war
 * umgehängt, das Umbenennen scheiterte, `self` war weg — und damit gab es keinen
 * zweiten Versuch mehr. Der Browser stand mit einem gültigen Schlüssel und
 * lauter unauffindbaren Sitzungen da. Ein Fehlschlag darf einen Versuch kosten,
 * nicht den Gesprächsfaden.
 */
async function holeSitzungenNach(kontoId: number, geraet: EigenesGeraet): Promise<void> {
  try {
    await uebernehmeAltbestand(geraet.kennung)
    await schreibe(kontoId, geraet, true)
  } catch {
    // Beim nächsten Start noch einmal. `uebernehmeAltbestand` ist wiederholbar.
  }
}

// ==========================================
// Veröffentlichen
// ==========================================

let veroeffentlichtAls: string | null = null

/**
 * Meldet dieses Gerät beim Konto an. Idempotent und bei jedem Start fällig.
 *
 * Das erneute Melden ist kein Selbstzweck: es frischt `last_seen_at` auf, und
 * danach richtet sich der Gerätedeckel im Backend. Ein Gerät, das sich nie
 * wieder meldet, soll irgendwann Platz machen.
 */
export async function geraetVeroeffentlichen(label = ''): Promise<EigenesGeraet> {
  const geraet = await eigenesGeraet()
  if (veroeffentlichtAls === geraet.kennung) return geraet
  const antwort = await putEigenesGeraet({
    deviceId: geraet.kennung,
    publicKey: geraet.paar.publicKeyJwk,
    signingPublicKey: geraet.signaturPaar.publicKeyJwk,
    label,
  })
  setzeEigeneFreigabe(antwort?.is_approved !== false)
  veroeffentlichtAls = geraet.kennung
  return geraet
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

// ==========================================
// Gegenstellen
// ==========================================

const CACHE_FRIST_MS = 600000
/**
 * Wie alt die Liste höchstens sein darf, wenn eine Prüfung „frisch" verlangt.
 * Kurz genug für ein Gerät, das sich gerade angemeldet hat; lang genug, dass
 * eine Flut gefälschter Umschläge nicht in eine Flut von Abrufen übersetzt.
 */
const FRISCH_MS = 30_000

const geraeteCache = new Map<number, { geraete: E2eeGeraetItem[]; geholtAm: number }>()

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

// ==========================================
// Vertraute Geräte: der Server hat nicht das letzte Wort
// ==========================================

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

/**
 * - `neues_geraet`: ein Gerät kam dazu, von einem vertrauten freigegeben.
 * - `unbestaetigt`: der Server nennt ein Gerät, das keine gültige Freigabe
 *   trägt — neu oder mit neuem Schlüssel. Es bekommt nichts.
 * - `konto_neustart`: keines der bekannten Geräte ist mehr da. Das ist der
 *   Neustart nach Verlust aller Geräte — oder ein Server, der Geräte
 *   austauscht. Unterscheiden kann das nur der Mensch: Sicherheitsnummer.
 */
export type SchluesselWarnungTyp = 'neues_geraet' | 'unbestaetigt' | 'konto_neustart'

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
 */
export async function vertrauteGeraete(
  userId: number,
  liste: E2eeGeraetItem[],
): Promise<E2eeGeraetItem[]> {
  const hashes = await Promise.all(liste.map((g) => sha256Hex(utf8ToBytes(g.public_key))))
  const fingerabdruck = (i: number): VertrautesGeraet => ({
    k: hashes[i],
    s: liste[i].signing_public_key || '',
  })
  const bekannt = liesVertraute(userId)

  if (bekannt === null) {
    const neu: VertrauteListe = {}
    liste.forEach((g, i) => {
      neu[g.device_id] = fingerabdruck(i)
    })
    schreibeVertraute(userId, neu)
    return liste
  }

  const eigenes = geraetImRam?.konto === userId ? geraetImRam.geraet : null
  const vertraut = new Map<string, VertrautesGeraet>()
  const offen: number[] = []
  liste.forEach((g, i) => {
    const fp = fingerabdruck(i)
    const alt = bekannt[g.device_id]
    const istEigenes =
      eigenes?.kennung === g.device_id && eigenes.paar.publicKeyJwk === g.public_key
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
      const g = liste[i]
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

  if (vertraut.size === 0 && liste.length > 0) {
    const neu: VertrauteListe = {}
    liste.forEach((g, i) => {
      neu[g.device_id] = fingerabdruck(i)
    })
    schreibeVertraute(userId, neu)
    melde({ userId, typ: 'konto_neustart', geraete: liste.map((g) => g.device_id) })
    return liste
  }

  // Entfernte Geräte bleiben in der Liste: ihr Signaturschlüssel beglaubigt
  // weiter, was sie freigegeben haben, bevor sie gingen.
  schreibeVertraute(userId, { ...bekannt, ...Object.fromEntries(vertraut) })
  if (neuFreigegeben.length > 0) melde({ userId, typ: 'neues_geraet', geraete: neuFreigegeben })
  if (offen.length > 0) {
    melde({ userId, typ: 'unbestaetigt', geraete: offen.map((i) => liste[i].device_id) })
  }
  return liste.filter((g) => vertraut.has(g.device_id))
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

function setzeEigeneFreigabe(wert: boolean | null): void {
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

async function unterschreibeMitEigenem(daten: string): Promise<{ kennung: string; sig: string }> {
  const meins = await eigenesGeraet()
  return { kennung: meins.kennung, sig: await signiere(daten, meins.signaturPaar.privateKeyJwk) }
}

/** Gibt ein wartendes Gerät des eigenen Kontos frei — unterschrieben von diesem. */
export async function gebeGeraetFrei(geraet: E2eeGeraetItem): Promise<void> {
  const konto = meinKonto()
  const daten = await freigabeDaten(
    konto,
    geraet.device_id,
    geraet.public_key,
    geraet.signing_public_key,
  )
  const { kennung, sig } = await unterschreibeMitEigenem(daten)
  await approveEigenesGeraet(geraet.device_id, kennung, sig)
  vergessenGeraete(konto)
}

/**
 * Entfernt ein Gerät des eigenen Kontos. Ein freigegebenes verlangt die
 * Unterschrift dieses Geräts; ein wartendes nicht — es hat nie etwas bekommen.
 */
export async function entferneGeraet(geraet: E2eeGeraetItem): Promise<void> {
  const konto = meinKonto()
  let unterschrift: { kennung: string; sig: string } | null = null
  if (geraet.is_approved !== false) {
    unterschrift = await unterschreibeMitEigenem(
      await entfernenDaten(konto, geraet.device_id, geraet.public_key),
    )
  }
  await removeEigenesGeraet(geraet.device_id, unterschrift?.kennung, unterschrift?.sig)
  vergessenGeraete(konto)
}

/**
 * Alle Geräte verloren: das Verzeichnis dieses Kontos leeren und neu beginnen.
 * Alle anderen Sitzungen fliegen hinaus, dieses Gerät meldet sich danach als
 * erstes wieder an, und die Kontakte bekommen eine Warnung.
 */
export async function geraeteZuruecksetzen(passwort: string): Promise<void> {
  const konto = meinKonto()
  await resetEigeneGeraete(passwort)
  veroeffentlichtAls = null
  vergessenGeraete(konto)
  await geraetVeroeffentlichen()
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
async function holeGeraete(userId: number, frist = CACHE_FRIST_MS): Promise<E2eeGeraetItem[]> {
  const cached = geraeteCache.get(userId)
  if (cached && Date.now() - cached.geholtAm < frist) {
    return cached.geraete
  }
  try {
    // Gecacht wird die geprüfte Liste: wer aus dem Cache liest, bekommt nie
    // ein Gerät, das die Prüfung nicht bestanden hat.
    const roh = await getE2eeGeraete(userId)
    // Die Liste nennt nur freigegebene Geräte. Steht dieses darin, hat ein
    // anderes es inzwischen freigegeben — der Hinweis kann weg.
    if (geraetImRam?.konto === userId && roh.some((g) => g.device_id === geraetImRam?.geraet.kennung)) {
      setzeEigeneFreigabe(true)
    }
    const geraete = await vertrauteGeraete(userId, roh)
    geraeteCache.set(userId, { geraete, geholtAm: Date.now() })
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
  { frisch = false }: { frisch?: boolean } = {},
): Promise<E2eeGeraetItem[]> {
  return holeGeraete(userId, frisch ? FRISCH_MS : CACHE_FRIST_MS)
}

export function vergessenGeraete(userId: number): void {
  geraeteCache.delete(userId)
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

/**
 * Leert die RAM-Zwischenspeicher. Die IndexedDB überlebt das Abmelden.
 *
 * Der Schlüssel bleibt absichtlich liegen: wer sich wieder anmeldet, soll sein
 * Gespräch vorfinden und nicht jedes Mal eine neue Sitzung aufbauen. Dass er
 * dabei nicht mehr in fremde Hände gerät, regelt der Kontoschlüssel der Ablage,
 * nicht das Aufräumen hier.
 */
export function clearGeraeteMemory(): void {
  geraeteCache.clear()
  vertrauteImRam.clear()
  setzeEigeneFreigabe(null)
  geraetImRam = null
  aufbau = null
  veroeffentlichtAls = null
}
