/**
 * Ein Durchlauf durch ein Gespraech: lesen, entschluesseln, auswerten.
 *
 * Der Koerper stand bis 23.10.2026 in `useKonversation` — als `useCallback`
 * mit fuenf Referenzen auf React-Zustand. Fuer den Hintergrundlauf, der die
 * Eintraege des Geraetepostfachs den Gespraechen zustellt, war er damit nicht
 * benutzbar: ein Dienst kann keinen Hook aufrufen.
 *
 * Herausgezogen ist nur die Mechanik. Die Entscheidungen bleiben, wo sie
 * waren: der Hook rechnet die Kontexte (Mailbox, Gruppe, Direktchat) und
 * reicht sie herein; dieser Dienst liest, oeffnet und wertet aus. Alles, was
 * die Oberflaeche braucht, kommt als Rueckgabe oder als Rueckruf heraus.
 *
 * **Kein React.** Wer hier etwas importiert, das einen Hook braucht, hat die
 * Grenze verletzt.
 */

import { fetchAlleE2eeEnvelopes, type BlindEnvelopeItem } from '@/api/social'
import { decryptE2eeHybridWithKeyring, envelopePlaintextCache } from '@/services/e2eeCrypto'
import { QUITTUNG_PRAEFIX } from '@/services/auswertung/quittung'
import {
  eigeneWirkungenFuer,
  merkeEigeneWirkungen,
  merkeWirkungAusKlartext,
} from '@/services/eigeneWirkungen'
import {
  dmZiele,
  entschluesseleGruppenUmschlag,
  fordereDmGeheimnis,
  fordereGruppenSchluessel,
  gruppenZiele,
  holeGeraeteSteuerung,
  verarbeiteGruppenSteuerung,
  type GruppenKontext,
} from '@/services/gruppenSchluessel'
import {
  ladeUmschlagKlartexte,
  leseUmschlagKlartext,
  speichereUmschlagKlartext,
} from '@/services/messengerLocalStore'
import {
  drUrheber,
  liesDrUmschlag,
  verarbeiteBootstrap,
  verwirfDrSitzung,
  type DrKontext,
} from '@/services/ratchetSitzung'

import type { Lesung } from '@/hooks/useKonversation'

const HYBRID_PREFIX = 'sv-e2ee-hybrid-v1:'

/** Was der Durchlauf von aussen braucht. */
export interface GespraechsLauf {
  /** Die Mailbox dieses Gespraechs. */
  mailbox: string
  eigeneId: number
  /** Beim Direktchat die Gegenstelle, sonst `null`. */
  drKontext: DrKontext | null
  /** In der Gruppe der Schluesselkontext, sonst `null`. */
  gruppenKontext: GruppenKontext | null
  /** Die privaten Schluessel des Geraets, aus der Identitaet. */
  decryptionKeys: string[]
  /**
   * Nur die uebergebenen Umschlaege verarbeiten, nichts nachladen.
   *
   * Fuer den Hintergrundlauf: der liest das Geraetepostfach und reicht die
   * Eintraege herein. Die Mailboxen und die Geraete-Mailbox bleiben dann
   * unberuehrt — der offene Chat liest sie ohnehin.
   */
  nurPostfach?: boolean
}

/** Was der Durchlauf zurueckmeldet oder abfragt. */
export interface LaufOptionen {
  /** Bis wohin jede Mailbox gelesen ist. */
  lesestand: ReadonlyMap<string, number>
  /** Ist das Gespraech noch dasselbe? Ein Wechsel bricht den Lauf ab. */
  istAktuell: () => boolean
  /** Welche Mailboxen dieser Lauf liest — fuer das Abo im Echtzeitstrom. */
  meldeMailboxen?: (kennungen: string[]) => void
  meldeSitzungsbruch: (geraet: string) => void
  meldeAufbauAbgelehnt: (geraet: string) => void
}

export interface LaufErgebnis {
  lesungen: Lesung[]
  /** Der neue Stand je Mailbox. Erst der Uebernehmende schreibt ihn. */
  stand: Map<string, number>
  /**
   * Umschlaege, die dieser Lauf **nicht** abschliessend behandelt hat.
   *
   * Sie duerfen nicht quittiert werden: keine Sitzung, Aufbau noch offen,
   * fehlender Gruppenschluessel, Ablagefehler. Der naechste Lauf sieht sie
   * wieder — genau dafuer bleibt der Serverauftrag stehen.
   */
  offen: Set<number>
}

