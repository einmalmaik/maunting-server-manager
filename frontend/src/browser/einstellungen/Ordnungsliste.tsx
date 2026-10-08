/**
 * Eine Liste zum Ordnen und Ein- und Ausblenden: Ziehen ordnet mit der Maus,
 * die Pfeilknöpfe mit der Tastatur, der Schalter blendet aus. Für die Einträge
 * der Leiste (`Anordnung.tsx`) und die Widgets der Startseite.
 */
import { useState, type DragEvent } from 'react'
import { ArrowDown, ArrowUp, GripVertical, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Kurzinfo, Switch } from '@/Singra/UI'

export interface Ordnungseintrag {
  schluessel: string
  name: string
  symbol: LucideIcon
  an: boolean
}

const PFEIL =
  'flex h-8 w-8 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface disabled:opacity-30 disabled:hover:bg-transparent [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11'

/** `art` trennt die Listen einer Seite: Gezogenes aus der einen landet nicht in der anderen. */
export function Ordnungsliste({
  art,
  eintraege,
  verschieben,
  umschalten,
}: {
  art: string
  eintraege: Ordnungseintrag[]
  verschieben: (von: number, nach: number) => void
  umschalten: (schluessel: string) => void
}) {
  const { t } = useTranslation()
  const [ziel, setZiel] = useState<number | null>(null)
  const typ = `text/msb-ordnung-${art}`

  const ablegen = (e: DragEvent, nach: number) => {
    setZiel(null)
    if (!e.dataTransfer.types.includes(typ)) return
    const von = Number(e.dataTransfer.getData(typ))
    if (Number.isInteger(von) && von !== nach) verschieben(von, nach)
  }

  return (
    <ul className="flex flex-col gap-1">
      {eintraege.map((e, i) => {
        const Symbol = e.symbol
        const nameId = `msb-ordnung-${art}-${e.schluessel}`
        return (
          <li
            key={e.schluessel}
            draggable
            onDragStart={(ev) => ev.dataTransfer.setData(typ, String(i))}
            onDragOver={(ev) => {
              if (!ev.dataTransfer.types.includes(typ)) return
              ev.preventDefault()
              setZiel(i)
            }}
            onDragLeave={() => setZiel(null)}
            onDrop={(ev) => ablegen(ev, i)}
            className={`flex items-center gap-2 rounded-lg bg-surface-container px-2 py-1.5 ${ziel === i ? 'ring-2 ring-primary/50' : ''}`}
          >
            <GripVertical className="h-4 w-4 shrink-0 cursor-grab text-on-surface-variant" aria-hidden="true" />
            <Symbol className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
            <span id={nameId} className="min-w-0 flex-1 truncate text-body-sm text-on-surface">
              {e.name}
            </span>
            <Kurzinfo text={t('browser.lesezeichen.nachOben')}>
              <button type="button" disabled={i === 0} onClick={() => verschieben(i, i - 1)} aria-label={t('browser.einstellungen.nachObenName', { name: e.name })} className={PFEIL}>
                <ArrowUp className="h-4 w-4" aria-hidden="true" />
              </button>
            </Kurzinfo>
            <Kurzinfo text={t('browser.lesezeichen.nachUnten')}>
              <button
                type="button"
                disabled={i === eintraege.length - 1}
                onClick={() => verschieben(i, i + 1)}
                aria-label={t('browser.einstellungen.nachUntenName', { name: e.name })}
                className={PFEIL}
              >
                <ArrowDown className="h-4 w-4" aria-hidden="true" />
              </button>
            </Kurzinfo>
            <Switch aria-labelledby={nameId} checked={e.an} onCheckedChange={() => umschalten(e.schluessel)} />
          </li>
        )
      })}
    </ul>
  )
}

/** Die Liste nach einer Verschiebung, als volle Reihenfolge zum Speichern. */
export function verschoben<T>(liste: T[], von: number, nach: number): T[] {
  if (nach < 0 || nach >= liste.length || von === nach) return liste
  const neu = [...liste]
  const [eintrag] = neu.splice(von, 1)
  neu.splice(nach, 0, eintrag)
  return neu
}
