/**
 * Ein Gespräch: welche Mailbox, welches Verfahren, was ein Umschlag bedeutet.
 *
 * Bis 09/2026 stand das alles in `Messenger.tsx` — in einer Renderfunktion mit
 * 5.800 Zeilen, zwischen Anhangvorschau und Anrufknopf. Die Projektregel dazu
 * ist eindeutig: *„UI-Komponenten dürfen keine Crypto-Policy definieren."*
 * Praktisch schwerer wiegt, dass der Sende- und Lesepfad so nicht prüfbar war:
 * um eine Entschlüsselungsentscheidung zu testen, musste man die ganze Seite
 * rendern.
 *
 * **Was hier liegt** ist die Entscheidung: welche Mailbox gehört zu diesem
 * Gespräch, mit welchem Verfahren wird versiegelt, und was ist ein Umschlag,
 * den man gerade gelesen hat — Nachricht, Steuerpaket, fremde Kopie oder
 * Sitzungsbruch.
 *
 * **Was dort bleibt** ist die Darstellung: Häkchen, Quittungen, Bearbeiten und
 * Löschen, die optimistische Zeile, die Warteschlange ohne Netz. Das ist
 * Zustandsführung einer Oberfläche und hat mit Krypto nichts zu tun.
 *
 * Die Trennlinie verläuft bewusst bei „Umschlag ↔ Klartext". Alles, was unter
 * dieser Linie liegt, ist Verfahren; alles darüber ist Anzeige.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import i18n from '@/i18n'

import { fetchAlleE2eeEnvelopes, type BlindEnvelopeItem } from '@/api/social'
import {
  decryptE2eeHybridWithKeyring,
  deriveBlindMailboxId,
  deriveGroupBlindMailboxId,
  deriveUserDeviceMailboxId,
  encryptE2eeHybrid,
  envelopePlaintextCache,
  getCachedBlindMailboxId,
  getCachedGroupBlindMailboxId,
} from '@/services/e2eeCrypto'
import { QUITTUNG_PRAEFIX } from '@/services/deliveryReceiptService'
import {
  eigeneWirkungenFuer,
  merkeEigeneWirkungen,
  merkeWirkungAusKlartext,
} from '@/services/eigeneWirkungen'
import { eigenesGeraet, verlangeGeraeteVon } from '@/services/e2eeGeraet'
import type { E2eeIdentity } from '@/services/e2eeIdentity'
import {
  entschluesseleGruppenUmschlag,
  fordereDmGeheimnis,
  fordereGruppenSchluessel,
  dmZiele,
  gruppenZiele,
  holeGeraeteSteuerung,
  verarbeiteGruppenSteuerung,
  verschluesseleFuerGruppe,
  type GruppenKontext,
} from '@/services/gruppenSchluessel'
import { abonniereMailbox } from '@/services/mailboxAbo'
import { mailboxNachweis } from '@/services/mailboxNachweis'
import {
  ladeUmschlagKlartexte,
  leseUmschlagKlartext,
  speichereUmschlagKlartext,
} from '@/services/messengerLocalStore'
import {
  baueZustellungen,
  drUrheber,
  liesDrUmschlag,
  verarbeiteBootstrap,
  verwirfDrSitzung,
  type DrKontext,
} from '@/services/ratchetSitzung'

const HYBRID_PREFIX = 'sv-e2ee-hybrid-v1:'

/** Mit wem oder was gesprochen wird. */
export type GespraechsZiel =
  | { art: 'direkt'; peerId: number }
  | {
      art: 'gruppe'
      groupId: number
      mitglieder: readonly number[]
      /** Nur der Eigentümer darf das Gruppengeheimnis erzeugen. Siehe `GruppenKontext`. */
      istEigentuemer: boolean
    }
  | { art: 'keins' }

/**
 * Was ein Umschlag für den Verlauf bedeutet.
 *
 * `still` ist der wichtigste Fall und der am leichtesten zu übersehende: eine
 * Kopie für ein anderes Gerät, die eigene Ratchet-Nachricht, eine
 * Schlüsselzustellung. Nichts davon ist ein Fehler, und nichts davon darf als
 * „Verschlüsselte Nachricht" im Gespräch stehen — sonst ist der Verlauf bei
 * zwei Geräten je Seite zur Hälfte Rauschen.
 */
