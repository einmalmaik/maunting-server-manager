/**
 * Das @-Abzeichen erscheint, ohne dass man den Chat öffnet.
 *
 * ## Warum es diese Komponente überhaupt braucht
 *
 * Der Server kennt den Inhalt einer Nachricht nicht und kann deshalb nicht
 * melden „du wurdest erwähnt". Eine Erwähnung wird erkannt, sobald **ein
 * Gerät** die Nachricht entschlüsselt hat. Ohne Wache hieße das: erst beim
 * Öffnen der Gruppe — also genau dann nicht, wenn es nützlich wäre.
 *
 * ## Warum sie den Lesepfad nicht nachbaut
 *
 * Entschlüsseln heißt Gruppenschlüssel, Schlüsselzustellungen, Nachforderung,
 * Verbrauchsmarken. Das steht vollständig in `useKonversation`, und eine
 * zweite Fassung davon wäre binnen eines Monats eine andere Fassung. Statt
 * dessen bekommt **jede** beobachtete Gruppe ihre eigene kleine Komponente mit
 * genau diesem Hook. React erlaubt keine Hooks in Schleifen — eine Komponente
 * je Gruppe ist der idiomatische Weg dahin.
 *
 * ## Was sie kostet
 *
 * Nichts im Leerlauf: gelesen wird nur, wenn für diese Mailbox ein
 * `msm:sync-event` hereinkommt. Die aktive Gruppe ist ausgenommen, die liest
 * der Messenger selbst. Bei gesperrtem Messenger ruht alles — ohne Schlüssel
 * gibt es nichts zu lesen, und ein Fehlversuch verbrennt Ratchet-Zustand.
 */

import { useEffect, useRef, type MutableRefObject } from 'react'

import type { ChatGroupItem } from '@/api/social'
import { useKonversation } from '@/hooks/useKonversation'
import { binIchGemeint } from '@/services/erwaehnungen'
import { istOffen, siegelAktiv } from '@/services/lokaleVersiegelung'
import type { E2eeIdentity } from '@/services/e2eeIdentity'
import { STEUERTYPEN } from '@/services/nachrichtBezug'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

interface WacheProps {
  gruppe: ChatGroupItem
  eigeneId: number
  identitaetRef: MutableRefObject<E2eeIdentity>
  /** Wird mit der Mailbox-Kennung gerufen, sobald dort eine Erwähnung liegt. */
  onErwaehnung: (blindMailboxId: string) => void
}

