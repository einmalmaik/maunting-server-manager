/**
 * Alle Tabs als Raster über dem ganzen Bildschirm, wie die Tab-Übersicht von
 * Chrome am Handy / Tablet.
 *
 * Unterstützt:
 * - Touch Long-Press Geste mit Vibration: Kachel halten und an eine Bildschirmkante
 *   (oben, unten, links, rechts) ziehen dockt den Tab im Split-Viewer an.
 * - Direkter Split-Knopf auf jeder Kachel für bequeme Einhandbedienung.
 */
import { useRef, useState } from 'react'
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Columns2, EyeOff, Plus, Rows2, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Dialog, DialogContent } from '@/Singra/UI/Dialog'
import { nachbarKachel } from '@/Singra/UI/Rasterfokus'

import { useEinstellungenStore } from '../services/einstellungenStore'
import { useTabsStore, type SplitRichtung } from '../services/tabsStore'
import { Knopf } from './knopf'
import { TabSymbol, tabTitel } from './Kopfleiste'

function host(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return ''
  }
}

type SnapRichtung = 'links' | 'rechts' | 'oben' | 'unten'

export function TabUebersicht({ offen, onSchliessen }: { offen: boolean; onSchliessen: () => void }) {
  const { t } = useTranslation()
  const tabs = useTabsStore((s) => s.tabs)
  const aktivId = useTabsStore((s) => s.aktivId)
  const splitAktivieren = useTabsStore((s) => s.splitAktivieren)
  const splitTauschen = useTabsStore((s) => s.splitTauschen)
  const { neuerTab, aktivieren, schliessen } = useTabsStore.getState()
  const privatAus = useEinstellungenStore((s) => s.ausgeblendet.includes('privaterTab'))
  const raster = useRef<HTMLUListElement>(null)
  const [fokus, setFokus] = useState<string | null>(null)
  const [ziehenderTab, setZiehenderTab] = useState<string | null>(null)
  const [aktiveSnapKante, setAktiveSnapKante] = useState<SnapRichtung | null>(null)
  const longPressTimerRef = useRef<number | null>(null)

  const name = t('browser.tabs.liste')
  const halt = tabs.some((x) => x.id === fokus) ? fokus : (aktivId ?? tabs[0]?.id)

  const oeffnen = (id?: string, privat = false) => {
    if (id) aktivieren(id)
    else neuerTab(undefined, { privat })
    onSchliessen()
  }

  const oeffneGeteilt = (id: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    const istHochformat = typeof window !== 'undefined' ? window.innerHeight > window.innerWidth : false
    const richtung: SplitRichtung = istHochformat ? 'vertikal' : 'horizontal'
    splitAktivieren(id, richtung)
    onSchliessen()
  }

  const taste = (e: React.KeyboardEvent<HTMLUListElement>) => {
    const kachel = (e.target as HTMLElement).closest<HTMLElement>('[data-kachel]')
    if (!kachel || !raster.current) return
    if (e.key === 'Delete') {
      e.preventDefault()
      const id = kachel.dataset.tab
      const naechste = nachbarKachel(raster.current, kachel, 'ArrowRight') ?? nachbarKachel(raster.current, kachel, 'ArrowLeft')
      if (id) schliessen(id)
      naechste?.focus()
      return
    }
    const ziel = nachbarKachel(raster.current, kachel, e.key)
    if (!ziel) return
    e.preventDefault()
    ziel.focus()
  }

  // Touch Long-Press Gesten-Erkennung
  const onPointerDownKachel = (tabId: string, e: React.PointerEvent) => {
    if (e.button !== 0) return
    if ((e.target as HTMLElement).closest('button[data-aktion]')) return

    const startX = e.clientX
    const startY = e.clientY
    const targetElement = e.currentTarget

    if (longPressTimerRef.current) {
      window.clearTimeout(longPressTimerRef.current)
    }

    longPressTimerRef.current = window.setTimeout(() => {
      // Haptisches Feedback auf mobilen Geräten
      if (typeof navigator !== 'undefined' && 'vibrate' in navigator) {
        navigator.vibrate?.(40)
      }
      setZiehenderTab(tabId)
      try {
        targetElement.setPointerCapture?.(e.pointerId)
      } catch {}
    }, 320)

    const onPointerMove = (ev: PointerEvent) => {
      const dist = Math.hypot(ev.clientX - startX, ev.clientY - startY)
      if (dist > 10 && !ziehenderTab && longPressTimerRef.current) {
        window.clearTimeout(longPressTimerRef.current)
        longPressTimerRef.current = null
      }

      if (ziehenderTab) {
        const w = window.innerWidth
        const h = window.innerHeight
        const schwelleX = Math.max(70, w * 0.15)
        const schwelleY = Math.max(70, h * 0.15)

        if (ev.clientX <= schwelleX) setAktiveSnapKante('links')
        else if (ev.clientX >= w - schwelleX) setAktiveSnapKante('rechts')
        else if (ev.clientY <= schwelleY) setAktiveSnapKante('oben')
        else if (ev.clientY >= h - schwelleY) setAktiveSnapKante('unten')
        else setAktiveSnapKante(null)
      }
    }

    const onPointerUp = () => {
      if (longPressTimerRef.current) {
        window.clearTimeout(longPressTimerRef.current)
        longPressTimerRef.current = null
      }
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
      window.removeEventListener('pointercancel', onPointerUp)

      if (ziehenderTab && aktiveSnapKante) {
        const richtung: SplitRichtung =
          aktiveSnapKante === 'links' || aktiveSnapKante === 'rechts' ? 'horizontal' : 'vertikal'
        splitAktivieren(ziehenderTab, richtung)
        if (aktiveSnapKante === 'links' || aktiveSnapKante === 'oben') {
          splitTauschen()
        }
        onSchliessen()
      }

      setZiehenderTab(null)
      setAktiveSnapKante(null)
    }

    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
    window.addEventListener('pointercancel', onPointerUp)
  }

  const vorschauKlassen: Record<SnapRichtung, string> = {
    links: 'left-2 top-2 bottom-2 w-[calc(50%-12px)]',
    rechts: 'right-2 top-2 bottom-2 w-[calc(50%-12px)]',
    oben: 'left-2 right-2 top-2 h-[calc(50%-12px)]',
    unten: 'left-2 right-2 bottom-2 h-[calc(50%-12px)]',
  }

  const kantenTexte: Record<SnapRichtung, string> = {
    links: t('browser.split.snapLinks', { defaultValue: 'Links andocken' }),
    rechts: t('browser.split.snapRechts', { defaultValue: 'Rechts andocken' }),
    oben: t('browser.split.snapOben', { defaultValue: 'Oben andocken' }),
    unten: t('browser.split.snapUnten', { defaultValue: 'Unten andocken' }),
  }

  const kantenIcons: Record<SnapRichtung, React.ReactNode> = {
    links: <ArrowLeft className="h-6 w-6 text-primary animate-pulse" />,
    rechts: <ArrowRight className="h-6 w-6 text-primary animate-pulse" />,
    oben: <ArrowUp className="h-6 w-6 text-primary animate-pulse" />,
    unten: <ArrowDown className="h-6 w-6 text-primary animate-pulse" />,
  }

  return (
    <Dialog open={offen} onOpenChange={(o) => !o && onSchliessen()}>
      <DialogContent
        aria-label={name}
        showCloseButton={false}
        overlayClassName={`!items-stretch !p-0 transition-opacity duration-200 ${
          ziehenderTab ? '!bg-black/60 !backdrop-blur-sm' : '!bg-background !backdrop-blur-none'
        }`}
        className="!max-w-none !rounded-none !border-0 h-full bg-background pb-[var(--msm-unten-sicher)] pl-[var(--msm-links-sicher)] pr-[var(--msm-rechts-sicher)] pt-[var(--msm-oben-sicher)]"
      >
        <div className="flex h-14 shrink-0 items-center gap-1 px-2">
          <h2 className="min-w-0 flex-1 truncate px-2 text-title-md text-on-surface">
            {name} <span className="text-on-surface-variant tabular-nums">{tabs.length}</span>
          </h2>
          {!privatAus && (
            <Knopf name={t('browser.tabs.neuPrivat')} onClick={() => oeffnen(undefined, true)} lage="unten" seite="ende">
              <EyeOff className="h-5 w-5" aria-hidden="true" />
            </Knopf>
          )}
          <Knopf name={t('browser.tabs.neu')} onClick={() => oeffnen()} lage="unten" seite="ende">
            <Plus className="h-5 w-5" aria-hidden="true" />
          </Knopf>
          <Knopf name={t('common.close')} onClick={onSchliessen} lage="unten" seite="ende">
            <X className="h-5 w-5" aria-hidden="true" />
          </Knopf>
        </div>

        <ul ref={raster} onKeyDown={taste} className="grid min-h-0 flex-1 auto-rows-min grid-cols-2 gap-3 overflow-y-auto p-3 sm:grid-cols-3">
          {tabs.map((tab) => {
            const titel = tabTitel(tab, { neu: t('browser.tabs.neu'), einstellungen: t('browser.einstellungen.titel') })
            const aktiv = tab.id === aktivId
            const wirdGezogen = ziehenderTab === tab.id

            return (
              <li
                key={tab.id}
                onPointerDown={(e) => onPointerDownKachel(tab.id, e)}
                className={`relative transition-transform duration-150 ${wirdGezogen ? 'scale-105 z-40' : ''}`}
              >
                <button
                  type="button"
                  data-kachel
                  data-tab={tab.id}
                  tabIndex={tab.id === halt ? 0 : -1}
                  onFocus={() => setFokus(tab.id)}
                  onClick={() => oeffnen(tab.id)}
                  aria-current={aktiv ? 'page' : undefined}
                  className={`flex h-32 w-full min-w-0 flex-col gap-2 overflow-hidden rounded-xl border-2 bg-surface-container p-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                    wirdGezogen
                      ? 'border-primary shadow-2xl ring-2 ring-primary/60'
                      : aktiv
                      ? 'border-primary'
                      : 'border-transparent'
                  }`}
                >
                  <span className="flex w-full min-w-0 items-center gap-2 pr-16">
                    <TabSymbol tab={tab} />
                    <span className="min-w-0 truncate text-label-md text-on-surface">{titel}</span>
                  </span>
                  <span className="line-clamp-2 break-all text-label-sm text-on-surface-variant">{host(tab.url)}</span>
                  {tab.privat && <span className="mt-auto text-label-sm text-primary">{t('browser.tabs.privat')}</span>}
                </button>

                {/* Split-Knopf für Einhandbedienung auf Mobile/Tablet */}
                {tab.id !== aktivId && (
                  <button
                    type="button"
                    data-aktion="split"
                    tabIndex={-1}
                    onClick={(e) => oeffneGeteilt(tab.id, e)}
                    title={t('browser.split.oeffnen', { defaultValue: 'In geteilter Ansicht öffnen' })}
                    aria-label={t('browser.split.oeffnen', { defaultValue: 'In geteilter Ansicht öffnen' })}
                    className="absolute right-10 top-0 flex h-11 w-11 items-center justify-center rounded-full text-on-surface-variant hover:text-primary transition-colors"
                  >
                    <Columns2 className="h-4 w-4 hidden sm:block" aria-hidden="true" />
                    <Rows2 className="h-4 w-4 sm:hidden" aria-hidden="true" />
                  </button>
                )}

                <button
                  type="button"
                  data-aktion="schliessen"
                  tabIndex={-1}
                  onClick={() => schliessen(tab.id)}
                  aria-label={t('browser.tabs.schliessen', { titel })}
                  className="absolute right-0 top-0 flex h-11 w-11 items-center justify-center rounded-full text-on-surface-variant hover:text-on-surface transition-colors"
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                </button>
              </li>
            )
          })}
        </ul>

        {/* Snap-Vorschaufläche während der Touch-Geste */}
        {ziehenderTab && aktiveSnapKante && (
          <div
            aria-hidden="true"
            className="pointer-events-none fixed inset-0 z-50 overflow-hidden"
          >
            <div
              className={`absolute flex flex-col items-center justify-center gap-2 rounded-2xl border-2 border-primary/90 bg-primary/25 shadow-2xl backdrop-blur-md transition-all duration-200 ease-out animate-scale-in ${vorschauKlassen[aktiveSnapKante]}`}
            >
              <div className="flex h-12 w-12 items-center justify-center rounded-full border border-primary/40 bg-surface/90 shadow-md">
                {kantenIcons[aktiveSnapKante]}
              </div>
              <span className="rounded-md bg-surface/95 px-3 py-1 text-label-md font-semibold text-on-surface shadow-sm">
                {kantenTexte[aktiveSnapKante]}
              </span>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
