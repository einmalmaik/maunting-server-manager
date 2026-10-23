/**
 * Eine externe Web-App in der Seitenleiste: WhatsApp, Discord oder Telegram.
 * Die native WebView liegt über diesem Container; die Oberfläche misst ihn und
 * meldet sein Rechteck an Rust. Sitzung und Profil sind die normalen der Tabs.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Zustandsflaeche } from '@/Singra/UI/Zustandsflaeche'

import { seitenHost } from '../services/geraetKonfig'
import { SCHUTZ_NICHT_BEREIT, nativ, tabEreignisse } from '../services/nativ'
import { leererTab, webviewZeigen, type Tab } from '../services/tab'
import { ereignisAnwenden } from '../services/tabEreignis'
import { useTabsStore } from '../services/tabsStore'
import { panelRegistrieren, useStandbild } from '../services/ueberdeckung'
import { SeitenDialoge } from '../seite/SeitenDialoge'
import { SeitenMenue } from '../seite/SeitenMenue'
import { Sperrseite } from '../seite/Sperrseite'
import { BROWSER_EINTRAEGE } from './module'

type ExterneId = 'whatsapp' | 'discord' | 'telegram' | 'instagram'
type Rahmen = { x: number; y: number; breite: number; hoehe: number }

export function ExternPanel({ id, voll }: { id: ExterneId; voll: boolean }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const eintrag = BROWSER_EINTRAEGE.find((e) => e.id === id)
  const url = eintrag?.extern ?? ''
  const tabId = `tab-panel-${id}`
  const container = useRef<HTMLDivElement>(null)
  const lauf = useRef(0)
  const rahmenAktuell = useRef<Rahmen | null>(null)
  const rahmenSchluessel = useRef('')
  const [stand, setStand] = useState<Tab>(() => ({ ...leererTab(tabId), url, titel: eintrag ? t(eintrag.name) : '' }))
  const standRef = useRef(stand)

  const aendernLokal = useCallback((teil: Partial<Tab>) => {
    standRef.current = { ...standRef.current, ...teil }
    setStand(standRef.current)
  }, [])

  const zeigen = useCallback(() => {
    void nativ.panelRahmen(tabId, webviewZeigen(standRef.current) ? rahmenAktuell.current : null)
  }, [tabId])

  const laden = useCallback(() => {
    if (!url) return
    const nr = ++lauf.current
    aendernLokal({ url, laedt: true, nativDa: true, fehler: null, abgestuerzt: false })
    void nativ
      .tabLaden(tabId, url, false, undefined)
      .then(() => {
        if (lauf.current === nr) zeigen()
      })
      .catch((e) => {
        if (lauf.current !== nr) return
        aendernLokal({ laedt: false, ...(e === SCHUTZ_NICHT_BEREIT ? { fehler: e } : {}) })
        zeigen()
      })
  }, [tabId, url, aendernLokal, zeigen])

  const messen = useCallback(() => {
    const el = container.current
    if (!el) return
    const r = el.getBoundingClientRect()
    if (r.width <= 0 || r.height <= 0) return
    const schluessel = `${r.left},${r.top},${r.width},${r.height}`
    if (rahmenSchluessel.current === schluessel) return
    rahmenSchluessel.current = schluessel
    const rahmen = { x: r.left, y: r.top, breite: r.width, hoehe: r.height }
    rahmenAktuell.current = rahmen
    void nativ.panelRahmen(tabId, webviewZeigen(standRef.current) ? rahmen : null)
  }, [tabId])

  useEffect(() => {
    panelRegistrieren(tabId)
    if (voll) void nativ.tabAktivieren(null)
    laden()
    return () => {
      lauf.current++
      panelRegistrieren(null)
      void nativ.tabSchliessen(tabId)
      if (voll) void nativ.tabAktivieren(useTabsStore.getState().aktivId)
    }
  }, [tabId, laden, voll])

  useEffect(() => {
    let abmelden: (() => void) | null = null
    let aktiv = true
    void tabEreignisse((e) => {
      if (e.id !== tabId || e.art === 'download' || e.art === 'download_fortschritt') return
      if (e.art === 'verworfen') {
        laden()
        return
      }
      ereignisAnwenden(e, {
        finden: (id) => (id === tabId ? standRef.current : undefined),
        aendern: (id, teil) => {
          if (id === tabId) aendernLokal(teil)
        },
        zeigen,
        neuerTab: (url) => useTabsStore.getState().neuerTab(url, { privat: false }),
        schliessen: () => navigate('/'),
      })
    }).then((weg) => {
      if (aktiv) abmelden = weg
      else weg()
    })
    return () => {
      aktiv = false
      abmelden?.()
    }
  }, [tabId, laden, zeigen, aendernLokal, navigate])

  useEffect(() => {
    const el = container.current
    if (!el) return
    const beobachter = new ResizeObserver(messen)
    beobachter.observe(el)
    window.addEventListener('resize', messen)
    el.addEventListener('transitionend', messen)
    messen()
    return () => {
      beobachter.disconnect()
      window.removeEventListener('resize', messen)
      el.removeEventListener('transitionend', messen)
    }
  }, [messen])

  const bild = useStandbild((s) => s.bilder[tabId])
  const zeige = webviewZeigen(stand)
  if (!eintrag?.extern) return null

  return (
    <div ref={container} className="relative h-full w-full overflow-hidden bg-surface">
      {bild && <img src={bild} alt="" aria-hidden="true" draggable={false} className="h-full w-full select-none object-contain" />}
      {!zeige && stand.abgestuerzt && (
        <div className="flex h-full items-center justify-center p-6">
          <Zustandsflaeche
            art="fehler"
            titel={t('browser.seite.abgestuerztTitel')}
            text={t('browser.seite.abgestuerztText')}
            erneutLabel={t('browser.nav.neuLaden')}
            onErneut={laden}
          />
        </div>
      )}
      {!zeige && stand.fehler === 'gesperrt' && <Sperrseite tab={stand} />}
      {!zeige && !stand.abgestuerzt && stand.fehler && stand.fehler !== 'gesperrt' && (
        <div className="flex h-full items-center justify-center p-6">
          <Zustandsflaeche
            art={stand.fehler === 'offline' ? 'offline' : 'fehler'}
            titel={t(`browser.fehlerseite.${stand.fehler}Titel`, { defaultValue: t('browser.fehlerseite.unbekanntTitel') })}
            text={t(`browser.fehlerseite.${stand.fehler}Text`, {
              defaultValue: t('browser.fehlerseite.unbekanntText'),
              host: seitenHost(stand.url) ?? stand.url,
            })}
            erneutLabel={t('browser.nav.neuLaden')}
            onErneut={laden}
          />
        </div>
      )}
      <SeitenDialoge tab={stand} />
      <SeitenMenue tab={stand} flaeche={container} />
    </div>
  )
}
