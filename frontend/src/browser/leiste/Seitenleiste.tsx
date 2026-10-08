/**
 * Die schmale Leiste links: MSM-Module, dann Lesezeichen, Verlauf, Downloads,
 * unten die Einstellungen. Ein Klick öffnet das Panel, ein zweiter schließt es.
 *
 * Ohne Kopplung stehen die MSM-Module trotzdem da und zeigen, wie man koppelt.
 * Gekoppelt fällt weg, was das Panel abgeschaltet hat oder wofür das Konto
 * kein Recht hat, wie in MSS.
 */
import { useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { useHasPermission } from '@/hooks/useHasPermission'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import { usePublicSettingsStore } from '@/stores/publicSettingsStore'

import { useDownloadsStore } from '../services/downloadsStore'
import { useEinstellungenStore } from '../services/einstellungenStore'
import { istGekoppelt, useSitzung } from '../services/sitzung'
import { useLeistenname, useLeistennameAufraeumen } from './leistenname'
import { BROWSER_EINTRAEGE, EINSTELLUNGEN_EINTRAG, KOPPELN_EINTRAG, MSM_EINTRAEGE, panelAusPfad, type Leisteneintrag, type PanelId } from './module'

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

/**
 * Nur das Symbol; den Namen sagt `aria-label`, für Maus und Tastatur eine
 * Blase rechts daneben, die Windows über die Seite zeichnet (`leistenname.ts`).
 * Der offene Eintrag trägt einen Strich am linken Rand.
 */
function Eintrag({ eintrag, offen, zahl }: { eintrag: Leisteneintrag; offen: boolean; zahl?: number }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const Symbol = eintrag.symbol
  const name = t(eintrag.name)
  const namen = useLeistenname(name)
  return (
    <button
      type="button"
      onClick={() => navigate(offen ? '/' : `/${eintrag.id}`)}
      aria-label={zahl ? t('browser.leiste.mitZahl', { name, zahl }) : name}
      aria-pressed={offen}
      {...namen}
      className={`relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition-colors before:absolute before:-left-2 before:top-2.5 before:bottom-2.5 before:w-[3px] before:rounded-r-full before:transition-opacity ${
        offen
          ? 'bg-primary/15 text-primary before:bg-primary before:opacity-100'
          : 'text-on-surface-variant before:opacity-0 hover:bg-surface-container-high hover:text-on-surface'
      }`}
    >
      <Symbol className="h-5 w-5" aria-hidden="true" />
      {!!zahl && (
        <span aria-hidden="true" className="absolute -right-1 -top-1 min-w-[1.125rem] rounded-full bg-primary px-1 text-center text-label-sm leading-[1.125rem] text-on-primary ring-2 ring-surface-container-lowest">
          {zahl > 99 ? '99+' : zahl}
        </span>
      )}
    </button>
  )
}

export function Seitenleiste() {
  const { t } = useTranslation()
  const offen = panelAusPfad(useLocation().pathname)
  const stand = useSitzung((s) => s.stand)
  const sichtbar = useSichtbareModule()
  const ungelesen = useMessengerNotificationStore((s) => s.totalUnreadCount)
  const neueDownloads = useDownloadsStore((s) => s.neu)
  const msm = MSM_EINTRAEGE.filter((e) => sichtbar.has(e.id))
  useLeistennameAufraeumen()

  return (
    <nav aria-label={t('browser.leiste.name')} className="flex w-14 shrink-0 flex-col items-center gap-1 overflow-y-auto border-r border-outline-variant bg-surface-container-lowest py-2 msm-ohne-rollbalken">
      {msm.map((e) => (
        <Eintrag key={e.id} eintrag={e} offen={offen === e.id} zahl={e.id === 'chat' && istGekoppelt(stand) ? ungelesen : undefined} />
      ))}
      {msm.length > 0 && <div className="my-1 h-px w-6 shrink-0 bg-outline-variant" />}
      {BROWSER_EINTRAEGE.map((e) => (
        <Eintrag key={e.id} eintrag={e} offen={offen === e.id} zahl={e.id === 'downloads' ? neueDownloads : undefined} />
      ))}
      <div className="flex-1" />
      {!istGekoppelt(stand) && stand !== 'pruefen' && (
        <Eintrag eintrag={KOPPELN_EINTRAG} offen={offen === 'koppeln'} />
      )}
      <Eintrag eintrag={EINSTELLUNGEN_EINTRAG} offen={offen === 'einstellungen'} />
    </nav>
  )
}