/**
 * Liest die Mailboxen dieses Gespraechs und liefert die Lesungen.
 *
 * `null` heisst: der Lauf wurde verworfen (Gespraech gewechselt). Dann bleibt
 * der Lesestand stehen und der naechste Abruf holt dieselben Umschlaege noch
 * einmal.
 */
/**
 * Ist dieser Umschlag eine Nebenkopie, also nichts, was je im Verlauf stehen soll?
 *
 * Die Unterscheidung muss an der `client_uuid` hängen, weil ein
 * `sv-e2ee-hybrid-v1:` im Gegensatz zum Ratchet-Umschlag **keinen Kopf mit dem
 * Zielgerät** trägt: von aussen sieht eine Quittung für ein fremdes Gerät
 * genauso aus wie eine Nachricht, die für mich bestimmt war und die ich nicht
 * öffnen kann. Beides still zu verwerfen verstecke echte Brüche; beides
 * anzuzeigen füllt den Verlauf mit Rauschen, je Gerät und je Quittung eine
 * Zeile.
 *
 * Die Marken gibt es schon, sie waren nur nicht durchgezogen: `#` trennt bei
 * jeder Auffächerung die Gerätekennung ab (`…#<geraet>`, `…#i<geraet>` beim
 * Sitzungsaufbau, `…#<n>` bei der Gruppenschlüsselzustellung), und eine
 * Quittung trägt seit 09/2026 `deliv-`. Die drei Wortmarken darunter stammen
 * aus älteren Fassungen und bleiben, damit gespeicherte Umschläge von damals
 * weiter still bleiben.
 */
export function istNebenkopie(clientUuid: string | null | undefined): boolean {
  if (!clientUuid) return false
  if (clientUuid.startsWith(QUITTUNG_PRAEFIX)) return true
  if (clientUuid.includes('#')) return true
  const klein = clientUuid.toLowerCase()
  return klein.startsWith('ctrl-') || klein.includes('control') || klein.includes('receipt')
}

