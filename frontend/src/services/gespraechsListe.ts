/**
 * Mit wem man schreibt — seit Stufe 6b weiss das nur noch der Client.
 *
 * `direct_chats.user_a_id`, `user_b_id` und `initiated_by_user_id` sind
 * entfernt, und `GET /social/direct-chats` mit ihnen. Das war die Abfrage, die
 * auf „mit wem schreibt Konto 42?" antwortete: ein `SELECT` je Konto, und das
 * ganze soziale Netz lag offen, ohne eine einzige Nachricht zu entschlüsseln.
 * Für die meisten Fragen über einen Menschen braucht es den Inhalt ohnehin
 * nicht — es genügt zu wissen, dass er mit einer Suchtberatung schreibt, mit
 * einer Kanzlei oder mit einer Gewerkschaft.
 *
 * **Woher die Liste jetzt kommt.** Aus zwei Ereignissen, beide auf diesem
 * Gerät:
 *
 * 1. *Man schreibt jemanden an.* `POST /chat/start/{id}` nennt das Gegenüber
 *    im Aufruf selbst; was zurückkommt, wandert hierher.
 * 2. *Jemand schreibt einen an.* Die Nachricht kommt entschlüsselt aus einer
 *    Mailbox, und wer sie geschrieben hat, steht beglaubigt in der Nutzlast.
 *
 * Freunde, Teammitglieder und öffentliche Profile stehen weiterhin in der
 * Kontaktliste des Servers — die sind keine Auskunft darüber, wer mit wem
 * schreibt. Diese Ablage trägt genau die Lücke: das Gespräch mit jemandem, der
 * keins von beidem ist.
 *
 * **Versiegelt.** Dieselbe Linie wie bei den Gruppennamen ([[gruppenName.ts]]):
 * eine offene Zeile in `localStorage` wäre die Metadatenzeile, die gerade aus
 * der Datenbank verschwunden ist, nur auf einer anderen Platte. Ist der
 * Messenger verschlossen, gibt es hier keine Gespräche — das ist richtig so
 * und kein Fehler.
 */

import type { DirectChatItem } from '@/api/social'
import { entsiegleZeile, istOffen, versiegleZeile } from './lokaleVersiegelung'

const ABLAGE_SCHLUESSEL = 'msm:gespraeche'

/** Mehr als das passt nicht in eine Kontaktliste, die ein Mensch noch liest. */
const DECKEL = 500

/**
 * Bindet jede Zeile an ihr Gegenüber.
 *
 * Zwei Zeilen zu tauschen ergibt damit keine gültige Zeile mehr, sondern einen
 * Entschlüsselungsfehler — sonst liesse sich der Name eines Menschen unter der
 * Kennung eines anderen unterschieben, und man schriebe an den Falschen.
 */
function gespraechsAad(userId: number): string {
  return `msm-gespraech:${userId}`
}

/** Was über ein Gespräch zu wissen ist, ohne eine einzige Nachricht zu lesen. */
export interface Gespraech {
  userId: number
  /**
   * Wie der Mensch heisst — oder `null`, solange dieses Gerät es nicht weiss.
   *
   * Der Fall tritt genau einmal auf: ein Chatgeheimnis kommt an, bevor die
   * Kontaktliste geladen ist. Der Umschlag trägt eine Konto-Id und keinen
   * Namen, und das soll er auch nicht — ein Name im Steuerumschlag wäre ein
   * Feld, das jemand später zum Unterschieben missbraucht. Der Name kommt aus
   * der Kontaktliste, sobald sie da ist (`fuelleNamenNach`).
   *
   * Eine Zeile ohne Namen zählt trotzdem: über sie wird die Mailbox abonniert.
   * Angezeigt wird sie nicht — `gespraechsListe()` lässt sie aus.
   */
  username: string | null
  avatarUrl?: string | null
  /** Die Mailbox, über die es läuft. Leer, solange sie nur abgeleitet ist. */
  blindMailboxId?: string | null
  /** Zuletzt gebraucht. Ordnet die Liste, sonst nichts. */
  zuletzt: number
}

let stand: Map<number, Gespraech> | null = null

/** War beim letzten Laden zu? Siehe `standWarZu` in [[gruppenName.ts]]. */
let standWarZu = false