export type Lesung =
  | { art: 'klartext'; env: BlindEnvelopeItem; text: string; vonKonto?: number; vonGeraet?: string }
  | { art: 'still'; env: BlindEnvelopeItem }
  | { art: 'unlesbar'; env: BlindEnvelopeItem }

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

/** Ein fertiger Auftrag ans blinde Relais. */
export interface Versandauftrag {
  blind_mailbox_id: string
  ciphertext_envelope: string
  client_uuid: string
  is_control?: boolean
  control_type?: string
}

export interface KonversationOptionen {
  ziel: GespraechsZiel
  eigeneId: number
  /**
   * Über eine Ref, nicht als Wert: Quittungen und Abrufe laufen auch aus
   * Listenern, die sonst den Stand ihrer Registrierung festhalten und gegen
   * einen Schlüssel arbeiten, den es nicht mehr gibt.
   */
  identitaetRef: { current: E2eeIdentity }
  /** Wird gerufen, wenn eine Sitzung neu aufgebaut werden musste. */
  meldeSitzungsbruch: (geraet: string) => void
  /**
   * Wird gerufen, wenn ein Sitzungsaufbau abgewiesen wurde: seine Unterschrift
   * passt nicht zum Verzeichnis, sie fehlt bei einem Konto, das unterschreiben
   * kann, oder er nennt ein fremdes Konto. Einmal je Aufbau — der abgewiesene
   * bleibt gemerkt. Die laufende Sitzung bleibt unberührt.
   */
  meldeAufbauAbgelehnt: (geraet: string) => void
}

export interface Konversation {
  /** Leer, solange keine Mailbox feststeht. */
  blindMailboxId: string
  /**
   * Alle Mailboxen, aus denen dieses Gespräch liest — die Kennung aus dem
   * Chatgeheimnis und die alte, abgeleitete. Die Liste steht hier, weil der
   * Echtzeitstrom sie braucht: ein Umschlag in der neuen Mailbox ist kein
   * fremder Chat, auch wenn er nicht die Kennung des Gesprächs trägt.
   */
  leseMailboxen: string[]
  /** Der Gruppenkontext, oder `null` im Direktchat und bei unvollständiger Mitgliederliste. */
  gruppenKontext: GruppenKontext | null
  /**
   * Holt die Umschläge und entschlüsselt sie der Reihe nach.
   *
   * `null` heißt „das Gespräch hat inzwischen gewechselt" — das Ergebnis
   * gehört dann nicht mehr auf den Bildschirm.
   */
  liesUmschlaege: () => Promise<Lesung[] | null>
  /** Die Umschläge für eine Nachricht, in Zustellreihenfolge. */
  baueVersand: (payload: string, clientUuid: string) => Promise<Versandauftrag[]>
  /** Dasselbe für ein Steuerpaket (Quittung, Bearbeiten, Löschen). */
  /** Ein Steuerumschlag geht an die Geräte der Gegenseite, nie an die eigenen. */
  baueSteuerversand: (
    payload: string,
    clientUuid: string,
    typ: string,
  ) => Promise<Versandauftrag[]>
}

/**
 * Bestimmt die Mailbox-Kennung des Gesprächs.
 *
 * Zuerst synchron aus dem Zwischenspeicher, damit beim Wechsel zwischen zwei
 * Gesprächen kein Bild dazwischenblitzt, dann asynchron zur Sicherheit. Beides
 * stand vorher viermal nebeneinander in einem Effekt — einmal je Kombination
 * aus Gruppe/Kontakt und schnell/langsam.
 */
function useMailboxId(ziel: GespraechsZiel, eigeneId: number): string {
  const sofort =
    ziel.art === 'gruppe'
      ? getCachedGroupBlindMailboxId(ziel.groupId)
      : ziel.art === 'direkt' && eigeneId
        ? getCachedBlindMailboxId(eigeneId, ziel.peerId)
        : undefined

  const [mid, setMid] = useState<string>(sofort ?? '')

  useEffect(() => {
    if (ziel.art === 'keins' || (ziel.art === 'direkt' && !eigeneId)) {
      setMid('')
      return
    }

    let aktiv = true
    const schnell =
      ziel.art === 'gruppe'
        ? getCachedGroupBlindMailboxId(ziel.groupId)
        : getCachedBlindMailboxId(eigeneId, ziel.peerId)
    // Der Zwischenspeicher trifft ab dem zweiten Öffnen. Beim ersten Mal bleibt
    // die Kennung kurz leer, und genau das soll sie: eine falsche Mailbox wäre
    // ein fremdes Gespräch.
    setMid(schnell ?? '')

    const versprechen =
      ziel.art === 'gruppe'
        ? deriveGroupBlindMailboxId(ziel.groupId)
        : deriveBlindMailboxId(eigeneId, ziel.peerId)

    versprechen
      .then((berechnet) => {
        if (aktiv) setMid(berechnet)
      })
      .catch(() => {})

    return () => {
      aktiv = false
    }
    // Die Mitgliederliste gehört bewusst nicht dazu: sie ändert die Mailbox nicht.
  }, [ziel.art, ziel.art === 'gruppe' ? ziel.groupId : ziel.art === 'direkt' ? ziel.peerId : 0, eigeneId])

  return mid
}

