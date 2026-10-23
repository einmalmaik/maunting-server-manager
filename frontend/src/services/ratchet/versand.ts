/** Versand-Auffächerung des Double Ratchet an alle Zielgeräte. */

import { bytesToBase64, utf8ToBytes } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'
import {
  destroyRatchetState,
  encryptMessage,
  serializeRatchetMessage,
} from '@msdis/shield/messaging'

import type { E2eeGeraetItem } from '@/api/social'
import {
  geraeteVon,
  geraetVeroeffentlichen,
  vergessenGeraete,
  verlangeGeraeteVon,
  verzeichnisVon,
} from '../e2eeGeraet'
import { leseBindung, schritt, sitzungsId, type AusstehendeZustellung } from '../ratchetSpeicher'
import { beginneSitzung } from './aufbau'
import {
  DrGeraetNichtEingetragenError,
  DrZustellungFehlgeschlagenError,
  KENNUNG_KURZ,
  baueUmschlag,
  type DrKontext,
  type DrZustellung,
} from './format'

export async function baueZustellungen(
  kontext: DrKontext,
  klartext: string,
  basisUuid: string,
  /**
   * Die Mailbox des Gesprächs, für die Versandabsichten.
   *
   * Fehlt sie (etwa in engen Tests), entstehen keine Absichten — der Aufrufer
   * bekäme dann Umschläge ohne Wiederaufnahme. Im Messenger reicht
   * `baueVersandFuer` sie immer durch.
   */
  mailbox = '',
): Promise<DrZustellung[]> {
  const meins = await geraetVeroeffentlichen()
  const eigene = (
    await eigenesVerzeichnis(kontext.eigeneId, meins.kennung, meins.paar.publicKeyJwk)
  ).filter(
    (g) => g.device_id !== meins.kennung,
  )
  const fremde = await verlangeGeraeteVon(kontext.peerId)

  const ziele = [
    ...fremde.map((g) => ({ geraet: g, konto: kontext.peerId })),
    ...eigene.map((g) => ({ geraet: g, konto: kontext.eigeneId })),
  ]

  const zustellungen: DrZustellung[] = []
  const gescheitert: unknown[] = []
  for (const ziel of ziele) {
    const zielGeraet = ziel.geraet.device_id
    const id = sitzungsId(meins.kennung, ziel.konto, zielGeraet)
    try {
      /*
       * Gehört die Sitzung noch zu diesem Schlüssel?
       *
       * Der Abdruck entsteht beim Aufbau (aus dem Verzeichnis) und hier. Hat
       * die Gegenstelle ihren Schlüssel unter derselben Geräte-ID gewechselt —
       * Neuinstallation, neuer Browser, zurückgesetzte Ablage —, läuft die alte
       * Ratchet-Sitzung sonst weiter, und zwar dauerhaft stumm: die Gegenstelle
       * hat ihr Gegenstück nicht mehr, sendet aber unter derselben Kennung. Bis
       * 10/2026 wurde das nie erkannt.
       */
      const bindung = await sha256Hex(utf8ToBytes(ziel.geraet.public_key))
      const gebaut = await schritt<{ bootstrap: string | null; nachricht: string }>(
        id,
        async (zustand) => {
          let arbeitszustand = zustand
          let bootstrap: string | null = null
          let selbstErzeugt = false

          const gemerkt = await leseBindung(id)
          if (arbeitszustand && gemerkt !== null && gemerkt !== bindung) {
            // Alte Sitzung verwerfen — unter demselben Schloss, in dem gleich
            // der neue Zustand geschrieben wird. Ein `await` danach gilt nicht
            // mehr: was hier gelesen wurde, ist die Grundlage dieser Runde.
            destroyRatchetState(arbeitszustand)
            arbeitszustand = null
          }
          /*
           * `gemerkt === null` heisst: die Sitzung stammt aus der Zeit vor
           * dieser Bindung. Sie wird **nicht** weggeworfen — sonst zeigte jedes
           * Bestandsgespräch nach dem Update einmal „Sitzung neu aufgebaut",
           * und zwar beim Gegenüber. Stattdessen bekommt sie den aktuellen
           * Abdruck; war der Schlüssel wirklich schon gewechselt, merkt es die
           * Gegenseite beim Entschlüsseln und baut von sich aus neu auf.
           */

          // `sendingChainKey === null` heisst: dieser Zustand hat noch nie
          // etwas entschlüsselt und kann deshalb nicht senden — so kommt jeder
          // Empfängerzustand aus `initReceiverState`. Im Normalfall ist das
          // eine Momentaufnahme: die erste eingehende Nachricht dreht den
          // Ratchet und legt die Sendekette an. Bleibt er so liegen, weil ein
          // zweiter Sitzungsaufbau den arbeitenden Zustand ersetzt hat, wäre
          // dieses Gerät dauerhaft stumm und käme aus eigener Kraft nie wieder
          // heraus. Deshalb zählt er hier wie „keine Sitzung".
          if (!arbeitszustand || arbeitszustand.sendingChainKey === null) {
            const begonnen = await beginneSitzung(
              kontext,
              meins,
              zielGeraet,
              ziel.geraet.public_key,
            )
            bootstrap = begonnen.umschlag
            arbeitszustand = begonnen.zustand
            selbstErzeugt = true
          }

          if (!arbeitszustand) throw new Error('Sitzung verschwunden')
          const { nextState, message } = await encryptMessage(
            arbeitszustand,
            utf8ToBytes(klartext),
          )
          // Ein gerade erzeugter Anfangszustand kam nicht aus der Ablage;
          // `schritt` räumt nur den geladenen ab.
          if (selbstErzeugt) destroyRatchetState(arbeitszustand)

          const clientUuid = `${basisUuid}#${zielGeraet.slice(0, KENNUNG_KURZ)}`
          const bootstrapClientUuid = `${basisUuid}#i${zielGeraet.slice(0, KENNUNG_KURZ)}`
          const umschlag = baueUmschlag({
            vonKonto: kontext.eigeneId,
            vonGeraet: meins.kennung,
            fuerGeraet: zielGeraet,
            rumpf: bytesToBase64(utf8ToBytes(serializeRatchetMessage(message))),
          })
          const zielInbox = ziel.geraet.inbox_hash || null
          /*
           * **Beide Bytes und die Sitzung gehören in eine Transaktion.**
           *
           * Bis 10/2026 entstand der Umschlag nur im Arbeitsspeicher: brach
           * die App zwischen dem Schreiben des Nachfolgers und dem Absenden
           * ab, war die Kettenposition verbraucht und der Umschlag weg — eine
           * Nachricht, die niemand mehr senden kann. Jetzt liegt er, bevor
           * irgendetwas hinausgeht, unter `versand:<clientUuid>`.
           */
          const versand: AusstehendeZustellung[] = []
          const angelegtAm = new Date().toISOString()
          if (bootstrap && mailbox) {
            versand.push({
              clientUuid: bootstrapClientUuid,
              eigeneId: kontext.eigeneId,
              peerId: kontext.peerId,
              mailbox,
              ciphertext: bootstrap,
              isControl: true,
              controlType: 'dr-init',
              zielInbox,
              angelegtAm,
              reihenfolge: 0,
            })
          }
          if (mailbox) {
            versand.push({
              clientUuid,
              eigeneId: kontext.eigeneId,
              peerId: kontext.peerId,
              mailbox,
              ciphertext: umschlag,
              zielInbox,
              angelegtAm,
              reihenfolge: 1,
            })
          }

          return {
            naechster: nextState,
            // Die Bindung gehört zum Zustand, in derselben Transaktion.
            bindung,
            versand,
            ergebnis: { bootstrap, nachricht: umschlag },
          }
        },
      )

      zustellungen.push({
        empfaengerId: kontext.peerId,
        zielGeraet,
        bootstrap: gebaut.bootstrap,
        nachricht: gebaut.nachricht,
        clientUuid: `${basisUuid}#${zielGeraet.slice(0, KENNUNG_KURZ)}`,
        bootstrapClientUuid: `${basisUuid}#i${zielGeraet.slice(0, KENNUNG_KURZ)}`,
        zielInbox: ziel.geraet.inbox_hash || null,
      })
    } catch (fehler) {
      // Ein Gerät, für das sich kein Umschlag bauen lässt, hält die übrigen
      // nicht auf. Der Mensch bekommt seine Nachricht auf den anderen.
      gescheitert.push(fehler)
    }
  }

  // Kein einziges Ziel hat funktioniert. Das als leere Liste zurückzugeben sähe
  // für den Aufrufer genauso aus wie „die Gegenstelle hat kein Gerät
  // angemeldet" — und genau das stand dann im Fehlertext, obwohl die Geräte da
  // waren und nur das Verschlüsseln scheiterte. Eine Verschlüsselung, die
  // stillschweigend nichts liefert, ist schlimmer als eine, die scheitert.
  if (zustellungen.length === 0 && ziele.length > 0) {
    throw new DrZustellungFehlgeschlagenError(gescheitert)
  }

  return zustellungen
}

