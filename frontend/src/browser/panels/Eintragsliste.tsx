/**
 * Eine Liste von Seiten (Lesezeichen, Verlauf) mit Suche. Ein Klick öffnet die
 * Seite im vorderen Tab, mit gedrückter Strg-Taste oder der mittleren Taste in
 * einem neuen.
 *
 * Mit `aktionen` trägt jede Zeile statt des Papierkorbs ein Menü (auch per
 * Rechtsklick), mit `verschieben` lässt sie sich ziehen, solange nicht gesucht
 * wird: die Reihenfolge einer gefilterten Liste ist nicht die ganze.
 */
import { useState, type DragEvent, type ReactNode } from 'react'
import { Globe, MoreHorizontal, Search, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Input, Kurzinfo, type ActionMenuItem } from '@/Singra/UI'
import { Kontextmenue } from '@/Singra/UI/Kontextmenue'
import { Zustandsflaeche } from '@/Singra/UI/Zustandsflaeche'

import { seiteOeffnen } from '../services/tabsStore'
import type { Eintrag } from '../services/verlaufStore'

interface Props {
  eintraege: Eintrag[]
  leer: string
  entfernen: (e: Eintrag) => void
  /** Eine Zeile über einem Eintrag, etwa das Datum im Verlauf. */
  ueberschrift?: (e: Eintrag, vorher: Eintrag | undefined) => ReactNode
  kopf?: ReactNode
  aktionen?: (e: Eintrag, index: number) => ActionMenuItem[]
  verschieben?: (von: number, nach: number) => void
}

const ZIEHEN = 'text/msb-eintrag'

export function Eintragsliste({ eintraege, leer, entfernen, ueberschrift, kopf, aktionen, verschieben }: Props) {
  const { t } = useTranslation()
  const [suche, setSuche] = useState('')
  const [menue, setMenue] = useState<{ ort: { x: number; y: number }; items: ActionMenuItem[]; ausloeser: HTMLElement | null } | null>(null)
  const s = suche.trim().toLowerCase()
  const sichtbar = s ? eintraege.filter((e) => e.url.toLowerCase().includes(s) || e.titel.toLowerCase().includes(s)) : eintraege
  // Mehr zeichnet niemand durch; die Suche findet den Rest.
  const gezeigt = sichtbar.slice(0, 300)
  const ziehbar = !!verschieben && !s

  const menueOeffnen = (e: Eintrag, index: number, ort: { x: number; y: number }, ausloeser: HTMLElement | null) => {
    if (aktionen) setMenue({ ort, items: aktionen(e, index), ausloeser })
  }

  const ablegen = (ev: DragEvent, nach: number) => {
    if (!ev.dataTransfer.types.includes(ZIEHEN)) return
    const von = Number(ev.dataTransfer.getData(ZIEHEN))
    if (Number.isInteger(von)) verschieben?.(von, nach)
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 flex-col gap-2 p-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 z-10 h-4 w-4 -translate-y-1/2 text-on-surface-variant" aria-hidden="true" />
          <Input value={suche} onChange={(e) => setSuche(e.target.value)} aria-label={t('browser.liste.suchen')} placeholder={t('browser.liste.suchen')} className="pl-9" />
        </div>
        {kopf}
      </div>
      {gezeigt.length === 0 ? (
        <Zustandsflaeche art="leer" text={s ? t('browser.liste.keineTreffer') : leer} ansagen={!!s} />
      ) : (
        <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {gezeigt.map((e, i) => {
            const name = e.titel || e.url
            return (
              <li
                key={`${e.url}-${e.zeit}`}
                draggable={ziehbar}
                onDragStart={ziehbar ? (ev) => ev.dataTransfer.setData(ZIEHEN, String(i)) : undefined}
                onDragOver={ziehbar ? (ev) => ev.preventDefault() : undefined}
                onDrop={ziehbar ? (ev) => ablegen(ev, i) : undefined}
                onContextMenu={
                  aktionen
                    ? (ev) => {
                        ev.preventDefault()
                        menueOeffnen(e, i, { x: ev.clientX, y: ev.clientY }, null)
                      }
                    : undefined
                }
              >
                {ueberschrift?.(e, gezeigt[i - 1])}
                <div className="group flex items-center gap-1 rounded-md hover:bg-surface-container-high">
                  <button
                    type="button"
                    onClick={(ev) => seiteOeffnen(e.url, ev.ctrlKey || ev.metaKey)}
                    onAuxClick={(ev) => ev.button === 1 && seiteOeffnen(e.url, true)}
                    className="flex min-w-0 flex-1 items-center gap-3 px-2 py-2 text-left"
                  >
                    <Globe className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-body-sm text-on-surface">{name}</span>
                      <span className="block truncate text-label-sm text-on-surface-variant">{e.url}</span>
                    </span>
                  </button>
                  {aktionen ? (
                    <Kurzinfo text={t('browser.liste.aktionen')} seite="ende">
                      <button
                        type="button"
                        aria-haspopup="menu"
                        onClick={(ev) => {
                          const r = ev.currentTarget.getBoundingClientRect()
                          menueOeffnen(e, i, { x: r.right, y: r.bottom }, ev.currentTarget)
                        }}
                        aria-label={t('browser.liste.aktionenName', { titel: name })}
                        className="mr-1 flex h-7 w-7 items-center justify-center rounded-md text-on-surface-variant opacity-0 hover:bg-surface-container-highest hover:text-on-surface focus-visible:opacity-100 group-hover:opacity-100"
                      >
                        <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
                      </button>
                    </Kurzinfo>
                  ) : (
                    <Kurzinfo text={t('browser.liste.entfernen')} seite="ende">
                      <button
                        type="button"
                        onClick={() => entfernen(e)}
                        aria-label={t('browser.liste.entfernenName', { titel: name })}
                        className="mr-1 flex h-7 w-7 items-center justify-center rounded-md text-on-surface-variant opacity-0 hover:bg-surface-container-highest hover:text-status-destructive focus-visible:opacity-100 group-hover:opacity-100"
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      </button>
                    </Kurzinfo>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
      <Kontextmenue
        ort={menue?.ort ?? null}
        items={menue?.items ?? []}
        label={t('browser.liste.aktionen')}
        ausloeser={menue?.ausloeser}
        onSchliessen={() => setMenue(null)}
      />
    </div>
  )
}
