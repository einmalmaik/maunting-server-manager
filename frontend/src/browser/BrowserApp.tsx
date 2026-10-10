/**
 * Die Oberfläche des Maunting Secure Browsers.
 *
 * Oben Tabs und Navigation, in der Mitte die Seite (eine Webview von Rust),
 * am Handy statt oben eine Leiste unten (`HandyLeiste`, wie Chrome mit
 * Adressleiste unten) und darüber nur der Rand der Statusleiste,
 * daneben die Seitenleiste, falls gewählt (sonst stehen ihre Einträge im
 * Menü oder oben), und ein Panel für Lesezeichen, Verlauf,
 * Downloads und, gekoppelt, die Seiten von MSS. Die Einstellungen sind eine
 * eigene Seite im Tab (`einstellungen/`). Welches Panel
 * offen ist, sagt die Route des MemoryRouters (`leiste/module.ts`).
 */
import { useCallback, useEffect, useMemo, useRef } from 'react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'

import { BrowserBestaetigungDialog } from '@/components/BrowserBestaetigungDialog'
import { AnrufEbene } from '@/components/calling/AnrufEbene'
import { PanelPopupModal } from '@/components/popups/PanelPopupModal'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { PromptDialog } from '@/components/ui/PromptDialog'
import { eingangBeobachten } from '@/desktop/vault/tresorEingang'
import { tresorAutoSperrQuelle, useVaultStore } from '@/desktop/vault/vaultStore'
import { useAutoSperre } from '@/hooks/useAutoSperre'
import { useMessengerSperreBereitschaft } from '@/hooks/useMessengerSperre'
import { usePresenceAndActivity } from '@/hooks/usePresenceAndActivity'
import { usePublicSettingsStore } from '@/stores/publicSettingsStore'
import { useCallStore } from '@/stores/useCallStore'

import type { AdresszeileGriff } from './kopf/Adresszeile'
import { HandyLeiste } from './kopf/HandyLeiste'
import { Kopfleiste } from './kopf/Kopfleiste'
import { Navigationsleiste } from './kopf/Navigationsleiste'
import { Suchleiste } from './kopf/Suchleiste'
import { Panel } from './leiste/Panel'
import { Seitenleiste } from './leiste/Seitenleiste'
import { useOhneEdgeMenue } from './seite/EigenesMenue'
import { FormularLeiste } from './seite/FormularLeiste'
import { Meldungsleiste } from './seite/Meldungsleiste'
import { Seitenflaeche } from './seite/Seitenflaeche'
import { useAktualisierung } from './services/aktualisierung'
import { useEinstellungenStore } from './services/einstellungenStore'
import { useGeraetKonfig } from './services/geraetKonfig'
import { kuerzelAusfuehren, useKuerzel, type KuerzelZiele } from './services/kuerzel'
import { tabEreignisse } from './services/nativ'
import { erweiterungenStarten } from './erweiterungen/erweiterungen'
import { useLeistung } from './services/leistung'
import { istAndroid } from './services/plattform'
import { istGekoppelt, useSitzung, useSitzungsLauf } from './services/sitzung'
import { useTabsStore } from './services/tabsStore'
import { einrichten as gesperrtEinrichten } from './services/tresorGesperrt'
import { useWidget } from './services/widget'
import { useUeberdeckungBeobachten } from './services/ueberdeckung'
import { useVerlaufFrist } from './services/verlaufStore'
import { Uebersetzungsleiste } from './uebersetzung/Uebersetzungsleiste'

export function BrowserApp() {
  return (
    <MemoryRouter initialEntries={['/']}>
      <Wurzel />
    </MemoryRouter>
  )
}

/** Ereignisse aus den Tabs: Tastenkürzel an die Kürzel, alles andere an den Store. */
function useTabEreignisse(ziele: KuerzelZiele) {
  const aktuell = useRef(ziele)
  aktuell.current = ziele
  useEffect(() => {
    let abmelden: (() => void) | null = null
    let aktiv = true
    void tabEreignisse((e) => {
      if (e.art === 'taste') kuerzelAusfuehren(e.taste, aktuell.current)
      else useTabsStore.getState().ereignis(e)
    }).then((weg) => {
      if (aktiv) abmelden = weg
      else weg()
    })
    return () => {
      aktiv = false
      abmelden?.()
    }
  }, [])
}

