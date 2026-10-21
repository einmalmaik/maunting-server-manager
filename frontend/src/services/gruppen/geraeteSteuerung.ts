/**
 * Die eigene Geraete-Mailbox: Schluesselzustellungen und Chatgeheimnisse.
 *
 * Der Lesestand liegt auf der Platte und wandert nur vorwaerts. Sonst laese ein
 * Geraet nach jedem Neustart wieder die aeltesten 200 Umschlaege und bekaeme die
 * Zustellung dahinter nie zu sehen.
 */

import { fetchAlleE2eeEnvelopes } from '@/api/social'
import { MESSENGER_KEYS, getStorageItem, setStorageItem } from '@/lib/offlineAblage'
import {
  E2EE_HYBRID_ENVELOPE_SPEC,
  deriveGroupBlindMailboxId,
  deriveUserDeviceMailboxId,
} from '../e2eeCrypto'
import {
  DM_ANFRAGE_TYP,
  DM_GEHEIMNIS_TYP,
  beantworteDmAnfrage,
  leereDmAnfragen,
  nimmDmGeheimnis,
} from './dmGeheimnis'
import { leereAntwortSperren, verarbeiteGruppenSteuerung } from './gruppenSteuerung'
import { normalisiereMitglieder, type GruppenKontext } from './typen'

/**
 * Wie weit die eigene Geräte-Mailbox schon gelesen ist, je Konto.
 *
 * **Auf der Platte, nicht nur im Arbeitsspeicher.** Bis 10/2026 fiel der Stand
 * bei jedem Neuladen auf 0 zurück. Das war nicht bloß eine Wiederholung: der
 * Server gibt höchstens 200 Umschläge je Abruf heraus, und wer die ältesten
 * 200 schon verarbeitet hat, bekam beim nächsten Start wieder genau die und
 * damit nie die Schlüsselzustellung, die inzwischen dahinter lag. Ein Gerät,
 * das eine Weile geschlossen war, blieb so ohne Gruppenschlüssel und ohne
 * Chatgeheimnis.
 *
 * Die Ablage ist kontogebunden (`offlineAblage`); ein Kontowechsel im selben
 * Tab erbt den Stand des vorigen nicht. Der Wert ist keine geheime Nachricht,
 * sondern eine Umschlagkennung — er verrät nur, wie weit dieses Konto gelesen
 * hat.
 */
const geraeteStand = new Map<number, number>()

function standLesen(eigeneId: number): number {
  const imSpeicher = geraeteStand.get(eigeneId) ?? 0
  const roh = Number(getStorageItem(MESSENGER_KEYS.GERAETE_STAND) ?? 0)
  // **Immer der höhere Stand.** Ein zweiter Tab darf den Cursor nicht
  // zurücksetzen, nur weil er selbst noch nicht so weit gelesen hat. Sonst
  // überspränge der nächste Start genau die Umschläge, die der andere Tab
  // schon verarbeitet hat — und der eigene Cursor fiele hinter sie zurück.
  const stand = Math.max(imSpeicher, Number.isFinite(roh) && roh > 0 ? roh : 0)
  geraeteStand.set(eigeneId, stand)
  return stand
}

function standMerken(eigeneId: number, stand: number): void {
  if (!Number.isFinite(stand) || stand <= 0) return
  if (stand <= standLesen(eigeneId)) return
  geraeteStand.set(eigeneId, stand)
  setStorageItem(MESSENGER_KEYS.GERAETE_STAND, String(stand))
}

/**
 * Vergisst den Lesestand im Arbeitsspeicher und die Antwortsperren.
 *
 * Die **Ablage** bleibt: der Stand ist der Fortschritt dieses Kontos und soll
 * ein Neuladen überleben. Beim Abmelden fällt er mit der ganzen
 * kontogebundenen Ablage (`leereOfflineAblage`). Wer den Messenger nur
 * zurücksetzt, ruft beides.
 */
export function leereGeraeteStand(): void {
  geraeteStand.clear()
  leereDmAnfragen()
  leereAntwortSperren()
}

/**
 * Liest die Steuerumschläge aus der eigenen Geräte-Mailbox.
 *
 * Der Weg, auf dem ein Gruppenschlüssel heute ankommt. Er hängt an keiner
 * Gruppe: ein Gerät, das von einer Gruppe noch gar nichts weiss, bekommt hier
 * ihren Schlüssel und ihr Geheimnis — und erst damit kann es ihre Mailbox
 * öffnen. Genau diese Reihenfolge fehlte, solange die Zustellung durch die
 * Gruppenmailbox lief.
 *
 * Der Gruppenkontext entsteht aus der Nutzlast selbst, denn beim Lesen ist
 * noch nicht bekannt, um welche Gruppe es geht. Das ist keine Lücke: der
 * Umschlag war hybrid für dieses Gerät versiegelt, und was er setzen kann,
 * entscheidet `verarbeiteGruppenSteuerung` — ein Schlüssel wird abgelegt, aber
 * verdrängt keinen, und ein Geheimnis kommt nur an, wo noch keines ist.
 *
 * Auf demselben Weg kommt das **Chatgeheimnis** eines Direktchats an. Es hat
 * keine `groupId` und biegt darum früher ab.
 *
 * `entsiegle` kommt von aussen, damit diese Datei den Schlüsselbund des
 * Geräts nicht kennen muss. Wirft sie, war der Umschlag für ein anderes Gerät
 * — der Normalfall, kein Fehler.
 */