export async function verarbeiteGespraech(
  kontext: GespraechsLauf,
  optionen: LaufOptionen,
  zusatz?: BlindEnvelopeItem[],
): Promise<LaufErgebnis | null> {
  const { mailbox: mid, eigeneId, drKontext, gruppenKontext } = kontext
  const schluessel = kontext.decryptionKeys
  if (!mid || !eigeneId) return null

  // Zuerst die eigene Geräte-Mailbox: dort liegen die Gruppenschlüssel und
  // die Chatgeheimnisse. Ein Gerät, das sie noch nicht hat, bekäme sie sonst
  // nie — und stünde vor einer Mailbox voller „Verschlüsselte Nachricht"
  // oder, beim Direktchat, vor einer leeren. Vor dem Lesen, nicht danach:
  // sonst wäre der erste Durchlauf immer der blinde.
  // **Wirkungen aus den eigenen Zweitgeräten.** Sie liegen in derselben
  // Geräte-Mailbox, sind aber keine Schlüsselzustellung: eine Quittung, eine
  // Bearbeitung, eine Löschung oder eine Reaktion. Eingesammelt und unten an
  // die Lesung gehängt, damit `werteUmschlaegeAus` sie mit denselben
  // Absenderprüfungen anwendet wie die Pakete der Gegenseite. Ohne diesen
  // Weg blieb ein Häkchen, ein Edit oder eine Löschung auf dem Gerät, an dem
  // sie passiert ist.
  const eigeneSteuerung: Lesung[] = []
  /**
   * Was dieser Durchlauf schon eingesammelt hat, geht bei einem Abbruch nicht
   * verloren.
   *
   * `holeGeraeteSteuerung` schiebt seinen Cursor weiter, sobald es die Pakete
   * gelesen hat. Wechselt der Mensch danach das Gespräch, endet dieser Lauf
   * mit `null` — und ohne diese Ablage wäre eine Bearbeitung oder Löschung aus
   * der App weg, obwohl ihre Quelle verbraucht ist. Abgelegt wird sie unter
   * ihrer eigenen Mailbox; der nächste Durchlauf liest sie über
   * `eigeneWirkungenFuer` wieder ein und wendet sie mit denselben Prüfungen
   * an.
   */
  const behalteEigeneSteuerung = () => {
    const offen = eigeneSteuerung.flatMap((lesung) =>
      lesung.art === 'klartext'
        ? [{ mailbox: mid, payload: lesung.text, zeitpunkt: lesung.env.created_at }]
        : [],
    )
    eigeneSteuerung.length = 0
    if (offen.length === 0) return
    void merkeEigeneWirkungen(offen).catch(() => {})
  }
  if (!kontext.nurPostfach) await holeGeraeteSteuerung(
    eigeneId,
    (umschlag) => decryptE2eeHybridWithKeyring(umschlag, schluessel),
    (klartext, env) => {
      let roh: Record<string, unknown>
      try {
        const geparst = JSON.parse(klartext)
        if (!geparst || typeof geparst !== 'object') return
        roh = geparst as Record<string, unknown>
      } catch {
        return
      }
      // Nur, was zu **diesem** Gespräch gehört. Ein Zweitgerät kann viele
      // Chats offen gehabt haben; ohne die Marke landete eine Löschung aus
      // einem anderen Chat im falschen Verlauf. Für ein noch nicht
      // geöffnetes Gespräch wird sie aufbewahrt, statt mit dem Cursor
      // verloren zu gehen.
      if (roh.mailbox !== mid) {
        // Derselbe Helfer wie beim Start des Messengers: was zu einem anderen
        // Gespräch gehört, wird für dessen Öffnen aufbewahrt. Der Schreibweg
        // ist asynchron (das Siegel rechnet) und darf den Leseumlauf nicht
        // aufhalten; scheitert er — etwa im verschlossenen Zustand —, bleibt
        // die Zeile liegen, statt den Durchlauf zu kippen.
        void merkeWirkungAusKlartext(klartext, env)
        return
      }
      eigeneSteuerung.push({
        art: 'klartext',
        // Eigene Kennung im negativen Bereich: die Nummern der Geräte-Mailbox
        // und der Chat-Mailbox laufen unabhängig, positive kollidierten.
        env: {
          id: -env.id,
          blind_mailbox_id: mid,
          ciphertext_envelope: '',
          client_uuid: `own-${env.id}`,
          created_at: env.created_at,
        },
        text: klartext,
        vonKonto: eigeneId,
      })
    },
  ).catch(() => 0)
  if (!optionen.istAktuell()) {
    behalteEigeneSteuerung()
    return null
  }

  /**
   * Aus welchen Mailboxen dieser Durchlauf liest.
   *
   * Zwei, solange der Umzug läuft: die Kennung aus dem Geheimnis und die
   * alte, abgeleitete. Die Gegenseite bekommt das Geheimnis nicht im selben
   * Augenblick — es reist als Steuerumschlag in ihre Geräte-Mailbox —, und
   * wer es noch nicht hat, sendet weiter in die alte. Läse jeder nur seine
   * eigene, verlöre das Gespräch lautlos die Hälfte seiner Nachrichten.
   *
   * Nach `holeGeraeteSteuerung`, nicht davor: genau dort kommt das
   * Geheimnis an, und ein Durchlauf, der vorher fragt, liest die neue
   * Mailbox erst beim nächsten Mal.
   */
  let lesen: string[]
  if (kontext.nurPostfach) {
    lesen = []
  } else if (gruppenKontext) {
    lesen = (await gruppenZiele(gruppenKontext.groupId, mid)).lesen
  } else if (drKontext) {
    const ziele = await dmZiele(drKontext.eigeneId, drKontext.peerId, mid)
    lesen = ziele.lesen
    if (!ziele.nachweis) {
      // Kein Geheimnis: die neue Mailbox ist diesem Gerät unbekannt. Die
      // eigenen Geschwister haben es vielleicht — fragen kostet einen
      // Umschlag im eigenen Postfach, und die Antwort kommt beim nächsten
      // Durchlauf an. Gedrosselt und ohne Warten.
      void fordereDmGeheimnis(drKontext.eigeneId, drKontext.peerId).catch(() => {})
    }
  } else {
    lesen = [mid]
  }
  if (!kontext.nurPostfach) optionen.meldeMailboxen?.([...new Set([mid, ...lesen])].sort())
  if (!optionen.istAktuell()) {
    behalteEigeneSteuerung()
    return null
  }

  const umschlaege: BlindEnvelopeItem[] = kontext.nurPostfach
    ? (zusatz ?? [])
    : (
        await Promise.all(
          lesen.map((kennung) =>
            fetchAlleE2eeEnvelopes(kennung, optionen.lesestand.get(kennung) ?? 0).catch(
              () => [] as Awaited<ReturnType<typeof fetchAlleE2eeEnvelopes>>,
            ),
          ),
        )
      )
        .flat()
        // Beide Mailboxen zählen für sich, ihre Nummern laufen also durcheinander.
        // Sortiert wird nach Zeit, und bei Gleichstand nach Nummer — sonst
        // sprängen die Nachrichten zweier Mailboxen im Verlauf hin und her.
        .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id)
  if (!optionen.istAktuell()) {
    behalteEigeneSteuerung()
    return null
  }
  // Was hier steht, ist schon einmal geöffnet worden. Unverzichtbar, nicht
  // bloß schnell: ein Ratchet-Nachrichtenschlüssel ist nach dem ersten Öffnen
  // verbraucht, ein zweiter Versuch am selben Umschlag muss scheitern.
  const bekannt = drKontext ? await ladeUmschlagKlartexte(mid) : new Map<number, string>()

  const gebrochene: { vonKonto: number; vonGeraet: string }[] = []
  /**
   * Geräte, deren Sitzungsaufbau in diesem Durchlauf offen blieb, als
   * `konto:gerät`.
   *
   * Ihre Nachrichten werden in diesem Durchlauf nicht geöffnet. Liefen sie
   * gegen eine Sitzung, die es noch nicht gibt, wären sie als Bruch gewertet
   * und verloren; so kommen sie mit dem Aufbau im nächsten Durchlauf.
   */
  const aufbauOffen = new Set<string>()
  /**
   * Geräte, von denen ein abgewiesener Sitzungsaufbau im Fenster liegt, als
   * `konto:gerät`.
   *
   * Was von ihnen kommt und sich gegen die bestehende Sitzung nicht öffnen
   * lässt, gehört zu diesem Aufbau: still, und keine Sitzung verworfen.
   * Sonst kippte die Nachricht hinter einer Fälschung die echte Sitzung
   * doch noch.
   */
  const aufbauAbgewiesen = new Set<string>()
  /**
   * Die jüngste Nachricht, für die der Gruppenschlüssel fehlt.
   *
   * Nur sie löst eine Nachforderung aus. Ältere unlesbare Nachrichten stammen
   * aus der Zeit vor dem Beitritt oder aus einer Generation, die niemand mehr
   * herausgibt — danach zu fragen brächte nichts und erzeugte bei jedem
   * Ladevorgang neue Umschläge.
   */
  const fehlenderSchluessel = { envId: 0, keyId: '' }

  /**
   * Umschläge, die dieser Durchlauf **nicht** abschließend behandeln konnte.
   *
   * Sie müssen beim nächsten Abruf wieder mitkommen, und deshalb darf der
   * Lesestand nicht über sie hinwegwandern. Das ist keine Vorsicht, sondern
   * die Bedingung dafür, dass ein Cursor überhaupt erlaubt ist:
   *
   * * `keine-sitzung` / `zurueckgestellt` — der Sitzungsaufbau liegt vor
   *   ihnen in derselben oder der nächsten Runde; ohne ihn ist der
   *   Ratchet-Schlüssel nicht zu finden.
   * * `kein-schluessel` (Gruppe) — die Zustellung ist angefordert und kommt
   *   als Umschlag zurück.
   * * `fehler` — die lokale Ablage streikte; „einmal nicht geklappt" ist
   *   kein „nie mehr lesen".
   *
   * Endgültig bleibt dagegen, was aus einem anderen Grund nicht aufgeht: eine
   * Kopie für ein fremdes Gerät, ein Umschlag aus einer geschlossenen
   * Schlüsselgeneration, eine abgewiesene Fälschung. Diese erneut zu lesen
   * änderte nichts — sie hielten den Stand sonst für immer fest, und jeder
   * Abruf läse wieder den ganzen Verlauf.
   */
  const nochEinmal = new Set<number>()

  const einUmschlag = async (env: BlindEnvelopeItem): Promise<Lesung> => {
    const gespeichert = bekannt.get(env.id)
    if (gespeichert !== undefined) {
      // Abgelegt wird nur, was sich über den Ratchet öffnen liess, und dessen
      // Kopf nennt den Absender. Der Zwischenspeicher weiss es nach einem
      // Neuladen nicht mehr — ohne Absender griffe die Downgrade-Schranke
      // im Messenger nicht.
      const kopf = drUrheber(env.ciphertext_envelope)
      const zwischen = envelopePlaintextCache.get(env.id)
      return {
        art: 'klartext',
        env,
        text: gespeichert,
        vonKonto: kopf?.vonKonto ?? zwischen?.vonKonto,
        vonGeraet: kopf?.vonGeraet ?? zwischen?.vonGeraet,
      }
    }
    const zwischen = envelopePlaintextCache.get(env.id)
    if (zwischen?.ok) {
      return {
        art: 'klartext',
        env,
        text: zwischen.plain,
        vonKonto: zwischen.vonKonto,
        vonGeraet: zwischen.vonGeraet,
      }
    }

    if (vorabAufbau.has(env.id)) return { art: 'still', env }
    const vorab = vorabKlartext.get(env.id)
    if (vorab !== undefined) return { art: 'klartext', env, text: vorab }

    try {
      if (gruppenKontext) {
        if (env.ciphertext_envelope.startsWith(HYBRID_PREFIX)) {
          // In einer Gruppenmailbox ist ein Hybridumschlag immer ein
          // Steuerumschlag: eine Schlüsselzustellung oder eine Nachforderung.
          // Die Kopien für andere Geräte lassen sich nicht öffnen — beides
          // darf nie als Nachricht im Verlauf landen.
          try {
            const klartext = await decryptE2eeHybridWithKeyring(env.ciphertext_envelope, schluessel)
            await verarbeiteGruppenSteuerung(gruppenKontext, klartext)
          } catch {
            // Nicht für dieses Gerät bestimmt.
          }
          return { art: 'still', env }
        }

        const lesung = await entschluesseleGruppenUmschlag(
          gruppenKontext.groupId,
          env.ciphertext_envelope,
        )
        if (lesung.art === 'klartext') {
          envelopePlaintextCache.set(env.id, { plain: lesung.text, ok: true })
          return { art: 'klartext', env, text: lesung.text }
        }
        if (lesung.art === 'kein-schluessel') {
          nochEinmal.add(env.id)
          if (env.id > fehlenderSchluessel.envId) {
            fehlenderSchluessel.envId = env.id
            fehlenderSchluessel.keyId = lesung.keyId
          }
        }
        // 'unbekannt' — Altbestand aus der Zeit, als sich der Gruppenschlüssel
        // aus der Gruppenkennung ableiten ließ. Der Weg dorthin ist
        // geschlossen, auch lesend.
        // 'bruch' — der Schlüssel liegt vor, der Tag stimmt nicht.
        return { art: 'unlesbar', env }
      }

      if (drKontext) {
        if (env.ciphertext_envelope.startsWith(HYBRID_PREFIX)) {
          // In einer DM-Mailbox liegen die Kopien aller Zielgeräte
          // nebeneinander: je Nachricht eine, dazu Sitzungsaufbauten und
          // Quittungen. Die meisten kann dieses Gerät nicht öffnen, und das
          // ist der Normalfall, kein Fehler. Bis 09/2026 fiel jede davon in
          // den äusseren `catch` und wurde zu einer Zeile „Verschlüsselte
          // Nachricht" — am laufenden System standen nach einer Stunde 99
          // unechte Nachrichten im Verlauf.
          //
          // Still wird aber nur, was sich als Nebenkopie ausweist. Ein
          // Umschlag mit gewöhnlicher Kennung war für dieses Gerät gedacht;
          // dass er sich nicht öffnen lässt, gehört dann angezeigt und nicht
          // verschwiegen.
          let klartext: string
          try {
            klartext = await decryptE2eeHybridWithKeyring(env.ciphertext_envelope, schluessel)
          } catch (fehler) {
            if (istNebenkopie(env.client_uuid)) return { art: 'still', env }
            throw fehler
          }
          const aufbau = await verarbeiteBootstrap(drKontext, klartext)
          if (aufbau.istAufbau) {
            if (aufbau.offen && aufbau.vonKonto && aufbau.vonGeraet) {
              aufbauOffen.add(`${aufbau.vonKonto}:${aufbau.vonGeraet}`)
            }
            if (aufbau.abgewiesen && aufbau.vonKonto && aufbau.vonGeraet) {
              aufbauAbgewiesen.add(`${aufbau.vonKonto}:${aufbau.vonGeraet}`)
            }
            if (aufbau.abgelehnt && aufbau.vonGeraet) optionen.meldeAufbauAbgelehnt(aufbau.vonGeraet)
            if (aufbau.ersetzt && aufbau.vonGeraet) optionen.meldeSitzungsbruch(aufbau.vonGeraet)
            return { art: 'still', env }
          }
          envelopePlaintextCache.set(env.id, { plain: klartext, ok: true })
          return { art: 'klartext', env, text: klartext }
        }

        const lesung = await liesDrUmschlag(drKontext, env.ciphertext_envelope, {
          // `bekannt` oben ist eine Momentaufnahme vom Beginn dieses
          // Durchlaufs. Diese Abfrage läuft im Sitzungsschloss und sieht
          // deshalb auch, was ein überlappender Durchlauf oder ein zweiter
          // Tab inzwischen geöffnet hat.
          lies: () => leseUmschlagKlartext(mid, env.id),
          // Erst der Klartext auf die Platte, dann der fortgeschriebene
          // Zustand. Schlägt das fehl, scheitert der ganze Schritt und der
          // Umschlag bleibt beim nächsten Mal lesbar.
          lege: (text) => speichereUmschlagKlartext(mid, env.id, text),
        }, {
          zurueckstellen: (vonKonto, vonGeraet) => aufbauOffen.has(`${vonKonto}:${vonGeraet}`),
          schonen: (vonKonto, vonGeraet) => aufbauAbgewiesen.has(`${vonKonto}:${vonGeraet}`),
        })
        if (lesung.art === 'klartext') {
          envelopePlaintextCache.set(env.id, {
            plain: lesung.text,
            ok: true,
            vonKonto: lesung.vonKonto,
            vonGeraet: lesung.vonGeraet,
          })
          return {
            art: 'klartext',
            env,
            text: lesung.text,
            vonKonto: lesung.vonKonto,
            vonGeraet: lesung.vonGeraet,
          }
        }
        if (lesung.art === 'bruch') {
          gebrochene.push({ vonKonto: lesung.vonKonto, vonGeraet: lesung.vonGeraet })
          return { art: 'still', env }
        }
        if (lesung.art === 'keine-sitzung') {
          /*
           * Noch nicht aufgebaut — kein Bruch, keine Marke, keine verlorene
           * Nachricht. Sie bleibt ungeöffnet und kommt im nächsten Durchlauf
           * wieder, dann mit dem Aufbau davor. Deshalb bleibt der Lesestand
           * hinter ihr (`nochEinmal`), sonst wäre sie mit dem Cursor weg.
           */
          nochEinmal.add(env.id)
          return { art: 'still', env }
        }
        if (
          lesung.art === 'unbekannt' ||
          lesung.art === 'fehler' ||
          lesung.art === 'beurteilt'
        ) {
          // 'unbekannt' — Altbestand aus der Zeit der ableitbaren
          // Kanalschlüssel. 'fehler' — die lokale Ablage streikte; die Sitzung
          // bleibt unangetastet, der nächste Durchlauf versucht es erneut.
          // 'beurteilt' — schon einmal als Bruch gewertet; sichtbar bleibt er,
          // aber ein zweites Mal kostet er keine Sitzung mehr.
          // `fehler` ist die Ausnahme: die lokale Ablage hat gestreikt, und
          // der nächste Durchlauf kann es schaffen.
          if (lesung.art === 'fehler') nochEinmal.add(env.id)
          return { art: 'unlesbar', env }
        }
        // 'eigen' — der Absender kann seine eigene Ratchet-Nachricht nicht
        // öffnen, das ist der Sinn der Sache; sein Gesprächsanteil kommt aus
        // dem lokalen Speicher. 'fremd' — eine der aufgefächerten Kopien für
        // ein anderes Gerät. 'zurueckgestellt' — der Aufbau davor ist noch
        // offen; ungeöffnet und nicht zwischengespeichert, kommt der Umschlag
        // im nächsten Durchlauf wieder. 'abgewiesen' — die Nachricht hinter
        // einem gefälschten Aufbau; gemeldet ist der Aufbau schon.
        if (lesung.art === 'zurueckgestellt') nochEinmal.add(env.id)
        return { art: 'still', env }
      }

      return { art: 'unlesbar', env }
    } catch {
      return { art: 'unlesbar', env }
    }
  }

  /**
   * Erst **alle** Sitzungsaufbauten, dann alles andere.
   *
   * Bis 10/2026 lief beides in einer Schleife nach Zeit und Kennung. Kam die
   * Nachricht vor ihrem Aufbau — umgekehrte Zeitfolge, zwei Geräte, ein
   * Fenster voller Umschläge —, lief sie gegen eine Sitzung, die es noch nicht
   * gab: sie wurde zum Bruch mit Marke und war dauerhaft verloren, obwohl der
   * Aufbau im selben Fenster lag. Beim ersten Kontakt meldete der Verlauf
   * zusätzlich „Die Sicherheitssitzung wurde neu aufgebaut", ohne dass je
   * eine bestand.
   *
   * Der Vorlauf entschlüsselt die Hybridumschläge einmal, verarbeitet die
   * Aufbauten und merkt sich den Klartext der Nachrichten. Die Hauptschleife
   * findet beides vor und entschlüsselt nicht ein zweites Mal — ein
   * Ratchet-Nachrichtenschlüssel wäre beim zweiten Versuch verbraucht.
   */
  const vorabKlartext = new Map<number, string>()
  const vorabAufbau = new Set<number>()
  if (drKontext) {
    for (const env of umschlaege) {
      if (!env.ciphertext_envelope.startsWith(HYBRID_PREFIX)) continue
      const gespeichert = bekannt.get(env.id)
      if (gespeichert !== undefined) {
        vorabKlartext.set(env.id, gespeichert)
        continue
      }
      const frueher = envelopePlaintextCache.get(env.id)
      if (frueher?.ok) {
        vorabKlartext.set(env.id, frueher.plain)
        continue
      }
      let klartext: string
      try {
        klartext = await decryptE2eeHybridWithKeyring(env.ciphertext_envelope, schluessel)
      } catch {
        // Für ein anderes Gerät dieses Kontos. Die Hauptschleife entscheidet
        // wie bisher, ob das still bleibt oder angezeigt gehört.
        continue
      }
      const aufbau = await verarbeiteBootstrap(drKontext, klartext)
      if (aufbau.istAufbau) {
        if (aufbau.offen && aufbau.vonKonto && aufbau.vonGeraet) {
          aufbauOffen.add(`${aufbau.vonKonto}:${aufbau.vonGeraet}`)
        }
        if (aufbau.abgewiesen && aufbau.vonKonto && aufbau.vonGeraet) {
          aufbauAbgewiesen.add(`${aufbau.vonKonto}:${aufbau.vonGeraet}`)
        }
        if (aufbau.abgelehnt && aufbau.vonGeraet) optionen.meldeAufbauAbgelehnt(aufbau.vonGeraet)
        if (aufbau.ersetzt && aufbau.vonGeraet) optionen.meldeSitzungsbruch(aufbau.vonGeraet)
        vorabAufbau.add(env.id)
        continue
      }
      envelopePlaintextCache.set(env.id, { plain: klartext, ok: true })
      vorabKlartext.set(env.id, klartext)
    }
  }
  if (!optionen.istAktuell()) {
    behalteEigeneSteuerung()
    return null
  }

  /**
   * Der Reihe nach, nicht nebenläufig.
   *
   * Nicht aus Vorsicht, sondern weil die Reihenfolge trägt: im Direktchat muss
   * ein Sitzungsaufbau verarbeitet sein, bevor die Nachricht ankommt, für die
   * er gilt; in der Gruppe muss die Schlüsselzustellung vor der ersten
   * Nachricht dieser Generation liegen. Nebenläufig gelesen käme die Nachricht
   * mit einer Chance von fünfzig Prozent zuerst.
   */
  const gelesen: Lesung[] = []
  for (const env of umschlaege) gelesen.push(await einUmschlag(env))
  // Die Wirkungen der eigenen Zweitgeräte kommen **fertig entschlüsselt**
  // dazu. Sie dürfen nicht durch `einUmschlag`: ihr Umschlag liegt in der
  // Geräte-Mailbox und trägt keinen Chat-Chiffretext. Als `klartext` mit
  // eigener, negativer Nummer durchlaufen sie dieselben Prüfungen wie ein
  // Paket der Gegenseite — Signatur, `actor_id`, `mailbox`-Bindung.
  gelesen.push(...eigeneSteuerung)
  // Dazu, was ein anderes Gerät geschickt hat, während dieser Chat zu war.
  ;(await eigeneWirkungenFuer(mid)).forEach((wirkung, i) => {
    gelesen.push({
      art: 'klartext',
      env: {
        id: -1_000_000 - i,
        blind_mailbox_id: mid,
        ciphertext_envelope: '',
        client_uuid: `own-spaet-${i}`,
        created_at: wirkung.zeitpunkt,
      },
      text: wirkung.payload,
      vonKonto: eigeneId,
    })
  })

  if (!optionen.istAktuell()) {
    behalteEigeneSteuerung()
    return null
  }

  if (gruppenKontext && fehlenderSchluessel.keyId) {
    // Gedrosselt und ohne Warten: die Antwort kommt als Umschlag im nächsten
    // Durchlauf, nicht als Rückgabewert.
    void fordereGruppenSchluessel(gruppenKontext, fehlenderSchluessel.keyId).catch(() => {})
  }

  // Gebrochene Sitzungen wegwerfen und sichtbar melden. Der nächste
  // Sendevorgang baut von selbst eine frische auf. Eine still neu aufgebaute
  // Sicherheitssitzung ist genau das, was ein Angreifer sich wünscht.
  for (const bruch of gebrochene) {
    await verwirfDrSitzung(bruch.vonKonto, bruch.vonGeraet).catch(() => {})
    optionen.meldeSitzungsbruch(bruch.vonGeraet)
  }

  /**
   * Den Lesestand fortschreiben — nur bis vor den ältesten offenen Umschlag.
   *
   * Ohne offene bleibt es die höchste gelesene Kennung. Umschlagkennungen
   * sind der Primärschlüssel und wachsen mit der Zeit, deshalb kann ein
   * späterer Umschlag nie unter einem einmal gesehenen Stand liegen — ein
   * Sprung über etwas hinweg ist ausgeschlossen.
   *
   * Nur wenn das Gespräch dasselbe geblieben ist: sonst wurde `gelesen`
   * verworfen (`return null`), und derselbe Stand würde beim nächsten Öffnen
   * Umschläge überspringen, die nie auf dem Bildschirm angekommen sind.
   * Ebenso ohne Kontext — steht die Mitgliederliste einer Gruppe noch aus,
   * ist jeder Umschlag oben „unlesbar" geworden, und das sagt nichts über
   * ihn aus.
   */
  if (!kontext.nurPostfach && (gruppenKontext || drKontext)) {
    const hoechste = new Map<string, number>()
    const offen = new Map<string, number>()
    for (const env of umschlaege) {
      const kennung = env.blind_mailbox_id
      if (!kennung) continue
      hoechste.set(kennung, Math.max(hoechste.get(kennung) ?? 0, env.id))
      if (nochEinmal.has(env.id)) {
        offen.set(kennung, Math.min(offen.get(kennung) ?? env.id, env.id))
      }
    }
    const stand = new Map<string, number>()
    for (const [kennung, bis] of hoechste) {
      const warten = offen.get(kennung)
      stand.set(kennung, warten === undefined ? bis : warten - 1)
    }
    // **Nicht** schreiben. Erst wer diese Lesung übernimmt, rückt den Stand
    // vor (`uebernimmLesestand`) — ein verworfener Durchlauf darf keine
    // Umschläge mitnehmen.
    return { lesungen: gelesen, stand, offen: new Set(nochEinmal) }
  }

  return { lesungen: gelesen, stand: new Map<string, number>(), offen: new Set(nochEinmal) }
}
