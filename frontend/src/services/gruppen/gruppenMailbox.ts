/**
 * Geheimnis, Mailbox und Besitznachweis einer Gruppe.
 *
 * Aus dem Geheimnis entstehen zwei getrennte Hashes: die Mailbox-Kennung und
 * ihr Besitznachweis. Wer die Kennung kennt, hat damit nicht den Nachweis.
 */

import { randomBytes } from '@msdis/shield/random'
import { bytesToBase64 } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'

import { registriereMailbox } from '@/api/social'
import { abonniereMailbox } from '../mailboxAbo'
import { mailboxNachweis, merkeMailboxNachweis } from '../mailboxNachweis'
import { ablage } from './gruppenAblage'
import {
  SCHLUESSEL_BYTES,
  type GruppenGeheimnis,
  type GruppenKontext,
} from './typen'

/**
 * Zwei Werte aus einem Geheimnis, und beide sind ein SHA-256 mit eigenem
 * Vorsatz.
 *
 * Der Vorsatz ist nicht Zierde: ohne ihn wäre die Mailbox-Kennung derselbe
 * Wert wie der Besitznachweis. Wer die Kennung kennt — und die steht in jedem
 * Aufruf —, hätte damit auch den Nachweis, und das ganze Schloss wäre ein
 * Türschild. Dieselbe Trennung wie beim Tresor, wo `bucketId` und
 * `bucketAuthToken` aus demselben Seed mit verschiedenen Zusätzen entstehen.
 */
const MAILBOX_VORSATZ = 'msm:gruppe:mailbox:'
const NACHWEIS_VORSATZ = 'msm:gruppe:nachweis:'

/** Die Mailbox-Kennung einer Gruppe aus ihrem Geheimnis. 64 Hexzeichen. */
export async function mailboxAusGeheimnis(geheimnis: string): Promise<string> {
  return sha256Hex(new TextEncoder().encode(MAILBOX_VORSATZ + geheimnis))
}

/** Der Besitznachweis derselben Mailbox. 64 Hexzeichen, nie gleich der Kennung. */
export async function nachweisAusGeheimnis(geheimnis: string): Promise<string> {
  return sha256Hex(new TextEncoder().encode(NACHWEIS_VORSATZ + geheimnis))
}

/**
 * Wohin eine Gruppe sendet und woraus sie liest.
 *
 * Der Umzug in einem Wert. `senden` ist die Kennung aus dem Geheimnis, sobald
 * es eines gibt, sonst die alte aus der `group_id`. `lesen` sind **beide**,
 * solange es zwei gibt.
 *
 * Warum beide: die Mitglieder bekommen das Geheimnis nicht gleichzeitig. Es
 * reist als Steuerumschlag in ihre Geräte-Mailbox und kommt beim nächsten
 * Durchlauf an — bei einem heute, beim anderen morgen. Wer es schon hat,
 * sendet in die neue Mailbox; wer nicht, in die alte. Läse jeder nur seine
 * eigene, verlöre das Gespräch für Tage die Hälfte seiner Nachrichten, und
 * zwar lautlos. Gelesen wird deshalb aus beiden, bis der Bestand versiegt.
 *
 * **Die Kennung ist nicht die Gesprächskennung.** Der örtliche Klartext-Cache,
 * die Anzeige und die Wache gegen das Umschalten hängen weiter an der alten,
 * ableitbaren Kennung — die bleibt, was sie war. Bewegt hat sich nur die
 * Adresse auf dem Umschlag.
 */
export interface GruppenZiele {
  /** Die Mailbox, in die neue Nachrichten gehen. */
  senden: string
  /** Alle Mailboxen, aus denen zu lesen ist. Neue zuerst. */
  lesen: string[]
  /** Der Besitznachweis der neuen Kennung, falls es eine gibt. */
  nachweis: string | null
}