/**
 * Baut die Umschläge für ein Ziel — ohne an das gerade offene Gespräch gebunden
 * zu sein.
 *
 * Steht hier draußen, weil das Weiterleiten in einen **anderen** Chat schreibt
 * als den offenen. Ein an den Hook gebundener Versand könnte das nicht: er
 * kennt nur den Kontext, der gerade auf dem Bildschirm steht, und würde die
 * weitergeleitete Nachricht still an das falsche Gespräch schicken.
 *
 * Der Hook ruft dieselbe Funktion mit seinem eigenen Kontext.
 */
export async function baueVersandFuer(
  gruppenKontext: GruppenKontext | null,
  drKontext: DrKontext | null,
  blindMailboxId: string,
  payload: string,
  clientUuid: string,
): Promise<Versandauftrag[]> {
  const mid = blindMailboxId
  if (!mid) throw new Error(i18n.t('chat.errors.noMailbox'))

  if (gruppenKontext) {
    // Adressiert wird die Kennung aus dem Gruppengeheimnis, sobald es eines
    // gibt. `blindMailboxId` bleibt die Kennung des **Gesprächs** — daran
    // hängen der örtliche Klartext-Cache und die Anzeige, und die dürfen beim
    // Umzug nicht mitwandern, sonst steht der Verlauf plötzlich woanders.
    // Der Gruppenschlüssel rotiert hier, falls sich die Mitgliedschaft
    // geändert hat, und die Zustellung an alle Geräte läuft mit. Erst danach
    // die Ziele holen: das Verschlüsseln legt das Geheimnis an, wenn es noch
    // keines gab, und vorher gefragt ginge diese Nachricht noch in die alte
    // Mailbox — die eine, die niemand sonst mehr liest.
    const umschlag = await verschluesseleFuerGruppe(gruppenKontext, payload)
    const ziele = await gruppenZiele(gruppenKontext.groupId, mid)
    return [
      { blind_mailbox_id: ziele.senden, ciphertext_envelope: umschlag, client_uuid: clientUuid },
    ]
  }

  if (drKontext) {
    // Double Ratchet, je Empfängergerät und je eigenem Zweitgerät ein eigener
    // Umschlag. Schlägt das fehl, geht die Nachricht nicht raus — früher fiel
    // sie hier auf einen Schlüssel zurück, den das Backend aus den beiden
    // Benutzerkennungen selbst bilden kann.
    const zustellungen = await baueZustellungen(drKontext, payload, clientUuid)
    // Wie bei der Gruppe: adressiert wird die Kennung aus dem Chatgeheimnis,
    // `mid` bleibt die Kennung des Gesprächs. `erzeuge` steht nur hier — der
    // Versand ist der Augenblick, in dem ein Gespräch wirklich beginnt, und
    // erzeugen heisst Umschläge an die Geräte der Gegenstelle. Beim Lesen
    // wäre das eine Spur fürs blosse Nachsehen.
    const ziel = await dmZiele(drKontext.eigeneId, drKontext.peerId, mid, { erzeuge: true })
    const auftraege: Versandauftrag[] = []
    for (const z of zustellungen) {
      // Reihenfolge ist bindend: ohne den Aufbau findet die Gegenstelle keine
      // Sitzung und läuft in den Sitzungsbruch.
      if (z.bootstrap) {
        auftraege.push({
          blind_mailbox_id: ziel.senden,
          ciphertext_envelope: z.bootstrap,
          client_uuid: z.bootstrapClientUuid,
          is_control: true,
          control_type: 'dr-init',
        })
      }
      auftraege.push({
        blind_mailbox_id: ziel.senden,
        ciphertext_envelope: z.nachricht,
        client_uuid: z.clientUuid,
      })
    }
    return auftraege
  }

  return []
}

