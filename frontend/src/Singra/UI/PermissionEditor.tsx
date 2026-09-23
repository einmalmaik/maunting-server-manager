import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  FolderOpen,
  Power,
  Puzzle,
  Search,
  Server,
  Settings2,
  Shield,
  Sliders,
  Sparkles,
  Terminal,
  Users,
  X,
  type LucideIcon,
} from 'lucide-react'
import type { PermissionDef } from '@/types/permissions'
import { Button } from '@/Singra/UI'
import { RechteAbschnitte, type RechteAbschnittDefinition, type RechteZeile } from './RechteAbschnitte'
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
      'panel.popups.manage',
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
      'ai.satellite.use',
      'ai.autonomous.use',
      'ai.tasks.manage',
      'ai.background.use',
      'ai.desktop.use',
      'ai.desktop.install',
      'ai.mailbox.use',
      'ai.calendar.use',
      'ai.notes.use',
      'ai.popups.manage',
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
      'cloudflare.manage',
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

/**
 * Ein Schild je Gruppe.
 *
 * Steht hier und nicht in `RechteAbschnitte`: das Bauteil soll das
 * Panelvokabular nicht kennen. Die Gruppenkennungen sind dieselben wie in
 * `SUBGROUPS`; eine ohne Eintrag bekommt das Schild von `other`.
 */
const GRUPPEN_SYMBOLE: Record<string, LucideIcon> = {
  users: Users,
  panel: Sliders,
  ai: Sparkles,
  infrastructure: Server,
  server_basic: Power,
  server_config: Settings2,
  server_console: Terminal,
  server_files: FolderOpen,
  server_features: Puzzle,
  other: Shield,
}

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

  /**
   * Welche Gruppe ein Recht trägt. Was in keiner `SUBGROUPS`-Liste steht,
   * fällt auf `other` — so bleibt ein Recht, das das Backend neu ausliefert,
   * sichtbar, statt lautlos aus der Ansicht zu fallen.
   */
  const gruppeVon = useMemo(() => {
    const zuordnung = new Map<string, string>()
    for (const gruppe of SUBGROUPS) {
      for (const key of gruppe.keys) zuordnung.set(key, gruppe.id)
    }
    return zuordnung
  }, [])

  const zeilen = useMemo<RechteZeile[]>(
    () =>
      filteredDefs.map((def) => ({
        key: def.key,
        kategorie: gruppeVon.get(def.key) ?? 'other',
        titel: titelVon(t, def),
        beschreibung: beschreibungVon(t, def),
        // Die rohe Kennung bleibt sichtbar: sie steht in Fehlermeldungen, im
        // Prüfprotokoll und in der Hoster-API, und ein Betreiber, der einem
        // Bericht nachgeht, sucht genau danach.
        kennung: def.key,
      })),
    [filteredDefs, gruppeVon, t],
  )

  const abschnitte = useMemo<RechteAbschnittDefinition[]>(
    () =>
      [...SUBGROUPS.map((g) => g.id), 'other'].map((id) => ({
        titel: t(`permissionEditor.groups.${id}`),
        symbol: GRUPPEN_SYMBOLE[id] ?? Shield,
        kategorien: [id],
      })),
    [t],
  )

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
            <Button variant="secondary" size="sm"
              type="button"
              onClick={handleSelectVisible}
            >
              {t('permissionEditor.selectAll')}
            </Button>
            <Button variant="secondary" size="sm"
              type="button"
              onClick={handleDeselectVisible}
            >
              {t('permissionEditor.deselectAll')}
            </Button>
          </div>
        )}
      </div>

      {/*
        Dasselbe Bauteil wie im Messenger — siehe `RechteAbschnitte`.
        Hier stand bis 09/2026 ein eigenes dreispaltiges Kachelraster, in dem
        nur Titel und rohe Kennung Platz hatten. Die **Beschreibung** bekam man
        erst zu sehen, wenn man mit der Maus über eine Kachel fuhr; sie
        erschien dann unten in einem eigenen Erklärfeld. Auf einem Gerät ohne
        Maus gab es sie also gar nicht, und wer wissen wollte, was drei Rechte
        tun, musste sie nacheinander überfahren und sich den Text merken.

        Jetzt steht die Beschreibung in der Zeile. Damit ist das Erklärfeld
        ersatzlos entfallen — es war nie eine Funktion, sondern der Ausgleich
        für fehlenden Platz.
      */}
      <div className="max-h-[380px] overflow-y-auto pr-1">
        {zeilen.length === 0 ? (
          <div className="p-8 text-center text-on-surface-variant bg-surface-container-low/40 rounded-lg border border-outline-variant/30 font-body-md text-sm">
            {t('permissionEditor.empty')}
          </div>
        ) : (
          <RechteAbschnitte
            rechte={zeilen}
            abschnitte={abschnitte}
            gesetzt={selected}
            onToggle={(key) => togglePermission(key)}
            disabled={disabled}
            zeilenBeschriftung={(titel) => t('permissionEditor.allow', { name: titel })}
          />
        )}
      </div>
    </div>
  )
}
