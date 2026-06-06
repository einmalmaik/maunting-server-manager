import { useTranslation } from 'react-i18next'
import type { LucideIcon } from 'lucide-react'

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
  icon: LucideIcon
  /** Wird in der Tabs-Reihenfolge zuerst versteckt (z.B. fuer Danger-Zone). */
  variant?: 'default' | 'danger'
}

interface TabBarProps<TId extends string> {
  tabs: TabDef<TId>[]
  active: TId
  onChange: (id: TId) => void
  /** Optionaler a11y-Label fuer die umschliessende Tab-Liste. */
  ariaLabel?: string
}

/**
 * Gemeinsame Tab-Leiste. Wird in `Settings.tsx` und `Profile.tsx` eingesetzt,
 * damit beide Seiten dasselbe Verhalten, dieselben i18n-Keys und dasselbe Design
 * teilen. Die Auswahl der Tabs liegt weiterhin in der jeweiligen Orchestrator-Komponente.
 */
export function TabBar<TId extends string>({ tabs, active, onChange, ariaLabel }: TabBarProps<TId>) {
  const { t } = useTranslation()

  return (
    <div role="tablist" aria-label={ariaLabel} className="msm-card p-2 inline-flex flex-wrap gap-1">
      {tabs.map((tab) => {
        const Icon = tab.icon
        const isActive = active === tab.id
        const isDanger = tab.variant === 'danger'
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(tab.id)}
            className={`px-4 py-2 rounded-md text-sm font-medium inline-flex items-center gap-2 transition-colors ${
              isActive
                ? isDanger
                  ? 'bg-status-error/15 text-status-error'
                  : 'bg-secondary-container text-on-secondary-container'
                : isDanger
                  ? 'text-status-error/80 hover:bg-status-error/10'
                  : 'text-on-surface-variant hover:bg-surface-container-high'
            }`}
          >
            <Icon className="w-4 h-4" />
            {t(tab.labelKey)}
          </button>
        )
      })}
    </div>
  )
}
