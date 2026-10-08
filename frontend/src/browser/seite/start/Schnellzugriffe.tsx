/**
 * Die Kacheln der Startseite. Eigene Liste in den Einstellungen, anfangs ein
 * paar bekannte Seiten.
 *
 * Das Raster ist ein Tab-Halt, die Pfeile wandern darin (AGENTS.md Punkt 88);
 * Strg+Pfeil links/rechts verschiebt die Kachel, Ziehen mit der Maus auch.
 * Rechtsklick oder die Menütaste öffnen Bearbeiten, Verschieben, Entfernen.
 * Symbole kommen aus `marken.tsx` oder sind ein Buchstabe: die Startseite lädt
 * nichts von fremden Servern.
 */
import { useRef, useState, type DragEvent, type KeyboardEvent, type MouseEvent } from 'react'
import { ArrowLeft, ArrowRight, Pencil, Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Kurzinfo, type ActionMenuItem } from '@/Singra/UI'
import { Kontextmenue } from '@/Singra/UI/Kontextmenue'
import { nachbarKachel } from '@/Singra/UI/Rasterfokus'
import { toast } from '@/stores/toastStore'

import { MarkenSymbol, type Marke } from '../../marken'
import { SCHNELLZUGRIFFE_MAX, useEinstellungenStore, type Schnellzugriff } from '../../services/einstellungenStore'
import { seitenHost } from '../../services/geraetKonfig'
import { seiteOeffnen } from '../../services/tabsStore'
import { verschoben } from '../../einstellungen/Ordnungsliste'
import { KachelDialog } from './KachelDialog'

const BEKANNTE: { host: string; marke: Marke }[] = [
  { host: 'wikipedia.org', marke: 'wikipedia' },
  { host: 'youtube.com', marke: 'youtube' },
  { host: 'github.com', marke: 'github' },
  { host: 'reddit.com', marke: 'reddit' },
  { host: 'duckduckgo.com', marke: 'duckduckgo' },
  { host: 'google.com', marke: 'google' },
  { host: 'bing.com', marke: 'bing' },
  { host: 'ecosia.org', marke: 'ecosia' },
  { host: 'search.brave.com', marke: 'brave' },
]

function markeFuer(url: string): Marke | null {
  const host = seitenHost(url)
  if (!host) return null
  return BEKANNTE.find((b) => host === b.host || host.endsWith(`.${b.host}`))?.marke ?? null
}

const ZIEHEN = 'text/msb-kachel'

const KACHEL = 'group flex flex-col items-center gap-2 rounded-lg p-2 outline-none hover:bg-surface-container/80 focus-visible:ring-2 focus-visible:ring-primary'
const RUND = 'flex h-12 w-12 items-center justify-center rounded-full bg-surface-container-high text-on-surface group-hover:bg-surface-container-highest'

type Bearbeiten = { art: 'neu' } | { art: 'aendern'; index: number }

