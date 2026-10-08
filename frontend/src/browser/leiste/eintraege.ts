/**
 * Welche Einträge (Module, Lesezeichen, Verlauf, Downloads) zu sehen sind und
 * in welcher Reihenfolge. Leiste, Menü und die Symbole oben lesen dieselbe
 * Liste, damit es eine Wahrheit gibt und nicht drei.
 */
import { useLocation, useNavigate } from 'react-router-dom'

import { useHasPermission } from '@/hooks/useHasPermission'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import { usePublicSettingsStore } from '@/stores/publicSettingsStore'

import { useDownloadsStore } from '../services/downloadsStore'
import { useEinstellungenStore, type Leistenziel } from '../services/einstellungenStore'
import { interneSeite } from '../services/intern'
import { istGekoppelt, useSitzung } from '../services/sitzung'
import { useTabsStore } from '../services/tabsStore'
import { BROWSER_EINTRAEGE, MSM_EINTRAEGE, panelAusPfad, type Leisteneintrag, type PanelId } from './module'

/** Unter diesem Namen steht ein Eintrag in `anordnung` und `ausgeblendet`. */
export function leistenziel(e: Leisteneintrag): Leistenziel {
  return e.modul ?? (e.id as Leistenziel)
}

/** Alle Einträge in der Grundordnung: erst MSM, dann der Browser. */
export const ALLE_EINTRAEGE: Leisteneintrag[] = [...MSM_EINTRAEGE, ...BROWSER_EINTRAEGE]

/** Ordnet nach `anordnung`; was dort fehlt (neu dazugekommen), folgt in der Grundordnung. */
export function geordnet(eintraege: Leisteneintrag[], anordnung: Leistenziel[]): Leisteneintrag[] {
  const rang = (e: Leisteneintrag) => {
    const i = anordnung.indexOf(leistenziel(e))
    return i < 0 ? anordnung.length + ALLE_EINTRAEGE.indexOf(e) : i
  }
  return [...eintraege].sort((a, b) => rang(a) - rang(b))
}

/** Welche MSM-Module gekoppelt sichtbar sind; dieselben Schalter wie in MSS. */
export function useSichtbareModule(): Set<PanelId> {
  const gekoppelt = istGekoppelt(useSitzung((s) => s.stand))
  const ausgeblendet = useEinstellungenStore((s) => s.ausgeblendet)
  const einstellungen = usePublicSettingsStore()
  // Rechteabfragen immer, nie hinter einer Bedingung (AGENTS.md Punkt 26).
  const chat = useHasPermission('ai.chat.use')
  const kalender = useHasPermission('ai.calendar.use')
  const notizen = useHasPermission('ai.notes.use')

  const sichtbar = new Set<PanelId>()
  for (const e of MSM_EINTRAEGE) {
    if (e.modul && ausgeblendet.includes(e.modul)) continue
    if (gekoppelt) {
      if (e.id === 'ai' && !chat) continue
      if (e.id === 'chat' && !einstellungen.social_enabled) continue
      if (e.id === 'kalender' && !(einstellungen.calendar_enabled && kalender)) continue
      if (e.id === 'notizen' && !(einstellungen.notes_enabled && notizen)) continue
      if (e.id === 'tresor' && !einstellungen.vault_enabled) continue
    }
    sichtbar.add(e.id)
  }
  return sichtbar
}

/** Die sichtbaren Einträge in der Reihenfolge des Nutzers. */
export function useLeistenEintraege(): Leisteneintrag[] {
  const sichtbar = useSichtbareModule()
  const ausgeblendet = useEinstellungenStore((s) => s.ausgeblendet)
  const anordnung = useEinstellungenStore((s) => s.anordnung)
  const da = ALLE_EINTRAEGE.filter((e) => (e.modul ? sichtbar.has(e.id) : !ausgeblendet.includes(leistenziel(e))))
  return geordnet(da, anordnung)
}

/** Zahlen an den Einträgen: ungelesene Nachrichten, neue Downloads. */
export function useLeistenZahlen(): Partial<Record<PanelId, number>> {
  const gekoppelt = istGekoppelt(useSitzung((s) => s.stand))
  const ungelesen = useMessengerNotificationStore((s) => s.totalUnreadCount)
  const downloads = useDownloadsStore((s) => s.neu)
  return { chat: gekoppelt ? ungelesen : 0, downloads }
}

/** Das offene Panel, ob die Einstellungen vorne sind, und wie man beides öffnet. */
export function useLeistenZiele() {
  const navigate = useNavigate()
  const offen = panelAusPfad(useLocation().pathname)
  const einstellungenOffen = useTabsStore((s) => interneSeite(s.tabs.find((t) => t.id === s.aktivId)?.url ?? '')?.seite === 'einstellungen')
  const einstellungen = useTabsStore((s) => s.einstellungen)
  return {
    offen,
    einstellungenOffen,
    /** Öffnet das Panel; ist es schon offen, schließt es. */
    panel: (id: PanelId) => navigate(offen === id ? '/' : `/${id}`),
    einstellungen: () => einstellungen(),
  }
}