export async function holeGeraeteSteuerung(
  eigeneId: number,
  entsiegle: (umschlag: string) => Promise<string>,
  empfangeSteuerung?: (klartext: string, env: { id: number; created_at: string }) => void,
): Promise<number> {
  if (!Number.isInteger(eigeneId) || eigeneId <= 0) return 0

  const mid = await deriveUserDeviceMailboxId(eigeneId)
  const seit = standLesen(eigeneId)

  let umschlaege: { id: number; ciphertext_envelope: string; created_at: string }[]
  try {
    // **Alle Seiten, nicht nur die erste.** Das Limit von 200 lag vorher genau
    // zwischen dem Gerät und seinem Schlüssel. `since_id` ist der zuletzt
    // vollständig gelesene Stand aus der Ablage, nicht 0 nach jedem Neustart.
    umschlaege = await fetchAlleE2eeEnvelopes(mid, seit)
  } catch {
    // Kein Netz oder ein Server, der diese Mailbox nicht kennt. Der nächste
    // Durchlauf holt es nach; der Stand bleibt stehen.
    return 0
  }

  let verarbeitet = 0
  // Ein Schreibvorgang je Durchlauf, nicht je Umschlag: die Ablage ist
  // localStorage, und ein Schwung von zweihundert Schreibern hielte den
  // Lesepfad an. Was zählt, ist der höchste **verarbeitete** Umschlag.
  let stand = seit
  // Ab dem ersten unerwarteten Fehler bleibt der Stand davor stehen. Alles
  // danach wird beim nächsten Durchlauf wiederholt — die Zustellungen sind
  // idempotent, und ein Überspringen wäre der einzige wirkliche Verlust.
  let angehalten = false
  for (const env of umschlaege) {
    try {
      if (!env.ciphertext_envelope.startsWith(E2EE_HYBRID_ENVELOPE_SPEC.currentPrefix)) {
        if (!angehalten) stand = env.id
        continue
      }

      let klartext: string
      try {
        klartext = await entsiegle(env.ciphertext_envelope)
      } catch {
        // Für ein anderes Gerät desselben Kontos.
        if (!angehalten) stand = env.id
        continue
      }

      let roh: Record<string, unknown>
      try {
        const geparst = JSON.parse(klartext)
        if (!geparst || typeof geparst !== 'object') {
          if (!angehalten) stand = env.id
          continue
        }
        roh = geparst as Record<string, unknown>
      } catch {
        // Kein JSON — gehört einem anderen Nutzer dieser Mailbox
        // (Notizen, Kalender) und nicht hierher.
        if (!angehalten) stand = env.id
        continue
      }

      // Ein Geschwistergerät hat keines und bittet darum. Die Antwort geht
      // zurück in dieselbe Mailbox, an genau das fragende Gerät.
      if (roh.typ === DM_ANFRAGE_TYP) {
        try {
          if (await beantworteDmAnfrage(eigeneId, roh)) verarbeitet += 1
        } catch {
          // Eine Anfrage hält die übrigen Zustellungen nicht auf.
        }
        if (!angehalten) stand = env.id
        continue
      }

      // Ein Chatgeheimnis kennt keine Gruppe. Es muss vor der `groupId`-Prüfung
      // abbiegen, sonst fiele es durch — der Fehler wäre stumm und der
      // Direktchat bliebe für immer auf der alten Mailbox.
      if (roh.typ === DM_GEHEIMNIS_TYP) {
        try {
          if (await nimmDmGeheimnis(eigeneId, roh)) verarbeitet += 1
        } catch {
          // Wie unten: eine Zustellung hält die übrigen nicht auf.
        }
        if (!angehalten) stand = env.id
        continue
      }

      const groupId = Number(roh.groupId)
      const mitglieder = normalisiereMitglieder(Array.isArray(roh.mitglieder) ? roh.mitglieder : [])
      if (!Number.isInteger(groupId) || groupId <= 0) {
        // **Kein Gruppenschlüssel, aber vielleicht eine Wirkung aus einem
        // anderen eigenen Gerät.** Quittung, Bearbeitung, Löschung, Reaktion
        // und Verfallsfrist reisen verschlüsselt hierher; der Aufrufer
        // entscheidet an `mailbox`, ob sie zu seinem offenen Gespräch gehören.
        // Der Umschlag selbst bleibt für ihn ungeöffnet, wenn er ein anderes
        // Ziel trägt.
        if (empfangeSteuerung && typeof roh.type === 'string') {
          try {
            empfangeSteuerung(klartext, { id: env.id, created_at: env.created_at })
          } catch {
            // Eine fehlerhafte Wirkung hält die übrigen nicht auf.
          }
        }
        if (!angehalten) stand = env.id
        continue
      }

      if (!mitglieder.includes(eigeneId)) mitglieder.push(eigeneId)
      const kontext: GruppenKontext = {
        groupId,
        blindMailboxId: await deriveGroupBlindMailboxId(groupId),
        eigeneId,
        mitglieder,
        // Aus einem eingehenden Umschlag wird niemand Eigentümer. Das Flag
        // entscheidet allein, ob dieses Gerät ein Geheimnis *erzeugen* darf —
        // und das darf es beim Empfangen unter keinen Umständen.
        istEigentuemer: false,
      }

      try {
        const ergebnis = await verarbeiteGruppenSteuerung(kontext, klartext)
        if (ergebnis.art !== 'keine') verarbeitet += 1
      } catch {
        // Eine Zustellung, die sich nicht verarbeiten lässt, hält die übrigen
        // nicht auf.
      }
      if (!angehalten) stand = env.id
    } catch {
      // Auch ein unerwarteter Fehler bei einem Umschlag darf die folgenden
      // nicht anhalten. Der Stand bleibt für diesen stehen und holt ihn nach.
      angehalten = true
    }
  }
  standMerken(eigeneId, stand)
  return verarbeitet
}
