/**
 * Workspace-Leiste (Arbeitsbereiche) für die vertikale Seitenleiste.
 *
 * Ermöglicht schnelles Wechseln zwischen getrennten Arbeitsbereichen
 * (z. B. Alltag, Dev, Gaming), Anlegen neuer Bereiche, Bearbeiten,
 * Löschen sowie Drag & Drop von Tabs zwischen Bereichen.
 */
import { useEffect, useMemo, useState, type DragEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  BookOpen,
  Briefcase,
  Code,
  Folder,
  Gamepad2,
  Globe,
  Layers,
  MoreVertical,
  Pencil,
  Plus,
  Server,
  Sparkles,
  Terminal,
  Trash2,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { FARB_PALETTE, farbwahl, type Farbwahl } from '@/config/farbpalette'
import { confirm } from '@/stores/confirmStore'
import { useLeistenname } from '../leiste/leistenname'
import { entpackeTabTransfer } from '../services/tabTransfer'
import { useTabsStore } from '../services/tabsStore'
import {
  STANDARD_WORKSPACE_ID,
  useWorkspacesStore,
  type Workspace,
} from '../services/workspacesStore'

export { FARB_PALETTE, farbwahl, type Farbwahl }

export const ICON_OPTIONEN = [
  { id: 'globe', label: 'Web' },
  { id: 'briefcase', label: 'Arbeit' },
  { id: 'code', label: 'Code' },
  { id: 'terminal', label: 'Terminal' },
  { id: 'server', label: 'Server' },
  { id: 'gamepad2', label: 'Gaming' },
  { id: 'sparkles', label: 'KI' },
  { id: 'folder', label: 'Ordner' },
  { id: 'book', label: 'Wissen' },
  { id: 'layers', label: 'Ebenen' },
]

export function WorkspaceIcon({ icon, className = 'h-3.5 w-3.5' }: { icon: string; className?: string }) {
  switch (icon) {
    case 'briefcase':
      return <Briefcase className={className} aria-hidden="true" />
    case 'code':
      return <Code className={className} aria-hidden="true" />
    case 'gamepad2':
      return <Gamepad2 className={className} aria-hidden="true" />
    case 'sparkles':
      return <Sparkles className={className} aria-hidden="true" />
    case 'terminal':
      return <Terminal className={className} aria-hidden="true" />
    case 'server':
      return <Server className={className} aria-hidden="true" />
    case 'book':
      return <BookOpen className={className} aria-hidden="true" />
    case 'layers':
      return <Layers className={className} aria-hidden="true" />
    case 'folder':
      return <Folder className={className} aria-hidden="true" />
    case 'globe':
    default:
      return <Globe className={className} aria-hidden="true" />
  }
}

interface WorkspaceModalProps {
  offen: boolean
  initial?: Workspace | null
  onSchliessen: () => void
  onSpeichern: (daten: { name: string; icon: string; farbe: string }) => void
}

function WorkspaceModal({ offen, initial, onSchliessen, onSpeichern }: WorkspaceModalProps) {
  const { t } = useTranslation()
  const [name, setName] = useState(initial?.name ?? '')
  const [icon, setIcon] = useState(initial?.icon ?? 'globe')
  const [farbe, setFarbe] = useState(initial?.farbe ?? 'primary')

  useEffect(() => {
    if (offen) {
      setName(initial?.name ?? '')
      setIcon(initial?.icon ?? 'globe')
      setFarbe(initial?.farbe ?? 'primary')
    }
  }, [offen, initial])

  useEffect(() => {
    if (!offen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onSchliessen()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [offen, onSchliessen])

  if (!offen) return null

  const absenden = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    onSpeichern({ name: name.trim(), icon, farbe })
    onSchliessen()
  }

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      data-msb-verdeckt="true"
      aria-labelledby="workspace-modal-titel"
      className="msm-modal-overlay animate-fade-in"
      onClick={(e) => {
        if (e.target === e.currentTarget) onSchliessen()
      }}
    >
      <div
        className="msm-card w-full max-w-sm p-5 shadow-2xl relative flex flex-col gap-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between pb-3 border-b border-outline-variant/30">
          <h2 id="workspace-modal-titel" className="text-title-sm font-semibold text-on-surface">
            {initial ? t('browser.workspaces.bearbeiten') : t('browser.workspaces.neu')}
          </h2>
          <button
            type="button"
            onClick={onSchliessen}
            className="flex h-7 w-7 items-center justify-center rounded-lg text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface transition-colors"
            aria-label={t('browser.workspaces.abbrechen')}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <form onSubmit={absenden} className="flex flex-col gap-3.5">
          <div>
            <label htmlFor="ws-name-input" className="block text-label-sm font-medium text-on-surface-variant mb-1.5">
              {t('browser.workspaces.name')}
            </label>
            <input
              id="ws-name-input"
              type="text"
              required
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('browser.workspaces.namePlatzhalter')}
              className="msm-input"
            />
          </div>

          <div>
            <label className="block text-label-sm font-medium text-on-surface-variant mb-1.5">
              {t('browser.workspaces.symbol')}
            </label>
            <div className="grid grid-cols-5 gap-1.5">
              {ICON_OPTIONEN.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => setIcon(opt.id)}
                  className={`flex h-9 items-center justify-center rounded-lg border transition-all ${
                    icon === opt.id
                      ? 'border-primary bg-primary/20 text-primary shadow-sm ring-1 ring-primary'
                      : 'border-outline-variant/50 bg-surface-container-high hover:bg-surface-container-highest hover:border-outline text-on-surface-variant hover:text-on-surface'
                  }`}
                  aria-label={opt.label}
                >
                  <WorkspaceIcon icon={opt.id} className="h-4 w-4" />
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-label-sm font-medium text-on-surface-variant mb-1.5">
              {t('browser.workspaces.farbe')}
            </label>
            <div className="flex flex-wrap items-center gap-2">
              {FARB_PALETTE.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => setFarbe(opt.id)}
                  className={`flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-label-sm font-medium border transition-all ${opt.flaecheStark} ${opt.text} ${
                    farbe === opt.id ? `ring-2 ring-primary ${opt.rand} shadow-sm` : 'opacity-70 hover:opacity-100 hover:border-outline'
                  }`}
                  aria-label={t(opt.labelKey)}
                >
                  <span className={`h-2.5 w-2.5 rounded-full ${opt.flaecheStark} border ${opt.rand}`} />
                  <span>{t(opt.labelKey)}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="mt-1 flex justify-end gap-2 border-t border-outline-variant/30 pt-3.5">
            <button
              type="button"
              onClick={onSchliessen}
              className="msm-btn-secondary px-3.5 py-1.5"
            >
              {t('browser.workspaces.abbrechen')}
            </button>
            <button
              type="submit"
              disabled={!name.trim()}
              className="msm-btn-primary px-4 py-1.5"
            >
              {initial ? t('browser.workspaces.speichern') : t('browser.workspaces.anlegen')}
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body
  )
}

