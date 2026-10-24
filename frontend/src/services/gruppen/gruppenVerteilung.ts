/**
 * Erzeugen, Rotieren und Zustellen des Gruppenschluessels.
 *
 * Zugestellt wird in die Geraete-Mailbox jedes Empfaengers, nie in die
 * Gruppenmailbox: sonst laege der Schluessel hinter dem Schloss, das er oeffnet.
 */

import { randomBytes } from '@msdis/shield/random'
import { bytesToBase64 } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'

import { getGroupMembers, relayE2eeEnvelope } from '@/api/social'
import { deriveUserDeviceMailboxId, encryptE2eeHybrid } from '../e2eeCrypto'
import { eigenesGeraet, verlangeGeraeteVon } from '../e2eeGeraet'
import { signiereNutzlast } from '../nutzlastSignatur'
import { ablage } from './gruppenAblage'
import { geheimnisFuer, sichereBesitznachweis } from './gruppenMailbox'
import {
  GRUPPEN_SCHLUESSEL_TYP,
  KEY_ID_LAENGE,
  SCHLUESSEL_BYTES,
  GruppenSchluesselNichtZugestelltError,
  gleicheMitglieder,
  normalisiereMitglieder,
  type GruppenKontext,
  type GruppenSchluesselEintrag,
  type GruppenZustellung,
} from './typen'

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
export async function frischeMitglieder(kontext: GruppenKontext): Promise<number[]> {
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
export async function schluesselZumSenden(kontext: GruppenKontext): Promise<GruppenSchluesselEintrag> {
  const jetzige = await frischeMitglieder(kontext)
  const { geheimnis, frischErzeugt } = await geheimnisFuer(kontext)
  if (geheimnis) await sichereBesitznachweis(kontext, geheimnis)

  const vorhanden = await ablage.liesAktuellen(kontext.groupId)
  /**
   * Ein **frisch erzeugtes** Geheimnis muss zu den anderen, und der einzige
   * Weg dorthin ist die Schlüsselzustellung. Ohne Mitgliederwechsel rotiert
   * aber nichts — also erzwingt es hier einmal eine Rotation. Das kostet eine
   * Generation und heilt sich von selbst; alles andere wäre ein zweiter
   * Zustellweg neben dem ersten, der genauso oft scheitern kann.
   */
  if (vorhanden && !frischErzeugt && gleicheMitglieder(vorhanden.mitglieder, jetzige)) {
    // Ein früherer Lauf kann einzelne Geräte verpasst haben. Der Auftrag liegt
    // dauerhaft; hier ist der nächste Anlass, ihn nachzuholen.
    await wiederholeOffene(kontext.groupId, kontext.eigeneId)
    return vorhanden
  }

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
  await verteileSchluessel(kontext, eintrag, jetzige, geheimnis)
  return eintrag
}

/**
 * Versiegelt eine Nutzlast für jedes Gerät der genannten Konten und relayed sie.
 *
 * Zugestellt wird in die **Geräte-Mailbox des Empfängers**, nicht in die der
 * Gruppe. Bis 09/2026 lief es andersherum, mit zwei Folgen:
 *
 * 1. Die Gruppenmailbox liess sich nicht verschliessen. Wer den Schlüssel noch
 *    nicht hatte, hätte ihn hinter genau dem Schloss holen müssen, das er
 *    aufsperren sollte — eine Laufzeitprobe zeigte das Mitglied danach vor
 *    einer stummen Mailbox.
 * 2. Das Lesefenster verhungerte: 99 von 100 Umschlägen waren
 *    Schlüsselzustellungen für fremde Geräte, die dieses Gerät nie öffnen kann.
 *
 * Gelesen wird weiterhin aus beiden Quellen — Altbestand liegt noch in den
 * Gruppenmailboxen, und der soll nicht verloren gehen.
 */
export async function anJedesGeraet(
  kontext: GruppenKontext,
  empfaengerIds: readonly number[],
  nutzlast: string,
  kontrolltyp: string,
  nur?: readonly string[],
): Promise<{ anzahl: number; kennungen: string[]; fehlend: string[]; ziele: number }> {
  const meins = await eigenesGeraet()
  const basis =
    typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : `gk-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
  // Für ein Nachtragen an **einzelne** Geräte: die übrigen haben es schon.
  const erlaubt = nur ? new Set(nur) : null

  let lfd = 0
  let zugestellt = 0
  let ziele = 0
  const kennungen: string[] = []
  const fehlend: string[] = []
  for (const empfaengerId of empfaengerIds) {
    // Frisch aus dem Verzeichnis: nach einer Freigabe oder einem Entfernen
    // darf weder ein altes Gerät den Schlüssel bekommen noch ein neues fehlen.
    const geraete = await verlangeGeraeteVon(empfaengerId, { frisch: true })
    const ziel = await deriveUserDeviceMailboxId(empfaengerId)
    for (const geraet of geraete) {
      // Das eigene Gerät hat den Schlüssel schon.
      if (empfaengerId === kontext.eigeneId && geraet.device_id === meins.kennung) continue
      if (erlaubt && !erlaubt.has(geraet.device_id)) continue
      ziele += 1
      const nummer = lfd++
      try {
        const umschlag = await encryptE2eeHybrid(nutzlast, geraet.public_key)
        await relayE2eeEnvelope({
          blind_mailbox_id: ziel,
          ciphertext_envelope: umschlag,
          // Je Gerät eine eigene Kennung: das Relais gibt beim zweiten Aufruf
          // mit derselben Kennung still den ersten Umschlag zurück.
          client_uuid: `${basis}#${nummer}`,
          is_control: true,
          control_type: kontrolltyp,
        })
        zugestellt += 1
        kennungen.push(geraet.device_id)
      } catch {
        // Ein Gerät, das sich nicht erreichen lässt, hält die übrigen nicht auf.
        // Es wird aber **festgehalten**: bis 10/2026 war der Teilfehler still,
        // und nur die eigene Nachfrage des Geräts holte den Schlüssel manchmal
        // nach. Wer den Auftrag schreibt, entscheidet `verteileSchluessel`.
        fehlend.push(geraet.device_id)
      }
    }
  }

  return { anzahl: zugestellt, kennungen, fehlend, ziele }
}