function GruppenWache({ gruppe, eigeneId, identitaetRef, onErwaehnung }: WacheProps) {
  const konversation = useKonversation({
    ziel: {
      art: 'gruppe',
      groupId: gruppe.id,
      mitglieder: (gruppe.members || []).map((m) => m.user_id),
    },
    eigeneId,
    identitaetRef,
    // Ein Sitzungsbruch gehört in das Gespräch, in dem er auftritt, und wird
    // dort gemeldet. Die Wache schweigt: eine Systemzeile in einem Chat, den
    // gerade niemand ansieht, wäre eine Meldung ins Leere.
    meldeSitzungsbruch: () => {},
  })

  const mid = konversation.blindMailboxId
  const gruppeRef = useRef(gruppe)
  gruppeRef.current = gruppe

  useEffect(() => {
    if (!mid || !eigeneId) return
    let lebt = true

    const pruefe = async () => {
      if (!lebt) return
      if (siegelAktiv() && !istOffen()) return
      const gelesen = await konversation.liesUmschlaege().catch(() => null)
      if (!lebt || !gelesen) return

      for (const lesung of gelesen) {
        if (lesung.art !== 'klartext' || !lesung.text) continue
        let paket: Record<string, unknown>
        try {
          paket = JSON.parse(lesung.text)
        } catch {
          continue
        }
        if (!paket || typeof paket !== 'object') continue
        if (typeof paket.type === 'string' && STEUERTYPEN.has(paket.type)) continue

        const senderId = Number(paket.sender_id || 0)
        if (!senderId || senderId === eigeneId) continue
        const gemeint = binIchGemeint(
          {
            senderId,
            erwaehnungen: Array.isArray(paket.erwaehnungen) ? (paket.erwaehnungen as number[]) : undefined,
            erwaehntAlle: Boolean(paket.erwaehnt_alle),
          },
          eigeneId,
          gruppeRef.current,
        )
        if (gemeint) {
          onErwaehnung(mid)
          return
        }
      }
    }

    /**
     * Zwei Auslöser, und beide sind bewusst schmal.
     *
     * `msm:mailbox-neu` meldet, dass in genau dieser Mailbox etwas Neues liegt.
     * Es kommt **vor** der Stummschaltung — der Ungelesen-Zähler allein reichte
     * nicht, denn in einer stummen Gruppe steigt er nie, und dann erschiene
     * nicht einmal das @-Abzeichen. Genau das war bis zum 20.09.2026 der Fall.
     *
     * Der Zähler bleibt als zweiter Weg: er deckt die Fälle ab, in denen die
     * Zählung von anderswo kommt. `msm:sync-event` wäre der falsche Draht — es
     * trägt auch Anrufe, Präsenz und Servermeldungen, und darauf zu lesen hieße,
     * bei jedem Tastendruck eines Fremden eine fremde Mailbox zu entschlüsseln.
     */
    const aufNeue = (e: Event) => {
      const ce = e as CustomEvent<{ mid?: string }>
      if (ce.detail?.mid === mid) void pruefe()
    }
    window.addEventListener('msm:mailbox-neu', aufNeue)

    let letzterStand = useMessengerNotificationStore.getState().unreadCounts[mid] || 0
    const abbestellen = useMessengerNotificationStore.subscribe((zustand) => {
      const jetzt = zustand.unreadCounts[mid] || 0
      if (jetzt > letzterStand) void pruefe()
      letzterStand = jetzt
    })

    // Einmal beim Anhängen, damit ein Abzeichen auch dann erscheint, wenn die
    // Nachricht ankam, während die Seite geschlossen war.
    void pruefe()

    return () => {
      lebt = false
      window.removeEventListener('msm:mailbox-neu', aufNeue)
      abbestellen()
    }
  }, [mid, eigeneId, konversation, onErwaehnung])

  return null
}

export interface ErwaehnungsWacheProps {
  gruppen: readonly ChatGroupItem[]
  /** Die gerade geöffnete Mailbox — die liest der Messenger selbst. */
  aktiveMailboxId: string | null
  eigeneId: number
  identitaetRef: MutableRefObject<E2eeIdentity>
  onErwaehnung: (blindMailboxId: string) => void
  /** Aus, solange der Messenger gesperrt oder die Seite nicht sichtbar ist. */
  aktiv: boolean
}

/**
 * Beobachtet die Gruppen, die gerade **nicht** offen sind.
 *
 * Gruppen ohne geladene Mitgliederliste bleiben außen vor: ohne sie ließe sich
 * weder ein Gruppenschlüssel bilden noch eine Erwähnung auflösen.
 */
export function ErwaehnungsWache({
  gruppen,
  aktiveMailboxId,
  eigeneId,
  identitaetRef,
  onErwaehnung,
  aktiv,
}: ErwaehnungsWacheProps) {
  if (!aktiv || !eigeneId) return null
  return (
    <>
      {gruppen
        .filter((g) => (g.members?.length || 0) > 0)
        .map((g) => (
          <GruppenWache
            key={g.id}
            gruppe={g}
            eigeneId={eigeneId}
            identitaetRef={identitaetRef}
            onErwaehnung={(mid) => {
              if (mid === aktiveMailboxId) return
              onErwaehnung(mid)
            }}
          />
        ))}
    </>
  )
}
