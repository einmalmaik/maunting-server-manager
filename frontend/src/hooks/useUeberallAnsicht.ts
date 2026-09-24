/**
 * Die Ansicht über alle Chats: Suche, Markiertes oder „an mich".
 *
 * Dieselbe Durchsicht, drei Fragen. Bei gesetztem PIN kostet das Entsiegeln
 * Rechenzeit, deshalb läuft es asynchron mit sichtbarem „wird durchgesehen".
 * Bis 09/2026 stand das in `Messenger.tsx`.
 */

import { useState } from 'react'

import type { ChatGroupItem } from '@/api/social'
import { binIchGemeint } from '@/services/erwaehnungen'
import {
  sammleAnMich,
  sammleMarkierte,
  sucheUeberall,
  type ChatTreffer,
} from '@/services/verlaufSuche'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'

export type UeberallFrage = 'suche' | 'markiert' | 'anMich'

export function useUeberallAnsicht(currentUserId: number, groups: ChatGroupItem[]) {
  const [art, setArt] = useState<UeberallFrage | 'aus'>('aus')
  const [frage, setFrage] = useState('')
  const [chats, setChats] = useState<ChatTreffer[]>([])
  const [laeuft, setLaeuft] = useState(false)
  const [gesperrt, setGesperrt] = useState(false)

  const oeffne = async (welche: UeberallFrage, suchtext = '') => {
    setArt(welche)
    setFrage(suchtext)
    setChats([])
    setLaeuft(true)
    setGesperrt(false)
    try {
      const ergebnis =
        welche === 'suche'
          ? await sucheUeberall(suchtext)
          : welche === 'markiert'
            ? await sammleMarkierte()
            : await sammleAnMich(currentUserId, (m, mid) => {
                // Eine Antwort auf meine Nachricht zählt genauso wie eine
                // Erwähnung: beides heißt „hier werde ich gebraucht".
                const bezug = m.antwortAuf as { absenderId?: number } | undefined
                if (bezug?.absenderId && Number(bezug.absenderId) === Number(currentUserId)) return true
                // Dieselbe Empfängerprüfung wie im Verlauf: ob aus `@everyone`
                // eine Erwähnung wird, entscheidet das Recht des Absenders.
                const meta = useMessengerNotificationStore.getState().mailboxDirectory[mid]
                const gruppe = meta?.groupId ? groups.find((g) => g.id === meta.groupId) : null
                return binIchGemeint(m, currentUserId, gruppe ?? null)
              })
      setChats(ergebnis.chats)
      setGesperrt(ergebnis.gesperrt)
    } finally {
      setLaeuft(false)
    }
  }

  return { art, frage, chats, laeuft, gesperrt, oeffne, schliesse: () => setArt('aus') }
}
