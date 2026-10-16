/**
 * Der Platz, an dem die Seite steht. Die Webview selbst zeichnet Rust darüber;
 * diese Fläche meldet nur, wo sie liegt (`tabs_rahmen` bzw. `tabs_split_setzen`).
 *
 * Im Split-Modus werden zwei Pane-Container nebeneinander oder übereinander
 * gerendert, getrennt durch den interaktiven `<SplitTrenner>`.
 * Das `<SnapZonenOverlay>` überwacht Kanten-Drags für das Windows Snap-Assist Andocken.
 */
import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Columns2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Zustandsflaeche } from '@/Singra/UI/Zustandsflaeche'

import { useSeitenflaeche } from '../entwickler/werkzeuge'
import { seitenHost } from '../services/geraetKonfig'
import { interneSeite } from '../services/intern'
import { nativ } from '../services/nativ'
import { useTabsStore, useSplitZustand, type Tab, type SplitRichtung } from '../services/tabsStore'
import { useEinstellungenStore } from '../services/einstellungenStore'
import { holeAktivenTabDrag, lauscheTabZiehNachrichten, type TabZiehNachricht } from '../services/tabTransfer'
import { bildSetzen, useStandbild } from '../services/ueberdeckung'
import { SeitenDialoge } from './SeitenDialoge'
import { SeitenMenue } from './SeitenMenue'
import { Sperrseite } from './Sperrseite'
import { MsmSuche, useZurueckZurSuche } from './MsmSuche'
import { Startseite } from './Startseite'
import { useEigenesMenue } from './EigenesMenue'
import { Aufnahmen } from '../aufnahme/Aufnahmen'
import { SplitTrenner } from './SplitTrenner'

const EinstellungenSeite = lazy(() => import('../einstellungen/EinstellungenSeite').then((m) => ({ default: m.EinstellungenSeite })))

function EinzelInhalt({ tab, aktion }: { tab?: Tab; aktion: (a: 'zurueck' | 'vor' | 'neu_laden' | 'anhalten', id?: string) => void }) {
  const { t } = useTranslation()
  const standbild = useStandbild((s) => s.bild)
  const intern = tab ? interneSeite(tab.url) : null

  if (!tab?.url) {
    return <Startseite privat={!!tab?.privat} />
  }
  if (intern?.seite === 'suche') {
    return <MsmSuche key={tab.url} tab={tab} />
  }
  if (intern) {
    return (
      <Suspense fallback={null}>
        <EinstellungenSeite teil={intern.teil} />
      </Suspense>
    )
  }
  if (tab.abgestuerzt) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <Zustandsflaeche
          art="fehler"
          titel={t('browser.seite.abgestuerztTitel')}
          text={t('browser.seite.abgestuerztText')}
          erneutLabel={t('browser.nav.neuLaden')}
          onErneut={() => aktion('neu_laden', tab.id)}
        />
      </div>
    )
  }
  if (tab.fehler === 'gesperrt') {
    return <Sperrseite tab={tab} />
  }
  if (tab.fehler) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <Zustandsflaeche
          art={tab.fehler === 'offline' ? 'offline' : 'fehler'}
          titel={t(`browser.fehlerseite.${tab.fehler}Titel`, { defaultValue: t('browser.fehlerseite.unbekanntTitel') })}
          text={t(`browser.fehlerseite.${tab.fehler}Text`, {
            defaultValue: t('browser.fehlerseite.unbekanntText'),
            host: seitenHost(tab.url) ?? tab.url,
          })}
          erneutLabel={t('browser.nav.neuLaden')}
          onErneut={() => aktion('neu_laden', tab.id)}
        />
      </div>
    )
  }
  if (standbild?.tab === tab.id) {
    return (
      <img
        src={standbild.url}
        alt=""
        aria-hidden="true"
        draggable={false}
        className="h-full w-full select-none object-contain"
        onError={() => bildSetzen(null)}
      />
    )
  }
  return null
}