function Wurzel() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const adresszeile = useRef<AdresszeileGriff>(null)
  const pfad = useRef(pathname)
  pfad.current = pathname
  useOhneEdgeMenue()

  const stand = useSitzung((s) => s.stand)
  const gekoppelt = istGekoppelt(stand)
  const angemeldet = stand === 'an'
  const social = usePublicSettingsStore((s) => s.social_enabled)
  const tresorOffen = useVaultStore((s) => s.isUnlocked)
  const anruf = useCallStore((s) => s.state !== 'idle')
  const leiste = useEinstellungenStore((s) => s.leiste)
  const panelSeite = useEinstellungenStore((s) => s.panelSeite)
  const handy = istAndroid()

  useSitzungsLauf()
  useUeberdeckungBeobachten()
  useVerlaufFrist()
  useLeistung()
  // Dieselben Schlösser wie in MSS: Tresor und Messenger sperren sich selbst.
  useAutoSperre(tresorAutoSperrQuelle, tresorOffen)
  useMessengerSperreBereitschaft()
  usePresenceAndActivity(angemeldet && social, angemeldet && social)
  useEffect(() => (gekoppelt ? eingangBeobachten() : undefined), [gekoppelt])
  // Offen einmal eingerichtet, kann der Browser auch bei gesperrtem Tresor
  // speichern. Erst nach dem Abgleich: sonst fehlte das Schlüsselpaar des
  // Tresors noch, und es entstünde ein zweites.
  const tresorAbgeglichen = useVaultStore((s) => s.isUnlocked && s.syncStatus === 'synced')
  useEffect(() => {
    if (gekoppelt && tresorAbgeglichen) void gesperrtEinrichten().catch(() => false)
  }, [gekoppelt, tresorAbgeglichen])

  useEffect(() => {
    void useGeraetKonfig.getState().laden()
    void useTabsStore.getState().hochfahren()
  }, [])
  useEffect(() => {
    let aus: (() => void) | null = null
    let weg = false
    void erweiterungenStarten().then((f) => (weg ? f() : (aus = f)))
    return () => {
      weg = true
      aus?.()
    }
  }, [])

  const ziele = useMemo<KuerzelZiele>(
    () => ({
      adresszeile: () => adresszeile.current?.fokussieren(),
      panel: (welches) => {
        const ziel = welches === 'singra' ? '/ai' : `/${welches}`
        navigate(pfad.current === ziel ? '/' : ziel)
      },
    }),
    [navigate],
  )
  useKuerzel(ziele)
  useTabEreignisse(ziele)
  const startseite = useCallback(() => navigate('/'), [navigate])
  useWidget(ziele.adresszeile, startseite)
  useAktualisierung()

  return (
    <div
      className={`flex h-dvh flex-col overflow-hidden bg-background text-on-surface ${handy ? 'pl-[var(--msm-links-sicher)] pr-[var(--msm-rechts-sicher)]' : ''}`}
    >
      {handy ? (
        // Unter Statusleiste und Kamera liegt nichts, was man antippen muss.
        <div aria-hidden="true" className="h-[var(--msm-oben-sicher)] shrink-0 bg-surface-container" />
      ) : (
        <>
          <Kopfleiste />
          <Navigationsleiste ref={adresszeile} />
        </>
      )}
      <Suchleiste />
      <div className="flex min-h-0 flex-1 bg-surface-container">
        {!handy && leiste === 'links' && <Seitenleiste seite="links" />}
        {!handy && panelSeite === 'links' && <Panel seite="links" />}
        <div className="flex min-w-0 flex-1 flex-col">
          <Meldungsleiste />
          <FormularLeiste />
          <Uebersetzungsleiste />
          <Seitenflaeche />
        </div>
        {!handy && panelSeite === 'rechts' && <Panel seite="rechts" />}
        {!handy && leiste === 'rechts' && <Seitenleiste seite="rechts" />}
      </div>
      {handy && (
        <>
          <HandyLeiste ref={adresszeile} />
          <Panel seite="voll" />
        </>
      )}

      {angemeldet && <PanelPopupModal />}
      {/* Das Anruffenster deckt alles ab und hat kein aria-modal: die Seite
          muss dafür eigens weichen (`ueberdeckung.ts`). */}
      {angemeldet && social && (
        <div data-msb-verdeckt={anruf ? '' : undefined}>
          <AnrufEbene />
        </div>
      )}
      <ConfirmDialog />
      <PromptDialog />
      <BrowserBestaetigungDialog />
    </div>
  )
}