function leseRoh(): Record<string, unknown> | null {
  if (typeof localStorage === 'undefined') return null
  try {
    const roh = localStorage.getItem(ABLAGE_SCHLUESSEL)
    return roh ? (JSON.parse(roh) as Record<string, unknown>) : null
  } catch {
    return null
  }
}

function alsGespraech(userId: number, roh: unknown): Gespraech | null {
  if (!roh || typeof roh !== 'object') return null
  const r = roh as Record<string, unknown>
  const username = typeof r.username === 'string' ? r.username.trim().slice(0, 64) : ''
  const mailbox =
    typeof r.blindMailboxId === 'string' && /^[0-9a-f]{64}$/.test(r.blindMailboxId)
      ? r.blindMailboxId
      : null
  return {
    userId,
    username: username || null,
    avatarUrl: typeof r.avatarUrl === 'string' && r.avatarUrl ? r.avatarUrl.slice(0, 512) : null,
    blindMailboxId: mailbox,
    zuletzt: typeof r.zuletzt === 'number' && Number.isFinite(r.zuletzt) ? r.zuletzt : 0,
  }
}

/** Lädt den versiegelten Stand. Mehrfach aufrufbar; lädt nur einmal. */
export async function ladeGespraeche(): Promise<Map<number, Gespraech>> {
  if (stand && !(standWarZu && istOffen())) return stand
  const frisch = new Map<number, Gespraech>()
  const roh = leseRoh()
  if (roh && istOffen()) {
    for (const [kennung, zeile] of Object.entries(roh)) {
      const id = Number(kennung)
      if (!Number.isInteger(id) || id <= 0) continue
      try {
        const offen = await entsiegleZeile<Record<string, unknown>>(zeile as never, gespraechsAad(id))
        const eintrag = alsGespraech(id, offen)
        if (eintrag) frisch.set(id, eintrag)
      } catch {
        // Eine Zeile, die sich nicht öffnen lässt, nimmt die anderen nicht mit.
      }
    }
  }
  stand = frisch
  standWarZu = !istOffen()
  return stand
}

async function schreibeStand(): Promise<void> {
  if (!stand || typeof localStorage === 'undefined' || !istOffen()) return

  // Deckel: die ältesten fallen zuerst. Eine Ablage, die nie kleiner wird,
  // sprengt irgendwann `localStorage`, und dann fällt sie ganz aus.
  if (stand.size > DECKEL) {
    const nach_alter = [...stand.values()].sort((a, b) => a.zuletzt - b.zuletzt)
    for (const weg of nach_alter.slice(0, stand.size - DECKEL)) stand.delete(weg.userId)
  }

  const raus: Record<string, unknown> = {}
  for (const [id, eintrag] of stand) {
    try {
      // Kein Klarfeld: an einer Zeile, die nur aus Name, Bild und Kennung
      // besteht, gibt es nichts, was lesbar bleiben müsste.
      raus[String(id)] = await versiegleZeile(eintrag as never, [], gespraechsAad(id))
    } catch {
      // Versiegelt oder gar nicht.
    }
  }
  try {
    localStorage.setItem(ABLAGE_SCHLUESSEL, JSON.stringify(raus))
  } catch {
    // Voller Speicher. Die Kontaktliste zeigt dann nur Freunde und öffentliche
    // Profile — unvollständig, aber nicht falsch.
  }
}

/**
 * Merkt sich ein Gespräch.
 *
 * Mehrfach aufrufbar: ein späterer Aufruf frischt Name, Bild und Zeitstempel
 * auf. Fehlende Felder überschreiben nichts — wer nur die Mailbox nachträgt,
 * verliert den Namen nicht.
 */
export async function merkeGespraech(
  userId: number,
  angabe: { username?: string | null; avatarUrl?: string | null; blindMailboxId?: string | null },
): Promise<void> {
  if (!Number.isInteger(userId) || userId <= 0) return
  const karte = await ladeGespraeche()
  const bisher = karte.get(userId)
  const name = (angabe.username ?? bisher?.username ?? '').trim()
  const geprueft = alsGespraech(userId, {
    username: name,
    avatarUrl: angabe.avatarUrl ?? bisher?.avatarUrl ?? null,
    blindMailboxId: angabe.blindMailboxId ?? bisher?.blindMailboxId ?? null,
    zuletzt: Date.now(),
  })
  if (!geprueft) return
  karte.set(userId, geprueft)
  await schreibeStand()
}