export async function schluesselNutzlast(
  kontext: GruppenKontext,
  eintrag: GruppenSchluesselEintrag,
  geheimnis: string | null,
): Promise<string> {
  const basis: Record<string, unknown> = {
    typ: GRUPPEN_SCHLUESSEL_TYP,
    v: 1,
    groupId: eintrag.groupId,
    keyId: eintrag.keyId,
    schluessel: eintrag.schluessel,
    mitglieder: eintrag.mitglieder,
    // Fehlt bei Gruppen, deren Eigentümer noch nicht gesendet hat. Ein
    // Empfänger, der nichts bekommt, bleibt schlicht ohne — er darf selbst
    // keines erzeugen.
    ...(geheimnis ? { geheimnis } : {}),
  }
  const signiert = await signiereNutzlast(kontext.blindMailboxId, kontext.eigeneId, basis)
  return JSON.stringify(signiert)
}

/**
 * Der Zustellauftrag wird **vor** dem Verteilen geschrieben.
 *
 * Scheitert ein einzelnes Gerät, liegt der Auftrag schon da und wird beim
 * nächsten Versand oder Start wiederholt. Scheitert gar keines, wird er wieder
 * gelöscht. Fällt das Schreiben aus, geht keine Nachricht mit einem Schlüssel
 * raus, dessen Verteilung niemand mehr nachholen kann.
 */
