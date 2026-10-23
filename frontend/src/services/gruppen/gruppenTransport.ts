/**
 * Wohin eine Gruppennachricht geht: in die Gerätepostfächer oder in die
 * geteilte Gruppenmailbox.
 *
 * **Die Entscheidung ist eine einzige Frage.** Kennt jedes Zielgerät aller
 * Mitglieder den neuen Transport (es hat eine `inbox_hash` veröffentlicht),
 * bekommt jedes Gerät seinen eigenen Zustellauftrag. Auch nur ein Gerät ohne
 * Fähigkeit, und die ganze Gruppe geht wie bisher als **ein** Umschlag in die
 * gemeinsame Mailbox — keine halbe Zustellung, keine Doppelspur.
 *
 * Warum nicht gemischt: der Empfänger liest beide Quellen. Ginge dieselbe
 * Nachricht teils per Postfach und teils per Mailbox, sähe ein Gerät sie
 * zweimal und müsste sich auf die Deduplizierung über die logische Kennung
 * verlassen — für eine reine Transportfrage zu viel Verlass auf die Anzeige.
 * Nur ein **abgelehnter** Auftrag fällt später einzeln zurück (`stelleZu`);
 * das ist die Ausnahme, nicht die Regel.
 *
 * **Frisch fragen.** Die Geräte kommen ohne Zwischenspeicher aus dem
 * Verzeichnis: wer gerade sein Telefon freigibt, muss dabei sein. Die
 * Mitglieder bestimmt `frischeMitglieder` — bei einem Ausfall gilt die
 * Momentaufnahme, dieselbe Regel wie beim Rotieren des Gruppenschlüssels.
 * Ein Netzfehler beim Geräteverzeichnis ist „unbekannt", nie „alles fähig";
 * dann trägt der Altweg.
 *
 * **Der Altweg bleibt offen.** Solange ein Gerät ohne Fähigkeit unterwegs sein
 * kann, ist die geteilte Mailbox der Rückfall. Sie zu schließen ist der letzte
 * Schritt des Plans (§19.2a): dann wählt diese Funktion die Mailbox nicht mehr,
 * sondern der Versand bricht mit einem sichtbaren Fehler ab. Das ist die eine
 * Bedingung an der einen Stelle.
 */

import { eigenesGeraet, verlangeGeraeteVon } from '../e2eeGeraet'
import { frischeMitglieder } from './gruppenVerteilung'
import type { GruppenKontext } from './typen'

/** Warum der alte Weg trägt. Nur für die Anzeige/Diagnose, nie für eine Zusage. */
export type GruppenTransportGrund = 'unfaehig' | 'unbekannt' | 'keine-geraete'

export type GruppenTransport =
  /** Jedes Zielgerät kann den neuen Weg; `inboxen` ist die Adressliste. */
  | { art: 'inbox'; inboxen: string[] }
  /** Mindestens ein Zielgerät kann ihn nicht (oder wir wissen es nicht). */
  | { art: 'mailbox'; grund: GruppenTransportGrund }

/**
 * Der Rückfall auf die geteilte Mailbox — **der eine Schalter**.
 *
 * Solange ein Gerät ohne Fähigkeit unterwegs sein kann, trägt sie die
 * Nachricht. Wer den Altweg schließt (§19.2a Schritt 4), ändert nur diese
 * Funktion: statt der Mailbox wirft sie dann einen sichtbaren Fehler, und der
 * Versand bricht ab, statt still auf den alten Weg auszuweichen.
 */
function altweg(grund: GruppenTransportGrund): GruppenTransport {
  return { art: 'mailbox', grund }
}

/**
 * Die Zielinboxen einer Gruppennachricht — oder die Entscheidung für den
 * Altweg.
 *
 * Das eigene, gerade sendende Gerät steht nicht in der Liste: es hat die
 * Nachricht lokal. Die eigenen übrigen Geräte gehören dazu, denn sie sind
 * eigenständige Zustände und keine Kopien.
 */
export async function gruppenTransportZiele(
  kontext: GruppenKontext,
): Promise<GruppenTransport> {
  // Die Mitglieder, wie der Server sie in diesem Augenblick sieht. Ohne
  // Antwort gilt die Momentaufnahme — dieselbe Regel wie beim Rotieren des
  // Gruppenschlüssels.
  const mitglieder = await frischeMitglieder(kontext)

  // **Frisch, ohne Zwischenspeicher und ohne Rückfall.** `verlangeGeraeteVon`
  // wirft bei einem Ausfall; das ist hier die richtige Antwort, denn eine zehn
  // Minuten alte Liste könnte ein gerade freigegebenes Gerät verschweigen —
  // und die Nachricht ginge ohne es raus.
  const meins = await eigenesGeraet().catch(() => null)
  if (!meins) {
    // Ohne eigenes Gerät lässt sich nicht sagen, welche Kopie fehlen darf.
    return altweg('unbekannt')
  }

  const listen = await Promise.all(
    mitglieder.map(async (konto) => ({
      konto,
      geraete: await verlangeGeraeteVon(konto, { frisch: true }),
    })),
  ).catch(() => null)
  if (!listen) {
    // Kein Gerät oder keine Antwort: beides heißt „nicht alle bekannt".
    return altweg('unbekannt')
  }

  const inboxen: string[] = []
  const gesehen = new Set<string>()
  for (const { konto, geraete } of listen) {
    for (const geraet of geraete) {
      // Das sendende Gerät hat die Nachricht schon. Ein zweiter Zustellauftrag
      // an die eigene Inbox wäre eine Kopie ohne Nutzen.
      if (konto === kontext.eigeneId && geraet.device_id === meins.kennung) continue
      if (!geraet.inbox_hash) {
        return altweg('unfaehig')
      }
      // Zwei Konten können nie dieselbe Inbox führen; die Menge hält nur einen
      // versehentlichen Doppeleintrag aus der Antwort heraus.
      if (gesehen.has(geraet.inbox_hash)) continue
      gesehen.add(geraet.inbox_hash)
      inboxen.push(geraet.inbox_hash)
    }
  }

  if (inboxen.length === 0) {
    // Nur das eigene Gerät: es gibt niemanden, dem ein Postfach nützt. Der
    // Altweg bleibt die Antwort, damit sich am Verhalten nichts ändert.
    return altweg('keine-geraete')
  }

  return { art: 'inbox', inboxen }
}
