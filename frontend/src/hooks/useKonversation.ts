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

import { fetchE2eeEnvelopes, type BlindEnvelopeItem } from '@/api/social'
import {
  decryptE2eeHybridWithKeyring,
  deriveBlindMailboxId,
  deriveGroupBlindMailboxId,
  encryptE2eeHybrid,
  envelopePlaintextCache,
  getCachedBlindMailboxId,
  getCachedGroupBlindMailboxId,
} from '@/services/e2eeCrypto'
import { verlangeGeraeteVon } from '@/services/e2eeGeraet'
import type { E2eeIdentity } from '@/services/e2eeIdentity'
import {
  entschluesseleGruppenUmschlag,
  fordereGruppenSchluessel,
  verarbeiteGruppenSteuerung,
  verschluesseleFuerGruppe,
  type GruppenKontext,
} from '@/services/gruppenSchluessel'
import {
  ladeUmschlagKlartexte,
  speichereUmschlagKlartext,
} from '@/services/messengerLocalStore'
import {
  baueZustellungen,
  liesDrUmschlag,
  verarbeiteBootstrap,
  verwirfDrSitzung,
  type DrKontext,
} from '@/services/ratchetSitzung'

const HYBRID_PREFIX = 'sv-e2ee-hybrid-v1:'

/** Mit wem oder was gesprochen wird. */
export type GespraechsZiel =
  | { art: 'direkt'; peerId: number }
  | { art: 'gruppe'; groupId: number; mitglieder: readonly number[] }
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
  | { art: 'klartext'; env: BlindEnvelopeItem; text: string }
  | { art: 'still'; env: BlindEnvelopeItem }
  | { art: 'unlesbar'; env: BlindEnvelopeItem }

/** Ein fertiger Auftrag ans blinde Relais. */
export interface Versandauftrag {
  blind_mailbox_id: string
  ciphertext_envelope: string
  recipient_id?: number
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
}

export interface Konversation {
  /** Leer, solange keine Mailbox feststeht. */
  blindMailboxId: string
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

export function useKonversation({
  ziel,
  eigeneId,
  identitaetRef,
  meldeSitzungsbruch,
}: KonversationOptionen): Konversation {
  const blindMailboxId = useMailboxId(ziel, eigeneId)

  /** Woran ein laufender Abruf merkt, dass er zu spät kommt. */
  const aktuelleMailbox = useRef('')
  aktuelleMailbox.current = blindMailboxId

  const gruppenKontext = useMemo<GruppenKontext | null>(() => {
    if (ziel.art !== 'gruppe' || !eigeneId || !blindMailboxId) return null
    // Eine leere Mitgliederliste heißt „noch nicht geladen", nicht „Gruppe ohne
    // Mitglieder": manche Antworten des Backends lassen sie aus. Daraus einen
    // Schlüssel zu münzen hieße, ihn an niemanden zu verteilen und ihn beim
    // nächsten vollständigen Stand sofort wieder zu ersetzen.
    if (ziel.mitglieder.length === 0) return null
    const mitglieder = [...ziel.mitglieder]
    if (!mitglieder.includes(eigeneId)) mitglieder.push(eigeneId)
    return { groupId: ziel.groupId, blindMailboxId, eigeneId, mitglieder }
  }, [ziel.art, ziel.art === 'gruppe' ? ziel.groupId : 0, ziel.art === 'gruppe' ? ziel.mitglieder.join(',') : '', eigeneId, blindMailboxId])

  const drKontext = useMemo<DrKontext | null>(
    () => (ziel.art === 'direkt' && eigeneId ? { eigeneId, peerId: ziel.peerId } : null),
    [ziel.art, ziel.art === 'direkt' ? ziel.peerId : 0, eigeneId],
  )

  const liesUmschlaege = useCallback(async (): Promise<Lesung[] | null> => {
    const mid = blindMailboxId
    if (!mid || !eigeneId) return null
    // Erst entschlüsseln, wenn feststeht, welchen Schlüssel dieses Gerät hat.
    // Ein Durchlauf davor hätte keinen, fiele auf den Altpfad und schriebe
    // dessen Ergebnis in den Zwischenspeicher — der richtige Klartext käme
    // danach nicht mehr durch.
    const identitaet = identitaetRef.current
    if (identitaet.state === 'loading') return null

    const umschlaege = await fetchE2eeEnvelopes(mid)
    if (aktuelleMailbox.current !== mid) return null

    const schluessel = identitaet.decryptionKeys
    // Was hier steht, ist schon einmal geöffnet worden. Unverzichtbar, nicht
    // bloß schnell: ein Ratchet-Nachrichtenschlüssel ist nach dem ersten Öffnen
    // verbraucht, ein zweiter Versuch am selben Umschlag muss scheitern.
    const bekannt = drKontext ? await ladeUmschlagKlartexte(mid) : new Map<number, string>()

    const gebrochene: { vonKonto: number; vonGeraet: string }[] = []
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
      if (gespeichert !== undefined) return { art: 'klartext', env, text: gespeichert }
      const zwischen = envelopePlaintextCache.get(env.id)
      if (zwischen?.ok) return { art: 'klartext', env, text: zwischen.plain }

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
            const klartext = await decryptE2eeHybridWithKeyring(env.ciphertext_envelope, schluessel)
            const aufbau = await verarbeiteBootstrap(drKontext, klartext)
            if (aufbau.istAufbau) {
              if (aufbau.ersetzt && aufbau.vonGeraet) meldeSitzungsbruch(aufbau.vonGeraet)
              return { art: 'still', env }
            }
            envelopePlaintextCache.set(env.id, { plain: klartext, ok: true })
            return { art: 'klartext', env, text: klartext }
          }

          const lesung = await liesDrUmschlag(
            drKontext,
            env.ciphertext_envelope,
            // Erst der Klartext auf die Platte, dann der fortgeschriebene
            // Zustand. Schlägt das fehl, scheitert der ganze Schritt und der
            // Umschlag bleibt beim nächsten Mal lesbar.
            (text) => speichereUmschlagKlartext(mid, env.id, text),
          )
          if (lesung.art === 'klartext') {
            envelopePlaintextCache.set(env.id, { plain: lesung.text, ok: true })
            return { art: 'klartext', env, text: lesung.text }
          }
          if (lesung.art === 'bruch') {
            gebrochene.push({ vonKonto: lesung.vonKonto, vonGeraet: lesung.vonGeraet })
            return { art: 'still', env }
          }
          if (lesung.art === 'unbekannt' || lesung.art === 'fehler') {
            // 'unbekannt' — Altbestand aus der Zeit der ableitbaren
            // Kanalschlüssel. 'fehler' — die lokale Ablage streikte; die Sitzung
            // bleibt unangetastet, der nächste Durchlauf versucht es erneut.
            return { art: 'unlesbar', env }
          }
          // 'eigen' — der Absender kann seine eigene Ratchet-Nachricht nicht
          // öffnen, das ist der Sinn der Sache; sein Gesprächsanteil kommt aus
          // dem lokalen Speicher. 'fremd' — eine der aufgefächerten Kopien für
          // ein anderes Gerät.
          return { art: 'still', env }
        }

        return { art: 'unlesbar', env }
      } catch {
        return { art: 'unlesbar', env }
      }
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