export function useKonversation({
  ziel,
  eigeneId,
  identitaetRef,
  meldeSitzungsbruch,
  meldeAufbauAbgelehnt,
}: KonversationOptionen): Konversation {
  const blindMailboxId = useMailboxId(ziel, eigeneId)

  /**
   * Was dieser Durchlauf liest, für den Echtzeitstrom.
   *
   * Vergleich über die zusammengesetzte Zeichenkette, nicht über die Liste:
   * `lesen` ensteht bei jedem Durchlauf neu, und eine neue Referenz mit
   * gleichem Inhalt würde den `useEffect` des Stroms bei jedem Takt neu binden.
   */
  const [leseMailboxen, setLeseMailboxen] = useState<string[]>([])
  const leseStand = useRef('')

  /** Woran ein laufender Abruf merkt, dass er zu spät kommt. */
  const aktuelleMailbox = useRef('')
  aktuelleMailbox.current = blindMailboxId

  // Dem Echtzeitstrom sagen, dass diese Mailbox uns angeht. Ein offenes
  // Gespräch ist der Fall, in dem eine verspätete Nachricht am meisten
  // auffällt — und für eine Kennung, die der Server nicht ausrechnen kann,
  // ist das Abo der einzige Weg, überhaupt davon zu erfahren.
  //
  // Nicht wieder gekündigt beim Schliessen: wer ein Gespräch zumacht, will
  // trotzdem wissen, wenn dort etwas ankommt. Gekündigt wird beim Verlassen
  // einer Gruppe und beim Abmelden.
  useEffect(() => {
    if (!blindMailboxId) return
    abonniereMailbox(blindMailboxId, mailboxNachweis(blindMailboxId))
  }, [blindMailboxId])

  const gruppenKontext = useMemo<GruppenKontext | null>(() => {
    if (ziel.art !== 'gruppe' || !eigeneId || !blindMailboxId) return null
    // Eine leere Mitgliederliste heißt „noch nicht geladen", nicht „Gruppe ohne
    // Mitglieder": manche Antworten des Backends lassen sie aus. Daraus einen
    // Schlüssel zu münzen hieße, ihn an niemanden zu verteilen und ihn beim
    // nächsten vollständigen Stand sofort wieder zu ersetzen.
    if (ziel.mitglieder.length === 0) return null
    const mitglieder = [...ziel.mitglieder]
    if (!mitglieder.includes(eigeneId)) mitglieder.push(eigeneId)
    return {
      groupId: ziel.groupId,
      blindMailboxId,
      eigeneId,
      mitglieder,
      istEigentuemer: ziel.istEigentuemer,
    }
  }, [ziel.art, ziel.art === 'gruppe' ? ziel.groupId : 0, ziel.art === 'gruppe' ? ziel.mitglieder.join(',') : '', ziel.art === 'gruppe' ? ziel.istEigentuemer : false, eigeneId, blindMailboxId])

  const drKontext = useMemo<DrKontext | null>(
    () => (ziel.art === 'direkt' && eigeneId ? { eigeneId, peerId: ziel.peerId } : null),
    [ziel.art, ziel.art === 'direkt' ? ziel.peerId : 0, eigeneId],
  )

  const einDurchlauf = useCallback(async (): Promise<Lesung[] | null> => {
    const mid = blindMailboxId
    if (!mid || !eigeneId) return null
    // Erst entschlüsseln, wenn feststeht, welchen Schlüssel dieses Gerät hat.
    // Ein Durchlauf davor hätte keinen, fiele auf den Altpfad und schriebe
    // dessen Ergebnis in den Zwischenspeicher — der richtige Klartext käme
    // danach nicht mehr durch.
    const identitaet = identitaetRef.current
    if (identitaet.state === 'loading') return null

    const schluessel = identitaet.decryptionKeys

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
    await holeGeraeteSteuerung(
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
    if (aktuelleMailbox.current !== mid) {
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
    if (gruppenKontext) {
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
    {
      const kennungen = [...new Set([mid, ...lesen])].sort()
      const stand = kennungen.join('|')
      if (leseStand.current !== stand) {
        leseStand.current = stand
        setLeseMailboxen(kennungen)
      }
    }
    if (aktuelleMailbox.current !== mid) {
      behalteEigeneSteuerung()
      return null
    }

    const umschlaege = (
      await Promise.all(
        lesen.map((kennung) =>
          fetchAlleE2eeEnvelopes(kennung).catch(
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
    if (aktuelleMailbox.current !== mid) {
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
          if (lesung.art === 'kein-schluessel' && env.id > fehlenderSchluessel.envId) {
            fehlenderSchluessel.envId = env.id
            fehlenderSchluessel.keyId = lesung.keyId
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
              if (aufbau.abgelehnt && aufbau.vonGeraet) meldeAufbauAbgelehnt(aufbau.vonGeraet)
              if (aufbau.ersetzt && aufbau.vonGeraet) meldeSitzungsbruch(aufbau.vonGeraet)
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
             * wieder, dann mit dem Aufbau davor.
             */
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
            return { art: 'unlesbar', env }
          }
          // 'eigen' — der Absender kann seine eigene Ratchet-Nachricht nicht
          // öffnen, das ist der Sinn der Sache; sein Gesprächsanteil kommt aus
          // dem lokalen Speicher. 'fremd' — eine der aufgefächerten Kopien für
          // ein anderes Gerät. 'zurueckgestellt' — der Aufbau davor ist noch
          // offen; ungeöffnet und nicht zwischengespeichert, kommt der Umschlag
          // im nächsten Durchlauf wieder. 'abgewiesen' — die Nachricht hinter
          // einem gefälschten Aufbau; gemeldet ist der Aufbau schon.
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
          if (aufbau.abgelehnt && aufbau.vonGeraet) meldeAufbauAbgelehnt(aufbau.vonGeraet)
          if (aufbau.ersetzt && aufbau.vonGeraet) meldeSitzungsbruch(aufbau.vonGeraet)
          vorabAufbau.add(env.id)
          continue
        }
        envelopePlaintextCache.set(env.id, { plain: klartext, ok: true })
        vorabKlartext.set(env.id, klartext)
      }
    }
    if (aktuelleMailbox.current !== mid) {
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

    if (aktuelleMailbox.current !== mid) {
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
      meldeSitzungsbruch(bruch.vonGeraet)
    }

    return gelesen
  }, [blindMailboxId, eigeneId, gruppenKontext, drKontext, identitaetRef, meldeSitzungsbruch, meldeAufbauAbgelehnt])

  /** Der laufende Durchlauf, und der eine, der hinter ihm warten darf. */
  const laufend = useRef<Promise<unknown>>(Promise.resolve())
  const wartend = useRef<Promise<Lesung[] | null> | null>(null)

  /**
   * Ein Durchlauf zur Zeit, und höchstens einer wartet.
   *
   * Der Messenger ruft hier aus fünf Quellen herein: dem Fünf-Sekunden-Takt,
   * jedem Sync-Ereignis, dem Sichtbarkeitswechsel, dem Senden und den
   * Bestätigungen der Warteschlange. Eine einzige Nachricht löst mehrere davon
   * fast gleichzeitig aus — Umschlag, Zustellquittung, Lesequittung sind drei
   * Ereignisse. Ohne diese Klammer holen zwei Durchläufe dasselbe Fenster aus
   * hundert Umschlägen und entschlüsseln jeden Hybridumschlag darin zweimal.
   *
   * Dass ein doppelt gelesener Ratchet-Umschlag keine Sitzung mehr kostet,
   * steht in `liesDrUmschlag` und muss dort stehen: zwei Tabs teilen sich
   * Ablage und Geräteschlüssel, aber nicht diese Refs.
   *
   * Warteschlange statt gemeinsamer Antwort: wer ruft, weil gerade ein
   * Umschlag eingetroffen ist, darf nicht das Ergebnis eines Abrufs bekommen,
   * der vor diesem Umschlag losgelaufen ist. Der Nächste wartet also, statt
   * mitzulesen. Mehr als einen zu stapeln brächte nichts — sie fragen alle
   * dasselbe.
   */
  const liesUmschlaege = useCallback((): Promise<Lesung[] | null> => {
    if (wartend.current) return wartend.current
    const starte = () => {
      wartend.current = null
      return einDurchlauf()
    }
    const naechster = laufend.current.then(starte, starte)
    wartend.current = naechster
    laufend.current = naechster.then(
      () => undefined,
      () => undefined,
    )
    return naechster
  }, [einDurchlauf])

  const baueVersand = useCallback(
    (payload: string, clientUuid: string): Promise<Versandauftrag[]> =>
      baueVersandFuer(gruppenKontext, drKontext, blindMailboxId, payload, clientUuid),
    [blindMailboxId, gruppenKontext, drKontext],
  )

  const baueSteuerversand = useCallback(
    async (payload: string, clientUuid: string, typ: string): Promise<Versandauftrag[]> => {
      const mid = blindMailboxId
      if (!mid || !eigeneId) return []

      /**
       * Dieselbe Wirkung für die **eigenen übrigen Geräte**.
       *
       * Sie geht nicht in die Chat-Mailbox: das Relais weist einen Umschlag
       * an das eigene Konto dort mit 400 ab. Ihr Weg ist die Geräte-Mailbox,
       * in der schon die Schlüssel liegen, versiegelt gegen jedes einzelne
       * Gerät. Das sendende Gerät überspringt sich selbst — es hat die Wirkung
       * bereits lokal angewandt.
       *
       * Ohne diesen Schritt blieb „gelesen", „bearbeitet", „gelöscht" und
       * „reagiert" auf dem Gerät stehen, an dem es passiert ist: das Web zeigte
       * ein Häkchen, während die App weiter die alte Fassung hatte.
       */
      const eigeneAuftraege = async (): Promise<Versandauftrag[]> => {
        const sendPair = identitaetRef.current.sendPair
        if (!sendPair) return []
        const [geraete, meins, geraeteMailbox] = await Promise.all([
          verlangeGeraeteVon(eigeneId).catch(() => []),
          eigenesGeraet().catch(() => null),
          deriveUserDeviceMailboxId(eigeneId),
        ])
        const auftraege = await Promise.all(
          geraete
            .filter((geraet) => geraet.device_id !== meins?.kennung)
            .map(async (geraet, i) => ({
              blind_mailbox_id: geraeteMailbox,
              ciphertext_envelope: await encryptE2eeHybrid(
                payload,
                geraet.public_key,
                sendPair.publicKeyJwk,
              ),
              // Eigene Kennung je Gerät; `#own` hält die Kopie aus dem
              // Nachrichtenfenster des Empfängers heraus.
              client_uuid: `${clientUuid}#own${i}`,
              is_control: true,
              control_type: typ,
            })),
        )
        return auftraege
      }

      if (gruppenKontext) {
        // Quittungen laufen in der Gruppe über denselben Schlüssel wie die
        // Nachrichten. Anders als beim Ratchet kostet das nichts: ein
        // Gruppenschlüssel verbraucht sich nicht.
        const umschlag = await verschluesseleFuerGruppe(gruppenKontext, payload)
        return [
          {
            blind_mailbox_id: mid,
            ciphertext_envelope: umschlag,
            client_uuid: clientUuid,
            is_control: true,
            control_type: typ,
          },
          ...(await eigeneAuftraege()),
        ]
      }

      if (!drKontext) return []
      const sendPair = identitaetRef.current.sendPair
      if (!sendPair) return []

      // Quittungen bleiben auf dem Hybridumschlag und laufen bewusst **nicht**
      // durch den Ratchet. Sie sind häufig, sie sind klein, und sie dürfen den
      // Nachrichtenfaden unter keinen Umständen stören: eine verlorene Quittung
      // kostet ein Häkchen, eine verbrauchte Kettenposition kostet eine
      // Nachricht. Versiegelt wird trotzdem je Gerät einzeln — ein Konto hat
      // keinen gemeinsamen privaten Schlüssel mehr.
      const geraete = await verlangeGeraeteVon(drKontext.peerId)
      const ziel = await dmZiele(drKontext.eigeneId, drKontext.peerId, mid)
      const auftraege = await Promise.all(
        geraete.map(async (geraet, i) => ({
          blind_mailbox_id: ziel.senden,
          ciphertext_envelope: await encryptE2eeHybrid(payload, geraet.public_key, sendPair.publicKeyJwk),
          // Je Gerät eine eigene Kennung, sonst gibt das Relais beim zweiten
          // Aufruf still den ersten Umschlag zurück und nur ein Gerät erfährt
          // von der Quittung.
          client_uuid: `${clientUuid}#${i}`,
          is_control: true,
          control_type: typ,
        })),
      )
      return [...auftraege, ...(await eigeneAuftraege())]
    },
    [blindMailboxId, eigeneId, gruppenKontext, drKontext, identitaetRef],
  )

  return {
    blindMailboxId,
    leseMailboxen,
    gruppenKontext,
    liesUmschlaege,
    baueVersand,
    baueSteuerversand,
  }
}
