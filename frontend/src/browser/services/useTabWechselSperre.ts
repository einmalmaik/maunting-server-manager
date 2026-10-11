/**
 * Sperrt Tresor und Messenger beim Wechsel des aktiven Tabs, falls die jeweilige
 * Sperre beim Verlassen eingeschaltet ist.
 *
 * Verwendet dieselbe Schutzentscheidung wie die automatische Sperre bei
 * Fenster- und Sichtwechseln (`sperreBeiVerlassen`), nicht eine eigene Kopie.
 * Erstes Mounten, erneutes Aktivieren desselben Tabs oder Hintergrundereignisse
 * lösen keine Sperre aus.
 */
import { useEffect, useRef } from 'react'

import { sperreBeiVerlassen } from '@/services/autoSperre'
import { tresorAutoSperrQuelle } from '@/desktop/vault/vaultStore'
import { messengerAutoSperrQuelle } from '@/services/messengerSperre'

import { useTabsStore } from './tabsStore'

export function useTabWechselSperre(): void {
  const aktivId = useTabsStore((s) => s.aktivId)
  const vorherId = useRef<string | null>(null)

  useEffect(() => {
    if (vorherId.current === null) {
      // Erstes Mounten: keine Tabwechsel-Sperre
      vorherId.current = aktivId
      return
    }

    if (vorherId.current === aktivId) {
      // Erneutes Aktivieren desselben Tabs: keine Tabwechsel-Sperre
      return
    }

    vorherId.current = aktivId

    // Tatsächlicher Wechsel des aktiven Tabs:
    sperreBeiVerlassen(tresorAutoSperrQuelle)
    sperreBeiVerlassen(messengerAutoSperrQuelle)
  }, [aktivId])
}
