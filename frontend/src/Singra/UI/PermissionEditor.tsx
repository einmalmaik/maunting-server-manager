import { useState, useMemo } from 'react'
import { Search, Info, Check, X } from 'lucide-react'
import type { PermissionDef } from '@/types/permissions'

const PERMISSION_DETAILS: Record<string, { title: string; desc: string }> = {
  'users.read': {
    title: 'Benutzerliste einsehen',
    desc: 'Erlaubt das Auflisten und Betrachten aller registrierten Benutzer im System.',
  },
  'users.manage': {
    title: 'Benutzer verwalten',
    desc: 'Erlaubt das Erstellen, Bearbeiten und Löschen von Benutzern im Panel.',
  },
  'users.permissions.manage': {
    title: 'Berechtigungen zuweisen',
    desc: 'Erlaubt das Zuweisen von Rollen an Benutzer sowie das Delegieren von Server-Rechten.',
  },
  'roles.manage': {
    title: 'Rollen verwalten',
    desc: 'Erlaubt das Erstellen, Ändern und Löschen von Berechtigungsrollen.',
  },
  'panel.settings.read': {
    title: 'Einstellungen lesen',
    desc: 'Erlaubt das Einsehen der allgemeinen Panel-Konfiguration.',
  },
  'panel.settings.write': {
    title: 'Einstellungen bearbeiten',
    desc: 'Erlaubt das Ändern globaler Panel-Einstellungen (z.B. SMTP, Steam, Backups).',
  },
  'panel.database.read': {
    title: 'Panel-Datenbank lesen',
    desc: 'Erlaubt das Einsehen von Systemdatenbank-Tabellen (nur für Diagnose).',
  },
  'panel.database.admin': {
    title: 'Panel-Datenbank verwalten',
    desc: 'Erlaubt administrative Aufgaben und direkte Änderungen an der Panel-Datenbank.',
  },
  'servers.create': {
    title: 'Server erstellen',
    desc: 'Erlaubt das Erstellen neuer Gameserver auf verfügbaren Nodes.',
  },
  'servers.delete': {
    title: 'Server löschen',
    desc: 'Erlaubt das dauerhafte Löschen von Gameservern aus dem System (global, destruktiv).',
  },
  'system.view': {
    title: 'Systemstatus anzeigen',
    desc: 'Erlaubt das Betrachten der Systemauslastung, Log-Dateien und Netzwerkschnittstellen des Host-Systems.',
  },
  'nodes.read': {
    title: 'Nodes anzeigen',
    desc: 'Erlaubt das Auflisten der Nodes (Infrastruktur-Server) und deren Systemauslastung.',
  },
  'nodes.manage': {
    title: 'Nodes verwalten',
    desc: 'Erlaubt das Hinzufügen, Editieren, Löschen und Registrieren von Nodes.',
  },
  'panel.oauth.read': {
    title: 'OAuth-Anbieter anzeigen',
    desc: 'Erlaubt das Einsehen der konfigurierten OAuth/Social-Login-Anbieter.',
  },
  'panel.oauth.create': {
    title: 'OAuth-Anbieter erstellen',
    desc: 'Erlaubt das Hinzufügen neuer OAuth2-Identitätsprovider.',
  },
  'panel.oauth.update': {
    title: 'OAuth-Anbieter bearbeiten',
    desc: 'Erlaubt das Ändern von Client-IDs und Endpunkten der Login-Provider.',
  },
  'panel.oauth.delete': {
    title: 'OAuth-Anbieter löschen',
    desc: 'Erlaubt das Löschen von Social-Login-Anbietern.',
  },
  'panel.oauth.secret_update': {
    title: 'OAuth Client-Secret ändern',
    desc: 'Erlaubt das Rotieren und Aktualisieren des OAuth Client-Secrets.',
  },
  'panel.oauth.test': {
    title: 'OAuth-Verbindung testen',
    desc: 'Erlaubt das Testen der Authentifizierungsverbindung zum OAuth-Provider.',
  },
  'server.view': {
    title: 'Server anzeigen',
    desc: 'Erlaubt das Betrachten des Servers in der Liste und das Öffnen der Detail-Seiten.',
  },
  'server.start': {
    title: 'Server starten',
    desc: 'Erlaubt das Starten des Spieleservers.',
  },
  'server.stop': {
    title: 'Server stoppen',
    desc: 'Erlaubt das Herunterfahren des Spieleservers.',
  },
  'server.restart': {
    title: 'Server neustarten',
    desc: 'Erlaubt den Neustart des Spieleservers.',
  },
  'server.kill': {
    title: 'Server stoppen erzwingen',
    desc: 'Erlaubt das sofortige Beenden (SIGKILL) des Containers bei Hängern.',
  },
  'server.install': {
    title: 'Server installieren',
    desc: 'Erlaubt das (Neu-)Installieren des Servers über das Installationsskript.',
  },
  'server.update': {
    title: 'Server aktualisieren',
    desc: 'Erlaubt das Aktualisieren der Spieldateien und Outbound-Webhooks.',
  },
  'server.config.write': {
    title: 'Einstellungen anpassen',
    desc: 'Erlaubt das Ändern von Servername, Auto-Restart und Startparametern.',
  },
  'server.network.manage': {
    title: 'Netzwerk bearbeiten',
    desc: 'Erlaubt das Zuweisen von Ports und Ändern der Bind-IP.',
  },
  'server.resources.manage': {
    title: 'Ressourcen anpassen',
    desc: 'Erlaubt das Festlegen von CPU-, RAM- und Festplatten-Limits.',
  },
  'server.console.read': {
    title: 'Konsole mitlesen',
    desc: 'Erlaubt das Betrachten der Live-Konsole und Logs.',
  },
  'server.console.write': {
    title: 'Befehle senden',
    desc: 'Erlaubt das Senden von Spielbefehlen an die Server-Konsole.',
  },
  'server.console.exec': {
    title: 'Exec ausführen',
    desc: 'Erlaubt das Ausführen beliebiger Befehle im Server-Container.',
  },
  'server.files.read': {
    title: 'Dateien anzeigen',
    desc: 'Erlaubt das Browsen und Herunterladen von Spieldateien.',
  },
  'server.files.write': {
    title: 'Dateien hochladen/ändern',
    desc: 'Erlaubt das Erstellen, Editieren, Hochladen und Entpacken von Dateien.',
  },
  'server.files.delete': {
    title: 'Dateien löschen',
    desc: 'Erlaubt das Löschen von Dateien aus dem Dateisystem des Servers.',
  },
  'server.backups.read': {
    title: 'Backups anzeigen',
    desc: 'Erlaubt das Auflisten der Backups.',
  },
  'server.backups.create': {
    title: 'Backups erstellen',
    desc: 'Erlaubt das Erstellen von Server-Sicherungen.',
  },
  'server.backups.restore': {
    title: 'Backups einspielen',
    desc: 'Erlaubt das Wiederherstellen von Spieldateien aus einem Backup.',
  },
  'server.backups.delete': {
    title: 'Backups löschen',
    desc: 'Erlaubt das dauerhafte Löschen von Server-Sicherungen.',
  },
  'server.mods.read': {
    title: 'Mods anzeigen',
    desc: 'Erlaubt das Auflisten installierter Mods und das Durchsuchen des Steam Workshops.',
  },
  'server.mods.write': {
    title: 'Mods verwalten',
    desc: 'Erlaubt das Abonnieren, Sortieren und Deinstallieren von Workshop-Mods.',
  },
  'server.mods.toggle': {
    title: 'Mods ein/ausschalten',
    desc: 'Erlaubt das temporäre Aktivieren oder Deaktivieren installierter Mods.',
  },
  'server.databases.read': {
    title: 'PostgreSQL lesen',
    desc: 'Erlaubt das Betrachten der PostgreSQL-Datenbanken des Servers.',
  },
  'server.databases.write': {
    title: 'PostgreSQL bearbeiten',
    desc: 'Erlaubt das Ändern von Tabellen und Daten der Server-Datenbank.',
  },
  'server.databases.admin': {
    title: 'PostgreSQL verwalten',
    desc: 'Erlaubt das Hinzufügen, Löschen und Konfigurieren von PostgreSQL-Datenbanken und Usern.',
  },
}

