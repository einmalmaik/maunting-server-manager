import type { LucideIcon } from 'lucide-react'
import { Search } from 'lucide-react'
import type { ReactNode } from 'react'

interface Props {
  icon: LucideIcon
  title: string
  description: string
  /** Rechts in der Kopfzeile — etwa der Schalter „Gedächtnis aktiv". */
  headerAction?: ReactNode
  /** Suchfeld: weglassen, wenn eine Liste zu kurz zum Filtern ist. */
  search?: { value: string; onChange: (value: string) => void; label: string }
  /** Filter-Chips und Massenaktionen, rechts neben der Suche. */
  filters?: ReactNode
  /** Wieviel von wievielem gerade sichtbar ist. */
  count?: string
  /** Ein Satz unter der Kopfzeile — was dieser Bereich umfasst und was nicht. */
  note?: string
  children: ReactNode
}

/**
 * Die gemeinsame Hülle für Erinnerungen und Skills.
 *
 * Beide Bereiche beantworten dieselbe Frage — „was weiß der Assistent?" — und
 * tauchen an denselben zwei Stellen auf: im Profil für einen selbst, unter
 * Teams für das Team. Deshalb eine Hülle statt zweier ähnlicher Karten: wer
 * gelernt hat, wo die Suche steht, hat es für beide gelernt.
 *
 * Bewusst nur Rahmen und Werkzeugleiste. Liste und Formular unterscheiden sich
 * wirklich (ein Schlüssel-Wert-Paar ist kein mehrseitiger Text), und sie
 * gleichzumachen hieße, beides schlechter zu machen.
 */
export function AiKnowledgeShell({
  icon: Icon, title, description, headerAction, search, filters, count, note, children,
}: Props) {
  return (
    <section className="msm-card space-y-5 p-6" aria-label={title}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Icon className="h-5 w-5 text-secondary" aria-hidden="true" />
            <h2 className="font-headline text-lg font-semibold text-on-surface">{title}</h2>
          </div>
          <p className="mt-2 max-w-3xl text-sm text-on-surface-variant">{description}</p>
        </div>
        {headerAction}
      </div>

      {note && (
        <p className="max-w-3xl rounded-lg border border-outline-variant/40 bg-surface-container-low/45 p-3 text-xs leading-5 text-on-surface-variant">
          {note}
        </p>
      )}

      {(search || filters) && (
        <div className="flex flex-wrap items-center gap-3">
          {search && (
            <label className="relative min-w-[14rem] flex-1">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-on-surface-variant"
                aria-hidden="true"
              />
              <input
                type="search"
                className="msm-input pl-9"
                value={search.value}
                onChange={(event) => search.onChange(event.target.value)}
                placeholder={search.label}
                aria-label={search.label}
              />
            </label>
          )}
          {filters}
        </div>
      )}

      {count && <p className="text-xs text-on-surface-variant">{count}</p>}

      {children}
    </section>
  )
}
