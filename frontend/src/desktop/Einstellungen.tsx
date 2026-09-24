/**
 * Die Einstellungen der Desktop-App — dieselbe Formensprache wie die
 * Panel-Einstellungen: eine Reiterleiste oben (`TabBar`, dieselbe Komponente
 * wie `/settings` und `/profile` im Panel), darunter Karten. Eigene Inhalte:
 * was dieser **Rechner** tut, nicht was das Panel tut.
 *
 * Vier Reiter: Desktop-Integration (Autostart, Hotkeys, Diagnose), Wake-Word
 * (Kalibrierung, Aktiv-Schalter), Audio (Geräteauswahl, Ducking) und die
 * Gefahrenzone. `?tab=wakeword` wählt einen Reiter vor — der Weg des
 * Neukalibrierungs-Hinweises nach einer Umbenennung.
 *
 * Diese Datei wählt nur den Reiter; die Inhalte liegen je Reiter in
 * `einstellungsreiter/`.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'
import {
  AlertTriangle,
  FileSignature,
  Lock,
  Mic,
  MonitorCog,
  ShieldCheck,
  User,
  Users,
  Volume2,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { MessengerSicherheitTab } from '@/pages/profile/MessengerSicherheitTab'
import { TresorSicherheitTab } from './vault/TresorSicherheitTab'
import { usePublicSettingsStore } from '@/stores/publicSettingsStore'
import { Gefahrenzone } from './Gefahrenzone'
import { WakewordEinrichtung } from './WakewordEinrichtung'
import {
  AudioEinstellungen,
  DesktopIntegration,
  KontoEinstellungen,
  RechtlichesEinstellungen,
  SocialEinstellungen,
} from './einstellungsreiter'

type EinstellungsTab = 'konto' | 'social' | 'messenger' | 'tresor' | 'desktop' | 'wakeword' | 'audio' | 'rechtliches' | 'gefahr'

const isAndroidClient = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)

export function Einstellungen({ onKonfigAenderung }: { onKonfigAenderung?: () => void }) {
  const { t } = useTranslation()
  const ort = useLocation()
  const publicSettings = usePublicSettingsStore()

  const tabs: TabDef<EinstellungsTab>[] = useMemo(() => [
    { id: 'konto', labelKey: 'profile.tabs.account', icon: User },
    ...(publicSettings.social_enabled
      ? [
          { id: 'social' as const, labelKey: 'profile.tabs.social', icon: Users },
          { id: 'messenger' as const, labelKey: 'profile.tabs.messenger', icon: Lock },
        ]
      : []),
    ...(publicSettings.vault_enabled
      ? [{ id: 'tresor' as const, labelKey: 'profile.tabs.vault', icon: ShieldCheck }]
      : []),
    {
      id: 'desktop',
      labelKey: isAndroidClient ? 'mss.einstellungen.tab.app' : 'mss.einstellungen.tab.desktop',
      icon: MonitorCog,
    },
    { id: 'wakeword', labelKey: 'mss.einstellungen.tab.wakeword', icon: Mic },
    { id: 'audio', labelKey: 'mss.einstellungen.tab.audio', icon: Volume2 },
    { id: 'rechtliches', labelKey: 'mss.einstellungen.tab.rechtliches', icon: FileSignature },
    { id: 'gefahr', labelKey: 'mss.einstellungen.tab.gefahr', icon: AlertTriangle, variant: 'danger' },
  ], [publicSettings.social_enabled, publicSettings.vault_enabled])

  const tabAusSuche = useCallback((suche: string): EinstellungsTab => {
    const wunsch = new URLSearchParams(suche).get('tab')
    if (wunsch === 'profil' || wunsch === 'account') return 'konto'
    if (wunsch === 'social' && !publicSettings.social_enabled) return 'desktop'
    if (wunsch === 'messenger' && !publicSettings.social_enabled) return 'desktop'
    if (wunsch === 'tresor' && !publicSettings.vault_enabled) return 'desktop'
    return tabs.some((entry) => entry.id === wunsch) ? (wunsch as EinstellungsTab) : 'desktop'
  }, [tabs, publicSettings.social_enabled, publicSettings.vault_enabled])

  const [tab, setTab] = useState<EinstellungsTab>(() => tabAusSuche(ort.search))

  useEffect(() => {
    setTab(tabAusSuche(ort.search))
  }, [ort.search, tabAusSuche])

  useEffect(() => {
    if (tab === 'social' && !publicSettings.social_enabled) setTab('desktop')
    if (tab === 'messenger' && !publicSettings.social_enabled) setTab('desktop')
    if (tab === 'tresor' && !publicSettings.vault_enabled) setTab('desktop')
  }, [tab, publicSettings.social_enabled, publicSettings.vault_enabled])

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <TabBar
        tabs={tabs}
        active={tab}
        onChange={setTab}
        ariaLabel={t('mss.app.einstellungen')}
      />
      {tab === 'konto' && <KontoEinstellungen />}
      {tab === 'social' && publicSettings.social_enabled && <SocialEinstellungen />}
      {tab === 'messenger' && publicSettings.social_enabled && <MessengerSicherheitTab />}
      {tab === 'tresor' && publicSettings.vault_enabled && <TresorSicherheitTab />}
      {tab === 'desktop' && <DesktopIntegration onKonfigAenderung={onKonfigAenderung} />}
      {tab === 'wakeword' && <WakewordEinrichtung />}
      {tab === 'audio' && <AudioEinstellungen />}
      {tab === 'rechtliches' && <RechtlichesEinstellungen />}
      {tab === 'gefahr' && <Gefahrenzone />}
    </div>
  )
}
