/**
 * Die eigenen Rollen einer Gruppe — verschlüsselt abgelegt, hier ausgewertet.
 *
 * **Was vorher war.** Der Rechte-Dialog meldete „Rolle erstellt" und rief
 * `setRoles` auf React-State. Beim Schliessen war sie weg. Es gab weder Route
 * noch Tabelle; die vier Systemrollen standen fest im Quelltext.
 *
 * **Warum das hier und nicht im Backend steht.** Die Zusage an den Betreiber
 * lautet: so wenig wie möglich speichern, und was gespeichert wird, ist
 * verschlüsselt — Metadaten eingeschlossen. Ein Rollenname wie „Aufsicht
 * Nachtschicht" ist eine Metadatenzeile über eine Gruppe, die es nicht geben
 * soll. Der Server bekommt deshalb einen Block und eine Zahl. Die Zahl ist die
 * Revision, und sie ist das Einzige, was er versteht.
 *
 * **Drei Prüfungen, und keine davon kann der Server übernehmen:**
 *
 * 1. *Entschlüsseln* — mit dem Gruppenschlüssel. Wer ihn nicht hat, sieht
 *    einen Block.
 * 2. *Beglaubigen* — die Unterschrift liegt **in** der Nutzlast
 *    (`nutzlastSignatur.ts`). Ein Gruppenschlüssel beweist „von jemandem aus
 *    dieser Gruppe", nie „von Anna". Ohne diese Prüfung könnte jedes Mitglied
 *    sich selbst zum Verwalter erklären.
 * 3. *Befugnis* — durfte diese Person das schreiben? Das entscheidet der
 *    Aufrufer über `darfSchreiben`, denn nur er kennt die Rollenlage.
 *
 * **Die ehrliche Grenze.** Die erste Fassung hat keine Vorgängerin, gegen die
 * man sie prüfen könnte — dasselbe Henne-Ei wie beim Gruppenschlüssel selbst.
 * Wer die Gruppe gründet, setzt den ersten Stand. Erst mit MLS (Stufe 5) wird
 * die Mitgliedschaft eine kryptographische Tatsache; bis dahin stützt sich die
 * Befugnisprüfung auf die Rollenzeile, die der Server führt.
 */

import { getGroupConfig, putGroupConfig } from '@/api/social'
import {
  entschluesseleGruppenUmschlag,
  verschluesseleFuerGruppe,
  type GruppenKontext,
} from './gruppenSchluessel'
import { pruefeNutzlast, signiereNutzlast } from './nutzlastSignatur'

/** Formatnummer des Blockinhalts. Siehe `liesGruppenzustand`. */
export const KONFIG_FORMAT = 1

export interface GruppenRolle {
  /** Stabil über Umbenennungen hinweg — die Zuordnung hängt daran. */
  id: string
  name: string
  beschreibung: string
  rechte: string[]
}

export interface Gruppenzustand {
  /** Formatnummer, nicht die Revision. Die zählt der Server. */
  v: number
  /**
   * Wie die Gruppe heisst — seit Stufe 6 nur noch hier.
   *
   * `chat_groups.name` ist geräumt. Der Server kennt den Namen einer Gruppe
   * nicht mehr, und er soll ihn nicht kennen: er ist die eine Zeile, aus der
   * sich ohne jede Nachricht ablesen liesse, worum es geht.
   *
   * `null` heisst „dieser Stand sagt nichts dazu", nicht „namenlos" — ein
   * Altbestand aus der Zeit vor Stufe 6 hat hier nichts stehen, und der
   * Aufrufer greift dann auf seinen örtlichen Namensspeicher zurück.
   */
  name?: string | null
  beschreibung?: string | null
  /**
   * Das Logo als Data-URL (128 px, WebP) — wie in der Einladungskarte.
   *
   * Eine Adresse wie bisher müsste der Server ausliefern und wüsste dabei,
   * wer sich gerade eine Gruppe ansieht. Die Route dafür gibt es seit Stufe 6
   * nicht mehr.
   */
  logo?: string | null
  rollen: GruppenRolle[]
  /**
   * Rollenkennung → Konten, die sie tragen.
   *
   * Steht hier und nicht in `chat_group_members`, weil dort genau das
   * Klartextwissen entstünde, das nicht entstehen soll: „Konto 42 ist
   * Moderator in Gruppe 7".
   */
  zuordnung: Record<string, number[]>
}