/**
 * Die eigenen Geräte — und die Gewissheit, dass dieses darunter ist.
 *
 * `geraetVeroeffentlichen` meldet sich je Sitzung einmal. Wird das Gerät
 * danach in der Geräteliste entfernt, bleibt dieser Tab offen und sendete
 * weiter Aufbauten, die drüben niemand prüfen kann: sie blieben als
 * „unbekannt" zurückgestellt, bis sie aus dem Fenster rutschen. Sich neu
 * einzutragen wäre die bequeme Antwort und nähme dem Entfernen, was der
 * Dialog verspricht — also `DrGeraetNichtEingetragenError`.
 *
 * Frisch gefragt, weil eine zehn Minuten alte Liste das Gerät noch führen
 * kann, wenn es längst entfernt ist; gedrosselt ist das über `FRISCH_MS`.
 * Fehlt es, wird noch einmal ohne Zwischenspeicher nachgesehen: eine Liste
 * von vor der eigenen Anmeldung ist kein Befund. Keine Antwort auch nicht —
 * ob das Relais erreichbar ist, entscheidet der Versand selbst.
 */
async function eigenesVerzeichnis(
  eigeneId: number,
  kennung: string,
  publicKeyJwk: string,
): Promise<E2eeGeraetItem[]> {
  const eigenes = { kennung, publicKeyJwk }
  const fuehrt = (liste: E2eeGeraetItem[]) => liste.some((g) => g.device_id === kennung)
  try {
    // **Mit wartenden Geräten.** Ein Gerät, das seinen Signaturschlüssel
    // nachgerüstet hat, steht serverseitig auf „wartend", bis ein anderes es
    // beglaubigt. Es ist aber nicht entfernt — und der Unterschied entscheidet
    // hier über Senden oder Schweigen. `DrGeraetNichtEingetragenError` gilt nur
    // noch, wenn die Zeile wirklich fehlt (Entfernen, Zurücksetzen).
    const liste = await verzeichnisVon(eigeneId, {
      frisch: true,
      mitWartenden: true,
      eigenes,
    })
    if (fuehrt(liste)) return liste
    vergessenGeraete(eigeneId)
    const nochmal = await verzeichnisVon(eigeneId, {
      frisch: true,
      mitWartenden: true,
      eigenes,
    })
    if (fuehrt(nochmal)) return nochmal
  } catch {
    // Keine Antwort ist kein Befund. Ob das Relais erreichbar ist, entscheidet
    // der Versand selbst — hier wird nur entschieden, ob dieses Gerät aus der
    // Liste **entfernt** wurde, und das sagt ein Netzfehler nicht.
    return await geraeteVon(eigeneId)
  }
  throw new DrGeraetNichtEingetragenError()
}