export function Seitenflaeche() {
  const { t } = useTranslation()
  const flaeche = useRef<HTMLDivElement>(null)
  const flaechePrimaer = useRef<HTMLDivElement>(null)
  const flaecheSekundaer = useRef<HTMLDivElement>(null)

  const { split, primaerTab, sekundaerTab } = useSplitZustand()
  const vertikaleTabsEingeklappt = useEinstellungenStore((s) => s.vertikaleTabsEingeklappt)
  const aktion = useTabsStore((s) => s.aktion)
  const splitAktivieren = useTabsStore((s) => s.splitAktivieren)
  const splitAnteilAendern = useTabsStore((s) => s.splitAnteilAendern)
  const splitFokusSetzen = useTabsStore((s) => s.splitFokusSetzen)
  const splitTauschen = useTabsStore((s) => s.splitTauschen)

  const [snapVorschau, setSnapVorschau] = useState<{
    kante: 'links' | 'rechts' | 'oben' | 'unten'
    tabId: string
    titel: string
    favicon: string | null
  } | null>(null)

  useZurueckZurSuche(primaerTab)
  const eigenes = useEigenesMenue(primaerTab)

  // Lauschen auf plattformübergreifenden Tab-Drag für Windows Snap-Vorschau
  useEffect(() => {
    return lauscheTabZiehNachrichten((nachricht: TabZiehNachricht) => {
      if (split || !primaerTab || nachricht.tabId === primaerTab.id) {
        if (snapVorschau) setSnapVorschau(null)
        return
      }

      const el = flaeche.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      const clientX = typeof nachricht.clientX === 'number' ? nachricht.clientX : nachricht.screenX - window.screenX
      const clientY = typeof nachricht.clientY === 'number' ? nachricht.clientY : nachricht.screenY - window.screenY

      const imBereich =
        clientX >= rect.left &&
        clientX <= rect.right &&
        clientY >= rect.top &&
        clientY <= rect.bottom

      if (nachricht.art === 'ziehen') {
        if (!imBereich) {
          setSnapVorschau(null)
          return
        }

        const x = clientX - rect.left
        const y = clientY - rect.top
        const w = rect.width
        const h = rect.height

        let kante: 'links' | 'rechts' | 'oben' | 'unten'
        if (x < w * 0.35) {
          kante = 'links'
        } else if (x > w * 0.65) {
          kante = 'rechts'
        } else if (y < h * 0.35) {
          kante = 'oben'
        } else if (y > h * 0.65) {
          kante = 'unten'
        } else {
          kante = x < w / 2 ? 'links' : 'rechts'
        }

        setSnapVorschau((prev) => {
          if (prev?.kante === kante && prev?.tabId === nachricht.tabId) return prev
          return {
            kante,
            tabId: nachricht.tabId,
            titel: nachricht.titel,
            favicon: nachricht.favicon,
          }
        })
      } else if (nachricht.art === 'ablegen') {
        const sekId = snapVorschau?.tabId ?? nachricht.tabId
        if (sekId && sekId !== primaerTab.id && imBereich) {
          let kante = snapVorschau?.kante
          if (!kante) {
            const x = clientX - rect.left
            const y = clientY - rect.top
            const w = rect.width
            const h = rect.height
            if (x < w * 0.35) kante = 'links'
            else if (x > w * 0.65) kante = 'rechts'
            else if (y < h * 0.35) kante = 'oben'
            else if (y > h * 0.65) kante = 'unten'
            else kante = x < w / 2 ? 'links' : 'rechts'
          }

          const richtung: SplitRichtung = kante === 'oben' || kante === 'unten' ? 'vertikal' : 'horizontal'
          const droppedAufLinksOderOben = kante === 'links' || kante === 'oben'

          setSnapVorschau(null)
          splitAktivieren(sekId, richtung, 0.5)
          if (droppedAufLinksOderOben) {
            splitTauschen()
          }
          return
        }
        setSnapVorschau(null)
      } else if (nachricht.art === 'abbrechen') {
        setSnapVorschau(null)
      }
    })
  }, [split, primaerTab, snapVorschau, splitAktivieren, splitTauschen])

  // Bounds-Meldung an Rust (Single-View oder Split-View)
  useLayoutEffect(() => {
    let zuletzt = ''
    const melden = () => {
      if (split && flaechePrimaer.current && flaecheSekundaer.current && primaerTab && sekundaerTab) {
        const r1 = flaechePrimaer.current.getBoundingClientRect()
        const r2 = flaecheSekundaer.current.getBoundingClientRect()
        const pRahmen = { x: Math.round(r1.left), y: Math.round(r1.top), breite: Math.round(r1.width), hoehe: Math.round(r1.height) }
        const sRahmen = { x: Math.round(r2.left), y: Math.round(r2.top), breite: Math.round(r2.width), hoehe: Math.round(r2.height) }
        const schluessel = JSON.stringify({ p: pRahmen, s: sRahmen })
        if (schluessel === zuletzt) return
        zuletzt = schluessel

        useSeitenflaeche.setState({ breite: pRahmen.breite, hoehe: pRahmen.hoehe })
        void nativ.tabsSplitSetzen(primaerTab.id, pRahmen, sekundaerTab.id, sRahmen).catch(() => {
          zuletzt = ''
        })
      } else if (flaechePrimaer.current) {
        const r = flaechePrimaer.current.getBoundingClientRect()
        const rahmen = { x: Math.round(r.left), y: Math.round(r.top), breite: Math.round(r.width), hoehe: Math.round(r.height) }
        const schluessel = JSON.stringify(rahmen)
        if (schluessel === zuletzt) return
        zuletzt = schluessel

        useSeitenflaeche.setState({ breite: rahmen.breite, hoehe: rahmen.hoehe })
        void nativ.tabsRahmen(rahmen).catch(() => {
          zuletzt = ''
        })
      } else if (flaeche.current) {
        const r = flaeche.current.getBoundingClientRect()
        const rahmen = { x: Math.round(r.left), y: Math.round(r.top), breite: Math.round(r.width), hoehe: Math.round(r.height) }
        const schluessel = JSON.stringify(rahmen)
        if (schluessel === zuletzt) return
        zuletzt = schluessel

        useSeitenflaeche.setState({ breite: rahmen.breite, hoehe: rahmen.hoehe })
        void nativ.tabsRahmen(rahmen).catch(() => {
          zuletzt = ''
        })
      }
    }

    melden()
    const beobachter = new ResizeObserver(melden)
    if (flaeche.current) beobachter.observe(flaeche.current)
    if (flaechePrimaer.current) beobachter.observe(flaechePrimaer.current)
    if (flaecheSekundaer.current) beobachter.observe(flaecheSekundaer.current)
    window.addEventListener('resize', melden)
    window.addEventListener('transitionend', melden)

    return () => {
      beobachter.disconnect()
      window.removeEventListener('resize', melden)
      window.removeEventListener('transitionend', melden)
    }
  }, [split, primaerTab, sekundaerTab, vertikaleTabsEingeklappt])

  // Wenn die vertikale Tableiste ein- oder ausgeklappt wird,
  // animiert Tailwind die Breite. Wir triggern die Neupositionierung während und nach der Transition.
  useEffect(() => {
    const interval = setInterval(() => {
      window.dispatchEvent(new Event('resize'))
    }, 40)
    const timeout = setTimeout(() => {
      clearInterval(interval)
      window.dispatchEvent(new Event('resize'))
    }, 260)
    return () => {
      clearInterval(interval)
      clearTimeout(timeout)
    }
  }, [vertikaleTabsEingeklappt])

  // Stellt sicher, dass keine Geister-Vorschau aktiv bleibt, wenn die Maus ohne Klick bewegt wird
  useEffect(() => {
    const onGlobalPointerMove = (e: PointerEvent) => {
      if (e.buttons === 0 && snapVorschau) {
        setSnapVorschau(null)
      }
    }
    window.addEventListener('pointermove', onGlobalPointerMove)
    return () => window.removeEventListener('pointermove', onGlobalPointerMove)
  }, [snapVorschau])

  const onContainerDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
    const aktiverDrag = holeAktivenTabDrag()
    if (!aktiverDrag || aktiverDrag.tabId === primaerTab?.id || split) return

    const el = flaeche.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top
    const w = rect.width
    const h = rect.height

    let kante: 'links' | 'rechts' | 'oben' | 'unten'
    if (x < w * 0.35) kante = 'links'
    else if (x > w * 0.65) kante = 'rechts'
    else if (y < h * 0.35) kante = 'oben'
    else if (y > h * 0.65) kante = 'unten'
    else kante = x < w / 2 ? 'links' : 'rechts'

    setSnapVorschau((prev) => {
      if (prev?.kante === kante && prev?.tabId === aktiverDrag.tabId) return prev
      return {
        kante,
        tabId: aktiverDrag.tabId,
        titel: aktiverDrag.titel,
        favicon: aktiverDrag.favicon,
      }
    })
  }

  const onContainerDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const aktiverDrag = holeAktivenTabDrag()
    const zielTabId = snapVorschau?.tabId ?? aktiverDrag?.tabId
    if (zielTabId && primaerTab && zielTabId !== primaerTab.id) {
      const el = flaeche.current
      const rect = el?.getBoundingClientRect()
      let kante = snapVorschau?.kante
      if (!kante && rect) {
        const x = e.clientX - rect.left
        const y = e.clientY - rect.top
        const w = rect.width
        const h = rect.height
        if (x < w * 0.35) kante = 'links'
        else if (x > w * 0.65) kante = 'rechts'
        else if (y < h * 0.35) kante = 'oben'
        else if (y > h * 0.65) kante = 'unten'
        else kante = x < w / 2 ? 'links' : 'rechts'
      }
      if (kante) {
        const richtung: SplitRichtung = kante === 'oben' || kante === 'unten' ? 'vertikal' : 'horizontal'
        const droppedAufLinksOderOben = kante === 'links' || kante === 'oben'

        setSnapVorschau(null)
        splitAktivieren(zielTabId, richtung, 0.5)
        if (droppedAufLinksOderOben) {
          splitTauschen()
        }
        return
      }
    }
    setSnapVorschau(null)
  }

  const onContainerDragLeave = (e: React.DragEvent) => {
    if (!flaeche.current?.contains(e.relatedTarget as Node)) {
      setSnapVorschau(null)
    }
  }

  return (
    <main
      ref={flaeche}
      onContextMenu={eigenes.oeffnen}
      onDragOver={onContainerDragOver}
      onDrop={onContainerDrop}
      onDragLeave={onContainerDragLeave}
      className="relative min-w-0 flex-1 overflow-hidden bg-surface"
    >
      {split && primaerTab && sekundaerTab ? (
        <div
          className={`flex h-full w-full overflow-hidden ${
            split.richtung === 'horizontal' ? 'flex-row' : 'flex-col'
          }`}
        >
          {/* Primärer Split-Bereich */}
          <div
            ref={flaechePrimaer}
            onPointerDown={() => splitFokusSetzen('primaer')}
            style={{
              flexBasis: `${split.anteil * 100}%`,
              flexGrow: 0,
              flexShrink: 0,
            }}
            className={`relative min-h-0 min-w-0 overflow-hidden transition-shadow duration-150 ${
              split.aktiveHaelfte === 'primaer' ? 'ring-1 ring-inset ring-primary/40' : ''
            }`}
          >
            <EinzelInhalt tab={primaerTab} aktion={aktion} />
          </div>

          {/* Interaktiver Divider */}
          <SplitTrenner
            richtung={split.richtung}
            anteil={split.anteil}
            onAnteilAendern={splitAnteilAendern}
            containerRef={flaeche}
          />

          {/* Sekundärer Split-Bereich */}
          <div
            ref={flaecheSekundaer}
            onPointerDown={() => splitFokusSetzen('sekundaer')}
            style={{
              flexBasis: `${(1 - split.anteil) * 100}%`,
              flexGrow: 1,
              flexShrink: 1,
            }}
            className={`relative min-h-0 min-w-0 overflow-hidden transition-shadow duration-150 ${
              split.aktiveHaelfte === 'sekundaer' ? 'ring-1 ring-inset ring-primary/40' : ''
            }`}
          >
            <EinzelInhalt tab={sekundaerTab} aktion={aktion} />
          </div>
        </div>
      ) : (
        <div className="relative h-full w-full overflow-hidden">
          {/* Primärer Tab: bleibt während des Schwebens bei 100%, um native Flackerschleifen zu verhindern */}
          <div
            ref={flaechePrimaer}
            className="relative h-full w-full overflow-hidden"
          >
            <EinzelInhalt tab={primaerTab} aktion={aktion} />
          </div>

          {/* Windows Snap-Assist Vorschau-Overlay (überlagert die Zielhälfte und verdeckt temporär den nativen HWND für butterweiche Darstellung) */}
          {snapVorschau && (
            <div
              data-msb-verdeckt="true"
              style={{
                position: 'absolute',
                left: snapVorschau.kante === 'rechts' ? '50%' : 0,
                right: snapVorschau.kante === 'links' ? '50%' : 0,
                top: snapVorschau.kante === 'unten' ? '50%' : 0,
                bottom: snapVorschau.kante === 'oben' ? '50%' : 0,
                width: snapVorschau.kante === 'links' || snapVorschau.kante === 'rechts' ? '50%' : '100%',
                height: snapVorschau.kante === 'oben' || snapVorschau.kante === 'unten' ? '50%' : '100%',
                transition: 'all 200ms cubic-bezier(0.16, 1, 0.3, 1)',
              }}
              className="pointer-events-none z-50 flex flex-col items-center justify-center gap-3 p-6 select-none bg-primary/20 border-2 border-primary/80 backdrop-blur-md rounded-xl shadow-[0_0_30px_rgba(99,102,241,0.3)] animate-scale-in"
            >
              <div className="flex items-center gap-2.5 rounded-xl border border-primary/40 bg-surface/95 px-4 py-2.5 shadow-2xl">
                {snapVorschau.favicon ? (
                  <img src={snapVorschau.favicon} alt="" className="h-4 w-4 rounded shrink-0" />
                ) : (
                  <Columns2 className="h-4 w-4 text-primary shrink-0" />
                )}
                <span className="text-label-md font-semibold text-on-surface max-w-[200px] truncate">
                  {snapVorschau.titel}
                </span>
              </div>
              <div className="flex items-center gap-2 text-label-sm font-medium text-primary">
                <Columns2 className="h-4 w-4 animate-pulse" />
                <span>{t('browser.split.snapDropHinweis', { defaultValue: 'In geteilter Ansicht andocken' })}</span>
              </div>
            </div>
          )}
        </div>
      )}

      <SeitenMenue flaeche={flaeche} />
      {eigenes.ansicht}
      <SeitenDialoge />
      <Aufnahmen />
    </main>
  )
}