export type Konfiglesung =
  /** Gelesen, beglaubigt, befugt. */
  | { art: 'zustand'; zustand: Gruppenzustand; revision: number; vonKonto: number }
  /** Die Gruppe hat noch keinen Zustand. Der nächste Schreibende legt ihn an. */
  | { art: 'leer' }
  /** Kein Schlüssel zu dieser Kennung — nachfordern und erneut lesen. */
  | { art: 'kein-schluessel'; keyId: string; revision: number }
  /** Unterschrift fehlt, ist falsch, oder der Unterzeichner durfte nicht. */
  | { art: 'unbefugt'; behauptet: number; revision: number }
  /** Entschlüsseln gescheitert oder Inhalt unbrauchbar. */
  | { art: 'unlesbar'; grund: string; revision: number }

export type Konfigschreibung =
  | { art: 'gespeichert'; revision: number }
  /**
   * Ein anderes Gerät war schneller. Nichts wurde überschrieben — der Aufrufer
   * liest neu, wendet seine Änderung auf den frischen Stand an und schreibt
   * noch einmal.
   */
  | { art: 'konflikt' }
  /** Dieses Gerät kann gerade nicht unterschreiben. Siehe `schreibeGruppenzustand`. */
  | { art: 'nicht-unterschreibbar' }

/** Ein leerer Ausgangszustand, wenn die Gruppe noch keinen hat. */
export function leererGruppenzustand(): Gruppenzustand {
  return { v: KONFIG_FORMAT, name: null, beschreibung: null, logo: null, rollen: [], zuordnung: {} }
}

/** Ein Text aus fremder Hand, auf ein erträgliches Mass gestutzt. */
function alsText(wert: unknown, hoechstens: number): string | null {
  if (typeof wert !== 'string') return null
  const sauber = wert.trim()
  return sauber ? sauber.slice(0, hoechstens) : null
}

/**
 * Nur `data:image/...` — dieselbe Schranke wie bei der Einladungskarte.
 *
 * Der Block kommt von einem anderen Menschen. `data:text/html,…` wäre in
 * einem `<img src>` harmlos, in einem späteren `<a href>` oder `window.open`
 * nicht. Die Schranke steht am Lesen, damit sie nicht an jeder Anzeige
 * einzeln stehen muss.
 */
function alsBild(wert: unknown): string | null {
  if (typeof wert !== 'string') return null
  return /^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$/.test(wert) ? wert : null
}

function istRolle(wert: unknown): wert is GruppenRolle {
  if (!wert || typeof wert !== 'object') return false
  const r = wert as Record<string, unknown>
  return (
    typeof r.id === 'string' &&
    r.id.length > 0 &&
    typeof r.name === 'string' &&
    typeof r.beschreibung === 'string' &&
    Array.isArray(r.rechte) &&
    r.rechte.every((e) => typeof e === 'string')
  )
}

/**
 * Liest den Block streng und wirft weg, was nicht passt.
 *
 * Nachsichtig lesen wäre hier falsch herum: ein halb verstandener Block ist
 * eine Rechtetabelle, von der niemand weiss, was in ihr steht. Lieber „nicht
 * lesbar" als „vielleicht".
 */
function liesGruppenzustand(roh: Record<string, unknown>): Gruppenzustand | null {
  // Eine höhere Formatnummer kommt von einem neueren Gerät. Dieses hier fasst
  // sie nicht an — sonst schriebe es beim nächsten Speichern alles zurück, was
  // es nicht verstanden hat.
  if (Number(roh.v) !== KONFIG_FORMAT) return null
  if (!Array.isArray(roh.rollen) || !roh.rollen.every(istRolle)) return null

  const zuordnung: Record<string, number[]> = {}
  const rohZuordnung = roh.zuordnung
  if (rohZuordnung && typeof rohZuordnung === 'object' && !Array.isArray(rohZuordnung)) {
    for (const [rollenId, konten] of Object.entries(rohZuordnung as Record<string, unknown>)) {
      if (!Array.isArray(konten)) return null
      const sauber = konten.filter((k): k is number => Number.isInteger(k) && (k as number) > 0)
      if (sauber.length !== konten.length) return null
      zuordnung[rollenId] = sauber
    }
  } else if (rohZuordnung !== undefined) {
    return null
  }

  return {
    v: KONFIG_FORMAT,
    // Nachsichtig, und hier mit Absicht anders als bei den Rollen: ein
    // krummer Name ist eine kaputte Überschrift, eine krumme Rechtetabelle
    // ist ein Sicherheitsproblem. Deshalb fällt dort der ganze Block durch
    // und hier nur das einzelne Feld.
    name: alsText(roh.name, 64),
    beschreibung: alsText(roh.beschreibung, 256),
    logo: alsBild(roh.logo),
    rollen: roh.rollen as GruppenRolle[],
    zuordnung,
  }
}