/**
 * Wohin eine alte Kennung umgezogen ist — synchron, ohne die Ablage zu fragen.
 *
 * Gebraucht dort, wo eine Kennung ankommt und niemand die Gruppe dazu kennt:
 * beim Löschen einer Nachricht etwa, das nur die Mailbox des Gesprächs hat.
 * Gefüllt wird die Karte überall, wo das Geheimnis ohnehin aufgeschlagen wird.
 *
 * Leer heisst „kein Umzug bekannt", nie „kein Umzug". Wer darauf baut, muss
 * den Fall vertragen — etwa indem er beide Kennungen bedient.
 */
const umzugsZiele = new Map<string, string>()

export function umgezogeneMailbox(altKennung: string): string | null {
  return umzugsZiele.get((altKennung || '').trim().toLowerCase()) ?? null
}

/** Nur für Tests: die gemerkten Umzüge vergessen. */
export function leereUmzuege(): void {
  umzugsZiele.clear()
}

export function merkeUmzug(altKennung: string, neu: string): void {
  const alt = (altKennung || '').trim().toLowerCase()
  if (!alt || !neu || alt === neu) return
  umzugsZiele.set(alt, neu)
}

/**
 * Die alte, abgeleitete Kennung — das Ergebnis, wenn nichts anderes bekannt
 * ist. Sie funktioniert immer: beide Seiten können sie ausrechnen.
 */
export function beiDerAltenKennung(altKennung: string): GruppenZiele {
  abonniereMailbox(altKennung, mailboxNachweis(altKennung))
  return { senden: altKennung, lesen: [altKennung], nachweis: null }
}

export async function gruppenZiele(groupId: number, altKennung: string): Promise<GruppenZiele> {
  // Die Ablage darf hier nicht durchschlagen. Wo ein Gespräch liegt, ist eine
  // Frage, auf die es immer eine Antwort geben muss — eine verschlossene oder
  // fehlende IndexedDB nähme sonst nicht den Umzug, sondern das **Lesen**:
  // `einDurchlauf` käme nie bis zum Abruf, und der Verlauf bliebe leer.
  let eintrag: GruppenGeheimnis | null
  try {
    eintrag = await ablage.liesGeheimnis(groupId)
  } catch {
    return beiDerAltenKennung(altKennung)
  }
  if (!eintrag?.geheimnis) {
    // Kein Geheimnis: Bestandsgruppe, oder das Geheimnis ist noch unterwegs.
    return beiDerAltenKennung(altKennung)
  }

  const neu = await mailboxAusGeheimnis(eintrag.geheimnis)
  const token = await nachweisAusGeheimnis(eintrag.geheimnis)
  merkeMailboxNachweis(neu, token)
  abonniereMailbox(neu, token)
  if (neu === altKennung) return { senden: neu, lesen: [neu], nachweis: token }

  merkeUmzug(altKennung, neu)
  abonniereMailbox(altKennung, mailboxNachweis(altKennung))
  return { senden: neu, lesen: [neu, altKennung], nachweis: token }
}

/**
 * Hinterlegt den Besitznachweis der Gruppenmailbox beim Server.
 *
 * **Auf der neuen Kennung, nie auf der alten** — und daran hängt alles. Der
 * erste Entwurf registrierte auf der alten, und eine Laufzeitprobe am
 * 22.09.2026 zeigte, warum das die Gruppe zerstört: damals lief die
 * Schlüsselzustellung über genau diese Mailbox. Sobald der Eigentümer
 * registriert hatte, bekam jedes Mitglied ohne Geheimnis dort 403 — und damit
 * nie den Schlüssel, aus dem das Geheimnis gekommen wäre. Gemessen: das
 * Mitglied sah die Nachricht nicht einmal mehr als „Verschlüsselte Nachricht".
 *
 * Ein Schloss vor die Tür zu hängen, deren Schlüssel dahinter liegt, ist kein
 * Sicherheitsgewinn, sondern ein Aussperren.
 *
 * Jetzt geht es, weil der Schlüssel einen anderen Weg nimmt: `anJedesGeraet`
 * stellt in die **Geräte-Mailbox** des Empfängers zu. Durch die neue
 * Gruppenmailbox kommt nichts, was man zum Öffnen der neuen Gruppenmailbox
 * bräuchte — der Kreis ist durchtrennt. Die alte Kennung bleibt offen und
 * unregistriert; sie trägt den Bestand, und `assert_mailbox_participant` hält
 * sie wie bisher.
 */