    if (aktuelleMailbox.current !== mid) return null

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
  }, [blindMailboxId, eigeneId, gruppenKontext, drKontext, identitaetRef, meldeSitzungsbruch])

  const baueVersand = useCallback(
    async (payload: string, clientUuid: string): Promise<Versandauftrag[]> => {
      const mid = blindMailboxId
      if (!mid) throw new Error('Für dieses Gespräch steht noch keine Mailbox fest.')

      if (gruppenKontext) {
        // Der Gruppenschlüssel rotiert hier, falls sich die Mitgliedschaft
        // geändert hat, und die Zustellung an alle Geräte läuft mit.
        const umschlag = await verschluesseleFuerGruppe(gruppenKontext, payload)
        return [{ blind_mailbox_id: mid, ciphertext_envelope: umschlag, client_uuid: clientUuid }]
      }

      if (drKontext) {
        // Double Ratchet, je Empfängergerät und je eigenem Zweitgerät ein
        // eigener Umschlag. Schlägt das fehl, geht die Nachricht nicht raus —
        // früher fiel sie hier auf einen Schlüssel zurück, den das Backend aus
        // den beiden Benutzerkennungen selbst bilden kann.
        const zustellungen = await baueZustellungen(drKontext, payload, clientUuid)
        const auftraege: Versandauftrag[] = []
        for (const z of zustellungen) {
          // Reihenfolge ist bindend: ohne den Aufbau findet die Gegenstelle
          // keine Sitzung und läuft in den Sitzungsbruch.
          if (z.bootstrap) {
            auftraege.push({
              blind_mailbox_id: mid,
              ciphertext_envelope: z.bootstrap,
              recipient_id: drKontext.peerId,
              client_uuid: z.bootstrapClientUuid,
              is_control: true,
              control_type: 'dr-init',
            })
          }
          auftraege.push({
            blind_mailbox_id: mid,
            ciphertext_envelope: z.nachricht,
            recipient_id: drKontext.peerId,
            client_uuid: z.clientUuid,
          })
        }
        return auftraege
      }

      return []
    },
    [blindMailboxId, gruppenKontext, drKontext],
  )

  const baueSteuerversand = useCallback(
    async (payload: string, clientUuid: string, typ: string): Promise<Versandauftrag[]> => {
      const mid = blindMailboxId
      if (!mid) return []

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
      return Promise.all(
        geraete.map(async (geraet, i) => ({
          blind_mailbox_id: mid,
          ciphertext_envelope: await encryptE2eeHybrid(payload, geraet.public_key, sendPair.publicKeyJwk),
          recipient_id: drKontext.peerId,
          // Je Gerät eine eigene Kennung, sonst gibt das Relais beim zweiten
          // Aufruf still den ersten Umschlag zurück und nur ein Gerät erfährt
          // von der Quittung.
          client_uuid: `${clientUuid}#${i}`,
          is_control: true,
          control_type: typ,
        })),
      )
    },
    [blindMailboxId, gruppenKontext, drKontext, identitaetRef],
  )

  return { blindMailboxId, gruppenKontext, liesUmschlaege, baueVersand, baueSteuerversand }
}
