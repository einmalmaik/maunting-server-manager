import { Fragment } from 'react'
import { useTranslation } from 'react-i18next'
import type { LucideIcon } from 'lucide-react'

import { Kurzinfo } from '@/Singra/UI/Kurzinfo'

/**
 * Definiert einen einzelnen Tab fuer den gemeinsamen {@link TabBar}.
 *
 * Das ist absichtlich ein generisches Modul, damit sowohl die Panel-Einstellungen
 * (`/settings`) als auch die Profil-Seite (`/profile`) die identische Tab-Logik
 * verwenden. Aenderungen am Verhalten wirken damit automatisch auf beide Seiten.
 */
export interface TabDef<TId extends string> {
  id: TId
  labelKey: string
  /** Ohne Symbol bleibt nur der Name (dichte Leisten wie in den Entwicklerwerkzeugen). */
  icon?: LucideIcon
  /** Wird in der Tabs-Reihenfolge zuerst versteckt (z.B. fuer Danger-Zone). */
  variant?: 'default' | 'danger'
  /** Optionale Zahl hinter dem Namen, etwa die Mitglieder eines Teams. */
  badge?: number
}

interface TabBarProps<TId extends string> {
  tabs: TabDef<TId>[]
  active: TId
  onChange: (id: TId) => void
  /** Optionaler a11y-Label fuer die umschliessende Tab-Liste. */
  ariaLabel?: string
  /**
   * Innerhalb einer Karte ohne eigene Karte darum — sonst stünde eine Karte in
   * der Karte.
   */
  embedded?: boolean
  /**
   * Am Handy eine Zeile, die sich seitlich wischen lässt, statt umzubrechen.
   * Ab md wie gewohnt.
   */
  einzeilig?: boolean
  /**
   * Kleiner und enger, für Werkzeugleisten in schmalen Panels. Unter md zeigen
   * Reiter mit Symbol nur das Symbol, der gewählte behält seinen Namen; den
   * Namen der übrigen nennt eine Kurzinfo.
   */
  kompakt?: boolean
}

/**
 * Gemeinsame Tab-Leiste. Wird in `Settings.tsx` und `Profile.tsx` eingesetzt,
 * damit beide Seiten dasselbe Verhalten, dieselben i18n-Keys und dasselbe Design
 * teilen. Die Auswahl der Tabs liegt weiterhin in der jeweiligen Orchestrator-Komponente.
 */
export function TabBar<TId extends string>({ tabs, active, onChange, ariaLabel, embedded = false, einzeilig = false, kompakt = false }: TabBarProps<TId>) {
  const { t } = useTranslation()

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={`${embedded ? `rounded-lg bg-surface-container-low/50 ${kompakt ? 'p-0.5' : 'p-1'}` : 'msm-card p-2'} inline-flex flex-wrap ${kompakt ? 'gap-0.5' : 'gap-1'} ${
        einzeilig ? 'max-md:flex max-md:w-full max-md:flex-nowrap max-md:overflow-x-auto msm-ohne-rollbalken' : ''
      }`}
    >
      {tabs.map((tab) => {
        const Icon = tab.icon
        const isActive = active === tab.id
        const isDanger = tab.variant === 'danger'
        const name = t(tab.labelKey)
        const nurSymbol = kompakt && !!Icon && !isActive
        const knopf = (
          <button
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={(e) => {
              onChange(tab.id)
              if (einzeilig) e.currentTarget.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
            }}
            className={`${kompakt ? `gap-1.5 px-2.5 py-1 text-xs [@media(pointer:coarse)]:min-h-11 ${nurSymbol ? 'max-md:px-1.5' : ''}` : 'gap-2 px-4 py-2 text-sm'} rounded-md font-medium inline-flex shrink-0 items-center transition-colors ${
              einzeilig ? 'max-md:min-h-11' : ''
            } ${
              isActive
                ? isDanger
                  ? 'bg-status-destructive/15 text-status-destructive'
                  : 'bg-secondary-container text-on-secondary-container'
                : isDanger
                  ? 'text-status-destructive/80 hover:bg-status-destructive/10'
                  : 'text-on-surface-variant hover:bg-surface-container-high'
            }`}
          >
            {Icon && <Icon className={kompakt ? 'h-3.5 w-3.5' : 'h-4 w-4'} aria-hidden="true" />}
            {nurSymbol ? <span className="max-md:sr-only">{name}</span> : name}
            {tab.badge !== undefined && (
              <>
                {/* Ohne Trenner liest ein Screenreader „Papierkorb1“. */}
                <span className="sr-only">, </span>
                <span className="min-w-[1.25rem] rounded-full bg-surface-container-high px-1.5 text-center text-xs tabular-nums">
                  {tab.badge}
                </span>
              </>
            )}
          </button>
        )
        // Die Hülle bleibt beim Wechsel des Reiters dieselbe, sonst verlöre der Knopf den Fokus.
        return kompakt ? (
          <Kurzinfo key={tab.id} text={name} className={nurSymbol ? 'md:!hidden' : '!hidden'}>
            {knopf}
          </Kurzinfo>
        ) : (
          <Fragment key={tab.id}>{knopf}</Fragment>
        )
      })}
    </div>
  )
}
