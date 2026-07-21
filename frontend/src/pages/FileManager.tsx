import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArchiveRestore,
  ChevronRight,
  Download,
  FilePlus2,
  FolderInput,
  FolderPlus,
  Info,
  Menu,
  MoreHorizontal,
  PackageOpen,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { api, SanitizedApiError } from '@/api/client'
import { apiUrl } from '@/config/api'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { ActionMenu, Switch, type ActionMenuItem } from '@/Singra/UI'
import { FileTree } from '@/components/server/FileTree'
import { FileEditorWorkspace } from '@/components/server/FileEditorWorkspace'
import {
  detectLineEnding,
  fileName,
  formatBytes,
  isWithin,
  joinPath,
  parentPath,
  pathSegments,
  reconcileSavedContent,
  serializeLineEndings,
  sortEntries,
  uploadDestinationKey,
} from '@/components/server/fileHelpers'
import type {
  BrowseResponse,
  EditorTab,
  FileEntry,
  FileMetadata,
  ReadResponse,
  SearchResponse,
} from '@/components/server/fileWorkspaceTypes'
import { uploadFile } from '@/components/server/chunkedUpload'

interface FileManagerProps {
  serverId: number
}

interface SelectedEntry {
  entry: FileEntry
  parent: string
}

interface ContextMenuState extends SelectedEntry {
  x: number
  y: number
}

interface UploadJob {
  id: string
  name: string
  fraction: number
  status: 'running' | 'done' | 'error'
}

interface PromptDialogState {
  title: string
  label: string
  initialValue: string
  confirmLabel: string
  onConfirm: (value: string) => void | Promise<void>
}

interface MoveDialogState extends SelectedEntry {}

interface WriteResponse extends FileMetadata {
  revision: string
}

interface FileVersion {
  id: string
  created_at: number
  size: number
}

const UNKNOWN_METADATA: FileMetadata = {
  size: 0,
  modified: 0,
  mode: null,
  owner: null,
  group: null,
}

function safeErrorMessage(error: unknown, fallback: string): string {
  return error instanceof SanitizedApiError ? error.message : fallback
}

function isArchive(name: string): boolean {
  const lower = name.toLowerCase()
  return ['.zip', '.tar.gz', '.tgz', '.tar.xz', '.txz', '.tar.bz2', '.tbz2'].some((extension) => lower.endsWith(extension))
}

function formatModified(value: number): string {
  if (!value) return 'Nicht verfügbar'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value * 1000))
}