const SUBGROUPS = [
  {
    id: 'users',
    title: 'Benutzer & Rollen',
    keys: ['users.read', 'users.manage', 'users.permissions.manage', 'roles.manage'],
  },
  {
    id: 'panel',
    title: 'Panel-Einstellungen',
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
    ],
  },
  {
    id: 'infrastructure',
    title: 'Infrastruktur & System',
    keys: ['servers.create', 'servers.delete', 'nodes.read', 'nodes.manage', 'system.view'],
  },
  {
    id: 'server_basic',
    title: 'Server-Basisrechte',
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
    title: 'Server-Konfiguration & Ressourcen',
    keys: ['server.config.write', 'server.network.manage', 'server.resources.manage'],
  },
  {
    id: 'server_console',
    title: 'Server-Konsole',
    keys: ['server.console.read', 'server.console.write', 'server.console.exec'],
  },
  {
    id: 'server_files',
    title: 'Dateien & Backups',
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
    title: 'Server-Erweiterungen (Mods & DBs)',
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
      const details = PERMISSION_DETAILS[p.key]
      const title = details ? details.title : p.label
      const desc = details ? details.desc : ''
      return (
        p.key.toLowerCase().includes(query) ||
        title.toLowerCase().includes(query) ||
        desc.toLowerCase().includes(query) ||
        p.label.toLowerCase().includes(query)
      )
    })
  }, [permissions, search])

  // Group filtered definitions
  const groupedData = useMemo(() => {
    const groups: { title: string; defs: PermissionDef[] }[] = []
    const mappedKeys = new Set<string>()

    // Predefined groups
    for (const group of SUBGROUPS) {
      const defsInGroup = filteredDefs.filter((p) => group.keys.includes(p.key))
      if (defsInGroup.length > 0) {
        groups.push({
          title: group.title,
          defs: defsInGroup,
        })
        defsInGroup.forEach((p) => mappedKeys.add(p.key))
      }
    }

    // Remaining items (fallback for future permissions)
    const remainingDefs = filteredDefs.filter((p) => !mappedKeys.has(p.key))
    if (remainingDefs.length > 0) {
      groups.push({
        title: 'Andere Berechtigungen',
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
    if (activeKey) {
      const details = PERMISSION_DETAILS[activeKey]
      const def = permissionMap.get(activeKey)
      return {
        key: activeKey,
        title: details ? details.title : (def ? def.label : activeKey),
        desc: details ? details.desc : (def ? def.label : ''),
      }
    }
    return null
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
            placeholder="Berechtigung suchen..."
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
              Alle auswählen
            </button>
            <button
              type="button"
              onClick={handleDeselectVisible}
              className="msm-btn-secondary text-xs px-3 py-1.5"
            >
              Auswahl aufheben
            </button>
          </div>
        )}
      </div>

      {/* Permissions Grid */}
      <div className="space-y-6 max-h-[380px] overflow-y-auto pr-1">
        {groupedData.length === 0 ? (
          <div className="p-8 text-center text-on-surface-variant bg-surface-container-low/40 rounded-lg border border-outline-variant/30 font-body-md text-sm">
            Keine Berechtigungen gefunden
          </div>
        ) : (
          groupedData.map((group) => (
            <div key={group.title} className="space-y-2.5">
              <h4 className="font-label-md text-xs text-on-surface-variant uppercase tracking-wider pl-1">
                {group.title}
              </h4>
              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
                {group.defs.map((def) => {
                  const details = PERMISSION_DETAILS[def.key]
                  const title = details ? details.title : def.label
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
                        <input
                          id={id}
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => {}} // handled by click container
                          disabled={disabled}
                          className="sr-only"
                        />
                      </div>
                      <div className="flex flex-col gap-0.5 min-w-0">
                        <span className="font-label-md text-xs font-semibold text-on-surface group-hover:text-primary transition-colors truncate">
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
            <span>Fahre mit der Maus über eine Berechtigung, um eine Beschreibung anzuzeigen.</span>
          </div>
        )}
      </div>
    </div>
  )
}
