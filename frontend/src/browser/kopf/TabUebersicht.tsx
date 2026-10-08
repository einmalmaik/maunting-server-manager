/**
 * Alle Tabs als Raster über dem ganzen Bildschirm, wie die Tab-Übersicht von
 * Chrome am Handy. Sie ist ein Dialog: die Webview der Seite weicht
 * (`ueberdeckung.ts`), und die Zurück-Taste schließt sie.
 *
 * Das Raster ist ein Tab-Halt, die Pfeile wandern darin (Punkt 88); Entf
 * schließt den Tab mit dem Fokus. Die Schließen-Knöpfe sind für den Finger.
 */
import { useRef, useState } from 'react'
import { EyeOff, Plus, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Dialog, DialogContent } from '@/Singra/UI/Dialog'
import { nachbarKachel } from '@/Singra/UI/Rasterfokus'

import { useEinstellungenStore } from '../services/einstellungenStore'
import { useTabsStore } from '../services/tabsStore'
import { Knopf } from './knopf'
import { TabSymbol, tabTitel } from './Kopfleiste'

function host(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return ''
  }
}

export function TabUebersicht({ offen, onSchliessen }: { offen: boolean; onSchliessen: () => void }) {
  const { t } = useTranslation()
  const tabs = useTabsStore((s) => s.tabs)
  const aktivId = useTabsStore((s) => s.aktivId)
  const { neuerTab, aktivieren, schliessen } = useTabsStore.getState()
  const privatAus = useEinstellungenStore((s) => s.ausgeblendet.includes('privaterTab'))
  const raster = useRef<HTMLUListElement>(null)
  const [fokus, setFokus] = useState<string | null>(null)
  const name = t('browser.tabs.liste')
  // Der Tab-Halt liegt auf der zuletzt fokussierten Kachel, sonst auf dem aktiven Tab.
  const halt = tabs.some((x) => x.id === fokus) ? fokus : (aktivId ?? tabs[0]?.id)

  const oeffnen = (id?: string, privat = false) => {
    if (id) aktivieren(id)
    else neuerTab(undefined, { privat })
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

  return (
    <Dialog open={offen} onOpenChange={(o) => !o && onSchliessen()}>
      <DialogContent
        aria-label={name}
        showCloseButton={false}
        overlayClassName="!items-stretch !bg-background !p-0 !backdrop-blur-none"
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
            return (
              <li key={tab.id} className="relative">
                <button
                  type="button"
                  data-kachel
                  data-tab={tab.id}
                  tabIndex={tab.id === halt ? 0 : -1}
                  onFocus={() => setFokus(tab.id)}
                  onClick={() => oeffnen(tab.id)}
                  aria-current={aktiv ? 'page' : undefined}
                  className={`flex h-32 w-full min-w-0 flex-col gap-2 overflow-hidden rounded-xl border-2 bg-surface-container p-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                    aktiv ? 'border-primary' : 'border-transparent'
                  }`}
                >
                  <span className="flex w-full min-w-0 items-center gap-2 pr-9">
                    <TabSymbol tab={tab} />
                    <span className="min-w-0 truncate text-label-md text-on-surface">{titel}</span>
                  </span>
                  <span className="line-clamp-2 break-all text-label-sm text-on-surface-variant">{host(tab.url)}</span>
                  {tab.privat && <span className="mt-auto text-label-sm text-primary">{t('browser.tabs.privat')}</span>}
                </button>
                <button
                  type="button"
                  tabIndex={-1}
                  onClick={() => schliessen(tab.id)}
                  aria-label={t('browser.tabs.schliessen', { titel })}
                  className="absolute right-0 top-0 flex h-11 w-11 items-center justify-center rounded-full text-on-surface-variant hover:text-on-surface"
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                </button>
              </li>
            )
          })}
        </ul>
      </DialogContent>
    </Dialog>
  )
}