async function verteileSchluessel(
  kontext: GruppenKontext,
  eintrag: GruppenSchluesselEintrag,
  empfaenger: readonly number[],
  geheimnis: string | null,
): Promise<number> {
  const basis: Omit<GruppenZustellung, 'offen'> = {
    groupId: eintrag.groupId,
    keyId: eintrag.keyId,
    blindMailboxId: kontext.blindMailboxId,
    mitglieder: [...empfaenger],
    empfaenger: [...empfaenger],
    seit: new Date().toISOString(),
  }
  const nutzlast = await schluesselNutzlast(kontext, eintrag, geheimnis)
  const ergebnis = await anJedesGeraet(
    kontext,
    empfaenger,
    nutzlast,
    GRUPPEN_SCHLUESSEL_TYP,
  )
  if (ergebnis.fehlend.length > 0) {
    await ablage.schreibeZustellung({ ...basis, offen: ergebnis.fehlend })
  } else {
    await ablage.loescheZustellung(kontext.groupId, eintrag.keyId)
  }

  // Kein einziges Ziel erreicht, obwohl es welche gab. Das war bis 09/2026 eine
  // stille 0, die niemand auswertete: der Absender schrieb munter weiter mit
  // einem Schlüssel, den ausser ihm keiner hatte, und im Verlauf der anderen
  // stand nur „Verschlüsselte Nachricht".
  if (ergebnis.ziele > 0 && ergebnis.anzahl === 0) {
    throw new GruppenSchluesselNichtZugestelltError(kontext.groupId, ergebnis.ziele)
  }
  return ergebnis.anzahl
}

/**
 * Holt eine einzelne offene Zustellung nach.
 *
 * Gibt es den Schlüssel nicht mehr (gruppenweise gelöscht), fällt der Auftrag
 * mit weg. Was inzwischen aus dem Verzeichnis verschwunden ist, gilt als
 * erledigt: ein entferntes Gerät braucht keinen Schlüssel mehr, und der
 * Auftrag soll nicht ewig liegen bleiben.
 */
async function wiederholeZustellung(
  zustellung: GruppenZustellung,
  eigeneId: number,
): Promise<void> {
  const eintrag = await ablage.lies(zustellung.groupId, zustellung.keyId)
  if (!eintrag) {
    await ablage.loescheZustellung(zustellung.groupId, zustellung.keyId)
    return
  }
  const geheimnis = (await ablage.liesGeheimnis(zustellung.groupId))?.geheimnis ?? null
  const kontext: GruppenKontext = {
    groupId: zustellung.groupId,
    blindMailboxId: zustellung.blindMailboxId,
    eigeneId,
    mitglieder: zustellung.mitglieder,
    // Aus einem Nachtrag entsteht kein Eigentümerrang — hier wird nur
    // zugestellt, nie ein Geheimnis erzeugt.
    istEigentuemer: false,
  }
  try {
    const nutzlast = await schluesselNutzlast(kontext, eintrag, geheimnis)
    const ergebnis = await anJedesGeraet(
      kontext,
      zustellung.empfaenger,
      nutzlast,
      GRUPPEN_SCHLUESSEL_TYP,
      zustellung.offen,
    )
    if (ergebnis.fehlend.length === 0) {
      await ablage.loescheZustellung(zustellung.groupId, zustellung.keyId)
    } else {
      await ablage.schreibeZustellung({ ...zustellung, offen: ergebnis.fehlend })
    }
  } catch {
    // Netz oder Relais: der Auftrag bleibt liegen und kommt beim nächsten
    // Anlass wieder. Ein Teilfehler darf das Senden nicht aufhalten.
  }
}

/** Offene Zustellungen dieser Gruppe nachholen. */
async function wiederholeOffene(groupId: number, eigeneId: number): Promise<void> {
  const offene = await ablage.liesZustellungen(groupId)
  for (const zustellung of offene) {
    await wiederholeZustellung(zustellung, eigeneId)
  }
}

/**
 * Alle offenen Schlüsselzustellungen nachholen — beim Start, nach einem
 * Netzwechsel und beim Entsperren.
 *
 * Ein Gerät, das beim Verteilen nicht erreichbar war, bekommt den Schlüssel so
 * ausdrücklich nachgeliefert, statt darauf zu warten, dass es die unbekannte
 * Kennung selbst bemerkt und nachfragt.
 */
export async function verteileOffeneZustellungenAller(eigeneId: number): Promise<number> {
  if (!Number.isInteger(eigeneId) || eigeneId <= 0) return 0
  let erledigt = 0
  const offene = await ablage.liesZustellungen()
  for (const zustellung of offene) {
    await wiederholeZustellung(zustellung, eigeneId)
    erledigt += 1
  }
  return erledigt
}
