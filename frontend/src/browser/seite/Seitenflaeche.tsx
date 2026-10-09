/**
 * Der Platz, an dem die Seite steht. Die Webview selbst zeichnet Rust darüber;
 * diese Fläche meldet nur, wo sie liegt (`tabs_rahmen`). Hat der vordere Tab
 * keine Seite, steht hier die Startseite, bei `msb://` die Seite des
 * Browsers (`intern.ts`); ist sein Prozess abgestürzt, ein
 * Hinweis mit „Neu laden“. Ist der Tab gerade verdeckt (Schild, Dialog),
 * steht hier sein Standbild.
 */
import { lazy, Suspense, useLayoutEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'

import { Zustandsflaeche } from '@/Singra/UI/Zustandsflaeche'

import { useSeitenflaeche } from '../entwickler/werkzeuge'
import { seitenHost } from '../services/geraetKonfig'
import { interneSeite } from '../services/intern'
import { nativ } from '../services/nativ'
import { useAktiverTab, useTabsStore } from '../services/tabsStore'
import { useStandbild } from '../services/ueberdeckung'
import { SeitenDialoge } from './SeitenDialoge'
import { SeitenMenue } from './SeitenMenue'
import { Sperrseite } from './Sperrseite'
import { MsmSuche, useZurueckZurSuche } from './MsmSuche'
import { Startseite } from './Startseite'

const EinstellungenSeite = lazy(() => import('../einstellungen/EinstellungenSeite').then((m) => ({ default: m.EinstellungenSeite })))

function useRahmenMelden(flaeche: React.RefObject<HTMLDivElement | null>) {
  useLayoutEffect(() => {
    const el = flaeche.current
    if (!el) return
    let zuletzt = ''
    const melden = () => {
      const r = el.getBoundingClientRect()
      const rahmen = { x: Math.round(r.left), y: Math.round(r.top), breite: Math.round(r.width), hoehe: Math.round(r.height) }
      const schluessel = JSON.stringify(rahmen)
      if (schluessel === zuletzt) return
      zuletzt = schluessel
      useSeitenflaeche.setState({ breite: rahmen.breite, hoehe: rahmen.hoehe })
      void nativ.tabsRahmen(rahmen).catch(() => {
        zuletzt = ''
      })
    }
    melden()
    const beobachter = new ResizeObserver(melden)
    beobachter.observe(el)
    window.addEventListener('resize', melden)
    return () => {
      beobachter.disconnect()
      window.removeEventListener('resize', melden)
    }
  }, [flaeche])
}

export function Seitenflaeche() {
  const { t } = useTranslation()
  const flaeche = useRef<HTMLDivElement>(null)
  const tab = useAktiverTab()
  const aktion = useTabsStore((s) => s.aktion)
  const standbild = useStandbild((s) => s.bild)
  useRahmenMelden(flaeche)
  useZurueckZurSuche(tab)
  const intern = tab ? interneSeite(tab.url) : null

  return (
    <main ref={flaeche} className="relative min-w-0 flex-1 overflow-hidden bg-surface">
      {!tab?.url ? (
        <Startseite privat={!!tab?.privat} />
      ) : intern?.seite === 'suche' ? (
        <MsmSuche key={tab.url} tab={tab} />
      ) : intern ? (
        <Suspense fallback={null}>
          <EinstellungenSeite teil={intern.teil} />
        </Suspense>
      ) : tab.abgestuerzt ? (
        <div className="flex h-full items-center justify-center p-6">
          <Zustandsflaeche
            art="fehler"
            titel={t('browser.seite.abgestuerztTitel')}
            text={t('browser.seite.abgestuerztText')}
            erneutLabel={t('browser.nav.neuLaden')}
            onErneut={() => aktion('neu_laden')}
          />
        </div>
      ) : tab.fehler === 'gesperrt' ? (
        <Sperrseite tab={tab} />
      ) : tab.fehler ? (
        <div className="flex h-full items-center justify-center p-6">
          <Zustandsflaeche
            art={tab.fehler === 'offline' ? 'offline' : 'fehler'}
            titel={t(`browser.fehlerseite.${tab.fehler}Titel`, { defaultValue: t('browser.fehlerseite.unbekanntTitel') })}
            text={t(`browser.fehlerseite.${tab.fehler}Text`, {
              defaultValue: t('browser.fehlerseite.unbekanntText'),
              host: seitenHost(tab.url) ?? tab.url,
            })}
            erneutLabel={t('browser.nav.neuLaden')}
            onErneut={() => aktion('neu_laden')}
          />
        </div>
      ) : standbild?.tab === tab.id ? (
        <img src={standbild.url} alt="" aria-hidden="true" draggable={false} className="h-full w-full select-none" />
      ) : null}
      <SeitenMenue flaeche={flaeche} />
      <SeitenDialoge />
    </main>
  )
}
