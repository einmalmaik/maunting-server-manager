import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import Editor from '@monaco-editor/react'
import {
  Folder,
  FileText,
  FileCode,
  File,
  ChevronRight,
  Upload,
  Trash2,
  FolderPlus,
  Download,
  Save,
  AlertCircle,
  AlertTriangle,
  FolderOpen,
  Archive,
  Loader2,
  Pencil,
  X,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { ApiError, configApi, filesApi, serversApi } from '@/lib/api'
import { bindMonacoSaveShortcut, isEditableTarget, useSaveShortcut } from '@/hooks/useSaveShortcut'
import { loadServerWorkspace, saveServerWorkspace } from '@/lib/workspace'
import {
  createEditorTabWorkspace,
  createFileManagerWorkspace,
  prepareFileManagerWorkspaceForStorage,
  restoreFileManagerWorkspace,
  type EditorTabWorkspace,
} from '@/lib/workspaces'
import type { ConfigQuickDirectory, FileEntry, ServersData } from '@/lib/types'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useUiLanguage } from '@/lib/ui-language'

const FILE_MANAGER_WORKSPACE_SCOPE = 'files-page'

const EXT_MAP: Record<string, string> = {
  sh: 'shell', py: 'python', json: 'json', cfg: 'ini', xml: 'xml',
  txt: 'plaintext', ini: 'ini', log: 'plaintext', js: 'javascript',
  ts: 'typescript', html: 'html', css: 'css', yaml: 'yaml', yml: 'yaml',
}

function detectLanguage(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() ?? ''
  return EXT_MAP[ext] ?? 'plaintext'
}

function isZipArchive(entry: FileEntry): boolean {
  return !entry.is_dir && entry.name.toLowerCase().endsWith('.zip')
}

function FileIcon({ entry }: { entry: FileEntry }) {
  if (entry.is_dir) return <Folder className="h-4 w-4 text-accent/80 shrink-0" />
  if (isZipArchive(entry)) return <Archive className="h-4 w-4 text-orange-400/80 shrink-0" />
  const ext = entry.name.split('.').pop()?.toLowerCase() ?? ''
  if (['sh', 'py', 'js', 'ts'].includes(ext)) return <FileCode className="h-4 w-4 text-green-400/80 shrink-0" />
  if (['json', 'ini', 'cfg', 'xml', 'yaml', 'yml'].includes(ext)) return <FileText className="h-4 w-4 text-yellow-400/80 shrink-0" />
  return <File className="h-4 w-4 text-muted-foreground shrink-0" />
}

function formatSize(bytes: number | null): string {
  if (bytes === null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

interface ContextMenuState {
  entry: FileEntry
  x: number
  y: number
}

interface UploadBatchEntry {
  file: File
  relativePath: string
}

type DataTransferItemWithEntry = DataTransferItem & {
  webkitGetAsEntry?: () => FileSystemEntry | null
}

function getParentPath(path: string): string {
  if (!path) return ''
  const parts = path.split('/').filter(Boolean)
  return parts.slice(0, -1).join('/')
}

function downloadBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => window.URL.revokeObjectURL(url), 60_000)
}

function renameNestedPath(path: string, fromPath: string, toPath: string): string {
  if (path === fromPath) return toPath
  if (!path.startsWith(`${fromPath}/`)) return path
  return `${toPath}${path.slice(fromPath.length)}`
}

function updateEditorTab(
  tabs: EditorTabWorkspace[],
  path: string,
  updater: (tab: EditorTabWorkspace) => EditorTabWorkspace,
): EditorTabWorkspace[] {
  return tabs.map((tab) => (tab.path === path ? updater(tab) : tab))
}

function hasFilePayload(dataTransfer: DataTransfer | null): boolean {
  if (!dataTransfer) return false
  return Array.from(dataTransfer.types).includes('Files')
}

function fileFromDroppedEntry(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => {
    entry.file(resolve, reject)
  })
}

function readDirectoryEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => {
    const collected: FileSystemEntry[] = []

    const readNext = () => {
      reader.readEntries((entries) => {
        if (entries.length === 0) {
          resolve(collected)
          return
        }
        collected.push(...entries)
        readNext()
      }, reject)
    }

    readNext()
  })
}

async function walkDroppedEntry(
  entry: FileSystemEntry,
  prefix: string,
  collected: UploadBatchEntry[],
): Promise<void> {
  if (entry.isFile) {
    const file = await fileFromDroppedEntry(entry as FileSystemFileEntry)
    const relativePath = prefix ? `${prefix}/${file.name}` : file.name
    collected.push({ file, relativePath })
    return
  }

  if (!entry.isDirectory) return

  const directory = entry as FileSystemDirectoryEntry
  const nextPrefix = prefix ? `${prefix}/${entry.name}` : entry.name
  const children = await readDirectoryEntries(directory.createReader())
  for (const child of children) {
    await walkDroppedEntry(child, nextPrefix, collected)
  }
}

async function collectDroppedUploadEntries(dataTransfer: DataTransfer): Promise<UploadBatchEntry[]> {
  const entryItems = Array.from(dataTransfer.items)
    .map((item) => (item as DataTransferItemWithEntry).webkitGetAsEntry?.() ?? null)
    .filter((entry): entry is FileSystemEntry => entry !== null)

  if (entryItems.length > 0) {
    const collected: UploadBatchEntry[] = []
    for (const entry of entryItems) {
      await walkDroppedEntry(entry, '', collected)
    }
    return collected.sort((a, b) => a.relativePath.localeCompare(b.relativePath))
  }

  return Array.from(dataTransfer.files)
    .map((file) => ({ file, relativePath: file.name }))
    .sort((a, b) => a.relativePath.localeCompare(b.relativePath))
}