interface WorkspaceLeisteProps {
  eingeklappt: boolean
}

export function WorkspaceLeiste({ eingeklappt }: WorkspaceLeisteProps) {
  const { t } = useTranslation()
  const workspaces = useWorkspacesStore((s) => s.workspaces)
  const aktiverWorkspaceId = useWorkspacesStore((s) => s.aktiverWorkspaceId)
  const wechselnWorkspace = useWorkspacesStore((s) => s.wechselnWorkspace)
  const neuerWorkspace = useWorkspacesStore((s) => s.neuerWorkspace)
  const bearbeitenWorkspace = useWorkspacesStore((s) => s.bearbeitenWorkspace)
  const loeschenWorkspace = useWorkspacesStore((s) => s.loeschenWorkspace)

  const tabs = useTabsStore((s) => s.tabs)
  const tabZaehler = useMemo(() => {
    const map: Record<string, number> = {}
    for (const t of tabs) {
      const ws = t.workspaceId ?? STANDARD_WORKSPACE_ID
      map[ws] = (map[ws] ?? 0) + 1
    }
    return map
  }, [tabs])

  const [modalOffen, setModalOffen] = useState(false)
  const [bearbeiteWs, setBearbeiteWs] = useState<Workspace | null>(null)
  const [aktivesMenue, setAktivesMenue] = useState<{ ws: Workspace; x: number; y: number } | null>(null)
  const [hoveredDropId, setHoveredDropId] = useState<string | null>(null)

  useEffect(() => {
    if (!aktivesMenue) return
    const schliessen = () => setAktivesMenue(null)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setAktivesMenue(null)
    }
    window.addEventListener('click', schliessen)
    window.addEventListener('contextmenu', schliessen)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('click', schliessen)
      window.removeEventListener('contextmenu', schliessen)
      window.removeEventListener('keydown', onKey)
    }
  }, [aktivesMenue])

  const behandleDrop = (e: DragEvent, wsId: string) => {
    e.preventDefault()
    e.stopPropagation()
    setHoveredDropId(null)
    const transfer = entpackeTabTransfer(e.dataTransfer)
    if (transfer?.tabId) {
      useTabsStore.getState().verschiebeTabInWorkspace(transfer.tabId, wsId)
    }
  }

  const oeffneNeuModal = () => {
    setBearbeiteWs(null)
    setModalOffen(true)
  }

  const oeffneBearbeiten = (ws: Workspace) => {
    setBearbeiteWs(ws)
    setAktivesMenue(null)
    setModalOffen(true)
  }

  const speichernModal = (daten: { name: string; icon: string; farbe: string }) => {
    if (bearbeiteWs) {
      bearbeitenWorkspace(bearbeiteWs.id, daten)
    } else {
      neuerWorkspace(daten.name, daten.icon, daten.farbe)
    }
  }

  const menueJSX = aktivesMenue && createPortal(
    <div
      role="menu"
      data-msb-verdeckt="true"
      style={{
        position: 'fixed',
        top: Math.min(aktivesMenue.y, window.innerHeight - 150),
        left: Math.min(aktivesMenue.x, window.innerWidth - 180),
        zIndex: 60,
      }}
      className="fixed z-60 flex flex-col min-w-40 rounded-lg border border-outline-variant bg-surface-container-high p-1.5 shadow-panel backdrop-blur-sm text-label-sm select-none"
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          const ws = aktivesMenue.ws
          setAktivesMenue(null)
          oeffneBearbeiten(ws)
        }}
        className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface transition-colors text-left"
      >
        <Pencil className="h-3.5 w-3.5 text-on-surface-variant" aria-hidden="true" />
        <span>{t('browser.workspaces.bearbeiten')}</span>
      </button>

      {aktivesMenue.ws.id !== STANDARD_WORKSPACE_ID && workspaces.length > 1 && (
        <button
          type="button"
          role="menuitem"
          onClick={async () => {
            const ws = aktivesMenue.ws
            setAktivesMenue(null)
            const ok = await confirm({
              title: t('browser.workspaces.loeschen'),
              message: t('browser.workspaces.loeschenBestaetigen', { name: ws.name }),
              danger: true,
            })
            if (ok) {
              loeschenWorkspace(ws.id)
            }
          }}
          className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-status-destructive hover:bg-status-destructive/10 transition-colors text-left"
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          <span>{t('browser.workspaces.loeschen')}</span>
        </button>
      )}
    </div>,
    document.body
  )

  const modalJSX = (
    <WorkspaceModal
      offen={modalOffen}
      initial={bearbeiteWs}
      onSchliessen={() => setModalOffen(false)}
      onSpeichern={speichernModal}
    />
  )

  if (eingeklappt) {
    // EINGEKLAPPTER MODUS: Schmale Symbolspalte
    return (
      <div className="flex flex-col items-center gap-1 border-b border-outline-variant/20 py-1.5 px-1">
        {workspaces.map((ws, index) => {
          const aktiv = ws.id === aktiverWorkspaceId
          const anzahl = tabZaehler[ws.id] ?? 0
          const fw = farbwahl(ws.farbe)
          const kuerzelText = index < 9 ? `Alt+${index + 1}` : ''
          const infoText = `${ws.name} (${anzahl}) ${kuerzelText ? `• ${kuerzelText}` : ''}`
          const tooltip = useLeistenname(infoText, 'rechts')

          return (
            <button
              key={ws.id}
              type="button"
              onClick={() => wechselnWorkspace(ws.id)}
              onContextMenu={(e) => {
                e.preventDefault()
                e.stopPropagation()
                setAktivesMenue({ ws, x: e.clientX, y: e.clientY })
              }}
              onDragOver={(e) => {
                e.preventDefault()
                if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
                setHoveredDropId(ws.id)
              }}
              onDragLeave={() => {
                if (hoveredDropId === ws.id) setHoveredDropId(null)
              }}
              onDrop={(e) => behandleDrop(e, ws.id)}
              className={`group relative flex h-7 w-7 items-center justify-center rounded-lg transition-all ${
                hoveredDropId === ws.id
                  ? 'bg-primary/30 ring-2 ring-primary scale-105'
                  : aktiv
                  ? `${fw.flaecheStark} ${fw.text} border ${fw.rand} shadow-sm`
                  : 'text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface'
              }`}
              aria-label={infoText}
              aria-pressed={aktiv}
              {...tooltip}
            >
              <WorkspaceIcon icon={ws.icon} className="h-3.5 w-3.5" />
              <span className={`absolute bottom-0.5 right-0.5 h-1.5 w-1.5 rounded-full ${fw.flaecheStark} border ${fw.rand}`} />
            </button>
          )
        })}

        <button
          type="button"
          onClick={oeffneNeuModal}
          className="flex h-6 w-6 items-center justify-center rounded-md text-on-surface-variant/70 hover:bg-surface-container-high hover:text-on-surface transition-colors mt-0.5"
          aria-label={t('browser.workspaces.neu')}
          {...useLeistenname(t('browser.workspaces.neu'), 'rechts')}
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        </button>

        {menueJSX}
        {modalJSX}
      </div>
    )
  }

  // AUSGEKLAPPTER MODUS: Kompakte Reihe von Chips mit Tab-Zähler und Optionen
  return (
    <div className="shrink-0 border-b border-outline-variant/20 px-2 py-1.5">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-label-sm font-semibold uppercase tracking-wider text-on-surface-variant/70">
          {t('browser.workspaces.titel')}
        </span>
        <button
          type="button"
          onClick={oeffneNeuModal}
          className="flex h-5 w-5 items-center justify-center rounded text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface transition-colors"
          aria-label={t('browser.workspaces.neu')}
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </div>

      <div className="flex flex-col gap-1">
        {workspaces.map((ws, index) => {
          const aktiv = ws.id === aktiverWorkspaceId
          const anzahl = tabZaehler[ws.id] ?? 0
          const fw = farbwahl(ws.farbe)
          const istOffen = aktivesMenue?.ws.id === ws.id

          return (
            <div
              key={ws.id}
              className="relative"
              onContextMenu={(e) => {
                e.preventDefault()
                e.stopPropagation()
                setAktivesMenue({ ws, x: e.clientX, y: e.clientY })
              }}
              onDragOver={(e) => {
                e.preventDefault()
                if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
                setHoveredDropId(ws.id)
              }}
              onDragLeave={() => {
                if (hoveredDropId === ws.id) setHoveredDropId(null)
              }}
              onDrop={(e) => behandleDrop(e, ws.id)}
            >
              <div
                role="button"
                tabIndex={0}
                onClick={() => wechselnWorkspace(ws.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    wechselnWorkspace(ws.id)
                  }
                }}
                className={`group flex h-8 w-full items-center justify-between rounded-lg px-2 text-label-sm font-medium transition-colors cursor-pointer select-none ${
                  hoveredDropId === ws.id
                    ? 'bg-primary/25 ring-2 ring-primary text-primary'
                    : aktiv
                    ? `${fw.flaecheStark} ${fw.text} border ${fw.rand} shadow-sm`
                    : 'text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface'
                }`}
              >
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <span className={`h-2 w-2 shrink-0 rounded-full ${fw.flaecheStark} border ${fw.rand}`} />
                  <WorkspaceIcon icon={ws.icon} className="h-3.5 w-3.5 shrink-0 opacity-80" />
                  <span className="truncate text-left flex-1">{ws.name}</span>
                </div>

                <div className="flex items-center gap-1 shrink-0 ml-1">
                  {index < 9 && (
                    <span className="text-label-sm text-on-surface-variant/50 hidden group-hover:inline">
                      Alt+{index + 1}
                    </span>
                  )}
                  <span
                    className={`rounded-full px-1.5 py-0.5 text-label-sm font-semibold ${
                      aktiv
                        ? 'bg-primary/20 text-primary'
                        : 'bg-surface-container-highest text-on-surface-variant'
                    }`}
                  >
                    {anzahl}
                  </span>

                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      if (istOffen) {
                        setAktivesMenue(null)
                      } else {
                        const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
                        setAktivesMenue({ ws, x: rect.right, y: rect.bottom + 4 })
                      }
                    }}
                    className={`flex h-5 w-5 items-center justify-center rounded hover:bg-surface-container-highest text-on-surface-variant hover:text-on-surface transition-opacity ${
                      istOffen ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                    }`}
                    aria-label={t('browser.workspaces.bearbeiten')}
                  >
                    <MoreVertical className="h-3 w-3" aria-hidden="true" />
                  </button>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {menueJSX}
      {modalJSX}
    </div>
  )
}