/**
 * Holt den Gruppenzustand und gibt ihn nur heraus, wenn alle drei Prüfungen
 * bestanden sind.
 *
 * `darfSchreiben` beantwortet: durfte das Konto, das unterschrieben hat, diesen
 * Stand setzen? Der Aufrufer weiss das — er kennt Eigentümer, Administratoren
 * und den vorigen Stand. Wird nichts übergeben, wird nur beglaubigt, nicht
 * befugt geprüft; das ist ausdrücklich die schwächere Betriebsart und
 * ausschliesslich für den Erstaufbau gedacht.
 */
export async function ladeGruppenzustand(
  kontext: GruppenKontext,
  darfSchreiben?: (konto: number) => boolean,
): Promise<Konfiglesung> {
  const antwort = await getGroupConfig(kontext.groupId)
  if (!antwort || !antwort.blob) return { art: 'leer' }

  const lesung = await entschluesseleGruppenUmschlag(kontext.groupId, antwort.blob)
  if (lesung.art === 'kein-schluessel') {
    return { art: 'kein-schluessel', keyId: lesung.keyId, revision: antwort.revision }
  }
  if (lesung.art !== 'klartext') {
    const grund = lesung.art === 'bruch' ? lesung.grund : 'unbekanntes-format'
    return { art: 'unlesbar', grund, revision: antwort.revision }
  }

  let roh: Record<string, unknown>
  try {
    const geparst = JSON.parse(lesung.text)
    if (!geparst || typeof geparst !== 'object' || Array.isArray(geparst)) {
      return { art: 'unlesbar', grund: 'kein-objekt', revision: antwort.revision }
    }
    roh = geparst as Record<string, unknown>
  } catch {
    return { art: 'unlesbar', grund: 'kein-json', revision: antwort.revision }
  }

  // Beglaubigen vor Auswerten. Eine unsignierte Rechtetabelle wird verworfen
  // und nicht etwa geduldet: anders als bei einer Nachricht ist „unsigniert"
  // hier genau der Angriff — jedes Mitglied hält den Gruppenschlüssel und
  // könnte sich sonst selbst jedes Recht eintragen.
  const beleg = await pruefeNutzlast(kontext.blindMailboxId, roh)
  if (beleg.art !== 'geprueft') {
    return {
      art: 'unbefugt',
      behauptet: beleg.art === 'gefaelscht' ? beleg.behauptet : 0,
      revision: antwort.revision,
    }
  }
  if (darfSchreiben && !darfSchreiben(beleg.vonKonto)) {
    return { art: 'unbefugt', behauptet: beleg.vonKonto, revision: antwort.revision }
  }

  const zustand = liesGruppenzustand(roh)
  if (!zustand) return { art: 'unlesbar', grund: 'unbekanntes-format', revision: antwort.revision }

  return { art: 'zustand', zustand, revision: antwort.revision, vonKonto: beleg.vonKonto }
}

/**
 * Schreibt den nächsten Stand: unterschreiben, verschlüsseln, ablegen.
 *
 * Verschlüsselt wird mit dem **aktuellen** Gruppenschlüssel — derselbe Weg wie
 * bei einer Nachricht. Damit wandert der Block nach einer Schlüsselrotation
 * beim nächsten Speichern von allein mit; ein eigener Umschreibepfad wäre eine
 * zweite Gelegenheit, ihn falsch zu machen.
 *
 * Kann dieses Gerät nicht unterschreiben — etwa weil das Signaturpaar fehlt —,
 * wird **nicht** geschrieben. `signiereNutzlast` gibt in diesem Fall die
 * Nutzlast unverändert zurück; bei einer Nachricht ist das richtig (lieber
 * unsigniert senden als gar nicht), bei einer Rechtetabelle nicht: jeder
 * Leser würde sie verwerfen, und die Gruppe stünde mit einem Stand da, den
 * niemand annimmt.
 */
