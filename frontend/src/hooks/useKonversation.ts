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

import type { BlindEnvelopeItem } from '@/api/social'
import {
  deriveBlindMailboxId,
  deriveGroupBlindMailboxId,
  getCachedBlindMailboxId,
  getCachedGroupBlindMailboxId,
} from '@/services/e2eeCrypto'
import type { E2eeIdentity } from '@/services/e2eeIdentity'
import {
  dmZiele,
  gruppenZiele,
  verschluesseleFuerGruppe,
  type GruppenKontext,
} from '@/services/gruppenSchluessel'
import { abonniereMailbox } from '@/services/mailboxAbo'
import { mailboxNachweis } from '@/services/mailboxNachweis'
import { baueZustellungen, type DrKontext } from '@/services/ratchetSitzung'
import { verarbeiteGespraech } from '@/services/gespraech/verarbeitung'
import {
  baueSteuerVersand,
  sendeSteuerpaket as sendeSteuerpaketDienst,
  type SteuerKontext,
} from '@/services/gespraech/steuerung'
import {
  sendeWirkung as sendeWirkungDienst,
  type SteuerAusgang,
} from '@/services/steuerOutbox'
import { stosseSteuerOutboxAn } from '@/services/steuerOutboxLauf'
import type { SteuerEintrag } from '@/services/steuerOutboxTypen'

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

