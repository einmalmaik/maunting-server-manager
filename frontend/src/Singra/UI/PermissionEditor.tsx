import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Search, Info, Check, X } from 'lucide-react'
import type { PermissionDef } from '@/types/permissions'

type Uebersetzer = ReturnType<typeof useTranslation>['t']

/**
 * Rechteschlüssel tragen Punkte ('server.files.read'), und i18next liest den
 * Punkt als Ebenentrenner. Der Unterstrich hält den Übersetzungsschlüssel
 * deshalb flach: 'permissionDetails.server_files_read.title'.
 */
const detailSchluessel = (key: string) => `permissionDetails.${key.replace(/\./g, '_')}`

/**
 * Kennt die Sprachdatei ein Recht nicht, bleibt der deutsche Text aus dem
 * Backend-Katalog stehen. Das ist der Fall für Rechte, die nach dieser Datei
 * dazugekommen sind — lieber ein deutscher Satz als ein roher Schlüssel.
 */
function titelVon(t: Uebersetzer, def: PermissionDef) {
  return t(`${detailSchluessel(def.key)}.title`, { defaultValue: def.label })
}

function beschreibungVon(t: Uebersetzer, def: PermissionDef) {
  return t(`${detailSchluessel(def.key)}.desc`, { defaultValue: def.label })
}

const SUBGROUPS = [
  {
    id: 'users',
    keys: ['users.read', 'users.manage', 'users.permissions.manage', 'roles.manage', 'teams.create'],
  },
  {
    id: 'panel',
    keys: [
      'panel.settings.read',
      'panel.settings.write',
      'panel.database.read',
      'panel.database.admin',
      'panel.oauth.read',
      'panel.oauth.create',
      'panel.oauth.update',
      'panel.oauth.delete',
      'panel.oauth.secret_update',
      'panel.oauth.test',
      'panel.hoster.read',
      'panel.hoster.write',
    ],
  },
  {
    id: 'ai',
    keys: [
      'ai.chat.use',
      'ai.voice.use',
      'ai.attachments.use',
      'ai.memory.use',
      'ai.skills.use',
      'ai.skills.manage',
      'ai.web_search.use',
      'ai.autonomous.use',
      'ai.tasks.manage',
      'ai.background.use',
      'ai.desktop.use',
      'ai.desktop.install',
      'ai.mailbox.use',
      'ai.calendar.use',
      'ai.usage.read.all',
    ],
  },
  {
    id: 'infrastructure',
    keys: [
      'servers.create',
      'servers.delete',
      'servers.hoster_customers.view',
      'blueprints.manage',
      'nodes.read',
      'nodes.manage',
      'system.view',
      'system.audit.read',
      'system.secrets.rotate',
    ],
  },
  {
    id: 'server_basic',
    // 'server.update' heißt in der Anzeige „Outbound-Webhooks verwalten": geprüft
    // wird das Recht nur in routers/webhooks_outbound.py. Wer die Spieldateien neu
    // holen darf, entscheidet 'server.install'. Der Schlüssel behält seinen
    // historischen Namen, weil er in bereits vergebenen Rollen steckt.
    keys: [
      'server.view',
      'server.start',
      'server.stop',
      'server.restart',
      'server.kill',
      'server.install',
      'server.update',
    ],
  },
  {
    id: 'server_config',
    keys: [
      'server.config.write',
      'server.network.manage',
      'server.resources.manage',
      'server.credentials.manage',
    ],
  },
  {
    id: 'server_console',
    keys: ['server.console.read', 'server.console.write', 'server.console.exec'],
  },
  {
    id: 'server_files',
    keys: [
      'server.files.read',
      'server.files.write',
      'server.files.delete',
      'server.backups.read',
      'server.backups.create',
      'server.backups.restore',
      'server.backups.delete',
    ],
  },
  {
    id: 'server_features',
    keys: [
      'server.mods.read',
      'server.mods.write',
      'server.mods.toggle',
      'server.databases.read',
      'server.databases.write',
      'server.databases.admin',
    ],
  },
]