/**
 * Alle Gegenstellen, auch die noch namenlosen.
 *
 * Für das Mailbox-Abo: abonniert wird nach Konto-Id, und ein Gespräch, dessen
 * Name noch fehlt, muss trotzdem gehört werden — sonst käme die erste Nachricht
 * eines neuen Gegenübers nirgends an.
 */
export async function gespraechsPartner(): Promise<number[]> {
  return [...(await ladeGespraeche()).keys()]
}

/**
 * Trägt Namen nach, die inzwischen bekannt sind.
 *
 * Ein Chatgeheimnis bringt eine Konto-Id und keinen Namen. Der kommt aus der
 * Kontaktliste — Freunde, Teammitglieder, öffentliche Profile —, und die ist
 * beim Eintreffen des Umschlags oft noch nicht geladen. Diese Stelle holt es
 * nach, sobald sie es ist.
 *
 * Gibt zurück, ob sich etwas geändert hat: der Aufrufer lädt dann die Liste
 * neu, statt bis zum nächsten Takt zu warten.
 */
export async function fuelleNamenNach(
  bekannt: readonly { userId: number; username: string; avatarUrl?: string | null }[],
): Promise<boolean> {
  const karte = await ladeGespraeche()
  let geaendert = false
  for (const person of bekannt) {
    const eintrag = karte.get(person.userId)
    if (!eintrag || eintrag.username) continue
    karte.set(person.userId, {
      ...eintrag,
      username: person.username.trim().slice(0, 64) || null,
      avatarUrl: eintrag.avatarUrl ?? person.avatarUrl ?? null,
    })
    geaendert = true
  }
  if (geaendert) await schreibeStand()
  return geaendert
}

/** Beim Löschen eines Chats. Was weg ist, soll auch hier weg sein. */
export async function vergissGespraech(userId: number): Promise<void> {
  const karte = await ladeGespraeche()
  if (!karte.delete(userId)) return
  await schreibeStand()
}

/** Beim Abmelden. Der nächste Mensch an diesem Gerät erbt keine Gespräche. */
export function leereGespraeche(): void {
  stand = null
  standWarZu = false
  try {
    localStorage?.removeItem(ABLAGE_SCHLUESSEL)
  } catch {
    /* nichts zu räumen */
  }
}

/**
 * Die Gesprächsliste in der Form, die der Server bis Stufe 6b lieferte.
 *
 * Damit bleibt `DirectChatItem` das eine Format, aus dem die Oberfläche liest,
 * und nichts weiter unten muss von der Änderung wissen — dieselbe Linie wie
 * `benenneGruppen` bei den Gruppennamen.
 *
 * Was fehlt und nicht zu ersetzen ist: `presence`, `is_friend` und
 * `is_blocked`. Die kamen aus Abfragen, die den Gesprächspartner kannten.
 * Anwesenheit gibt es für Freunde weiterhin über die Freundesliste; `is_friend`
 * füllt der Aufrufer aus derselben Quelle. Hier steht `false` statt einer
 * Behauptung, die dieses Gerät nicht prüfen kann.
 */
export async function gespraechsListe(): Promise<DirectChatItem[]> {
  const karte = await ladeGespraeche()
  return [...karte.values()]
    .filter((g): g is Gespraech & { username: string } => Boolean(g.username))
    .sort((a, b) => b.zuletzt - a.zuletzt)
    .map((g, i) => ({
      // Eine Ordnungszahl, keine Datenbankkennung: die Zeile, auf die sie
      // früher zeigte, gibt es serverseitig nicht mehr in dieser Form.
      id: -(i + 1),
      other_user_id: g.userId,
      other_username: g.username,
      other_avatar_url: g.avatarUrl ?? null,
      blind_mailbox_id: g.blindMailboxId ?? '',
      is_friend: false,
      is_blocked: false,
      other_privacy: 'friends' as const,
      presence: null,
      created_at: new Date(g.zuletzt).toISOString(),
      updated_at: new Date(g.zuletzt).toISOString(),
    }))
}
