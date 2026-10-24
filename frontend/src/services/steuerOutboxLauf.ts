/**
 * Die Outbox der Steuerwirkungen abarbeiten.
 *
 * Bearbeiten, Löschen, Reaktion, Anheften und Verfall liegen als Auftrag in der
 * kontogebundenen Ablage (`steuerOutboxTypen.ts`). Hier werden sie abgearbeitet
 * — bei jedem Anlass, nicht in einem Timer:
 *
 * * Start, sobald die Identität dieses Geräts bereit ist,
 * * `online` und die Rückkehr der App nach vorn (`useMessengerKern`),
 * * das Entsperren des Messengers (derselbe Effekt),
 * * nach jedem erfolgreichen Senden (`sendeWirkung` in `useKonversation`).
 *
 * **Ein Lauf zur Zeit, ein Anstoß geht nicht verloren.** Zwei Anlässe kurz
 * hintereinander (Start und `online`) würden sonst denselben Auftrag doppelt
 * bauen. Ein Anstoß während eines Laufs wird gemerkt und danach genau einmal
 * nachgeholt — dieselbe Regel wie beim Tresor-Uploader und beim Medien-Nachholen.
 *
 * **Kontowechsel bricht ab.** Der Lauf gehört dem Konto, das ihn angestoßen hat;
 * wechselt es, endet er, statt mit der Sitzung des neuen Kontos zu senden.
 *
 * **Kein stilles Aufgeben.** Ein endgültig abgelehnter Auftrag fällt heraus,
 * seine lokale Wirkung wird zurückgenommen, und die Oberfläche sagt es. Nach
 * `MAX_VERSUCHE` gilt dasselbe. Was übrig bleibt, ist ein sichtbarer Fehler —
 * nie eine Lücke.
 */

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { eigenesGeraet } from '@/services/e2eeGeraet'
import type { E2eeIdentity } from '@/services/e2eeIdentity'
import { sendeSteuerpaket, type SteuerKontext } from './gespraech/steuerung'
import { ladeSteuerAuftraege } from './messengerLocalStore'
import {
  merkeSteuerFehlversuch,
  nimmWirkungZurueck,
  vergissSteuerAuftrag,
} from './steuerOutbox'
import {
  istEndgueltigAbgelehnt,
  MAX_VERSUCHE,
  type SteuerAuftrag,
} from './steuerOutboxTypen'

/**
 * Warum ein Auftrag aufgegeben wurde.
 *
 * `abgelehnt`: der Server hat ihn mit einem endgültigen Status abgewiesen.
 * `aufgegeben`: die Versuche sind erschöpft (Netz, 429, 5xx über Tage).
 */
export type SteuerOutboxGrund = 'abgelehnt' | 'aufgegeben'

export type SteuerOutboxMelder = (auftrag: SteuerAuftrag, grund: SteuerOutboxGrund) => void

let melder: SteuerOutboxMelder | null = null

/**
 * Wer von einem aufgegebenen Auftrag erfährt.
 *
 * Der Lauf kennt keine React-Zustände. Die Oberfläche hängt sich hier ein und
 * zeigt eine Systemzeile; in Tests ist es ein einfacher Zähler.
 */
export function setzeSteuerOutboxMelder(neu: SteuerOutboxMelder | null): () => void {
  melder = neu
  return () => {
    if (melder === neu) melder = null
  }
}

export interface LaufOptionen {
  /** Die Uhr für Tests und Proben; im Betrieb `Date.now`. */
  jetzt?: () => number
}

/** Ein Lauf zur Zeit, und ein Anstoß währenddessen geht nicht verloren. */
let laeuft: Promise<void> | null = null
let nochEinmal = false

export function stosseSteuerOutboxAn(optionen: LaufOptionen = {}): Promise<void> {
  if (laeuft) {
    nochEinmal = true
    return laeuft
  }
  laeuft = (async () => {
    try {
      do {
        nochEinmal = false
        await einLauf(optionen)
      } while (nochEinmal)
    } catch {
      // Abarbeiten ist Hintergrundarbeit. Ein Fehler in der Ablage oder im Netz
      // darf weder den Chat aufhalten noch den nächsten Anlass.
    } finally {
      laeuft = null
    }
  })()
  return laeuft
}