export async function sichereBesitznachweis(kontext: GruppenKontext, geheimnis: string): Promise<void> {
  const mailbox = await mailboxAusGeheimnis(geheimnis)
  const token = await nachweisAusGeheimnis(geheimnis)
  merkeMailboxNachweis(mailbox, token)
  merkeUmzug(kontext.blindMailboxId, mailbox)
  // Und dem Echtzeitstrom sagen, dass diese Mailbox uns angeht. Für eine
  // Kennung, die der Server nicht ausrechnen kann, ist das der einzige Weg,
  // überhaupt etwas zu erfahren.
  abonniereMailbox(mailbox, token)

  try {
    await registriereMailbox(mailbox, token)
  } catch {
    // Still, und das mit Absicht. Ein 409 hiesse „schon registriert, mit einem
    // anderen Token" — dann hat jemand ein zweites Geheimnis erzeugt, und das
    // ist ein Fall für die Schlüsselzustellung, nicht fürs Senden. Alles
    // andere ist ein Netzfehler. In beiden Fällen lieber ohne Nachweis weiter
    // als gar nicht senden: der Nachweis ist jederzeit nachholbar, eine
    // verlorene Nachricht nicht.
    void kontext
  }
}

/**
 * Das Geheimnis dieser Gruppe — und wer es erzeugen darf, wenn es keines gibt.
 *
 * Bestandsgruppen haben keines. Eines zu erzeugen darf deshalb **nur der
 * Eigentümer**: erzeugten zwei Mitglieder gleichzeitig eines, hätte die Gruppe
 * zwei Mailboxen und zerfiele in zwei Hälften, die einander nicht mehr sehen.
 * Wer nicht Eigentümer ist, wartet — spätestens mit der nächsten
 * Schlüsselzustellung bekommt er es.
 */
export async function geheimnisFuer(
  kontext: GruppenKontext,
): Promise<{ geheimnis: string | null; frischErzeugt: boolean }> {
  const vorhanden = await ablage.liesGeheimnis(kontext.groupId)
  if (vorhanden) return { geheimnis: vorhanden.geheimnis, frischErzeugt: false }
  if (!kontext.istEigentuemer) return { geheimnis: null, frischErzeugt: false }

  const roh = randomBytes(SCHLUESSEL_BYTES)
  const neu = bytesToBase64(roh)
  roh.fill(0)
  await ablage.schreibeGeheimnis({
    groupId: kontext.groupId,
    geheimnis: neu,
    erzeugtAm: new Date().toISOString(),
  })
  // Nicht das gerade erzeugte zurückgeben, sondern das abgelegte: ein
  // gleichzeitiger zweiter Aufruf hat vielleicht gewonnen, und dann gilt
  // seines.
  const abgelegt = (await ablage.liesGeheimnis(kontext.groupId))?.geheimnis ?? neu
  return { geheimnis: abgelegt, frischErzeugt: true }
}

/**
 * Das Gruppengeheimnis, falls dieses Gerät es kennt.
 *
 * Die eine Stelle, an der es nach aussen geht — und bewusst nur lesend. Wer
 * es braucht, braucht etwas **daraus**: die Mailbox-Kennung, den
 * Besitznachweis, den Einladungsschlüssel. Jeder dieser Werte ist ein
 * einseitiger Hash, keiner führt zurück.
 *
 * `null` heisst: diese Gruppe hat noch keines, oder dieses Gerät hat es noch
 * nicht bekommen. Beides ist Alltag und kein Fehler — das Geheimnis entsteht,
 * wenn der Eigentümer zum ersten Mal sendet.
 */
export async function gruppenGeheimnis(groupId: number): Promise<string | null> {
  try {
    return (await ablage.liesGeheimnis(groupId))?.geheimnis ?? null
  } catch {
    // Verschlossene oder fehlende Ablage. Wie bei `gruppenZiele`: die Antwort
    // ist „weiss ich nicht", nie ein geworfener Fehler beim Aufrufer.
    return null
  }
}
