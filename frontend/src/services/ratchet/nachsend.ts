/**
 * Was liegen blieb, geht beim nächsten Lauf noch einmal hinaus.
 *
 * Der Sendepfad legt den fertigen Umschlag **zusammen mit dem Ratchet-Zustand**
 * in der Ablage ab (`versand:<clientUuid>`). Bis hierher gab es dafür keinen
 * Leser: brach die App zwischen diesem Commit und der HTTP-Antwort ab, lag die
 * Nachricht zwar als Absicht da, wurde aber nie wieder angefasst — und die
 * Kettenposition war verbraucht. Der Angstgegner ist nicht der Fehler, sondern
 * der stille Verlust.
 *
 * **Dieselben Bytes, dieselbe Kennung.** Ein erneuter Lauf verschlüsselt nichts
 * neu. Das wäre nicht nur ein zweiter Ratchet-Schritt, sondern ergäbe auch
 * einen Umschlag, den die Gegenstelle nicht mehr einordnen kann. Für die
 * Geräte-Inbox liegen die Kennungen (`payload_id`, `delivery_id`) schon vor dem
 * ersten Aufruf in der Absicht; der Server erkennt die Wiederholung an ihnen.
 *
 * **Aufgegeben wird nur, was endgültig ist.** Ein Netzwechsel, ein 5xx oder ein
 * 429 lassen die Absicht liegen; ein 400/404 sagt, dass dieser Auftrag nie
 * angenommen wird. Dann steht die Zeile im Verlauf sichtbar auf „gescheitert",
 * und der Mensch kann sie erneut senden — kein stilles Verschlucken.
 */

import { relayE2eeEnvelope } from '@/api/social'
import { logischeUuid } from './format'
import { updateMessageInLocalStore } from '../messengerLocalStore'
import {
  ausstehendeZustellungen,
  entferneZustellung,
  type AusstehendeZustellung,
} from '../ratchetSpeicher'

export interface NachsendErgebnis {
  /** In diesem Lauf nachweislich angenommen. */
  gesendet: number
  /** Wieder nicht durchgekommen — bleibt für den nächsten Lauf liegen. */
  offen: number
}

/** Der HTTP-Status eines Fehlers, sofern er einen trägt. */
function statusVon(fehler: unknown): number | null {
  const status = (fehler as { status?: unknown } | null)?.status
  return typeof status === 'number' ? status : null
}

/**
 * Endgültig heißt: derselbe Auftrag wird auch beim nächsten Mal abgelehnt.
 *
 * 429 und 5xx sind ausdrücklich **nicht** endgültig — sie sagen etwas über den
 * Server in diesem Moment, nichts über den Auftrag. Ein Netzfehler trägt gar
 * keinen Status.
 */
function istEndgueltig(fehler: unknown): boolean {
  const status = statusVon(fehler)
  return status === 400 || status === 404
}

/**
 * Macht aus einer endgültig abgelehnten Absicht eine sichtbare Zeile.
 *
 * Dieselbe Form wie die Offline-Outbox (`msm:message-failed`): der Verlauf
 * schreibt `failed` **vor** dem Ereignis, damit ein gleichzeitiges Neuladen
 * nicht den alten Stand zurückholt.
 */
async function aufgeben(absicht: AusstehendeZustellung, grund: string): Promise<void> {
  const kennung = logischeUuid(absicht.clientUuid) ?? absicht.clientUuid
  if (!absicht.isControl) {
    await updateMessageInLocalStore(absicht.mailbox, kennung, {
      status: 'failed',
    }).catch(() => {})
  }
  await entferneZustellung(absicht.clientUuid)
  if (typeof window !== 'undefined') {
    window.dispatchEvent(
      new CustomEvent('msm:message-failed', {
        detail: {
          client_uuid: kennung,
          blind_mailbox_id: absicht.mailbox,
          grund,
        },
      }),
    )
  }
}

/**
 * Trägt den Erfolg in die eigene Zeile nach.
 *
 * Nach einem Absturz hat der Sendepfad seinen Abschluss nie erreicht: die
 * Zeile stand weiter auf „wird gesendet", obwohl die Nachricht längst
 * angenommen war. Der Wiederaufnehmer schreibt deshalb `sent` (und die
 * Umschlagkennung, wenn es eine gibt) und meldet es der Oberfläche — dieselbe
 * Form wie die Offline-Outbox.
 */
async function bestaetigeLokal(absicht: AusstehendeZustellung, umschlagId?: number): Promise<void> {
  if (absicht.isControl) return
  const kennung = logischeUuid(absicht.clientUuid) ?? absicht.clientUuid
  await updateMessageInLocalStore(absicht.mailbox, kennung, {
    status: 'sent',
    ...(umschlagId && umschlagId > 0 ? { id: umschlagId } : {}),
  }).catch(() => {})
  if (typeof window !== 'undefined') {
    if (umschlagId && umschlagId > 0) {
      window.dispatchEvent(
        new CustomEvent('msm:message-confirmed', {
          detail: {
            client_uuid: kennung,
            envelope_id: umschlagId,
            blind_mailbox_id: absicht.mailbox,
          },
        }),
      )
    } else {
      // Eine Wiederholung trägt keine neue Kennung. Der Verlauf liest den
      // geschriebenen Status beim nächsten Durchgang aus der Ablage.
      window.dispatchEvent(new CustomEvent('msm:messages-updated'))
    }
  }
}

async function sendeEinen(
  absicht: AusstehendeZustellung,
): Promise<'gesendet' | 'offen' | 'aufgegeben'> {
  try {
    const r = await relayE2eeEnvelope({
      blind_mailbox_id: absicht.mailbox,
      ciphertext_envelope: absicht.ciphertext,
      client_uuid: absicht.clientUuid,
      is_control: absicht.isControl,
      control_type: absicht.controlType ?? null,
    })
    await bestaetigeLokal(absicht, typeof r?.id === 'number' ? r.id : undefined)
    await entferneZustellung(absicht.clientUuid)
    return 'gesendet'
  } catch (fehler) {
    if (istEndgueltig(fehler)) {
      await aufgeben(absicht, `http-${statusVon(fehler)}`)
      return 'aufgegeben'
    }
    return 'offen'
  }
}

/**
 * Arbeitet alle offenen Absichten dieses Kontos ab.
 *
 * Der Aufrufer ruft das beim Öffnen des Messengers, im Takt und bei `online`.
 * Ein Lauf schickt jede Absicht genau einmal; was scheitert, bleibt für den
 * nächsten. Zwei Tabs dürfen dasselbe senden — die Kennungen machen daraus
 * beim Server eine Wiederholung. Die Reihenfolge ist Anlage, und innerhalb
 * eines Schritts Aufbau vor Nachricht (`reihenfolge`), damit die Gegenstelle
 * eine Sitzung vorfindet, bevor die Nachricht für sie kommt.
 */
export async function sendeAusstehendeZustellungen(
  eigeneId: number,
): Promise<NachsendErgebnis> {
  if (!Number.isInteger(eigeneId) || eigeneId <= 0) return { gesendet: 0, offen: 0 }
  const eigene = await ausstehendeZustellungen(eigeneId)
  if (eigene.length === 0) return { gesendet: 0, offen: 0 }

  let gesendet = 0
  let weiterOffen = 0
  for (const absicht of eigene) {
    const ausgang = await sendeEinen(absicht)
    if (ausgang === 'gesendet') gesendet += 1
    else if (ausgang === 'offen') weiterOffen += 1
  }
  return { gesendet, offen: weiterOffen }
}