interface PermissionEditorProps {
  permissions: PermissionDef[]
  selected: Set<string>
  onChange: (selected: Set<string>) => void
  disabled?: boolean
}

export function PermissionEditor({
  permissions,
  selected,
  onChange,
  disabled = false,
}: PermissionEditorProps) {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const [hoveredKey, setHoveredKey] = useState<string | null>(null)

  // Map permissions by key for fast lookup
  const permissionMap = useMemo(() => {
    return new Map(permissions.map((p) => [p.key, p]))
  }, [permissions])

  // Filter permission definitions based on search query
  const filteredDefs = useMemo(() => {
    if (!search.trim()) return permissions
    const query = search.toLowerCase()
    return permissions.filter((p) => {
      return (
        p.key.toLowerCase().includes(query) ||
        titelVon(t, p).toLowerCase().includes(query) ||
        beschreibungVon(t, p).toLowerCase().includes(query) ||
        p.label.toLowerCase().includes(query)
      )
    })
  }, [permissions, search, t])

  // Group filtered definitions
  const groupedData = useMemo(() => {
    const groups: { id: string; defs: PermissionDef[] }[] = []
    const mappedKeys = new Set<string>()

    // Predefined groups
    for (const group of SUBGROUPS) {
      const defsInGroup = filteredDefs.filter((p) => group.keys.includes(p.key))
      if (defsInGroup.length > 0) {
        groups.push({
          id: group.id,
          defs: defsInGroup,
        })
        defsInGroup.forEach((p) => mappedKeys.add(p.key))
      }
    }

    // Remaining items (fallback for future permissions)
    const remainingDefs = filteredDefs.filter((p) => !mappedKeys.has(p.key))
    if (remainingDefs.length > 0) {
      groups.push({
        id: 'other',
        defs: remainingDefs,
      })
    }

    return groups
  }, [filteredDefs])

  const togglePermission = (key: string) => {
    if (disabled) return
    const next = new Set(selected)
    if (next.has(key)) {
      next.delete(key)
    } else {
      next.add(key)
    }
    onChange(next)
  }

  const handleSelectVisible = () => {
    if (disabled) return
    const next = new Set(selected)
    filteredDefs.forEach((p) => next.add(p.key))
    onChange(next)
  }

  const handleDeselectVisible = () => {
    if (disabled) return
    const next = new Set(selected)
    filteredDefs.forEach((p) => next.delete(p.key))
    onChange(next)
  }

  // Get description for hovered or first selected permission
  const getInfoDisplay = () => {
    const activeKey = hoveredKey
    if (!activeKey) return null
    const def = permissionMap.get(activeKey)
    if (!def) return { key: activeKey, title: activeKey, desc: '' }
    return {
      key: activeKey,
      title: titelVon(t, def),
      desc: beschreibungVon(t, def),
    }
  }

  const info = getInfoDisplay()

  return (
    <div className="space-y-4">
      {/* Search and Quick Actions */}
      <div className="flex flex-col sm:flex-row gap-3 items-center justify-between">
        <div className="relative w-full sm:max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-on-surface-variant" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('permissionEditor.searchPlaceholder')}
            className="msm-input pl-9 py-1.5 text-xs font-label-md"
            disabled={disabled && permissions.length === 0}
          />
          {search && (
            <button
              onClick={() => setSearch('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface"
              type="button"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        {!disabled && permissions.length > 0 && (
          <div className="flex gap-2 w-full sm:w-auto justify-end">
            <button
              type="button"
              onClick={handleSelectVisible}
              className="msm-btn-secondary text-xs px-3 py-1.5"
            >
              {t('permissionEditor.selectAll')}
            </button>
            <button
              type="button"
              onClick={handleDeselectVisible}
              className="msm-btn-secondary text-xs px-3 py-1.5"
            >
              {t('permissionEditor.deselectAll')}
            </button>
          </div>
        )}
      </div>

      {/* Permissions Grid */}
      <div className="space-y-6 max-h-[380px] overflow-y-auto pr-1">
        {groupedData.length === 0 ? (
          <div className="p-8 text-center text-on-surface-variant bg-surface-container-low/40 rounded-lg border border-outline-variant/30 font-body-md text-sm">
            {t('permissionEditor.empty')}
          </div>
        ) : (
          groupedData.map((group) => (
            <div key={group.id} className="space-y-2.5">
              <h4 className="font-label-md text-xs text-on-surface-variant uppercase tracking-wider pl-1">
                {t(`permissionEditor.groups.${group.id}`)}
              </h4>
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
                {group.defs.map((def) => {
                  const title = titelVon(t, def)
                  const isChecked = selected.has(def.key)
                  const id = `perm-editor-${def.key}`

                  return (
                    <div
                      key={def.key}
                      onMouseEnter={() => setHoveredKey(def.key)}
                      onMouseLeave={() => setHoveredKey(null)}
                      onClick={() => !disabled && togglePermission(def.key)}
                      className={`p-3 rounded-lg border text-left transition-all duration-150 flex items-start gap-3 select-none relative group ${
                        disabled ? 'opacity-65' : 'cursor-pointer'
                      } ${
                        isChecked
                          ? 'bg-primary/5 border-primary/40 shadow-sm shadow-primary/5'
                          : 'bg-surface-container-high/30 border-outline-variant/40 hover:bg-surface-container-high/60 hover:border-outline-variant'
                      }`}
                    >
                      <div className="mt-0.5 shrink-0">
                        <div
                          className={`w-4 h-4 rounded border flex items-center justify-center transition-colors ${
                            isChecked
                              ? 'bg-primary border-primary text-on-primary'
                              : 'bg-surface-container border-outline-variant'
                          }`}
                        >
                          {isChecked && <Check className="w-3 h-3 stroke-[3]" />}
                        </div>
                        {/*
                          Der zugängliche Name hängt an aria-labelledby und bewusst nicht an
                          einem <label htmlFor>: Den Umschalter trägt das umschließende <div>
                          mit onClick. Ein Label würde beim Klick auf den Titel zusätzlich
                          einen Klick auf das Eingabefeld auslösen, sodass derselbe Handler
                          zweimal liefe (nachgemessen: zwei Aufrufe pro Klick) — heute
                          unauffällig, weil React beide aus demselben Zustand berechnet, aber
                          eine Falle, die wir uns für einen bloßen Namen nicht einhandeln.
                          aria-labelledby vergibt den Namen, ohne den Klickweg anzufassen.
                          Ohne ihn meldet ein Screenreader für jedes der rund 90 Rechte nur
                          "Kontrollkästchen, nicht aktiviert", weil sr-only clip ist und die
                          Checkbox damit im Fokus bleibt, aber namenlos.
                        */}
                        <input
                          id={id}
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => {}} // handled by click container
                          disabled={disabled}
                          aria-labelledby={`${id}-title`}
                          className="sr-only"
                        />
                      </div>
                      <div className="flex flex-col gap-0.5 min-w-0">
                        <span
                          id={`${id}-title`}
                          className="font-label-md text-xs font-semibold text-on-surface group-hover:text-primary transition-colors truncate"
                        >
                          {title}
                        </span>
                        <span className="font-mono text-[10px] text-on-surface-variant/80 truncate">
                          {def.key}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Dynamic Explanation Panel */}
      <div className="p-3.5 rounded-lg border border-outline-variant/60 bg-surface-container-low min-h-[76px] flex flex-col justify-center transition-all duration-200">
        {info ? (
          <div className="space-y-0.5">
            <div className="flex items-center gap-1.5">
              <Info className="w-3.5 h-3.5 text-primary shrink-0" />
              <span className="font-label-md text-xs font-bold text-on-surface">
                {info.title}
              </span>
              <span className="font-mono text-[10px] text-on-surface-variant/70 bg-surface-container-high px-1.5 py-0.5 rounded ml-auto">
                {info.key}
              </span>
            </div>
            <p className="text-xs text-on-surface-variant mt-1 leading-relaxed">
              {info.desc}
            </p>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-on-surface-variant/60 italic text-xs">
            <Info className="w-3.5 h-3.5" />
            <span>{t('permissionEditor.hoverHint')}</span>
          </div>
        )}
      </div>
    </div>
  )
}