export function Schnellzugriffe() {
  const { t } = useTranslation()
  const liste = useEinstellungenStore((s) => s.schnellzugriffe)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const raster = useRef<HTMLUListElement>(null)
  const [halt, setHalt] = useState(0)
  const [ziel, setZiel] = useState<number | null>(null)
  const [menue, setMenue] = useState<{ ort: { x: number; y: number }; index: number; ausloeser: HTMLElement } | null>(null)
  const [bearbeiten, setBearbeiten] = useState<Bearbeiten | null>(null)
  const voll = liste.length >= SCHNELLZUGRIFFE_MAX
  // Die Kachel mit dem Tab-Halt; nach dem Entfernen der letzten rückt er nach.
  const tabHalt = Math.min(halt, voll ? liste.length - 1 : liste.length)

  const speichern = (neu: Schnellzugriff[]) => setzen({ schnellzugriffe: neu })

  const verschieben = (von: number, nach: number) => {
    if (nach < 0 || nach >= liste.length) return
    speichern(verschoben(liste, von, nach))
    setHalt(nach)
    // Der Fokus folgt der Kachel an ihren neuen Platz.
    requestAnimationFrame(() => raster.current?.querySelectorAll<HTMLElement>('[data-kachel]')[nach]?.focus())
  }

  const entfernen = (index: number) => {
    const vorher = liste
    const weg = liste[index]
    speichern(liste.filter((_, i) => i !== index))
    toast.success(t('browser.start.entfernt', { name: weg.titel }), {
      label: t('common.undo'),
      ausfuehren: () => speichern(vorher),
    })
  }

  const menueOeffnen = (ev: MouseEvent<HTMLElement>, index: number) => {
    ev.preventDefault()
    const el = ev.currentTarget
    // Die Menütaste meldet keinen Zeiger; dann am Rand der Kachel.
    const r = el.getBoundingClientRect()
    const ort = ev.clientX || ev.clientY ? { x: ev.clientX, y: ev.clientY } : { x: r.left + r.width / 2, y: r.bottom }
    setMenue({ ort, index, ausloeser: el })
  }

  const taste = (ev: KeyboardEvent) => {
    const kachel = (ev.target as HTMLElement).closest<HTMLElement>('[data-kachel]')
    if (!kachel || !raster.current) return
    const index = Number(kachel.dataset.kachel)
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') && index < liste.length) {
      ev.preventDefault()
      verschieben(index, index + (ev.key === 'ArrowLeft' ? -1 : 1))
      return
    }
    if (ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return
    const naechste = nachbarKachel(raster.current, kachel, ev.key)
    if (naechste) {
      ev.preventDefault()
      naechste.focus()
    }
  }

  const ablegen = (ev: DragEvent, nach: number) => {
    setZiel(null)
    if (!ev.dataTransfer.types.includes(ZIEHEN)) return
    const von = Number(ev.dataTransfer.getData(ZIEHEN))
    if (Number.isInteger(von) && von !== nach) verschieben(von, nach)
  }

  const menuePunkte = (index: number): ActionMenuItem[] => [
    { key: 'bearbeiten', label: t('browser.start.bearbeiten'), icon: <Pencil className="h-4 w-4" />, onSelect: () => setBearbeiten({ art: 'aendern', index }) },
    { key: 'vor', label: t('browser.start.nachVorn'), icon: <ArrowLeft className="h-4 w-4" />, disabled: index === 0, onSelect: () => verschieben(index, index - 1) },
    {
      key: 'zurueck',
      label: t('browser.start.nachHinten'),
      icon: <ArrowRight className="h-4 w-4" />,
      disabled: index === liste.length - 1,
      onSelect: () => verschieben(index, index + 1),
    },
    { key: 'weg', label: t('browser.liste.entfernen'), icon: <Trash2 className="h-4 w-4" />, destructive: true, separatorBefore: true, onSelect: () => entfernen(index) },
  ]

  const vorlage = bearbeiten?.art === 'aendern' ? liste[bearbeiten.index] : undefined

  return (
    <nav aria-label={t('browser.start.schnellzugriff')} className="w-full">
      <ul ref={raster} onKeyDown={taste} className="grid w-full grid-cols-4 gap-3 sm:grid-cols-8">
        {liste.map((s, i) => {
          const marke = markeFuer(s.url)
          const host = seitenHost(s.url) ?? s.url
          return (
            <li
              key={`${s.url}-${i}`}
              draggable
              onDragStart={(ev) => ev.dataTransfer.setData(ZIEHEN, String(i))}
              onDragOver={(ev) => {
                if (!ev.dataTransfer.types.includes(ZIEHEN)) return
                ev.preventDefault()
                setZiel(i)
              }}
              onDragLeave={() => setZiel(null)}
              onDrop={(ev) => ablegen(ev, i)}
              className={`rounded-lg ${ziel === i ? 'ring-2 ring-primary/50' : ''}`}
            >
              <button
                type="button"
                data-kachel={i}
                tabIndex={i === tabHalt ? 0 : -1}
                onFocus={() => setHalt(i)}
                onClick={(ev) => seiteOeffnen(s.url, ev.ctrlKey || ev.metaKey)}
                onAuxClick={(ev) => ev.button === 1 && seiteOeffnen(s.url, true)}
                onContextMenu={(ev) => menueOeffnen(ev, i)}
                aria-haspopup="menu"
                className={`${KACHEL} w-full`}
              >
                <span className={RUND}>
                  {marke ? <MarkenSymbol marke={marke} className="h-6 w-6" /> : <span className="text-title-md uppercase" aria-hidden="true">{host.charAt(0)}</span>}
                </span>
                <span className="w-full truncate text-center text-label-sm text-on-surface-variant">{s.titel || host}</span>
              </button>
            </li>
          )
        })}
        {!voll && (
          <li className="flex justify-center">
            <Kurzinfo text={t('browser.start.hinzufuegenTitel')}>
              <button
                type="button"
                data-kachel={liste.length}
                tabIndex={tabHalt === liste.length ? 0 : -1}
                onFocus={() => setHalt(liste.length)}
                onClick={() => setBearbeiten({ art: 'neu' })}
                aria-label={t('browser.start.hinzufuegenTitel')}
                className={KACHEL}
              >
                <span className={`${RUND} text-on-surface-variant`}>
                  <Plus className="h-5 w-5" aria-hidden="true" />
                </span>
              </button>
            </Kurzinfo>
          </li>
        )}
      </ul>
      <Kontextmenue
        ort={menue?.ort ?? null}
        items={menue ? menuePunkte(menue.index) : []}
        label={t('browser.liste.aktionen')}
        ausloeser={menue?.ausloeser}
        onSchliessen={() => setMenue(null)}
      />
      {bearbeiten && (
        <KachelDialog
          vorlage={vorlage}
          schliessen={() => setBearbeiten(null)}
          speichern={(kachel) => {
            if (bearbeiten.art === 'neu') speichern([...liste, kachel])
            else speichern(liste.map((s, i) => (i === bearbeiten.index ? kachel : s)))
            setBearbeiten(null)
          }}
        />
      )}
    </nav>
  )
}
