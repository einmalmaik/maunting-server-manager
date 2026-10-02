/**
 * Ordnerbaum der Tresor-Dateien, vom Stammverzeichnis aus. Der Weg zum
 * geöffneten Ordner ist immer aufgeklappt, damit man sieht, wo man ist.
 *
 * Mit `ablage` ist jeder Ordner ein Ablageziel. Wer etwas über einen
 * zugeklappten Ordner zieht und kurz wartet, klappt ihn auf, wie im Explorer.
 *
 * Bewusst kein `role="tree"`: das verspräche Pfeiltasten-Bedienung. Der Baum
 * ist eine Navigation aus verschachtelten Listen; jeder Ordner ist ein Knopf,
 * das Aufklappen ein eigener Knopf mit `aria-expanded`, und der geöffnete
 * Ordner trägt `aria-current` wie in der Pfadleiste.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, Folder, FolderOpen, HardDrive } from 'lucide-react'
import { ABLAGEZIEL } from '@/Singra/UI'
import type { VaultItem } from './vaultEintrag'

interface Props {
  ordner: VaultItem[]
  /** Geöffneter Ordner, `undefined` ist das Stammverzeichnis. */
  aktuell: string | undefined
  /** Ordner vom Stamm bis zum geöffneten. */
  pfad: VaultItem[]
  onWaehlen: (id: string | undefined) => void
  /** Ablegen auf einem Ordner; `undefined` ist das Stammverzeichnis. */
  ablage?: {
    kannAblegen: (id: string | undefined, daten: DataTransfer) => boolean
    onAblegen: (id: string | undefined, daten: DataTransfer) => void
  }
}

/** So lange schwebt etwas über einem zugeklappten Ordner, bis er aufgeht. */
const AUFKLAPPEN_NACH_MS = 700

export function TresorOrdnerBaum({ ordner, aktuell, pfad, onWaehlen, ablage }: Props) {
  const { t } = useTranslation()
  /** Von Hand auf- oder zugeklappt; sonst ist nur der Weg zum geöffneten Ordner offen. */
  const [geklappt, setGeklappt] = useState<Map<string, boolean>>(new Map())

  const kinder = useMemo(() => {
    const je = new Map<string | undefined, VaultItem[]>()
    for (const o of ordner) je.set(o.ordner, [...(je.get(o.ordner) ?? []), o])
    for (const liste of je.values()) liste.sort((a, b) => a.service.localeCompare(b.service))
    return je
  }, [ordner])
  const aufDemWeg = new Set(pfad.map((o) => o.id))
  const pfadSchluessel = pfad.map((o) => o.id).join('/')

  // Wer einen Ordner öffnet, sieht den Weg dorthin, auch wenn er ihn vorher zugeklappt hatte.
  useEffect(() => {
    setGeklappt((alt) => {
      const neu = new Map(alt)
      for (const id of pfadSchluessel.split('/')) neu.delete(id)
      return neu
    })
  }, [pfadSchluessel])

  const offen = (id: string) => geklappt.get(id) ?? aufDemWeg.has(id)
  const umschalten = (id: string) => setGeklappt((alt) => new Map(alt).set(id, !offen(id)))

  /** Ordner, über dem gerade etwas gezogen wird; `''` ist der Stamm. */
  const [ziel, setZiel] = useState<string | null>(null)
  const aufklappen = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(aufklappen.current), [])

  const ablageZiel = (id: string | undefined, zu: boolean) =>
    ablage
      ? {
          onDragEnter: () => {
            window.clearTimeout(aufklappen.current)
            if (id && zu) aufklappen.current = window.setTimeout(() => setGeklappt((alt) => new Map(alt).set(id, true)), AUFKLAPPEN_NACH_MS)
          },
          onDragOver: (event: React.DragEvent) => {
            if (!ablage.kannAblegen(id, event.dataTransfer)) return
            event.preventDefault()
            setZiel(id ?? '')
          },
          onDragLeave: () => {
            window.clearTimeout(aufklappen.current)
            setZiel((z) => (z === (id ?? '') ? null : z))
          },
          onDrop: (event: React.DragEvent) => {
            event.preventDefault()
            event.stopPropagation()
            window.clearTimeout(aufklappen.current)
            setZiel(null)
            ablage.onAblegen(id, event.dataTransfer)
          },
        }
      : {}

  const zeile = (aktiv: boolean, markiert: boolean) =>
    `flex min-w-0 flex-1 items-center gap-1.5 rounded-lg px-1.5 py-1 text-left text-xs ${
      markiert
        ? ABLAGEZIEL
        : aktiv
          ? 'bg-primary/10 font-semibold text-primary'
          : 'text-on-surface hover:bg-surface-container-high'
    }`

  const ebene = (eltern: string | undefined, tiefe: number): ReactNode => {
    const liste = kinder.get(eltern)
    if (!liste?.length || tiefe > 50) return null
    return (
      <ul>
        {liste.map((o) => {
          const hatKinder = !!kinder.get(o.id)?.length
          const istOffen = hatKinder && offen(o.id)
          const Icon = istOffen ? FolderOpen : Folder
          return (
            <li key={o.id}>
              <div className="flex items-center" style={{ paddingLeft: `${tiefe * 0.75}rem` }}>
                <button
                  type="button"
                  className={`flex h-6 w-5 shrink-0 items-center justify-center text-on-surface-variant ${hatKinder ? '' : 'invisible'}`}
                  onClick={() => umschalten(o.id)}
                  aria-label={t(istOffen ? 'mss.vault.dateien.zuklappen' : 'mss.vault.dateien.aufklappen', { name: o.service })}
                  aria-expanded={hatKinder ? istOffen : undefined}
                  tabIndex={hatKinder ? 0 : -1}
                >
                  {istOffen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                </button>
                <button
                  type="button"
                  className={zeile(aktuell === o.id, ziel === o.id)}
                  aria-current={aktuell === o.id ? 'page' : undefined}
                  onClick={() => onWaehlen(o.id)}
                  {...ablageZiel(o.id, hatKinder && !istOffen)}
                >
                  <Icon className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
                  <span className="truncate">{o.service}</span>
                </button>
              </div>
              {istOffen && ebene(o.id, tiefe + 1)}
            </li>
          )
        })}
      </ul>
    )
  }

  return (
    <nav aria-label={t('mss.vault.dateien.ordnerBaum')}>
      <ul>
        <li>
          <div className="flex items-center">
            <button
              type="button"
              className={zeile(aktuell === undefined, ziel === '')}
              aria-current={aktuell === undefined ? 'page' : undefined}
              onClick={() => onWaehlen(undefined)}
              {...ablageZiel(undefined, false)}
            >
              <HardDrive className="h-3.5 w-3.5 shrink-0" aria-hidden />
              <span className="truncate">{t('mss.vault.dateien.stamm')}</span>
            </button>
          </div>
          {ebene(undefined, 1)}
        </li>
      </ul>
    </nav>
  )
}