export function FileManager({ serverId }: FileManagerProps) {
  const { t } = useTranslation()
  const permissionsLoading = usePermissionsStore((state) => state.isLoading)
  const canWrite = useHasPermission('server.files.write', serverId)
  const canDelete = useHasPermission('server.files.delete', serverId)
  const showWriteActions = !permissionsLoading && canWrite
  const showDeleteActions = !permissionsLoading && canDelete

  const [nodes, setNodes] = useState<Record<string, FileEntry[]>>({})
  const [expanded, setExpanded] = useState<Set<string>>(new Set(['']))
  const [loadingPaths, setLoadingPaths] = useState<Set<string>>(new Set(['']))
  const [currentPath, setCurrentPath] = useState('')
  const [selectedEntry, setSelectedEntry] = useState<SelectedEntry | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResponse['results'] | null>(null)
  const [searchTruncated, setSearchTruncated] = useState(false)
  const [tabs, setTabs] = useState<EditorTab[]>([])
  const [versions, setVersions] = useState<Record<string, FileVersion[]>>({})
  const [activePath, setActivePath] = useState<string | null>(null)
  const [autosave, setAutosave] = useState(true)
  const [treeOpen, setTreeOpen] = useState(false)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [uploads, setUploads] = useState<UploadJob[]>([])
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null)
  const [promptDialog, setPromptDialog] = useState<PromptDialogState | null>(null)
  const [moveDialog, setMoveDialog] = useState<MoveDialogState | null>(null)
  const [moveTarget, setMoveTarget] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const promptInputRef = useRef<HTMLInputElement>(null)
  const treeTriggerRef = useRef<HTMLButtonElement>(null)
  const inspectorTriggerRef = useRef<HTMLButtonElement>(null)
  const treePanelRef = useRef<HTMLElement>(null)
  const inspectorPanelRef = useRef<HTMLElement>(null)
  const contextMenuRef = useRef<HTMLDivElement>(null)
  const contextTriggerRef = useRef<HTMLElement | null>(null)
  const tabsRef = useRef(tabs)
  const savingPathsRef = useRef(new Set<string>())
  const inFlightUploadsRef = useRef(new Set<string>())

  useEffect(() => { tabsRef.current = tabs }, [tabs])

  const loadDirectory = useCallback(async (path: string, force = false) => {
    if (!force && nodes[path]) return
    setLoadingPaths((current) => new Set(current).add(path))
    try {
      const response = await api<BrowseResponse>(`/files/${serverId}/browse?path=${encodeURIComponent(path)}`)
      setNodes((current) => ({ ...current, [path]: sortEntries(response.entries ?? []) }))
    } catch (error) {
      toast.error(safeErrorMessage(error, t('files.loadFailed')))
    } finally {
      setLoadingPaths((current) => {
        const next = new Set(current)
        next.delete(path)
        return next
      })
    }
  }, [nodes, serverId, t])

  useEffect(() => {
    setNodes({})
    setTabs([])
    setActivePath(null)
    setCurrentPath('')
    setExpanded(new Set(['']))
    void loadDirectory('', true)
    // serverId is the reset boundary; loadDirectory intentionally reads it here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverId])

  useEffect(() => {
    const query = searchQuery.trim()
    if (!query) {
      setSearchResults(null)
      setSearchTruncated(false)
      return
    }
    const handle = window.setTimeout(async () => {
      try {
        const response = await api<SearchResponse>(`/files/${serverId}/search?q=${encodeURIComponent(query)}`)
        setSearchResults(response.results ?? [])
        setSearchTruncated(response.truncated)
      } catch (error) {
        toast.error(safeErrorMessage(error, t('files.searchFailed')))
      }
    }, 300)
    return () => window.clearTimeout(handle)
  }, [searchQuery, serverId, t])

  useEffect(() => {
    if (!contextMenu) return
    const focusMenu = window.requestAnimationFrame(() => contextMenuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus())
    const dismiss = (restoreFocus = false) => {
      const trigger = contextTriggerRef.current
      setContextMenu(null)
      if (restoreFocus) trigger?.focus()
    }
    const onPointerDown = (event: MouseEvent) => {
      if (!contextMenuRef.current?.contains(event.target as Node)) dismiss(false)
    }
    const onScroll = () => dismiss(false)
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        dismiss(true)
        return
      }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
      const items = Array.from(contextMenuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? [])
      if (!items.length) return
      event.preventDefault()
      const currentIndex = items.indexOf(document.activeElement as HTMLButtonElement)
      const nextIndex = event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? items.length - 1
          : event.key === 'ArrowUp'
            ? (currentIndex - 1 + items.length) % items.length
            : (currentIndex + 1) % items.length
      items[nextIndex].focus()
    }
    document.addEventListener('mousedown', onPointerDown)
    window.addEventListener('scroll', onScroll, true)
    document.addEventListener('keydown', onKey)
    return () => {
      window.cancelAnimationFrame(focusMenu)
      document.removeEventListener('mousedown', onPointerDown)
      window.removeEventListener('scroll', onScroll, true)
      document.removeEventListener('keydown', onKey)
    }
  }, [contextMenu])

  useEffect(() => {
    if (!treeOpen && !inspectorOpen) return
    const focusPanel = window.requestAnimationFrame(() => {
      if (treeOpen) treePanelRef.current?.focus()
      else inspectorPanelRef.current?.focus()
    })
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || contextMenuRef.current) return
      event.preventDefault()
      if (treeOpen) {
        setTreeOpen(false)
        window.requestAnimationFrame(() => treeTriggerRef.current?.focus())
      } else {
        setInspectorOpen(false)
        window.requestAnimationFrame(() => inspectorTriggerRef.current?.focus())
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      window.cancelAnimationFrame(focusPanel)
      document.removeEventListener('keydown', onKey)
    }
  }, [inspectorOpen, treeOpen])

  useEffect(() => {
    if (!promptDialog) return
    const handle = window.setTimeout(() => promptInputRef.current?.focus(), 0)
    return () => window.clearTimeout(handle)
  }, [promptDialog])

  const refreshWorkspace = useCallback(async () => {
    await loadDirectory(currentPath, true)
    for (const path of expanded) {
      if (path && path !== currentPath && nodes[path]) void loadDirectory(path, true)
    }
  }, [currentPath, expanded, loadDirectory, nodes])

  const loadVersions = useCallback(async (path: string) => {
    try {
      const response = await api<{ versions: FileVersion[] }>(`/files/${serverId}/versions?path=${encodeURIComponent(path)}`)
      setVersions((current) => ({ ...current, [path]: response.versions ?? [] }))
    } catch {
      // The editor remains usable if history metadata is temporarily unavailable.
      setVersions((current) => ({ ...current, [path]: [] }))
    }
  }, [serverId])

  const toggleDirectory = useCallback((path: string) => {
    setCurrentPath(path)
    setSearchQuery('')
    setExpanded((current) => {
      const next = new Set(current)
      if (path && next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
    if (!nodes[path]) void loadDirectory(path)
  }, [loadDirectory, nodes])

  const openFile = useCallback(async (path: string) => {
    const existing = tabsRef.current.find((tab) => tab.path === path)
    setActivePath(path)
    setTreeOpen(false)
    setSelectedEntry(() => {
      const parent = parentPath(path)
      const entry = nodes[parent]?.find((item) => item.name === fileName(path))
      return entry ? { entry, parent } : null
    })
    if (existing) return

    setTabs((current) => [...current, {
      path,
      content: '',
      savedContent: '',
      revision: '',
      lineEnding: '\n',
      loading: true,
      saveState: 'clean',
      ...UNKNOWN_METADATA,
    }])
    try {
      const response = await api<ReadResponse>(`/files/${serverId}/read?path=${encodeURIComponent(path)}`)
      setTabs((current) => current.map((tab) => tab.path === path ? {
        ...tab,
        content: response.content,
        savedContent: response.content,
        revision: response.revision,
        lineEnding: detectLineEnding(response.content),
        loading: false,
        saveState: 'clean',
        size: response.size,
        modified: response.modified,
        mode: response.mode,
        owner: response.owner,
        group: response.group,
      } : tab))
      void loadVersions(path)
    } catch (error) {
      setTabs((current) => current.filter((tab) => tab.path !== path))
      setActivePath((current) => current === path ? null : current)
      toast.error(safeErrorMessage(error, t('files.readFailed')))
    }
  }, [loadVersions, nodes, serverId, t])

  const saveTab = useCallback(async (path: string) => {
    if (!canWrite || savingPathsRef.current.has(path)) return
    const tab = tabsRef.current.find((item) => item.path === path)
    if (!tab || tab.loading || tab.saveState === 'clean' || tab.saveState === 'conflict') return
    savingPathsRef.current.add(path)
    const submittedContent = tab.content
    setTabs((current) => current.map((item) => item.path === path ? { ...item, saveState: 'saving' } : item))
    try {
      const response = await api<WriteResponse>(`/files/${serverId}/write?path=${encodeURIComponent(path)}`, {
        method: 'PUT',
        body: JSON.stringify({
          content: serializeLineEndings(submittedContent, tab.lineEnding),
          expected_revision: tab.revision || undefined,
        }),
      })
      setTabs((current) => current.map((item) => item.path === path ? {
        ...item,
        ...reconcileSavedContent(item.content, submittedContent),
        revision: response.revision,
        size: response.size,
        modified: response.modified,
        mode: response.mode,
        owner: response.owner,
        group: response.group,
      } : item))
      void loadDirectory(parentPath(path), true)
      void loadVersions(path)
    } catch (error) {
      const conflict = error instanceof SanitizedApiError && (error.status === 409 || error.code === 'FILE_REVISION_CONFLICT')
      setTabs((current) => current.map((item) => item.path === path ? {
        ...item,
        saveState: conflict ? 'conflict' : 'error',
      } : item))
      if (!conflict) toast.error(safeErrorMessage(error, t('files.saveFailed')))
    } finally {
      savingPathsRef.current.delete(path)
    }
  }, [canWrite, loadDirectory, loadVersions, serverId, t])

  useEffect(() => {
    if (!autosave || !canWrite) return
    const dirtyPaths = tabs.filter((tab) => tab.saveState === 'dirty').map((tab) => tab.path)
    if (!dirtyPaths.length) return
    const handle = window.setTimeout(() => dirtyPaths.forEach((path) => void saveTab(path)), 1200)
    return () => window.clearTimeout(handle)
  }, [autosave, canWrite, saveTab, tabs])

  const updateTabContent = useCallback((path: string, content: string) => {
    setTabs((current) => current.map((tab) => tab.path === path ? {
      ...tab,
      content,
      saveState: content === tab.savedContent ? 'clean' : 'dirty',
    } : tab))
  }, [])

  const closeTab = useCallback(async (path: string) => {
    const tab = tabsRef.current.find((item) => item.path === path)
    if (!tab) return
    if (tab.saveState !== 'clean' && !(await confirm({ message: t('files.confirmCloseDirty'), confirmText: t('files.closeWithoutSaving') }))) return
    setTabs((current) => {
      const index = current.findIndex((item) => item.path === path)
      const next = current.filter((item) => item.path !== path)
      setActivePath((active) => active === path ? next[Math.max(0, index - 1)]?.path ?? next[0]?.path ?? null : active)
      return next
    })
  }, [t])

  const reloadTab = useCallback(async (path: string) => {
    if (!(await confirm({ message: t('files.confirmReloadConflict'), confirmText: t('files.reloadServerVersion') }))) return
    try {
      const response = await api<ReadResponse>(`/files/${serverId}/read?path=${encodeURIComponent(path)}`)
      setTabs((current) => current.map((tab) => tab.path === path ? {
        ...tab,
        content: response.content,
        savedContent: response.content,
        revision: response.revision,
        lineEnding: detectLineEnding(response.content),
        saveState: 'clean',
        size: response.size,
        modified: response.modified,
        mode: response.mode,
        owner: response.owner,
        group: response.group,
      } : tab))
    } catch (error) {
      toast.error(safeErrorMessage(error, t('files.readFailed')))
    }
  }, [serverId, t])

  const restoreVersion = useCallback(async (path: string, versionId: string) => {
    if (!canWrite || !(await confirm({
      message: t('files.confirmRestoreVersion'),
      confirmText: t('files.restoreVersion'),
    }))) return
    try {
      await api(`/files/${serverId}/versions/${versionId}/restore?path=${encodeURIComponent(path)}`, {
        method: 'POST',
      })
      const response = await api<ReadResponse>(`/files/${serverId}/read?path=${encodeURIComponent(path)}`)
      setTabs((current) => current.map((tab) => tab.path === path ? {
        ...tab,
        content: response.content,
        savedContent: response.content,
        revision: response.revision,
        lineEnding: detectLineEnding(response.content),
        saveState: 'clean',
        size: response.size,
        modified: response.modified,
        mode: response.mode,
        owner: response.owner,
        group: response.group,
      } : tab))
      await Promise.all([loadDirectory(parentPath(path), true), loadVersions(path)])
      toast.success(t('files.versionRestored'))
    } catch (error) {
      toast.error(safeErrorMessage(error, t('files.restoreFailed')))
    }
  }, [canWrite, loadDirectory, loadVersions, serverId, t])

  useEffect(() => {
    if (!activePath) return
    const poll = async () => {
      if (document.visibilityState !== 'visible') return
      try {
        const parent = parentPath(activePath)
        const response = await api<BrowseResponse>(`/files/${serverId}/browse?path=${encodeURIComponent(parent)}`)
        const entry = response.entries.find((item) => item.name === fileName(activePath))
        if (!entry) return
        setTabs((current) => current.map((tab) => tab.path === activePath ? {
          ...tab,
          size: entry.size,
          modified: entry.modified,
          mode: entry.mode,
          owner: entry.owner,
          group: entry.group,
        } : tab))
      } catch {
        // Metadata polling is best-effort; explicit refresh remains available.
      }
    }
    const handle = window.setInterval(() => void poll(), 15_000)
    return () => window.clearInterval(handle)
  }, [activePath, serverId])

  const enqueueUpload = useCallback((files: FileList | File[] | null, destinationPath = currentPath) => {
    if (!canWrite || !files) return
    const list = Array.isArray(files) ? files : Array.from(files)
    for (const file of list) {
      const destinationKey = uploadDestinationKey(destinationPath, file.name)
      if (inFlightUploadsRef.current.has(destinationKey)) {
        toast.error(t('files.uploadAlreadyRunning', { name: file.name }))
        continue
      }
      inFlightUploadsRef.current.add(destinationKey)
      const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
      setUploads((current) => [...current, { id, name: file.name, fraction: 0, status: 'running' }])
      uploadFile({
        serverId,
        destinationPath,
        file,
        onProgress: (fraction) => setUploads((current) => current.map((job) => job.id === id ? { ...job, fraction } : job)),
      }).then(() => {
        setUploads((current) => current.map((job) => job.id === id ? { ...job, fraction: 1, status: 'done' } : job))
        void loadDirectory(destinationPath, true)
        window.setTimeout(() => setUploads((current) => current.filter((job) => job.id !== id)), 1400)
      }).catch((error: unknown) => {
        setUploads((current) => current.map((job) => job.id === id ? { ...job, status: 'error' } : job))
        toast.error(safeErrorMessage(error, t('files.uploadFailed')))
      }).finally(() => inFlightUploadsRef.current.delete(destinationKey))
    }
  }, [canWrite, currentPath, loadDirectory, serverId, t])

  const createFolder = () => setPromptDialog({
    title: t('files.newFolder'), label: t('files.folderName'), initialValue: '', confirmLabel: t('common.create'),
    onConfirm: async (value) => {
      const name = value.trim()
      if (!name) return
      try {
        await api(`/files/${serverId}/mkdir?path=${encodeURIComponent(currentPath)}`, { method: 'POST', body: JSON.stringify({ name }) })
        setPromptDialog(null)
        await loadDirectory(currentPath, true)
      } catch (error) { toast.error(safeErrorMessage(error, t('files.createFailed'))) }
    },
  })

  const createFile = () => setPromptDialog({
    title: t('files.newFile'), label: t('files.fileName'), initialValue: '', confirmLabel: t('common.create'),
    onConfirm: async (value) => {
      const name = value.trim()
      if (!name) return
      const path = joinPath(currentPath, name)
      try {
        await api(`/files/${serverId}/write?path=${encodeURIComponent(path)}`, { method: 'PUT', body: JSON.stringify({ content: '', create_only: true }) })
        setPromptDialog(null)
        await loadDirectory(currentPath, true)
        void openFile(path)
      } catch (error) { toast.error(safeErrorMessage(error, t('files.createFailed'))) }
    },
  })

  const renameEntry = (selection: SelectedEntry) => setPromptDialog({
    title: t('files.renameTitle'), label: t('files.newName'), initialValue: selection.entry.name, confirmLabel: t('common.save'),
    onConfirm: async (value) => {
      const name = value.trim()
      if (!name || name === selection.entry.name) { setPromptDialog(null); return }
      const oldPath = joinPath(selection.parent, selection.entry.name)
      const newPath = joinPath(selection.parent, name)
      try {
        await api(`/files/${serverId}/rename?path=${encodeURIComponent(oldPath)}`, { method: 'POST', body: JSON.stringify({ new_name: name }) })
        setTabs((current) => current.map((tab) => tab.path === oldPath ? { ...tab, path: newPath } : tab))
        setActivePath((current) => current === oldPath ? newPath : current)
        setPromptDialog(null)
        await loadDirectory(selection.parent, true)
      } catch (error) { toast.error(safeErrorMessage(error, t('files.renameFailed'))) }
    },
  })

  const deleteEntry = async (selection: SelectedEntry) => {
    const path = joinPath(selection.parent, selection.entry.name)
    const message = selection.entry.is_dir ? t('files.confirmDeleteDir') : t('files.confirmDeleteFile')
    if (!(await confirm({ message, danger: true, confirmText: t('common.delete') }))) return
    try {
      await api(`/files/${serverId}/delete?path=${encodeURIComponent(path)}`, { method: 'DELETE' })
      setTabs((current) => current.filter((tab) => tab.path !== path && !tab.path.startsWith(`${path}/`)))
      setActivePath((current) => current === path || current?.startsWith(`${path}/`) ? null : current)
      setSelectedEntry(null)
      await loadDirectory(selection.parent, true)
    } catch (error) { toast.error(safeErrorMessage(error, t('files.deleteFailed'))) }
  }

  const downloadEntry = (selection: SelectedEntry) => {
    const path = joinPath(selection.parent, selection.entry.name)
    const link = document.createElement('a')
    link.href = apiUrl(`/files/${serverId}/download?path=${encodeURIComponent(path)}`)
    link.download = selection.entry.name
    document.body.appendChild(link)
    link.click()
    link.remove()
  }

  const extractEntry = async (selection: SelectedEntry) => {
    const path = joinPath(selection.parent, selection.entry.name)
    try {
      await api(`/files/${serverId}/extract?path=${encodeURIComponent(path)}`, { method: 'POST' })
      await loadDirectory(selection.parent, true)
    } catch (error) { toast.error(safeErrorMessage(error, t('files.extractFailed'))) }
  }

  const beginMove = (selection: SelectedEntry) => {
    setMoveTarget(selection.parent)
    setMoveDialog(selection)
  }

  const submitMove = async () => {
    if (!moveDialog) return
    const fromPath = joinPath(moveDialog.parent, moveDialog.entry.name)
    const destination = moveTarget.replace(/^\/+|\/+$/g, '')
    if (moveDialog.entry.is_dir && isWithin(fromPath, joinPath(destination, moveDialog.entry.name))) {
      toast.error(t('files.moveSelfError'))
      return
    }
    try {
      await api(`/files/${serverId}/move`, { method: 'POST', body: JSON.stringify({ from_path: fromPath, to_dir: destination }) })
      const newPath = joinPath(destination, moveDialog.entry.name)
      setTabs((current) => current.map((tab) => tab.path === fromPath ? { ...tab, path: newPath } : tab))
      setActivePath((current) => current === fromPath ? newPath : current)
      setMoveDialog(null)
      await Promise.all([loadDirectory(moveDialog.parent, true), loadDirectory(destination, true)])
    } catch (error) { toast.error(safeErrorMessage(error, t('files.moveFailed'))) }
  }

  const handleDragStart = (event: React.DragEvent, entry: FileEntry, parent: string) => {
    event.dataTransfer.setData('application/x-msm-path', joinPath(parent, entry.name))
    event.dataTransfer.effectAllowed = canWrite ? 'move' : 'none'
  }

  const handleDropFolder = async (event: React.DragEvent, entry: FileEntry, parent: string) => {
    event.preventDefault()
    event.stopPropagation()
    if (!canWrite) return
    const destination = joinPath(parent, entry.name)
    const source = event.dataTransfer.getData('application/x-msm-path')
    if (!source) {
      enqueueUpload(event.dataTransfer.files, destination)
      return
    }
    const sourceName = fileName(source)
    if (isWithin(source, joinPath(destination, sourceName))) {
      toast.error(t('files.moveSelfError'))
      return
    }
    try {
      await api(`/files/${serverId}/move`, { method: 'POST', body: JSON.stringify({ from_path: source, to_dir: destination }) })
      await Promise.all([loadDirectory(parentPath(source), true), loadDirectory(destination, true)])
    } catch (error) { toast.error(safeErrorMessage(error, t('files.moveFailed'))) }
  }

  const actionItems = useCallback((selection: SelectedEntry | null): ActionMenuItem[] => {
    if (!selection) return []
    const items: ActionMenuItem[] = []
    if (!selection.entry.is_dir) items.push({ key: 'download', label: t('files.download'), icon: <Download className="h-4 w-4" />, onSelect: () => downloadEntry(selection) })
    if (showWriteActions) {
      if (!selection.entry.is_dir && isArchive(selection.entry.name)) items.push({ key: 'extract', label: t('files.extract'), icon: <PackageOpen className="h-4 w-4" />, onSelect: () => void extractEntry(selection) })
      items.push(
        { key: 'rename', label: t('files.rename'), icon: <Pencil className="h-4 w-4" />, separatorBefore: items.length > 0, onSelect: () => renameEntry(selection) },
        { key: 'move', label: t('files.move'), icon: <FolderInput className="h-4 w-4" />, onSelect: () => beginMove(selection) },
      )
    }
    if (showDeleteActions) items.push({ key: 'delete', label: t('common.delete'), icon: <Trash2 className="h-4 w-4" />, destructive: true, separatorBefore: true, onSelect: () => void deleteEntry(selection) })
    return items
  }, [showDeleteActions, showWriteActions, t])

  const entries = nodes[currentPath] ?? []
  const directorySummary = useMemo(() => ({
    files: entries.filter((entry) => !entry.is_dir).length,
    folders: entries.filter((entry) => entry.is_dir).length,
    bytes: entries.reduce((sum, entry) => sum + (entry.is_dir ? 0 : entry.size), 0),
  }), [entries])
  const breadcrumbs = pathSegments(currentPath)
  const activeTab = tabs.find((tab) => tab.path === activePath) ?? null
  const selectedActions = actionItems(selectedEntry)

  const newItems: ActionMenuItem[] = [
    { key: 'file', label: t('files.newFile'), icon: <FilePlus2 className="h-4 w-4" />, onSelect: createFile },
    { key: 'folder', label: t('files.newFolder'), icon: <FolderPlus className="h-4 w-4" />, onSelect: createFolder },
    { key: 'upload', label: t('files.upload'), icon: <Upload className="h-4 w-4" />, separatorBefore: true, onSelect: () => fileInputRef.current?.click() },
  ]

  return (
    <div className="relative overflow-visible rounded-xl border border-outline-variant/80 bg-surface-container-lowest/65 shadow-panel">
      <header className="flex min-h-14 flex-wrap items-center gap-2 border-b border-outline-variant bg-surface-container-low/80 px-3 py-2">
        {showWriteActions && <div className="[&>button]:h-11 sm:[&>button]:h-8"><ActionMenu label={t('files.new')} icon={<FilePlus2 className="h-4 w-4" />} items={newItems} compact /></div>}
        <button type="button" onClick={() => void refreshWorkspace()} className="msm-btn-tertiary inline-flex h-11 items-center gap-2 px-3 text-xs sm:h-8 sm:px-2.5" aria-label={t('common.refresh')}><RefreshCw className="h-3.5 w-3.5" /> <span>{t('common.refresh')}</span></button>
        {selectedActions.length > 0 && <div className="[&>button]:h-11 sm:[&>button]:h-8"><ActionMenu label={t('files.more')} icon={<MoreHorizontal className="h-4 w-4" />} items={selectedActions} compact /></div>}
        <div className="flex w-full flex-wrap items-center gap-2 border-t border-outline-variant/70 pt-2 sm:ml-auto sm:w-auto sm:border-0 sm:pt-0">
          <div className="flex min-h-11 items-center gap-2 rounded-md px-1 sm:min-h-8">
            <div className="flex flex-col leading-tight">
              <span className="text-[11px] text-on-surface">{autosave ? t('files.autosaveOn') : t('files.autosaveOff')}</span>
              <span className={`text-[10px] ${activeTab?.saveState === 'clean' ? 'text-status-success' : 'text-on-surface-variant'}`}>
                {activeTab?.saveState === 'clean' ? t('files.allSaved') : activeTab?.saveState === 'conflict' ? t('files.conflict') : t('files.autosave')}
              </span>
            </div>
            <Switch checked={autosave} onCheckedChange={setAutosave} disabled={!canWrite} aria-label={t('files.autosave')} className="before:absolute before:-inset-x-2 before:-inset-y-3" />
          </div>
          <button ref={treeTriggerRef} type="button" onClick={() => { setInspectorOpen(false); setTreeOpen((value) => !value) }} className="msm-btn-tertiary inline-flex h-11 items-center justify-center gap-2 px-3 text-xs lg:hidden" aria-label={t('files.showTree')} aria-expanded={treeOpen}><Menu className="h-4 w-4" /><span>{t('files.filesDrawer')}</span></button>
          <button ref={inspectorTriggerRef} type="button" onClick={() => { setTreeOpen(false); setInspectorOpen((value) => !value) }} className="msm-btn-tertiary inline-flex h-11 items-center justify-center gap-2 px-3 text-xs xl:hidden" aria-label={t('files.showInspector')} aria-expanded={inspectorOpen}><Info className="h-4 w-4" /><span>{t('files.detailsDrawer')}</span></button>
        </div>
        <input ref={fileInputRef} type="file" multiple className="hidden" onChange={(event) => { enqueueUpload(event.target.files); event.target.value = '' }} />
      </header>

      <div className="flex min-h-10 flex-wrap items-center gap-2 border-b border-outline-variant px-3 py-2 text-xs">
        <nav aria-label={t('files.breadcrumb')} className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto font-mono text-on-surface-variant">
          <button type="button" onClick={() => toggleDirectory('')} className="whitespace-nowrap text-secondary hover:text-primary">{t('files.serverFiles')}</button>
          {breadcrumbs.map((segment, index) => (
            <span key={`${segment}-${index}`} className="flex items-center gap-1 whitespace-nowrap"><ChevronRight className="h-3 w-3" /><button type="button" onClick={() => toggleDirectory(breadcrumbs.slice(0, index + 1).join('/'))} className="hover:text-on-surface">{segment}</button></span>
          ))}
        </nav>
        <p className="shrink-0 text-[11px] text-on-surface-variant">{t('files.summary', { files: directorySummary.files, folders: directorySummary.folders, size: formatBytes(directorySummary.bytes) })}</p>
      </div>

      <div
        className="grid min-h-[560px] grid-cols-1 lg:h-[clamp(560px,calc(100vh-300px),760px)] lg:min-h-0 lg:grid-cols-[280px_minmax(0,1fr)] lg:overflow-hidden xl:grid-cols-[260px_minmax(0,1fr)_230px]"
        onDragOver={(event) => { if (canWrite) { event.preventDefault(); setDragOver(true) } }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDragOver(false)
          if (!event.dataTransfer.types.includes('application/x-msm-path')) enqueueUpload(event.dataTransfer.files)
        }}
      >
        {(treeOpen || inspectorOpen) && <button type="button" aria-label={t('common.close')} onClick={() => { if (treeOpen) { setTreeOpen(false); window.requestAnimationFrame(() => treeTriggerRef.current?.focus()) } else { setInspectorOpen(false); window.requestAnimationFrame(() => inspectorTriggerRef.current?.focus()) } }} className="fixed inset-0 z-30 cursor-default bg-black/55 backdrop-blur-sm xl:hidden" />}
        <aside ref={treePanelRef} tabIndex={treeOpen ? -1 : undefined} aria-label={t('files.filesDrawer')} className={`${treeOpen ? 'fixed inset-x-3 bottom-3 top-24 z-40 flex shadow-panel-strong' : 'hidden'} min-h-0 flex-col border-r border-outline-variant bg-surface-container-low/95 outline-none lg:static lg:flex lg:shadow-none`}>
          <div className="flex min-h-11 items-center gap-2 border-b border-outline-variant p-2.5">
            <div className="relative min-w-0 flex-1"><Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-on-surface-variant" /><input type="search" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder={t('files.searchPlaceholder')} className="msm-input h-8 pl-8 text-xs" /></div>
            {treeOpen && <button type="button" onClick={() => { setTreeOpen(false); window.requestAnimationFrame(() => treeTriggerRef.current?.focus()) }} className="msm-btn-tertiary inline-flex h-11 items-center justify-center gap-2 px-3 text-xs lg:hidden" aria-label={t('common.close')}><X className="h-4 w-4" /><span>{t('common.close')}</span></button>}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <FileTree
              nodes={nodes}
              expanded={expanded}
              loadingPaths={loadingPaths}
              activePath={activePath}
              searchResults={searchResults}
              searchTruncated={searchTruncated}
              emptyLabel={t('files.empty')}
              searchEmptyLabel={t('files.searchEmpty')}
              searchTruncatedLabel={t('files.searchTruncated')}
              onToggle={toggleDirectory}
              onOpenFile={(path) => void openFile(path)}
              onContextMenu={(event, entry, parent) => { event.preventDefault(); contextTriggerRef.current = event.currentTarget as HTMLElement; const selection = { entry, parent }; setSelectedEntry(selection); setContextMenu({ ...selection, x: event.clientX, y: event.clientY }) }}
              onDragStart={handleDragStart}
              onDropFolder={(event, entry, parent) => void handleDropFolder(event, entry, parent)}
            />
          </div>
          {uploads.length > 0 && <div className="border-t border-outline-variant p-2">{uploads.map((job) => <div key={job.id} className="py-1.5"><div className="flex justify-between gap-2 text-[10px]"><span className="truncate">{job.name}</span><span className={job.status === 'error' ? 'text-status-error' : 'text-on-surface-variant'}>{job.status === 'error' ? t('common.error') : `${Math.round(job.fraction * 100)}%`}</span></div><div className="mt-1 h-1 overflow-hidden rounded-full bg-surface-container-highest"><div className={`h-full ${job.status === 'error' ? 'bg-status-error' : 'bg-secondary'}`} style={{ width: `${job.fraction * 100}%` }} /></div></div>)}</div>}
          <footer className="border-t border-outline-variant px-3 py-2 text-[10px] text-on-surface-variant">{directorySummary.files} Dateien · {directorySummary.folders} Ordner · {formatBytes(directorySummary.bytes)}</footer>
        </aside>

        <div className={`min-h-0 min-w-0 lg:flex ${dragOver ? 'ring-1 ring-inset ring-secondary' : ''}`}>
          <FileEditorWorkspace tabs={tabs} activePath={activePath} canWrite={canWrite} tabListLabel={t('files.openFiles')} horizontalScrollHint={t('files.horizontalScrollHint')} onActivate={setActivePath} onChange={updateTabContent} onSave={(path) => void saveTab(path)} onClose={(path) => void closeTab(path)} onReload={(path) => void reloadTab(path)} />
        </div>

        <aside ref={inspectorPanelRef} tabIndex={inspectorOpen ? -1 : undefined} aria-label={t('files.detailsDrawer')} className={`${inspectorOpen ? 'fixed inset-x-3 bottom-3 top-24 z-40 block overflow-y-auto shadow-panel-strong' : 'hidden'} border-l border-outline-variant bg-surface-container-low/95 outline-none lg:col-span-2 lg:max-h-full lg:overflow-y-auto lg:border-l-0 lg:border-t lg:border-outline-variant xl:static xl:col-span-1 xl:block xl:border-l xl:border-t-0 xl:shadow-none`}>
          <div className="flex min-h-11 items-center justify-between border-b border-outline-variant px-3"><h3 className="text-xs font-semibold text-on-surface">{t('files.details')}</h3>{inspectorOpen && <button type="button" onClick={() => { setInspectorOpen(false); window.requestAnimationFrame(() => inspectorTriggerRef.current?.focus()) }} className="msm-btn-tertiary inline-flex h-11 items-center justify-center gap-2 px-3 text-xs xl:hidden" aria-label={t('common.close')}><X className="h-4 w-4" /><span>{t('common.close')}</span></button>}</div>
          {activeTab ? <>
            <div className="border-b border-outline-variant p-3"><div className="flex items-start gap-2"><ArchiveRestore className="mt-0.5 h-4 w-4 text-secondary" /><div className="min-w-0"><p className="truncate text-xs font-semibold text-on-surface">{fileName(activeTab.path)}</p><p className="mt-0.5 truncate font-mono text-[10px] text-on-surface-variant">{activeTab.path}</p></div></div></div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 border-b border-outline-variant p-3 text-[11px]"><dt className="text-on-surface-variant">{t('files.modified')}</dt><dd className="text-right text-on-surface">{formatModified(activeTab.modified)}</dd><dt className="text-on-surface-variant">{t('files.size')}</dt><dd className="text-right font-mono text-on-surface">{formatBytes(activeTab.size)}</dd><dt className="text-on-surface-variant">{t('files.permissions')}</dt><dd className="text-right font-mono text-on-surface">{activeTab.mode ?? t('files.notAvailable')}</dd><dt className="text-on-surface-variant">{t('files.owner')}</dt><dd className="truncate text-right text-on-surface">{activeTab.owner ?? t('files.notAvailable')}</dd><dt className="text-on-surface-variant">{t('files.group')}</dt><dd className="truncate text-right text-on-surface">{activeTab.group ?? t('files.notAvailable')}</dd></dl>
            <div className="border-b border-outline-variant p-3">
              <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">{t('files.versionHistory')}</h4>
              {(versions[activeTab.path] ?? []).length === 0 ? (
                <p className="text-[11px] text-on-surface-variant">{t('files.noVersions')}</p>
              ) : (
                <div className="max-h-56 space-y-1 overflow-y-auto pr-1">
                  {(versions[activeTab.path] ?? []).map((version) => (
                    <div key={version.id} className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-surface-container-highest/70">
                      <div className="min-w-0 flex-1">
                        <p className="text-[10px] text-on-surface">{formatModified(version.created_at)}</p>
                        <p className="font-mono text-[9px] text-on-surface-variant">{formatBytes(version.size)}</p>
                      </div>
                      {canWrite && (
                        <button type="button" onClick={() => void restoreVersion(activeTab.path, version.id)} className="msm-btn-tertiary h-7 px-2 text-[10px]">
                          {t('files.restoreVersion')}
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="p-3">
              <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-on-surface-variant">{t('files.quickActions')}</h4>
              <button type="button" onClick={() => { const parent = parentPath(activeTab.path); const entry = nodes[parent]?.find((item) => item.name === fileName(activeTab.path)); if (entry) downloadEntry({ entry, parent }) }} className="flex min-h-8 w-full items-center gap-2 rounded-md px-2 text-left text-xs text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface"><Download className="h-3.5 w-3.5" />{t('files.download')}</button>
            </div>
          </> : <p className="p-6 text-center text-xs text-on-surface-variant">{t('files.noDetails')}</p>}
        </aside>
      </div>

      {contextMenu && <div ref={contextMenuRef} className="fixed z-[120] min-w-48 rounded-lg border border-outline-variant bg-surface-container-high p-1.5 shadow-panel" style={{ left: Math.min(contextMenu.x, window.innerWidth - 210), top: Math.min(contextMenu.y, window.innerHeight - 240) }} onClick={(event) => event.stopPropagation()} role="menu" aria-label={t('files.more')}>{actionItems(contextMenu).map((item) => <button key={item.key} type="button" role="menuitem" disabled={item.disabled} onClick={() => { item.onSelect(); setContextMenu(null) }} className={`flex min-h-11 w-full items-center gap-2 rounded-md px-2.5 text-left text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary sm:min-h-9 ${item.separatorBefore ? 'mt-1 border-t border-outline-variant' : ''} ${item.destructive ? 'text-status-error hover:bg-status-error/10' : 'text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface'}`}>{item.icon}{item.label}</button>)}</div>}

      {promptDialog && <div className="fixed inset-0 z-[130] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"><div className="msm-card w-full max-w-md p-5"><h2 className="font-headline text-lg font-semibold text-on-surface">{promptDialog.title}</h2><label className="mt-4 block text-xs font-medium text-on-surface-variant">{promptDialog.label}</label><input ref={promptInputRef} defaultValue={promptDialog.initialValue} className="msm-input mt-1.5" onKeyDown={(event) => { if (event.key === 'Enter') void promptDialog.onConfirm(event.currentTarget.value); if (event.key === 'Escape') setPromptDialog(null) }} /><div className="mt-5 flex justify-end gap-2"><button type="button" className="msm-btn-secondary h-9 px-3 text-sm" onClick={() => setPromptDialog(null)}>{t('common.cancel')}</button><button type="button" className="msm-btn-primary h-9 px-3 text-sm" onClick={() => void promptDialog.onConfirm(promptInputRef.current?.value ?? '')}>{promptDialog.confirmLabel}</button></div></div></div>}

      {moveDialog && <div className="fixed inset-0 z-[130] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"><div className="msm-card w-full max-w-md p-5"><h2 className="font-headline text-lg font-semibold text-on-surface">{t('files.move')}</h2><p className="mt-2 text-sm text-on-surface-variant">{t('files.moveHint', { name: moveDialog.entry.name })}</p><label className="mt-4 block text-xs font-medium text-on-surface-variant">{t('files.targetFolder')}</label><input value={moveTarget} onChange={(event) => setMoveTarget(event.target.value)} className="msm-input mt-1.5" placeholder="mods/config" /><p className="mt-1 text-xs text-on-surface-variant">{t('files.moveTargetHint')}</p><div className="mt-5 flex justify-end gap-2"><button type="button" className="msm-btn-secondary h-9 px-3 text-sm" onClick={() => setMoveDialog(null)}>{t('common.cancel')}</button><button type="button" className="msm-btn-primary h-9 px-3 text-sm" onClick={() => void submitMove()}>{t('common.save')}</button></div></div></div>}
    </div>
  )
}