/**
 * Der Kontext, mit dem ein Auftrag neu gebaut wird.
 *
 * Die Zielgeräte löst `baueSteuerVersand` **beim Abarbeiten** frisch auf; hier
 * stehen nur die Angaben, die den Auftrag adressieren. Die Mitgliederliste ist
 * die vom Einreihen — `verschluesseleFuerGruppe` fragt sie ohnehin noch einmal
 * beim Server ab und rotiert den Schlüssel, wenn sie sich geändert hat. Fällt
 * die Abfrage aus, ist die Momentaufnahme die einzige Auskunft, die es gibt.
 */
function baueKontext(
  auftrag: SteuerAuftrag,
  konto: number,
  identitaet: E2eeIdentity,
): SteuerKontext {
  return {
    eigeneId: konto,
    mailbox: auftrag.mailbox,
    drKontext:
      auftrag.art === 'direkt' && auftrag.peerId
        ? { eigeneId: konto, peerId: auftrag.peerId }
        : null,
    gruppenKontext:
      auftrag.art === 'gruppe' && auftrag.groupId
        ? {
            groupId: auftrag.groupId,
            blindMailboxId: auftrag.mailbox,
            eigeneId: konto,
            mitglieder: auftrag.mitglieder?.length ? auftrag.mitglieder : [konto],
            istEigentuemer: Boolean(auftrag.istEigentuemer),
          }
        : null,
    identitaet,
  }
}

async function einLauf({ jetzt = () => Date.now() }: LaufOptionen): Promise<void> {
  const konto = angemeldetesKonto()
  if (konto === null) return
  const zeit = jetzt()
  /*
   * Einmal je Lauf, aus dem Geräteschlüssel dieses Geräts — nicht über
   * `resolveIdentity`: das würde bei jedem Anlass das Gerät erneut
   * veröffentlichen und dafür ins Netz greifen. Für die Kopie an die eigenen
   * übrigen Geräte zählt allein das Schlüsselpaar, und das liegt längst hier.
   * Ist der Messenger verschlossen, wirft der Zugriff; der Lauf endet, ohne
   * einen Versuch zu zählen.
   */
  const geraet = await eigenesGeraet()
  const identitaet: E2eeIdentity = {
    state: 'ready',
    sendPair: geraet.paar,
    decryptionKeys: [geraet.paar.privateKeyJwk],
  }
  if (angemeldetesKonto() !== konto) return

  for (const auftrag of await ladeSteuerAuftraege()) {
    if (angemeldetesKonto() !== konto) return
    // Was noch nicht dran ist, bleibt liegen; der nächste Anlass liest wieder.
    if (auftrag.naechsterVersuch > zeit) continue

    try {
      const kennung = await sendeSteuerpaket(
        baueKontext(auftrag, konto, identitaet),
        auftrag.felder,
        auftrag.id,
      )
      if (angemeldetesKonto() !== konto) return
      if (kennung) {
        await vergissSteuerAuftrag(auftrag.id)
        continue
      }
      // Kein Weg (keine Geräte, verschlossener Schlüssel): zählt als Versuch.
    } catch (fehler) {
      if (angemeldetesKonto() !== konto) return
      if (istEndgueltigAbgelehnt(fehler)) {
        await aufgeben(auftrag, 'abgelehnt')
        continue
      }
      // 401, 409, 429, 5xx und Netz bleiben offen.
    }

    const versuche = (Number.isInteger(auftrag.versuche) ? auftrag.versuche : 0) + 1
    if (versuche >= MAX_VERSUCHE) {
      await aufgeben(auftrag, 'aufgegeben')
      continue
    }
    await merkeSteuerFehlversuch(auftrag, zeit).catch(() => {})
  }
}

/**
 * Ein Auftrag, der nicht mehr kommt: zurücknehmen, austragen, melden.
 *
 * Die Reihenfolge ist bindend. Erst die lokale Wirkung zurücknehmen, dann den
 * Auftrag austragen, dann melden — wer die Meldung sieht, soll nicht auf einen
 * Zustand blicken, der noch die gescheiterte Wirkung zeigt.
 */
async function aufgeben(auftrag: SteuerAuftrag, grund: SteuerOutboxGrund): Promise<void> {
  await nimmWirkungZurueck(auftrag).catch(() => {})
  await vergissSteuerAuftrag(auftrag.id).catch(() => {})
  melder?.(auftrag, grund)
}