export default function FileManagerPage() {
  const { copy } = useUiLanguage()
  const t = copy.files
  const [workspace, setWorkspace] = useState(() => createFileManagerWorkspace())
  const [isNewFolderDialogOpen, setIsNewFolderDialogOpen] = useState(false)
  const [newFolderName, setNewFolderName] = useState('')
  const [selectedPaths, setSelectedPaths] = useState<string[]>([])
  const [selectionAnchor, setSelectionAnchor] = useState<string | null>(null)
  const [isRenameDialogOpen, setIsRenameDialogOpen] = useState(false)
  const [renameValue, setRenameValue] = useState('')
  const [deleteDialogPaths, setDeleteDialogPaths] = useState<string[] | null>(null)
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null)
  const [isDragActive, setIsDragActive] = useState(false)
  const [loadingTabPath, setLoadingTabPath] = useState<string | null>(null)
  const singleUploadRef = useRef<HTMLInputElement>(null)
  const folderUploadRef = useRef<HTMLInputElement>(null)
  const dragDepthRef = useRef(0)
  const loadRequestIdRef = useRef(0)
  const currentServerRef = useRef<string | null>(null)
  const queryClient = useQueryClient()

  const { data: serversData, isLoading: isServersLoading } = useQuery<ServersData>({
    queryKey: ['servers'],
    queryFn: serversApi.list,
  })
  const hasCurrentServer = Boolean(serversData?.current)
  const currentServer = serversData?.current ?? null
  const activeTab = useMemo(
    () => workspace.tabs.find((tab) => tab.path === workspace.activeTabPath) ?? null,
    [workspace.activeTabPath, workspace.tabs],
  )
  const activeTabDirty = activeTab?.isDirty ?? false
  const isDirty = activeTabDirty
  const editorContent = activeTab?.content ?? ''
  const editorContentRef = useRef('')
  const fileQuery = {
    isPending: activeTab !== null && !activeTab.hasLoaded && loadingTabPath === activeTab.path,
    isError: Boolean(activeTab?.loadError),
  }

  useEffect(() => {
    currentServerRef.current = currentServer
  }, [currentServer])

  useEffect(() => {
    editorContentRef.current = activeTab?.content ?? ''
  }, [activeTab?.content])

  useEffect(() => {
    loadRequestIdRef.current += 1
    setLoadingTabPath(null)
    if (!currentServer) {
      setWorkspace(createFileManagerWorkspace())
      return
    }
    setWorkspace(restoreFileManagerWorkspace(
      loadServerWorkspace(FILE_MANAGER_WORKSPACE_SCOPE, currentServer, createFileManagerWorkspace()),
    ))
  }, [currentServer])

  useEffect(() => {
    if (!currentServer) return
    saveServerWorkspace(FILE_MANAGER_WORKSPACE_SCOPE, currentServer, prepareFileManagerWorkspaceForStorage(workspace))
  }, [currentServer, workspace])

  const overviewQuery = useQuery({
    queryKey: ['config-overview', currentServer],
    queryFn: configApi.overview,
    enabled: hasCurrentServer,
    staleTime: 30_000,
  })

  const dirQuery = useQuery({
    queryKey: ['files', currentServer, 'list', workspace.currentPath],
    queryFn: () => filesApi.list(workspace.currentPath),
    staleTime: 10_000,
    enabled: hasCurrentServer,
  })

  useEffect(() => {
    if (folderUploadRef.current) {
      folderUploadRef.current.setAttribute('webkitdirectory', '')
      folderUploadRef.current.setAttribute('directory', '')
    }
  }, [])

  useEffect(() => {
    if (!contextMenu) return
    const closeMenu = () => setContextMenu(null)
    window.addEventListener('click', closeMenu)
    window.addEventListener('scroll', closeMenu, true)
    return () => {
      window.removeEventListener('click', closeMenu)
      window.removeEventListener('scroll', closeMenu, true)
    }
  }, [contextMenu])

  useEffect(() => {
    setSelectedPaths([])
    setSelectionAnchor(null)
    setContextMenu(null)
  }, [currentServer, workspace.currentPath])

  useEffect(() => {
    const clearDragState = () => {
      dragDepthRef.current = 0
      setIsDragActive(false)
    }

    window.addEventListener('dragend', clearDragState)
    window.addEventListener('drop', clearDragState)
    window.addEventListener('blur', clearDragState)
    return () => {
      window.removeEventListener('dragend', clearDragState)
      window.removeEventListener('drop', clearDragState)
      window.removeEventListener('blur', clearDragState)
    }
  }, [])

  const invalidateFileListings = useCallback((server: string | null = currentServerRef.current) => {
    if (!server) return
    void queryClient.invalidateQueries({ queryKey: ['files', server, 'list'] })
  }, [queryClient])

  const invalidateRelatedFileQueries = useCallback((server: string | null, path: string) => {
    if (!server) return
    void queryClient.invalidateQueries({ queryKey: ['files', server, 'content', path] })
    void queryClient.invalidateQueries({ queryKey: ['config-file', server, path] })
    void queryClient.invalidateQueries({ queryKey: ['config-overview', server] })
  }, [queryClient])

  const loadEditorTab = useCallback(async (path: string) => {
    if (!currentServer) return

    const requestId = ++loadRequestIdRef.current
    const serverAtStart = currentServer
    setLoadingTabPath(path)
    try {
      const data = await queryClient.fetchQuery({
        queryKey: ['files', serverAtStart, 'content', path],
        queryFn: () => filesApi.readContent(path),
        staleTime: Infinity,
      })
      if (requestId !== loadRequestIdRef.current) return
      const content = data.content ?? ''
      setWorkspace((prev) => ({
        ...prev,
        tabs: updateEditorTab(prev.tabs, path, (tab) => (
          tab.isDirty && tab.hasLoaded
            ? tab
            : {
                ...tab,
                name: data.path.split('/').pop() ?? tab.name,
                content,
                originalContent: content,
                isDirty: false,
                hasLoaded: true,
                loadError: null,
              }
        )),
      }))
    } catch (error) {
      if (requestId !== loadRequestIdRef.current) return
      const detail = error instanceof ApiError ? error.message : t.loadFileFailed
      setWorkspace((prev) => ({
        ...prev,
        tabs: updateEditorTab(prev.tabs, path, (tab) => ({
          ...tab,
          hasLoaded: true,
          loadError: detail,
        })),
      }))
    } finally {
      if (requestId === loadRequestIdRef.current) {
        setLoadingTabPath((prev) => (prev === path ? null : prev))
      }
    }
  }, [currentServer, queryClient, t.loadFileFailed])

  useEffect(() => {
    if (!activeTab || activeTab.hasLoaded || activeTab.loadError || loadingTabPath === activeTab.path) return
    void loadEditorTab(activeTab.path)
  }, [activeTab, loadEditorTab, loadingTabPath])

  const saveMutation = useMutation({
    mutationFn: ({ filePath, content }: { filePath: string; content: string; server: string }) => {
      return filesApi.writeContent(filePath, content)
    },
    onSuccess: (_data, variables) => {
      const isCurrentServer = variables.server === currentServerRef.current
      if (isCurrentServer) {
        toast.success(t.fileSaved)
        setWorkspace((prev) => ({
          ...prev,
          tabs: updateEditorTab(prev.tabs, variables.filePath, (tab) => ({
            ...tab,
            originalContent: variables.content,
            isDirty: tab.content !== variables.content,
            hasLoaded: true,
            loadError: null,
          })),
        }))
      }
      invalidateRelatedFileQueries(variables.server, variables.filePath)
      invalidateFileListings(variables.server)
    },
    onError: (error: unknown) => {
      const detail = error instanceof ApiError ? error.message : undefined
      toast.error(t.fileSaveFailed(detail))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (paths: string[]) => paths.length === 1 ? filesApi.delete(paths[0]) : filesApi.deleteBatch(paths),
    onSuccess: (_data, deletedPaths) => {
      toast.success(deletedPaths.length === 1 ? t.deleted : t.bulkDeleted(deletedPaths.length))
      invalidateFileListings()
      setDeleteDialogPaths(null)
      setSelectedPaths((prev) => prev.filter((path) => !deletedPaths.some((deletedPath) => path === deletedPath || path.startsWith(`${deletedPath}/`))))
      setWorkspace((prev) => {
        const remainingTabs = prev.tabs.filter((tab) => !deletedPaths.some((deletedPath) => tab.path === deletedPath || tab.path.startsWith(`${deletedPath}/`)))
        const activeTabPath = remainingTabs.some((tab) => tab.path === prev.activeTabPath)
          ? prev.activeTabPath
          : (remainingTabs.length > 0 ? remainingTabs[remainingTabs.length - 1].path : null)
        const currentPathDeleted = deletedPaths.some((deletedPath) => prev.currentPath === deletedPath || prev.currentPath.startsWith(`${deletedPath}/`))
        return {
          ...prev,
          currentPath: currentPathDeleted ? getParentPath(prev.currentPath) : prev.currentPath,
          activeTabPath,
          tabs: remainingTabs,
        }
      })
    },
    onError: (error: unknown) => {
      const detail = error instanceof ApiError ? error.message : undefined
      toast.error(t.deleteFailed(detail))
    },
  })

  const mkdirMutation = useMutation({
    mutationFn: (name: string) => filesApi.mkdir(workspace.currentPath ? `${workspace.currentPath}/${name}` : name),
    onSuccess: () => {
      toast.success(t.directoryCreated)
      setIsNewFolderDialogOpen(false)
      setNewFolderName('')
      invalidateFileListings()
    },
    onError: () => toast.error(t.directoryCreateFailed),
  })

  const extractMutation = useMutation({
    mutationFn: ({ path }: { path: string }) => filesApi.extractArchive(path),
    onSuccess: (_data, variables) => {
      const archiveName = variables.path.split('/').pop() ?? 'archive'
      const targetName = archiveName.replace(/\.zip$/i, '')
      toast.success(t.directoryExtracted(targetName))
      invalidateFileListings()
    },
    onError: () => toast.error(t.directoryExtractFailed),
  })

  const renameMutation = useMutation({
    mutationFn: ({ path, newName }: { path: string; newName: string }) => filesApi.rename(path, newName),
    onSuccess: (data, variables) => {
      toast.success(t.renamed(data.name))
      setIsRenameDialogOpen(false)
      setRenameValue('')
      setSelectedPaths([data.path])
      setSelectionAnchor(data.path)
      setWorkspace((prev) => ({
        ...prev,
        currentPath: renameNestedPath(prev.currentPath, variables.path, data.path),
        activeTabPath: prev.activeTabPath ? renameNestedPath(prev.activeTabPath, variables.path, data.path) : null,
        tabs: prev.tabs.map((tab) => {
          const nextPath = renameNestedPath(tab.path, variables.path, data.path)
          if (nextPath === tab.path) return tab
          return {
            ...tab,
            path: nextPath,
            name: nextPath.split('/').pop() ?? tab.name,
          }
        }),
      }))
      invalidateFileListings()
    },
    onError: (error: unknown) => {
      const detail = error instanceof ApiError ? error.message : undefined
      toast.error(t.renameFailed(detail))
    },
  })

  const breadcrumbs = (() => {
    if (!workspace.currentPath) return [{ label: t.serverRoot, path: '' }]
    const parts = workspace.currentPath.split('/')
    return [
      { label: t.serverRoot, path: '' },
      ...parts.map((part, i) => ({
        label: part,
        path: parts.slice(0, i + 1).join('/'),
      })),
    ]
  })()

  const entries = dirQuery.data?.entries ?? []
  const sortedEntries = [...entries].sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
    return a.name.localeCompare(b.name)
  })
  const quickDirectories = overviewQuery.data?.quick_directories ?? []
  const selectedSet = new Set(selectedPaths)
  const selectedEntries = sortedEntries.filter((entry) => selectedSet.has(entry.path))
  const singleSelectedEntry = selectedEntries.length === 1 ? selectedEntries[0] : null

  useEffect(() => {
    const available = new Set(sortedEntries.map((entry) => entry.path))
    setSelectedPaths((prev) => prev.filter((path) => available.has(path)))
    setSelectionAnchor((prev) => (prev && available.has(prev) ? prev : null))
  }, [sortedEntries])

  const selectSingleEntry = useCallback((entry: FileEntry) => {
    setSelectedPaths([entry.path])
    setSelectionAnchor(entry.path)
  }, [])

  const toggleEntrySelection = useCallback((entry: FileEntry) => {
    setSelectedPaths((prev) => {
      if (prev.includes(entry.path)) {
        return prev.filter((path) => path !== entry.path)
      }
      return [...prev, entry.path]
    })
    setSelectionAnchor(entry.path)
  }, [])

  const selectRangeToEntry = useCallback((entry: FileEntry) => {
    const anchorPath = selectionAnchor ?? selectedPaths[selectedPaths.length - 1] ?? entry.path
    const anchorIndex = sortedEntries.findIndex((item) => item.path === anchorPath)
    const targetIndex = sortedEntries.findIndex((item) => item.path === entry.path)
    if (anchorIndex === -1 || targetIndex === -1) {
      selectSingleEntry(entry)
      return
    }
    const [start, end] = anchorIndex < targetIndex ? [anchorIndex, targetIndex] : [targetIndex, anchorIndex]
    setSelectedPaths(sortedEntries.slice(start, end + 1).map((item) => item.path))
  }, [selectSingleEntry, selectedPaths, selectionAnchor, sortedEntries])

  const openEditorTab = useCallback((entry: FileEntry) => {
    setWorkspace((prev) => {
      const existing = prev.tabs.find((tab) => tab.path === entry.path)
      if (existing) {
        return {
          ...prev,
          activeTabPath: entry.path,
          tabs: existing.loadError
            ? updateEditorTab(prev.tabs, entry.path, (tab) => ({ ...tab, hasLoaded: false, loadError: null }))
            : prev.tabs,
        }
      }
      return {
        ...prev,
        activeTabPath: entry.path,
        tabs: [...prev.tabs, createEditorTabWorkspace(entry.path, entry.name)],
      }
    })
  }, [])

  const closeEditorTab = useCallback((path: string) => {
    setWorkspace((prev) => {
      const tab = prev.tabs.find((item) => item.path === path)
      if (!tab) return prev
      if (tab.isDirty && !confirm(t.unsavedChanges)) return prev
      const remainingTabs = prev.tabs.filter((item) => item.path !== path)
      const activeTabPath = prev.activeTabPath === path
        ? (remainingTabs.length > 0 ? remainingTabs[remainingTabs.length - 1].path : null)
        : prev.activeTabPath
      return {
        ...prev,
        activeTabPath,
        tabs: remainingTabs,
      }
    })
  }, [t.unsavedChanges])

  const handleDownloadSelection = useCallback(async (paths: string[]) => {
    if (paths.length === 0) return
    try {
      if (paths.length === 1 && singleSelectedEntry && !singleSelectedEntry.is_dir) {
        window.open(filesApi.downloadUrl(paths[0]), '_blank', 'noopener,noreferrer')
        return
      }
      const { blob, filename } = await filesApi.downloadBatch(paths)
      downloadBlob(blob, filename)
    } catch (error) {
      const detail = error instanceof ApiError ? error.message : undefined
      toast.error(t.downloadFailed(detail))
    }
  }, [singleSelectedEntry, t.downloadFailed])

  const activateEntry = useCallback((entry: FileEntry) => {
    setContextMenu(null)
    selectSingleEntry(entry)
    if (entry.is_dir) {
      setWorkspace((prev) => ({ ...prev, currentPath: entry.path }))
      return
    }
    openEditorTab(entry)
  }, [openEditorTab, selectSingleEntry])

  const handleEntryClick = useCallback((entry: FileEntry, event?: React.MouseEvent | React.KeyboardEvent) => {
    if (event && 'shiftKey' in event && event.shiftKey) {
      setContextMenu(null)
      selectRangeToEntry(entry)
      return
    }
    if (event && 'metaKey' in event && event.metaKey) {
      setContextMenu(null)
      toggleEntrySelection(entry)
      return
    }
    if (event && 'ctrlKey' in event && event.ctrlKey) {
      setContextMenu(null)
      toggleEntrySelection(entry)
      return
    }
    activateEntry(entry)
  }, [activateEntry, selectRangeToEntry, toggleEntrySelection])

  const handleBreadcrumbClick = useCallback((path: string) => {
    setWorkspace((prev) => ({ ...prev, currentPath: path }))
  }, [])

  const handleDelete = useCallback((entry: FileEntry, e?: React.MouseEvent) => {
    e?.stopPropagation()
    setContextMenu(null)
    setDeleteDialogPaths([entry.path])
  }, [])

  const handleRename = useCallback((entry: FileEntry, e?: React.MouseEvent) => {
    e?.stopPropagation()
    setContextMenu(null)
    setSelectedPaths([entry.path])
    setSelectionAnchor(entry.path)
    setRenameValue(entry.name)
    setIsRenameDialogOpen(true)
  }, [])

  const handleQuickDirectory = useCallback((entry: ConfigQuickDirectory) => {
    if (!entry.exists) return
    setWorkspace((prev) => ({ ...prev, currentPath: entry.path }))
  }, [])

  const handleSingleUpload = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const toastId = toast.loading(`${t.uploadFile}: ${file.name}...`)
    filesApi.upload(workspace.currentPath, file)
      .then(() => {
        toast.success(t.uploadComplete, { id: toastId })
        invalidateFileListings()
      })
      .catch((error: unknown) => {
        const detail = error instanceof ApiError ? error.message : undefined
        toast.error(t.uploadFailed(detail), { id: toastId })
      })
    e.target.value = ''
  }, [invalidateFileListings, t.uploadComplete, t.uploadFailed, t.uploadFile, workspace.currentPath])

  const handleFolderUpload = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    if (files.length === 0) return
    const toastId = toast.loading(`${t.uploadFolder}...`)
    filesApi.uploadBatch(workspace.currentPath, files)
      .then(() => {
        toast.success(t.uploadFolderComplete, { id: toastId })
        invalidateFileListings()
      })
      .catch((error: unknown) => {
        const detail = error instanceof ApiError ? error.message : undefined
        toast.error(t.uploadFolderFailed(detail), { id: toastId })
      })
    e.target.value = ''
  }, [invalidateFileListings, t.uploadFolder, t.uploadFolderComplete, t.uploadFolderFailed, workspace.currentPath])

  const uploadDroppedEntries = useCallback(async (entriesToUpload: UploadBatchEntry[]) => {
    if (entriesToUpload.length === 0) return
    const toastId = toast.loading(t.dropUploadLoading(entriesToUpload.length))
    try {
      const isSingleFlatFile = entriesToUpload.length === 1 && !entriesToUpload[0].relativePath.includes('/')
      if (isSingleFlatFile) {
        await filesApi.upload(workspace.currentPath, entriesToUpload[0].file)
      } else {
        await filesApi.uploadBatchEntries(workspace.currentPath, entriesToUpload)
      }
      toast.success(t.uploadItemsComplete(entriesToUpload.length), { id: toastId })
      invalidateFileListings()
    } catch (error) {
      const detail = error instanceof ApiError ? error.message : undefined
      toast.error(t.uploadItemsFailed(detail), { id: toastId })
    }
  }, [invalidateFileListings, t, workspace.currentPath])

  const handleCreateFolder = useCallback(() => {
    const trimmed = newFolderName.trim()
    if (!trimmed || /[/\\:]/.test(trimmed) || trimmed === '.' || trimmed === '..') {
      toast.error(t.invalidFolderName)
      return
    }
    mkdirMutation.mutate(trimmed)
  }, [mkdirMutation, newFolderName, t.invalidFolderName])

  const handleContextMenu = useCallback((e: React.MouseEvent, entry: FileEntry) => {
    e.preventDefault()
    if (!selectedSet.has(entry.path)) {
      setSelectedPaths([entry.path])
      setSelectionAnchor(entry.path)
    }
    setContextMenu({ entry, x: e.clientX, y: e.clientY })
  }, [selectedSet])

  const handleOpenRenameDialog = useCallback(() => {
    if (!singleSelectedEntry) return
    setRenameValue(singleSelectedEntry.name)
    setIsRenameDialogOpen(true)
  }, [singleSelectedEntry])

  const handleConfirmDelete = useCallback(() => {
    if (!deleteDialogPaths || deleteDialogPaths.length === 0 || deleteMutation.isPending) return
    deleteMutation.mutate(deleteDialogPaths)
  }, [deleteDialogPaths, deleteMutation])

  const handleConfirmRename = useCallback(() => {
    if (!singleSelectedEntry) return
    const trimmed = renameValue.trim()
    if (!trimmed || /[/\\]/.test(trimmed) || trimmed === '.' || trimmed === '..') {
      toast.error(t.invalidRename)
      return
    }
    renameMutation.mutate({ path: singleSelectedEntry.path, newName: trimmed })
  }, [renameMutation, renameValue, singleSelectedEntry, t.invalidRename])

  const handleActivateTab = useCallback((path: string) => {
    setWorkspace((prev) => ({ ...prev, activeTabPath: path }))
  }, [])

  const handleActiveTabContentChange = useCallback((content: string) => {
    if (!activeTab) return
    setWorkspace((prev) => ({
      ...prev,
      tabs: updateEditorTab(prev.tabs, activeTab.path, (tab) => ({
        ...tab,
        content,
        isDirty: content !== tab.originalContent,
        hasLoaded: true,
        loadError: null,
      })),
    }))
  }, [activeTab])

  const saveActiveTab = useCallback(() => {
    if (!activeTab || !activeTabDirty || saveMutation.isPending || !currentServer) return
    saveMutation.mutate({ filePath: activeTab.path, content: editorContentRef.current, server: currentServer })
  }, [activeTab, activeTabDirty, currentServer, saveMutation])

  const saveActiveTabRef = useRef(saveActiveTab)

  useEffect(() => {
    saveActiveTabRef.current = saveActiveTab
  }, [saveActiveTab])

  useSaveShortcut({
    enabled: hasCurrentServer && activeTab !== null,
    onSave: saveActiveTab,
  })

  const resetDragState = useCallback(() => {
    dragDepthRef.current = 0
    setIsDragActive(false)
  }, [])

  const handleDragEnter = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (!hasFilePayload(event.dataTransfer)) return
    event.preventDefault()
    event.stopPropagation()
    dragDepthRef.current += 1
    setIsDragActive(true)
  }, [])

  const handleDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (!hasFilePayload(event.dataTransfer)) return
    event.preventDefault()
    event.stopPropagation()
    event.dataTransfer.dropEffect = 'copy'
    setIsDragActive(true)
  }, [])

  const handleDragLeave = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (!hasFilePayload(event.dataTransfer)) return
    event.preventDefault()
    event.stopPropagation()
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1)
    if (dragDepthRef.current === 0) {
      setIsDragActive(false)
    }
  }, [])

  const handleDrop = useCallback(async (event: React.DragEvent<HTMLDivElement>) => {
    if (!hasFilePayload(event.dataTransfer)) return
    event.preventDefault()
    event.stopPropagation()
    resetDragState()
    try {
      const entriesToUpload = await collectDroppedUploadEntries(event.dataTransfer)
      await uploadDroppedEntries(entriesToUpload)
    } catch {
      toast.error(t.dropUploadReadFailed)
    }
  }, [resetDragState, t.dropUploadReadFailed, uploadDroppedEntries])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (isEditableTarget(e.target)) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
        if (sortedEntries.length === 0) return
        e.preventDefault()
        setSelectedPaths(sortedEntries.map((entry) => entry.path))
        setSelectionAnchor(sortedEntries[0]?.path ?? null)
        return
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedPaths.length === 0 || deleteMutation.isPending) return
        e.preventDefault()
        setDeleteDialogPaths(selectedPaths)
        return
      }
      if (e.key === 'Escape') {
        setContextMenu(null)
        setDeleteDialogPaths(null)
        setSelectedPaths([])
        setSelectionAnchor(null)
        return
      }
      if (e.key === 'F2' && selectedPaths.length === 1) {
        e.preventDefault()
        handleOpenRenameDialog()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [deleteMutation.isPending, handleOpenRenameDialog, selectedPaths, sortedEntries])

  if (!hasCurrentServer && !isServersLoading) {
    return (
      <Alert>
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>{t.noServerTitle}</AlertTitle>
        <AlertDescription>{t.noServerDescription}</AlertDescription>
      </Alert>
    )
  }

  return (
    <>
      <div className="flex h-full gap-4">
        <div
          className={cn(
            'relative flex w-72 shrink-0 flex-col overflow-hidden rounded-lg border bg-[var(--el-1)] transition-colors',
            isDragActive ? 'border-[var(--accent)] ring-1 ring-[var(--accent)]/40' : 'border-border',
          )}
          onDragEnter={handleDragEnter}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {isDragActive && (
            <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-background/80 backdrop-blur-[2px]">
              <div className="mx-4 flex max-w-60 flex-col items-center rounded-xl border border-[var(--accent)]/40 bg-background/95 px-4 py-5 text-center shadow-xl">
                <Upload className="mb-3 h-8 w-8 text-[var(--accent)]" />
                <div className="text-sm font-semibold text-foreground">{t.dropzoneActive}</div>
                <div className="mt-2 text-xs leading-5 text-muted-foreground">
                  {t.dropzoneDescription}
                </div>
              </div>
            </div>
          )}
          <div className="flex items-center gap-1 border-b border-border px-2 py-1.5">
            <button
              type="button"
              className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-accent/10 hover:text-foreground"
              title={t.uploadFile}
              onClick={() => singleUploadRef.current?.click()}
            >
              <Upload className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-accent/10 hover:text-foreground"
              title={t.uploadFolder}
              onClick={() => folderUploadRef.current?.click()}
            >
              <FolderOpen className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-accent/10 hover:text-foreground"
              title={t.newFolder}
              onClick={() => setIsNewFolderDialogOpen(true)}
            >
              <FolderPlus className="h-3.5 w-3.5" />
            </button>
            <input ref={singleUploadRef} type="file" className="hidden" onChange={handleSingleUpload} />
            <input ref={folderUploadRef} type="file" multiple className="hidden" onChange={handleFolderUpload} />
          </div>

          {selectedPaths.length > 0 && (
            <div className="flex items-center gap-1 border-b border-border bg-accent/5 px-2 py-1.5">
              <span className="min-w-0 flex-1 truncate px-1 text-[11px] font-medium text-foreground/80">
                {t.selectedCount(selectedPaths.length)}
              </span>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 gap-1 px-2 text-[11px]"
                disabled={selectedPaths.length === 0}
                onClick={() => void handleDownloadSelection(selectedPaths)}
                title={t.download}
              >
                <Download className="h-3 w-3" />
                {t.download}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 gap-1 px-2 text-[11px]"
                disabled={selectedPaths.length !== 1 || renameMutation.isPending}
                onClick={handleOpenRenameDialog}
                title={t.renameHotkey}
              >
                <Pencil className="h-3 w-3" />
                {t.rename}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 gap-1 px-2 text-[11px] text-destructive hover:text-destructive"
                disabled={deleteMutation.isPending}
                onClick={() => setDeleteDialogPaths(selectedPaths)}
                title={t.deleteHotkey}
              >
                <Trash2 className="h-3 w-3" />
                {t.delete}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 gap-1 px-2 text-[11px]"
                onClick={() => {
                  setSelectedPaths([])
                  setSelectionAnchor(null)
                }}
                title={t.clearSelection}
              >
                <X className="h-3 w-3" />
                {t.clear}
              </Button>
            </div>
          )}

          <div className="border-b border-border px-3 py-2">
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
              {t.quickAccess}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {quickDirectories.map((entry) => (
                <button
                  key={entry.key}
                  type="button"
                  disabled={!entry.exists}
                  onClick={() => handleQuickDirectory(entry)}
                  className={cn(
                    'rounded-full border px-2.5 py-1 text-[11px] transition-colors',
                    entry.exists
                      ? 'border-border bg-background text-foreground/80 hover:border-accent/40 hover:bg-accent/10 hover:text-foreground'
                      : 'cursor-not-allowed border-border/60 bg-muted/40 text-muted-foreground',
                    workspace.currentPath === entry.path && 'border-accent bg-accent/10 text-accent',
                  )}
                  title={entry.path || t.serverRoot}
                >
                  {entry.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-0.5 border-b border-border px-3 py-1.5 text-[11px] text-muted-foreground">
            {breadcrumbs.map((crumb, i) => (
              <span key={crumb.path} className="flex items-center gap-0.5">
                {i > 0 && <ChevronRight className="h-3 w-3 shrink-0" />}
                <button
                  className={cn(
                    'max-w-[80px] truncate transition-colors hover:text-foreground',
                    i === breadcrumbs.length - 1 && 'font-medium text-foreground',
                  )}
                  onClick={() => handleBreadcrumbClick(crumb.path)}
                  title={crumb.label}
                >
                  {crumb.label}
                </button>
              </span>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto">
            {dirQuery.isPending && (
              <div className="px-3 py-4 text-xs text-muted-foreground">{t.loading}</div>
            )}
            {dirQuery.isError && (
              <div className="flex items-center gap-1.5 px-3 py-4 text-xs text-destructive">
                <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                {t.loadDirectoryFailed}
              </div>
            )}
            {sortedEntries.map((entry) => (
              <div
                key={entry.path}
                role="button"
                tabIndex={0}
                className={cn(
                  'group flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-xs transition-colors',
                  'hover:bg-accent/10',
                  selectedSet.has(entry.path) && 'bg-accent/10 text-accent',
                  !selectedSet.has(entry.path) && activeTab?.path === entry.path && 'bg-accent/5 text-accent',
                  activeTab?.path !== entry.path && !selectedSet.has(entry.path) && 'text-foreground/80',
                )}
                onClick={(e) => handleEntryClick(entry, e)}
                onContextMenu={(e) => handleContextMenu(e, entry)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    handleEntryClick(entry, e)
                  }
                }}
              >
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 shrink-0 rounded border-border bg-transparent accent-[var(--accent)]"
                  checked={selectedSet.has(entry.path)}
                  aria-label={t.selectEntry(entry.name)}
                  onChange={() => undefined}
                  onClick={(e) => {
                    e.stopPropagation()
                    handleEntryClick(entry, e)
                  }}
                />
                <FileIcon entry={entry} />
                <span className="flex-1 truncate">{entry.name}</span>
                {!entry.is_dir && entry.size !== null && (
                  <span className="shrink-0 text-[10px] text-muted-foreground/60">
                    {formatSize(entry.size)}
                  </span>
                )}
                <button
                  className="ml-1 shrink-0 rounded p-0.5 text-muted-foreground opacity-0 transition-all hover:text-destructive focus:opacity-100 focus-visible:opacity-100 group-hover:opacity-100"
                  title={t.delete}
                  aria-label={`${t.delete} ${entry.name}`}
                  onClick={(e) => handleDelete(entry, e)}
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            ))}
            {!dirQuery.isPending && sortedEntries.length === 0 && (
              <div className="px-3 py-4 text-xs text-muted-foreground">
                <div>{t.emptyDirectory}</div>
                <div className="mt-2 text-[11px] text-muted-foreground/70">{t.dropzoneTitle}</div>
              </div>
            )}
          </div>
        </div>

        <div className="flex flex-1 flex-col overflow-hidden rounded-lg border border-border bg-[var(--el-1)]">
          <div className="flex items-center gap-1 overflow-x-auto border-b border-border px-2 py-1.5">
            {workspace.tabs.length === 0 ? (
              <span className="px-2 text-xs text-muted-foreground">{t.selectFile}</span>
            ) : workspace.tabs.map((tab) => (
              <div
                key={tab.path}
                className={cn(
                  'flex items-center gap-2 rounded-md border px-2.5 py-1 text-xs transition-colors',
                  workspace.activeTabPath === tab.path
                    ? 'border-accent bg-accent/10 text-accent'
                    : 'border-border bg-background text-foreground/75 hover:border-accent/30 hover:bg-accent/5 hover:text-foreground',
                )}
                title={tab.path}
              >
                <button
                  type="button"
                  className="flex items-center gap-2 truncate bg-transparent p-0 text-inherit"
                  onClick={() => handleActivateTab(tab.path)}
                >
                  <span className="truncate">{tab.name}</span>
                  {tab.isDirty && <span className="text-amber-400">●</span>}
                </button>
                <button
                  type="button"
                  className="rounded p-0.5 text-muted-foreground transition-colors hover:bg-background/70 hover:text-foreground"
                  onClick={(event) => {
                    event.stopPropagation()
                    closeEditorTab(tab.path)
                  }}
                  aria-label={t.closeTab(tab.name)}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>

          {activeTab ? (
            <>
              <div className="flex items-center gap-3 border-b border-border px-4 py-1.5">
                <FileIcon entry={{ name: activeTab.name, path: activeTab.path, is_dir: false, size: null, modified: null }} />
                <span className="flex-1 truncate text-xs font-medium text-foreground/80">
                  {activeTab.name}
                  {isDirty && <span className="ml-1 text-amber-400">●</span>}
                </span>
                <a
                  href={filesApi.downloadUrl(activeTab.path)}
                  download={activeTab.name}
                  className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-accent/10 hover:text-foreground"
                  title={t.download}
                >
                  <Download className="h-3.5 w-3.5" />
                </a>
                <Button
                  size="sm"
                  variant="default"
                  className="h-7 gap-1.5 text-xs"
                  disabled={!activeTabDirty || saveMutation.isPending}
                  onClick={saveActiveTab}
                  title={`${t.save} (Ctrl+S)`}
                >
                  <Save className="h-3.5 w-3.5" />
                  {t.save}
                </Button>
              </div>

              {fileQuery.isPending ? (
                <div className="flex flex-1 items-center justify-center text-xs text-muted-foreground">
                  {t.loading}
                </div>
              ) : fileQuery.isError ? (
                <div className="flex flex-1 items-center justify-center gap-1.5 text-xs text-destructive">
                  <AlertCircle className="h-4 w-4" />
                  {t.loadFileFailed}
                </div>
              ) : (
                <div className="flex-1 overflow-hidden">
                  <Editor
                    height="100%"
                    language={detectLanguage(activeTab.name)}
                    value={editorContent}
                    theme="vs-dark"
                    onMount={(editor, monaco) => bindMonacoSaveShortcut(editor, monaco, () => saveActiveTabRef.current())}
                    onChange={(val) => {
                      const content = val ?? ''
                      editorContentRef.current = content
                      handleActiveTabContentChange(content)
                    }}
                    options={{
                      minimap: { enabled: false },
                      fontSize: 13,
                      lineNumbers: 'on',
                      wordWrap: 'on',
                      scrollBeyondLastLine: false,
                      automaticLayout: true,
                      tabSize: 4,
                    }}
                  />
                </div>
              )}
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
              {t.selectFile}
            </div>
          )}
        </div>
      </div>

      <Dialog open={isNewFolderDialogOpen} onOpenChange={(open) => {
        if (!open && !mkdirMutation.isPending) {
          setIsNewFolderDialogOpen(false)
          setNewFolderName('')
          return
        }
        setIsNewFolderDialogOpen(open)
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.newFolderTitle}</DialogTitle>
            <DialogDescription>{t.newFolderDescription}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="new-folder-name">{t.folderName}</Label>
            <Input
              id="new-folder-name"
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              placeholder={t.folderName}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={mkdirMutation.isPending}
              onClick={() => {
                setIsNewFolderDialogOpen(false)
                setNewFolderName('')
              }}
            >
              {copy.dashboard.cancel}
            </Button>
            <Button
              type="button"
              disabled={mkdirMutation.isPending || !newFolderName.trim()}
              onClick={handleCreateFolder}
            >
              {mkdirMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t.createFolder}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isRenameDialogOpen} onOpenChange={(open) => {
        if (!open && !renameMutation.isPending) {
          setIsRenameDialogOpen(false)
          setRenameValue('')
          return
        }
        setIsRenameDialogOpen(open)
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.renameTitle}</DialogTitle>
            <DialogDescription>{t.renameDescription}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="rename-file-name">{t.renameField}</Label>
            <Input
              id="rename-file-name"
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              placeholder={t.renameField}
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={renameMutation.isPending}
              onClick={() => {
                setIsRenameDialogOpen(false)
                setRenameValue('')
              }}
            >
              {copy.dashboard.cancel}
            </Button>
            <Button
              type="button"
              disabled={renameMutation.isPending || !renameValue.trim()}
              onClick={handleConfirmRename}
            >
              {renameMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t.rename}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deleteDialogPaths !== null} onOpenChange={(open) => {
        if (!open && !deleteMutation.isPending) {
          setDeleteDialogPaths(null)
          return
        }
      }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.deleteTitle}</DialogTitle>
            <DialogDescription>
              {deleteDialogPaths?.length === 1
                ? t.deleteConfirm(selectedEntries.find((entry) => entry.path === deleteDialogPaths[0])?.name ?? deleteDialogPaths[0].split('/').pop() ?? deleteDialogPaths[0])
                : t.deleteManyConfirm(deleteDialogPaths?.length ?? 0)}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={deleteMutation.isPending}
              onClick={() => setDeleteDialogPaths(null)}
            >
              {copy.dashboard.cancel}
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={handleConfirmDelete}
            >
              {deleteMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {contextMenu && (
        <div
          className="fixed z-50 min-w-48 rounded-md border border-border bg-popover p-1 shadow-xl"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-foreground transition-colors hover:bg-accent/10"
            disabled={selectedPaths.length !== 1}
            onClick={() => {
              handleRename(contextMenu.entry)
              setContextMenu(null)
            }}
          >
            <Pencil className="h-4 w-4" />
            {t.rename}
          </button>
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-destructive transition-colors hover:bg-accent/10"
            onClick={() => {
              handleDelete(contextMenu.entry)
              setContextMenu(null)
            }}
          >
            <Trash2 className="h-4 w-4" />
            {t.delete}
          </button>
          {isZipArchive(contextMenu.entry) && (
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm text-foreground transition-colors hover:bg-accent/10"
              disabled={extractMutation.isPending}
              onClick={() => {
                extractMutation.mutate({ path: contextMenu.entry.path })
                setContextMenu(null)
              }}
            >
              <Archive className="h-4 w-4" />
              {t.extractArchive}
            </button>
          )}
        </div>
      )}
    </>
  )
}