/** Ein fertiger Auftrag ans blinde Relais. */
export interface Versandauftrag {
  blind_mailbox_id: string
  ciphertext_envelope: string
  client_uuid: string
  is_control?: boolean
  control_type?: string
  /**
   * Die Zustelladresse des Zielgeraets, wenn es den neuen Weg kennt.
   *
   * Leer heisst: ueber die geteilte Mailbox (der alte Weg). Gesetzt heisst:
   * genau dorthin, und nur dorthin — eine Doppelspur gibt es nicht.
   */
  ziel_inbox?: string | null
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
  /**
   * Den Lesestand einer Lesung übernehmen — und zwar **erst**, wenn der
   * Aufrufer sie wirklich angezeigt und abgelegt hat.
   *
   * Warum das nicht der Leselauf selbst tut: der Lauf endet, bevor jemand
   * weiss, ob sein Ergebnis noch gebraucht wird. `loadMessages` verwirft eine
   * Lesung, wenn inzwischen ein neuer Durchlauf gestartet ist
   * (`currentLoadSeqRef`) — richtig so, sonst überschreibt ein alter Stand den
   * neuen. Mit dem Cursor im Lauf wanderte der Stand trotzdem vor, und die
   * verworfenen Umschläge kamen nie wieder: am 22.10.2026 fehlten dadurch
   * Nachrichten der Gegenseite im Verlauf, obwohl sie entschlüsselt in der
   * Klartextablage lagen.
   */
  uebernimmLesestand: (lesung: Lesung[]) => void
  /** Die Umschläge für eine Nachricht, in Zustellreihenfolge. */
  baueVersand: (payload: string, clientUuid: string) => Promise<Versandauftrag[]>
  /** Dasselbe für ein Steuerpaket (Quittung, Bearbeiten, Löschen). */
  /** Ein Steuerumschlag geht an die Geräte der Gegenseite, nie an die eigenen. */
  baueSteuerversand: (
    payload: string,
    clientUuid: string,
    typ: string,
  ) => Promise<Versandauftrag[]>
  /**
   * Ein Steuerpaket signieren und abschicken.
   *
   * Derselbe Weg wie `baueSteuerversand`, nur vollständig: der Aufrufer
   * liefert den Inhalt, die Kennung entsteht hier. Der Hintergrundlauf des
   * Gerätepostfachs nimmt ihn für Zustellquittungen ohne offenen Chat.
   *
   * **Quittungen bleiben hier draußen.** Sie sind kumulativ, klein und häufig;
   * ein Stau in einer Warteschlange würde genau das kaputt machen, was sie
   * belegen. Eine verlorene Quittung kostet ein Häkchen.
   */
  sendeSteuerpaket: (payloadObj: Record<string, unknown>) => Promise<string | null>
  /**
   * Eine Wirkung auf eine bestehende Nachricht (Bearbeiten, Löschen, Reaktion,
   * Anheften, Verfall) — mit dauerhafter Outbox.
   *
   * Scheitert der Sofortversuch am Netz, liegt die Wirkung in der Ablage und
   * geht später hinaus; der Aufrufer behält seine lokale Wirkung. Eine
   * endgültige Ablehnung wirft, und der Aufrufer nimmt zurück.
   */
  sendeWirkung: (eintrag: SteuerEintrag) => Promise<SteuerAusgang>
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
          ziel_inbox: z.zielInbox,
        })
      }
      auftraege.push({
        blind_mailbox_id: ziel.senden,
        ciphertext_envelope: z.nachricht,
        client_uuid: z.clientUuid,
        ziel_inbox: z.zielInbox,
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

  /**
   * Bis wohin dieses Gerät jede Mailbox gelesen hat, je Kennung.
   *
   * Der offene Chat ruft hier alle fünf Sekunden herein. Ohne diesen Stand holte
   * jeder Abruf den ganzen Verlauf erneut: am laufenden System waren das 510
   * Abrufe und rund 66 MB Chiffrat in 23 Minuten für **ein** offenes Gespräch,
   * und der Browser entschlüsselte dieselben Pakete immer wieder. `since_id`
   * kann der Server längst (`fetchAlleE2eeEnvelopes`); jetzt nennt der Client
   * ihn auch.
   *
   * Je Mailbox und nicht je Gespräch: ein Direktchat liest während des Umzugs
   * zwei Kennungen, und ein Gespräch, das man wieder öffnet, soll dort
   * weiterlesen, wo es aufgehört hat.
   *
   * Der Stand lebt nur im Speicher. Ein frischer Tab beginnt bei 0 und holt
   * einmal alles — genau das, was er braucht, um den Verlauf aufzubauen.
   */
  const lesestand = useRef(new Map<string, number>())

  /**
   * Die Stände abgeschlossener Läufe, die noch nicht übernommen wurden.
   *
   * Nach der Lesung selbst geschlüsselt: derselbe Lauf kann mehrfach bestellt
   * werden (`liesUmschlaege` gibt bei gleichzeitigen Aufrufen dieselbe Antwort
   * zurück), und nur wer sie behält, darf den Stand fortschreiben. Was niemand
   * übernimmt, fällt mit der Lesung aus der `WeakMap` — und genau das ist
   * gewollt: dann bleibt der Stand stehen und der nächste Abruf holt die
   * Umschläge noch einmal.
   */
  const standsOffen = useRef(new WeakMap<Lesung[], Map<string, number>>())

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
    // Erst entschlüsseln, wenn feststeht, welchen Schlüssel dieses Gerät hat.
    // Ein Durchlauf davor hätte keinen, fiele auf den Altpfad und schriebe
    // dessen Ergebnis in den Zwischenspeicher — der richtige Klartext käme
    // danach nicht mehr durch.
    const identitaet = identitaetRef.current
    if (identitaet.state === 'loading') return null

    const ergebnis = await verarbeiteGespraech(
      {
        mailbox: blindMailboxId,
        eigeneId,
        drKontext,
        gruppenKontext,
        decryptionKeys: identitaet.decryptionKeys,
      },
      {
        lesestand: lesestand.current,
        istAktuell: () => aktuelleMailbox.current === blindMailboxId,
        meldeMailboxen: (kennungen) => {
          const kennung = kennungen.join('|')
          if (leseStand.current !== kennung) {
            leseStand.current = kennung
            setLeseMailboxen(kennungen)
          }
        },
        meldeSitzungsbruch,
        meldeAufbauAbgelehnt,
      },
    )
    if (!ergebnis) return null
    // **Nicht** schreiben. Erst wer diese Lesung übernimmt, rückt den Stand
    // vor (`uebernimmLesestand`) — ein verworfener Durchlauf darf keine
    // Umschläge mitnehmen.
    standsOffen.current.set(ergebnis.lesungen, ergebnis.stand)
    return ergebnis.lesungen
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

  /**
   * Den Stand einer Lesung übernehmen — nur nach oben.
   *
   * Gleichzeitige Läufe können in beliebiger Reihenfolge zurückkommen; jeder
   * Stand liegt über dem, was beim Start galt, ein kleinerer kann einen schon
   * übernommenen also nur wiederholen, nie zurückdrehen.
   */
  const uebernimmLesestand = useCallback((lesung: Lesung[]) => {
    const stand = standsOffen.current.get(lesung)
    if (!stand) return
    standsOffen.current.delete(lesung)
    for (const [kennung, bis] of stand) {
      if (bis > (lesestand.current.get(kennung) ?? 0)) {
        lesestand.current.set(kennung, bis)
      }
    }
  }, [])

  const baueVersand = useCallback(
    (payload: string, clientUuid: string): Promise<Versandauftrag[]> =>
      baueVersandFuer(gruppenKontext, drKontext, blindMailboxId, payload, clientUuid),
    [blindMailboxId, gruppenKontext, drKontext],
  )

  /*
   * Die Identität wird **beim Aufruf** gelesen, nicht beim Rendern: sie
   * wechselt mit dem Entsperren, und ein festgehaltenes Objekt schickte nach
   * dem Sperren weiter mit dem alten Schlüssel.
   */
  const steuerKontext = useCallback(
    (): SteuerKontext => ({
      eigeneId,
      mailbox: blindMailboxId,
      drKontext,
      gruppenKontext,
      identitaet: identitaetRef.current,
    }),
    [blindMailboxId, eigeneId, gruppenKontext, drKontext, identitaetRef],
  )

  const baueSteuerversand = useCallback(
    (payload: string, clientUuid: string, typ: string): Promise<Versandauftrag[]> =>
      baueSteuerVersand(steuerKontext(), payload, clientUuid, typ),
    [steuerKontext],
  )

  const sendeSteuerpaket = useCallback(
    (payloadObj: Record<string, unknown>) =>
      sendeSteuerpaketDienst(steuerKontext(), payloadObj, crypto.randomUUID()),
    [steuerKontext],
  )

  /**
   * Wirkungen gehen über die Outbox; nach einem erfolgreichen Senden wird
   * angestoßen, was ältere Aufträge noch offen hat.
   */
  const sendeWirkung = useCallback(
    async (eintrag: SteuerEintrag): Promise<SteuerAusgang> => {
      const ausgang = await sendeWirkungDienst(steuerKontext(), eintrag)
      if (ausgang === 'gesendet') void stosseSteuerOutboxAn().catch(() => {})
      return ausgang
    },
    [steuerKontext],
  )

  return {
    blindMailboxId,
    leseMailboxen,
    gruppenKontext,
    liesUmschlaege,
    uebernimmLesestand,
    baueVersand,
    baueSteuerversand,
    sendeSteuerpaket,
    sendeWirkung,
  }
}
