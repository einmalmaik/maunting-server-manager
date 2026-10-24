/**
 * Die Ablage des Geräteausweises: IndexedDB, Versiegelung, Altbestand.
 *
 * Hier entsteht die Identität dieses Geräts und hier bleibt sie. Der private
 * Teil verlässt das Gerät nie; veröffentlicht wird nur der öffentliche.
 *
 * **Ein Schlüssel je Gerät *und* Konto.** Bis 09/2026 lag hier genau ein Paar
 * unter dem festen Namen `self`, mit der Begründung, ein Gerät sei ein Gerät,
 * egal wer sich anmelde. Das war falsch, und zwar zweifach: der private
 * Schlüssel öffnete danach die Post beider Konten. Dazu stritten beide Konten
 * um dieselben Ratchet-Sitzungen. Ein Browser, in dem sich zwei Menschen
 * nacheinander anmelden, ist kein seltener Fall, und `clearSession` räumt die
 * IndexedDB nicht mit ab.
 */

import { randomBytes } from '@msdis/shield/random'
import { bytesToHex } from '@msdis/shield/core'
import i18n from '@/i18n'

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { erzeugeSignaturPaar, type SignaturPaar } from '../absenderSignatur'
import { generateLocalE2eeKeyPair, type LocalE2eeKeyPair } from '../e2eeCrypto'
import {
  MessengerVerschlossenError,
  entsiegleZeile,
  istOffen,
  versiegleZeile,
} from '../lokaleVersiegelung'
import { uebernehmeAltbestand as benenneSitzungenUm } from '../ratchetSpeicher'

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
    if (
      roh &&
      ((roh.konto !== undefined && Number(roh.konto) !== kontoId) ||
        (roh.userId !== undefined && Number(roh.userId) !== kontoId) ||
        (roh.user_id !== undefined && Number(roh.user_id) !== kontoId))
    ) {
      // Fremder Altbestand gehört einem anderen Konto — niemals übernehmen!
      return null
    }

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
export function meinKonto(): number {
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
    await benenneSitzungenUm(geraet.kennung)
    await schreibe(kontoId, geraet, true)
  } catch {
    // Beim nächsten Start noch einmal. `uebernehmeAltbestand` ist wiederholbar.
  }
}

/** Das im Arbeitsspeicher liegende Gerät eines Kontos, oder `null`. */
export function ramGeraetFuer(kontoId: number): EigenesGeraet | null {
  return geraetImRam?.konto === kontoId ? geraetImRam.geraet : null
}

/**
 * Leert die Zwischenspeicher im Arbeitsspeicher. Die IndexedDB bleibt.
 *
 * Der Schlüssel bleibt absichtlich liegen: wer sich wieder anmeldet, soll sein
 * Gespräch vorfinden und nicht jedes Mal eine neue Sitzung aufbauen.
 */
export function leereGeraeteRam(): void {
  geraetImRam = null
  aufbau = null
}