export async function schreibeGruppenzustand(
  kontext: GruppenKontext,
  zustand: Gruppenzustand,
  erwarteteRevision: number,
): Promise<Konfigschreibung> {
  // Die Felder stehen einzeln da und nicht als `...zustand`: was in den Block
  // wandert, soll an dieser Stelle aufgezählt sein. Ein durchgereichtes Objekt
  // nähme irgendwann ein Feld mit, das niemand hier haben wollte — und es
  // stünde dann unterschrieben und verschlüsselt auf dem Server.
  const nutzlast = await signiereNutzlast(kontext.blindMailboxId, kontext.eigeneId, {
    v: KONFIG_FORMAT,
    name: zustand.name ?? null,
    beschreibung: zustand.beschreibung ?? null,
    logo: zustand.logo ?? null,
    rollen: zustand.rollen,
    zuordnung: zustand.zuordnung,
  })
  if (typeof nutzlast.sig !== 'string' || !nutzlast.sig) {
    return { art: 'nicht-unterschreibbar' }
  }

  const umschlag = await verschluesseleFuerGruppe(kontext, JSON.stringify(nutzlast))
  try {
    const gespeichert = await putGroupConfig(kontext.groupId, umschlag, erwarteteRevision)
    return { art: 'gespeichert', revision: gespeichert.revision }
  } catch (fehler) {
    if ((fehler as { status?: number })?.status === 409) return { art: 'konflikt' }
    throw fehler
  }
}

/**
 * Lesen, ändern, schreiben — und bei einem Konflikt noch einmal von vorn.
 *
 * Zwei Geräte, die gleichzeitig eine Rolle anlegen, dürfen einander nicht
 * überschreiben. Deshalb wird die Änderung nicht auf den gelesenen Stand
 * gerechnet, sondern auf den jeweils frischesten: `aendere` bekommt ihn
 * übergeben und gibt den neuen zurück.
 *
 * Zwei Versuche, nicht endlos. Wer dreimal hintereinander verliert, hat kein
 * Nebenläufigkeitsproblem mehr, sondern ein anderes — und eine Schleife, die
 * niemand sieht, ist schlimmer als eine ehrliche Fehlermeldung.
 */
export async function aendereGruppenzustand(
  kontext: GruppenKontext,
  darfSchreiben: (konto: number) => boolean,
  aendere: (vorher: Gruppenzustand) => Gruppenzustand,
): Promise<Konfigschreibung | { art: 'nicht-lesbar'; lesung: Konfiglesung }> {
  for (let versuch = 0; versuch < 2; versuch++) {
    const lesung = await ladeGruppenzustand(kontext, darfSchreiben)
    if (lesung.art !== 'zustand' && lesung.art !== 'leer') {
      return { art: 'nicht-lesbar', lesung }
    }
    const vorher = lesung.art === 'zustand' ? lesung.zustand : leererGruppenzustand()
    const revision = lesung.art === 'zustand' ? lesung.revision : 0

    const ergebnis = await schreibeGruppenzustand(kontext, aendere(vorher), revision)
    if (ergebnis.art !== 'konflikt') return ergebnis
  }
  return { art: 'konflikt' }
}

/**
 * Alle Rechte, die ein Konto über die **eigenen** Rollen der Gruppe hat.
 *
 * Die Systemrollen (Eigentümer, Administrator, Moderator) stehen nicht hier,
 * sondern in der Mitgliederzeile, die der Server führt — bis Stufe 6 sind das
 * zwei Quellen, und der Aufrufer führt sie zusammen. Diese Funktion kennt nur
 * die Hälfte, die im verschlüsselten Block steht.
 */
export function rechteAusZustand(zustand: Gruppenzustand, konto: number): Set<string> {
  const rechte = new Set<string>()
  for (const rolle of zustand.rollen) {
    if ((zustand.zuordnung[rolle.id] ?? []).includes(konto)) {
      for (const recht of rolle.rechte) rechte.add(recht)
    }
  }
  return rechte
}
