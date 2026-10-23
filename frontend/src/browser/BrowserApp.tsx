/**
 * Die Oberfläche von Maunting Pontis.
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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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

import { Adresszeile, type AdresszeileGriff } from './kopf/Adresszeile'
import { HandyLeiste } from './kopf/HandyLeiste'
import { Kopfleiste } from './kopf/Kopfleiste'
import { Navigationsleiste } from './kopf/Navigationsleiste'
import { Suchleiste } from './kopf/Suchleiste'
import { VertikaleTableiste } from './kopf/VertikaleTableiste'
import { Panel } from './leiste/Panel'
import { Seitenleiste } from './leiste/Seitenleiste'
import { useOhneEdgeMenue } from './seite/EigenesMenue'
import { FormularLeiste } from './seite/FormularLeiste'
import { Meldungsleiste } from './seite/Meldungsleiste'
import { Seitenflaeche } from './seite/Seitenflaeche'
import { useAktualisierung } from './services/aktualisierung'
import { useEinstellungenStore } from './services/einstellungenStore'
import { useDownloadHinweis } from './services/useDownloadHinweis'
import { useGeraetKonfig } from './services/geraetKonfig'
import { kuerzelAusfuehren, useKuerzel, type KuerzelZiele } from './services/kuerzel'
import { tabEreignisse } from './services/nativ'
import { erweiterungenStarten } from './erweiterungen/erweiterungen'
import { useLeistung } from './services/leistung'
import { useDarstellung } from './services/darstellung'
import { useWetterWidget } from './services/wetterWidget'
import { istAndroid } from './services/plattform'
import { istGekoppelt, useSitzung, useSitzungsLauf } from './services/sitzung'
import { useTabsStore } from './services/tabsStore'
import { useTabWechselSperre } from './services/useTabWechselSperre'
import { einrichten as gesperrtEinrichten } from './services/tresorGesperrt'
import { useWidget } from './services/widget'
import { useUeberdeckungBeobachten } from './services/ueberdeckung'
import { useVerlaufFrist } from './services/verlaufStore'
import { zurueckSchliessenOffen } from '@/Singra/UI/useZurueckSchliesst'
import { Uebersetzungsleiste } from './uebersetzung/Uebersetzungsleiste'
import { vollbildHinweis } from './seite/vollbildHinweis'
import { useSuche } from './services/suche'

declare global {
  interface Window {
    __msbZurueck?: () => boolean
  }
}

export function BrowserApp() {
  return (
    <MemoryRouter initialEntries={['/']}>
      <Wurzel />
    </MemoryRouter>
  )
}

/** Ereignisse aus den Tabs: Tastenkürzel an die Kürzel, alles andere an den Store. */
function useTabEreignisse(ziele: KuerzelZiele, onVollbild?: (an: boolean) => void) {
  const aktuell = useRef(ziele)
  aktuell.current = ziele
  useEffect(() => {
    let abmelden: (() => void) | null = null
    let aktiv = true
    void tabEreignisse((e) => {
      if (e.art === 'taste') return kuerzelAusfuehren(e.taste, aktuell.current)
      if (e.art === 'vollbild') {
        onVollbild?.(e.an)
        vollbildHinweis(e.an, useTabsStore.getState().tabs.find((t) => t.id === e.id)?.url)
      }
      useTabsStore.getState().ereignis(e)
    }).then((weg) => {
      if (aktiv) abmelden = weg
      else weg()
    })
    return () => {
      aktiv = false
      abmelden?.()
    }
  }, [onVollbild])
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
  const tabAusrichtung = useEinstellungenStore((s) => s.tabAusrichtung)
  const handy = istAndroid()
  const [imVollbild, setImVollbild] = useState(false)

  useSitzungsLauf()
  useDownloadHinweis()
  useUeberdeckungBeobachten()
  useVerlaufFrist()
  useLeistung()
  useDarstellung()
  useWetterWidget()
  // Dieselben Schlösser wie in MSS: Tresor und Messenger sperren sich selbst.
  useAutoSperre(tresorAutoSperrQuelle, tresorOffen)
  useMessengerSperreBereitschaft()
  useTabWechselSperre()
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
  useTabEreignisse(ziele, setImVollbild)
  const startseite = useCallback(() => navigate('/'), [navigate])
  useWidget(ziele.adresszeile, startseite)
  useAktualisierung()

  const zurueckNavigieren = useCallback((): boolean => {
    // 1. Offenes Modal / Dialog über useZurueckSchliesst schließen
    if (zurueckSchliessenOffen()) return true
    // 2. Offene Suche schließen
    if (useSuche.getState().offen) {
      useSuche.getState().schliessen()
      return true
    }
    // 3. Offenes Panel schließen (zurück zur Hauptansicht)
    if (pfad.current !== '/') {
      navigate('/')
      return true
    }
    // 4. In der Tab-Chronik oder zum vorherigen Tab zurückgehen
    return useTabsStore.getState().zurueckGehen()
  }, [navigate])

  useEffect(() => {
    let letzterAufruf = 0
    const handler = (): boolean => {
      const jetzt = Date.now()
      if (jetzt - letzterAufruf < 180) return true
      letzterAufruf = jetzt
      return zurueckNavigieren()
    }
    window.__msbZurueck = handler
    return () => {
      delete window.__msbZurueck
    }
  }, [zurueckNavigieren])

  useEffect(() => {
    if (!handy) return
    let touchStart: { x: number; y: number; time: number } | null = null

    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length !== 1) return
      const t = e.touches[0]
      const amLinkenRand = t.clientX <= 48
      const amRechtenRand = t.clientX >= window.innerWidth - 48
      if (amLinkenRand || amRechtenRand) {
        touchStart = { x: t.clientX, y: t.clientY, time: Date.now() }
      } else {
        touchStart = null
      }
    }

    const onTouchEnd = (e: TouchEvent) => {
      if (!touchStart || e.changedTouches.length !== 1) return
      const t = e.changedTouches[0]
      const dx = t.clientX - touchStart.x
      const dy = t.clientY - touchStart.y
      const dt = Date.now() - touchStart.time
      const startX = touchStart.x
      touchStart = null

      const vomLinkenRand = startX <= 48 && dx >= 40
      const vomRechtenRand = startX >= window.innerWidth - 48 && dx <= -40

      if ((vomLinkenRand || vomRechtenRand) && Math.abs(dx) > Math.abs(dy) * 1.5 && dt < 500) {
        window.__msbZurueck?.()
      }
    }

    window.addEventListener('touchstart', onTouchStart, { passive: true })
    window.addEventListener('touchend', onTouchEnd, { passive: true })
    return () => {
      window.removeEventListener('touchstart', onTouchStart)
      window.removeEventListener('touchend', onTouchEnd)
    }
  }, [handy])

  return (
    <div
      className={`flex h-dvh flex-col overflow-hidden bg-background text-on-surface ${handy && !imVollbild ? 'pl-[var(--msm-links-sicher)] pr-[var(--msm-rechts-sicher)]' : ''}`}
    >
      {handy ? (
        !imVollbild && (
          <div className="shrink-0 bg-surface-container px-2 py-1.5 pt-[calc(var(--msm-oben-sicher)+0.375rem)]">
            <Adresszeile ref={adresszeile} />
          </div>
        )
      ) : (
        !imVollbild && (
          <>
            <Kopfleiste />
            <Navigationsleiste ref={adresszeile} />
          </>
        )
      )}
      {!imVollbild && <Suchleiste />}
      <div className="flex min-h-0 flex-1 bg-surface-container">
        {!imVollbild && !handy && tabAusrichtung === 'vertikal' && <VertikaleTableiste />}
        {!imVollbild && !handy && leiste === 'links' && <Seitenleiste seite="links" />}
        {!imVollbild && !handy && panelSeite === 'links' && <Panel seite="links" />}
        <div className="flex min-w-0 flex-1 flex-col">
          {!imVollbild && <Meldungsleiste />}
          {!imVollbild && <FormularLeiste />}
          {!imVollbild && <Uebersetzungsleiste />}
          <Seitenflaeche />
        </div>
        {!imVollbild && !handy && panelSeite === 'rechts' && <Panel seite="rechts" />}
        {!imVollbild && !handy && leiste === 'rechts' && <Seitenleiste seite="rechts" />}
      </div>
      {handy && !imVollbild && (
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
