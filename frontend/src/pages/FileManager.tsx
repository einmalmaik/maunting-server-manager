import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import CodeMirror from '@uiw/react-codemirror'
import { json } from '@codemirror/lang-json'
import { yaml } from '@codemirror/lang-yaml'
import { xml } from '@codemirror/lang-xml'
import { markdown } from '@codemirror/lang-markdown'
import { StreamLanguage } from '@codemirror/language'
import { properties as iniMode } from '@codemirror/legacy-modes/mode/properties'
import { EditorView } from '@codemirror/view'
import {
  ChevronRight,
  Download,
  File as FileIcon,
  Folder,
  FolderPlus,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Search,
  Trash2,
  Upload,
  X,
  ArrowRight,
} from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import {
  detectLanguage,
  formatBytes,
  isWithin,
  joinPath,
  parentPath,
  pathSegments,
  sortEntries,
} from '@/components/server/fileHelpers'
import { uploadFile } from '@/components/server/chunkedUpload'

interface FileEntry {
  name: string
  is_dir: boolean
  size: number
  modified: number
}

interface BrowseResponse {
  path: string
  entries: FileEntry[]
  exists: boolean
}

interface SearchResult {
  path: string
  is_dir: boolean
}

interface SearchResponse {
  query: string
  truncated: boolean
  results: SearchResult[]
}

interface ContextMenuState {
  x: number
  y: number
  entry: FileEntry
}

interface UploadJob {
  id: string
  name: string
  fraction: number
  status: 'running' | 'done' | 'error'
  error?: string
}

interface PromptDialogState {
  title: string
  label: string
  initialValue: string
  confirmLabel: string
  onConfirm: (value: string) => void | Promise<void>
}

interface MoveDialogState {
  entry: FileEntry
  fromPath: string
}

interface FileManagerProps {
  serverId: number
}

export function FileManager({ serverId }: FileManagerProps) {
  const { t } = useTranslation()
  const [currentPath, setCurrentPath] = useState('')
  const [entries, setEntries] = useState<FileEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState('')
  const [originalContent, setOriginalContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [editorBusy, setEditorBusy] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null)
  const [searchTruncated, setSearchTruncated] = useState(false)
  const [uploads, setUploads] = useState<UploadJob[]>([])
  const [promptDialog, setPromptDialog] = useState<PromptDialogState | null>(null)
  const [moveDialog, setMoveDialog] = useState<MoveDialogState | null>(null)
  const [moveTarget, setMoveTarget] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const promptInputRef = useRef<HTMLInputElement>(null)

  // ── Loader ───────────────────────────────────────────────────────────
  const fetchEntries = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api<BrowseResponse>(
        `/files/${serverId}/browse?path=${encodeURIComponent(currentPath)}`,
      )
      setEntries(sortEntries(data.entries || []))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
      setEntries([])
    } finally {
      setLoading(false)
    }
  }, [serverId, currentPath])

  useEffect(() => {
    setSelectedFile(null)
    setFileContent('')
    setOriginalContent('')
    void fetchEntries()
  }, [fetchEntries])

  // Server-side Search (debounced)
  useEffect(() => {
    const q = searchQuery.trim()
    if (!q) {
      setSearchResults(null)
      setSearchTruncated(false)
      return
    }
    const handle = setTimeout(async () => {
      try {
        const data = await api<SearchResponse>(
          `/files/${serverId}/search?q=${encodeURIComponent(q)}`,
        )
        setSearchResults(data.results || [])
        setSearchTruncated(!!data.truncated)
      } catch (err: unknown) {
        toast.error(err instanceof Error ? err.message : String(err))
      }
    }, 300)
    return () => clearTimeout(handle)
  }, [searchQuery, serverId])

  useEffect(() => {
    if (promptDialog) {
      const handle = window.setTimeout(() => promptInputRef.current?.focus(), 50)
      return () => window.clearTimeout(handle)
    }
  }, [promptDialog])

  // Close context menu on outside click / scroll
  useEffect(() => {
    if (!contextMenu) return
    const close = () => setContextMenu(null)
    window.addEventListener('click', close)
    window.addEventListener('scroll', close, true)
    return () => {
      window.removeEventListener('click', close)
      window.removeEventListener('scroll', close, true)
    }
  }, [contextMenu])

  // ── Navigation ───────────────────────────────────────────────────────
  const openDir = (name: string) => {
    setCurrentPath(joinPath(currentPath, name))
    setDrawerOpen(false)
  }

  const goUp = () => {
    setCurrentPath(parentPath(currentPath))
  }

  const navigateTo = (path: string) => {
    setCurrentPath(path)
    setDrawerOpen(false)
  }

  // ── File Open / Save ─────────────────────────────────────────────────
  const openFile = async (relPath: string) => {
    setEditorBusy(true)
    try {
      const data = await api<{ content: string; truncated: boolean }>(
        `/files/${serverId}/read?path=${encodeURIComponent(relPath)}`,
      )
      setSelectedFile(relPath)
      setFileContent(data.content || '')
      setOriginalContent(data.content || '')
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setEditorBusy(false)
    }
  }

  const saveFile = async () => {
    if (!selectedFile) return
    setSaving(true)
    try {
      await api(`/files/${serverId}/write?path=${encodeURIComponent(selectedFile)}`, {
        method: 'PUT',
        body: JSON.stringify({ content: fileContent }),
      })
      setOriginalContent(fileContent)
      toast.success(t('files.saved'))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  // ── Upload ───────────────────────────────────────────────────────────
  const enqueueUpload = (files: FileList | File[] | null) => {
    if (!files || (Array.isArray(files) ? files.length === 0 : files.length === 0)) return
    const list = Array.isArray(files) ? files : Array.from(files)
    list.forEach((file) => {
      const id = `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      setUploads((prev) => [...prev, { id, name: file.name, fraction: 0, status: 'running' }])
      uploadFile({
        serverId,
        destinationPath: currentPath,
        file,
        onProgress: (frac) =>
          setUploads((prev) => prev.map((u) => (u.id === id ? { ...u, fraction: frac } : u))),
      })
        .then(() => {
          setUploads((prev) => prev.map((u) => (u.id === id ? { ...u, fraction: 1, status: 'done' } : u)))
          void fetchEntries()
          window.setTimeout(() => {
            setUploads((prev) => prev.filter((u) => u.id !== id))
          }, 1500)
        })
        .catch((err: unknown) => {
          const msg = err instanceof Error ? err.message : String(err)
          setUploads((prev) =>
            prev.map((u) => (u.id === id ? { ...u, status: 'error', error: msg } : u)),
          )
          toast.error(`${file.name}: ${msg}`)
        })
    })
  }

  const onTreeDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    enqueueUpload(e.dataTransfer.files)
  }

  // ── Mutations ────────────────────────────────────────────────────────
  const handleDelete = async (entry: FileEntry) => {
    const relPath = joinPath(currentPath, entry.name)
    const message = entry.is_dir ? t('files.confirmDeleteDir') : t('files.confirmDeleteFile')
    if (!(await confirm({ message, danger: true, confirmText: t('common.delete') }))) return
    try {
      await api(`/files/${serverId}/delete?path=${encodeURIComponent(relPath)}`, {
        method: 'DELETE',
      })
      toast.success(t('files.deleted'))
      if (selectedFile === relPath) {
        setSelectedFile(null)
        setFileContent('')
        setOriginalContent('')
      }
      void fetchEntries()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  const handleRename = (entry: FileEntry) => {
    setPromptDialog({
      title: t('files.renameTitle'),
      label: t('files.newName'),
      initialValue: entry.name,
      confirmLabel: t('common.save'),
      onConfirm: async (value) => {
        const trimmed = value.trim()
        if (!trimmed || trimmed === entry.name) {
          setPromptDialog(null)
          return
        }
        const oldRel = joinPath(currentPath, entry.name)
        try {
          await api(`/files/${serverId}/rename?path=${encodeURIComponent(oldRel)}`, {
            method: 'POST',
            body: JSON.stringify({ new_name: trimmed }),
          })
          toast.success(t('files.renamed'))
          if (selectedFile === oldRel) {
            setSelectedFile(joinPath(currentPath, trimmed))
          }
          setPromptDialog(null)
          void fetchEntries()
        } catch (err: unknown) {
          toast.error(err instanceof Error ? err.message : String(err))
        }
      },
    })
  }

  const handleMkdir = () => {
    setPromptDialog({
      title: t('files.newFolder'),
      label: t('files.folderName'),
      initialValue: '',
      confirmLabel: t('common.create'),
      onConfirm: async (value) => {
        const trimmed = value.trim()
        if (!trimmed) {
          setPromptDialog(null)
          return
        }
        try {
          await api(`/files/${serverId}/mkdir?path=${encodeURIComponent(currentPath)}`, {
            method: 'POST',
            body: JSON.stringify({ name: trimmed }),
          })
          toast.success(t('files.folderCreated'))
          setPromptDialog(null)
          void fetchEntries()
        } catch (err: unknown) {
          toast.error(err instanceof Error ? err.message : String(err))
        }
      },
    })
  }

  const handleCreateFile = () => {
    setPromptDialog({
      title: t('files.newFile'),
      label: t('files.fileName'),
      initialValue: '',
      confirmLabel: t('common.create'),
      onConfirm: async (value) => {
        const trimmed = value.trim()
        if (!trimmed) {
          setPromptDialog(null)
          return
        }
        const target = joinPath(currentPath, trimmed)
        try {
          await api(`/files/${serverId}/write?path=${encodeURIComponent(target)}`, {
            method: 'PUT',
            body: JSON.stringify({ content: '' }),
          })
          setPromptDialog(null)
          void fetchEntries()
          void openFile(target)
        } catch (err: unknown) {
          toast.error(err instanceof Error ? err.message : String(err))
        }
      },
    })
  }

  const handleMove = (entry: FileEntry) => {
    setMoveTarget(currentPath)
    setMoveDialog({ entry, fromPath: joinPath(currentPath, entry.name) })
  }

  const submitMove = async () => {
    if (!moveDialog) return
    const fromPath = moveDialog.fromPath
    const toDir = moveTarget.replace(/^\/+|\/+$/g, '')
    if (moveDialog.entry.is_dir && isWithin(fromPath, joinPath(toDir, moveDialog.entry.name))) {
      toast.error(t('files.moveSelfError'))
      return
    }
    try {
      await api(`/files/${serverId}/move`, {
        method: 'POST',
        body: JSON.stringify({ from_path: fromPath, to_dir: toDir }),
      })
      toast.success(t('files.moved'))
      if (selectedFile === fromPath) {
        setSelectedFile(joinPath(toDir, moveDialog.entry.name))
      }
      setMoveDialog(null)
      void fetchEntries()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  const downloadEntry = (entry: FileEntry) => {
    const relPath = joinPath(currentPath, entry.name)
    window.open(`/api/files/${serverId}/download?path=${encodeURIComponent(relPath)}`, '_blank')
  }

  // ── Rendering helpers ────────────────────────────────────────────────
  const breadcrumbs = pathSegments(currentPath)
  const hasUnsavedChanges = selectedFile && fileContent !== originalContent

  const editorLanguage = useMemo(() => {
    if (!selectedFile) return []
    const lang = detectLanguage(selectedFile)
    switch (lang) {
      case 'json':
        return [json()]
      case 'yaml':
        return [yaml()]
      case 'xml':
        return [xml()]
      case 'markdown':
        return [markdown()]
      case 'ini':
      case 'properties':
        return [StreamLanguage.define(iniMode)]
      default:
        return []
    }
  }, [selectedFile])

  // Drag & Drop INNERHALB der Tree (Eintrag in einen Ordner verschieben).
  const handleEntryDragStart = (e: React.DragEvent, entry: FileEntry) => {
    e.dataTransfer.setData('application/x-msm-path', joinPath(currentPath, entry.name))
    e.dataTransfer.effectAllowed = 'move'
  }

  const handleEntryDropOnFolder = async (e: React.DragEvent, folder: FileEntry) => {
    e.preventDefault()
    e.stopPropagation()
    const fromPath = e.dataTransfer.getData('application/x-msm-path')
    if (!fromPath) return
    const fromBase = fromPath.split('/').pop() || fromPath
    const toDir = joinPath(currentPath, folder.name)
    if (isWithin(fromPath, joinPath(toDir, fromBase))) {
      toast.error(t('files.moveSelfError'))
      return
    }
    try {
      await api(`/files/${serverId}/move`, {
        method: 'POST',
        body: JSON.stringify({ from_path: fromPath, to_dir: toDir }),
      })
      toast.success(t('files.moved'))
      void fetchEntries()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  const renderEntryIcon = (entry: FileEntry) =>
    entry.is_dir ? (
      <Folder className="w-4 h-4 text-secondary" />
    ) : (
      <FileIcon className="w-4 h-4 text-on-surface-variant" />
    )

  const ListItem = ({ entry }: { entry: FileEntry }) => (
    <div
      role="button"
      tabIndex={0}
      draggable
      onDragStart={(e) => handleEntryDragStart(e, entry)}
      onDragOver={(e) => {
        if (entry.is_dir) {
          e.preventDefault()
          e.dataTransfer.dropEffect = 'move'
        }
      }}
      onDrop={(e) => {
        if (entry.is_dir) {
          void handleEntryDropOnFolder(e, entry)
        }
      }}
      onContextMenu={(e) => {
        e.preventDefault()
        setContextMenu({ x: e.clientX, y: e.clientY, entry })
      }}
      onClick={() => (entry.is_dir ? openDir(entry.name) : void openFile(joinPath(currentPath, entry.name)))}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          entry.is_dir ? openDir(entry.name) : void openFile(joinPath(currentPath, entry.name))
        }
      }}
      className={`w-full text-left px-3 py-2 flex items-center gap-2 text-sm font-body-md hover:bg-surface-container-highest transition-colors cursor-pointer ${
        selectedFile === joinPath(currentPath, entry.name) ? 'bg-surface-container-highest text-on-surface' : 'text-on-surface'
      }`}
    >
      {renderEntryIcon(entry)}
      <span className="flex-1 truncate">{entry.name}</span>
      {!entry.is_dir && (
        <span className="text-xs text-on-surface-variant font-mono shrink-0">{formatBytes(entry.size)}</span>
      )}
    </div>
  )

  const TreePanel = (
    <div
      className={`msm-card overflow-hidden flex flex-col ${dragOver ? 'border-secondary' : ''}`}
      onDragOver={(e) => {
        e.preventDefault()
        setDragOver(true)
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onTreeDrop}
    >
      {/* Search */}
      <div className="p-3 border-b border-outline">
        <div className="relative">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant" />
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t('files.searchPlaceholder')}
            className="msm-input pl-9 text-sm"
            aria-label={t('files.searchPlaceholder')}
          />
        </div>
      </div>

      {/* Breadcrumb */}
      {!searchResults && (
        <div className="p-3 border-b border-outline flex items-center gap-1 text-sm font-body-md overflow-x-auto">
          <button
            onClick={() => navigateTo('')}
            className="text-secondary hover:underline whitespace-nowrap"
          >
            /
          </button>
          {breadcrumbs.map((part, i) => (
            <span key={`${part}-${i}`} className="flex items-center gap-1">
              <ChevronRight className="w-3 h-3 text-on-surface-variant" />
              <button
                onClick={() => navigateTo(breadcrumbs.slice(0, i + 1).join('/'))}
                className="text-secondary hover:underline whitespace-nowrap"
              >
                {part}
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Toolbar */}
      <div className="px-3 py-2 border-b border-outline flex items-center gap-1.5 flex-wrap">
        <button onClick={() => fileInputRef.current?.click()} className="msm-btn-secondary px-2.5 py-1.5 text-xs inline-flex items-center gap-1.5">
          <Upload className="w-3.5 h-3.5" />
          {t('files.upload')}
        </button>
        <button onClick={handleMkdir} className="msm-btn-secondary px-2.5 py-1.5 text-xs inline-flex items-center gap-1.5">
          <FolderPlus className="w-3.5 h-3.5" />
          {t('files.newFolder')}
        </button>
        <button onClick={handleCreateFile} className="msm-btn-secondary px-2.5 py-1.5 text-xs inline-flex items-center gap-1.5">
          <Plus className="w-3.5 h-3.5" />
          {t('files.newFile')}
        </button>
        <button onClick={() => fetchEntries()} className="msm-btn-secondary px-2.5 py-1.5 text-xs inline-flex items-center gap-1.5 ml-auto" title={t('common.refresh')}>
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            enqueueUpload(e.target.files)
            // reset, damit derselbe File-Name nochmal getriggert werden kann.
            e.target.value = ''
          }}
        />
      </div>

      {/* List body */}
      <div className="flex-1 overflow-y-auto max-h-[60vh] md:max-h-[calc(100vh-380px)]">
        {searchResults ? (
          <>
            {searchTruncated && (
              <p className="p-3 text-xs text-status-warning border-b border-outline">{t('files.searchTruncated')}</p>
            )}
            {searchResults.length === 0 ? (
              <p className="p-6 text-center text-sm text-on-surface-variant">{t('files.searchEmpty')}</p>
            ) : (
              <ul>
                {searchResults.map((r) => (
                  <li key={r.path}>
                    <button
                      onClick={() => {
                        const parent = parentPath(r.path)
                        if (r.is_dir) {
                          navigateTo(r.path)
                        } else {
                          setCurrentPath(parent)
                          void openFile(r.path)
                        }
                        setSearchQuery('')
                      }}
                      className="w-full text-left px-3 py-2 flex items-center gap-2 text-sm font-body-md hover:bg-surface-container-highest transition-colors text-on-surface"
                    >
                      {r.is_dir ? <Folder className="w-4 h-4 text-secondary" /> : <FileIcon className="w-4 h-4 text-on-surface-variant" />}
                      <span className="truncate">{r.path}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : loading ? (
          <div className="flex items-center justify-center py-12">
            <span className="w-6 h-6 border-2 border-secondary border-t-transparent rounded-full animate-spin" />
          </div>
        ) : (
          <>
            {currentPath && (
              <button
                onClick={goUp}
                className="w-full text-left px-3 py-2 flex items-center gap-2 text-sm font-body-md hover:bg-surface-container-highest transition-colors text-on-surface-variant"
              >
                <Folder className="w-4 h-4" />
                ..
              </button>
            )}
            {entries.length === 0 ? (
              <p className="p-6 text-center text-sm text-on-surface-variant">{t('files.empty')}</p>
            ) : (
              entries.map((entry) => <ListItem key={entry.name} entry={entry} />)
            )}
          </>
        )}
      </div>

      {/* Upload progress */}
      {uploads.length > 0 && (
        <div className="border-t border-outline divide-y divide-outline">
          {uploads.map((u) => (
            <div key={u.id} className="px-3 py-2">
              <div className="flex items-center justify-between text-xs font-body-md">
                <span className="truncate text-on-surface">{u.name}</span>
                <span className={u.status === 'error' ? 'text-status-error' : 'text-on-surface-variant'}>
                  {u.status === 'error' ? t('common.error') : `${Math.round(u.fraction * 100)}%`}
                </span>
              </div>
              <div className="mt-1 h-1.5 rounded-full bg-surface-container-highest overflow-hidden">
                <div
                  className={`h-full ${u.status === 'error' ? 'bg-status-error' : 'bg-secondary'} transition-all`}
                  style={{ width: `${Math.round(u.fraction * 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )

  return (
    <div className="space-y-3">
      <div className="md:hidden flex items-center justify-between">
        <button
          onClick={() => setDrawerOpen((v) => !v)}
          className="msm-btn-secondary px-3 py-2 text-sm inline-flex items-center gap-2"
        >
          <Folder className="w-4 h-4" />
          {drawerOpen ? t('files.hideTree') : t('files.showTree')}
        </button>
        {selectedFile && (
          <p className="text-xs text-on-surface-variant truncate ml-3">{selectedFile}</p>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <div className={`lg:col-span-4 xl:col-span-3 ${drawerOpen ? '' : 'hidden md:block'}`}>{TreePanel}</div>

        {/* Editor */}
        <div className="lg:col-span-8 xl:col-span-9 msm-card overflow-hidden flex flex-col">
          <div className="px-4 py-2 border-b border-outline flex items-center justify-between gap-3 min-h-[44px]">
            <p className="font-mono text-xs text-on-surface-variant truncate">
              {selectedFile || t('files.selectFile')}
            </p>
            {selectedFile && (
              <button
                onClick={saveFile}
                disabled={saving || !hasUnsavedChanges}
                className="msm-btn-primary px-3 py-1.5 text-xs inline-flex items-center gap-1.5 disabled:opacity-50"
              >
                <Save className="w-3.5 h-3.5" />
                {saving ? t('common.loading') : t('common.save')}
              </button>
            )}
          </div>
          {editorBusy ? (
            <div className="flex items-center justify-center py-20">
              <span className="w-6 h-6 border-2 border-secondary border-t-transparent rounded-full animate-spin" />
            </div>
          ) : selectedFile ? (
            <CodeMirror
              value={fileContent}
              onChange={(value) => setFileContent(value)}
              theme="dark"
              extensions={[
                ...editorLanguage,
                EditorView.lineWrapping,
              ]}
              basicSetup={{
                lineNumbers: true,
                highlightActiveLine: true,
                foldGutter: true,
                tabSize: 2,
              }}
              height="calc(100vh - 360px)"
              minHeight="320px"
            />
          ) : (
            <div className="flex items-center justify-center py-20 text-on-surface-variant font-body-md text-sm">
              {t('files.selectFile')}
            </div>
          )}
        </div>
      </div>

      {/* Context menu */}
      {contextMenu && (
        <div
          className="fixed z-50 msm-card py-1 min-w-[180px] shadow-lg"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          {!contextMenu.entry.is_dir && (
            <button
              onClick={() => {
                downloadEntry(contextMenu.entry)
                setContextMenu(null)
              }}
              className="w-full text-left px-3 py-2 text-sm hover:bg-surface-container-highest inline-flex items-center gap-2"
            >
              <Download className="w-4 h-4" />
              {t('files.download')}
            </button>
          )}
          <button
            onClick={() => {
              handleRename(contextMenu.entry)
              setContextMenu(null)
            }}
            className="w-full text-left px-3 py-2 text-sm hover:bg-surface-container-highest inline-flex items-center gap-2"
          >
            <Pencil className="w-4 h-4" />
            {t('files.rename')}
          </button>
          <button
            onClick={() => {
              handleMove(contextMenu.entry)
              setContextMenu(null)
            }}
            className="w-full text-left px-3 py-2 text-sm hover:bg-surface-container-highest inline-flex items-center gap-2"
          >
            <ArrowRight className="w-4 h-4" />
            {t('files.move')}
          </button>
          <button
            onClick={() => {
              void handleDelete(contextMenu.entry)
              setContextMenu(null)
            }}
            className="w-full text-left px-3 py-2 text-sm hover:bg-surface-container-highest inline-flex items-center gap-2 text-status-error"
          >
            <Trash2 className="w-4 h-4" />
            {t('common.delete')}
          </button>
        </div>
      )}

      {/* Prompt dialog */}
      {promptDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div className="msm-card w-full max-w-md p-5 space-y-4">
            <h2 className="font-headline text-body-lg text-primary">{promptDialog.title}</h2>
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider text-xs">
                {promptDialog.label}
              </label>
              <input
                ref={promptInputRef}
                type="text"
                defaultValue={promptDialog.initialValue}
                className="msm-input"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    void promptDialog.onConfirm((e.target as HTMLInputElement).value)
                  }
                  if (e.key === 'Escape') setPromptDialog(null)
                }}
              />
            </div>
            <div className="flex gap-2 justify-end">
              <button className="msm-btn-secondary px-3 py-2 text-sm" onClick={() => setPromptDialog(null)}>
                {t('common.cancel')}
              </button>
              <button
                className="msm-btn-primary px-3 py-2 text-sm"
                onClick={() => void promptDialog.onConfirm(promptInputRef.current?.value || '')}
              >
                {promptDialog.confirmLabel}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Move dialog */}
      {moveDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div className="msm-card w-full max-w-md p-5 space-y-4">
            <h2 className="font-headline text-body-lg text-primary">{t('files.move')}</h2>
            <p className="text-sm text-on-surface-variant">
              {t('files.moveHint', { name: moveDialog.entry.name })}
            </p>
            <div>
              <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider text-xs">
                {t('files.targetFolder')}
              </label>
              <input
                type="text"
                value={moveTarget}
                onChange={(e) => setMoveTarget(e.target.value)}
                className="msm-input"
                placeholder="mods/configs"
              />
              <p className="text-xs text-on-surface-variant mt-1">{t('files.moveTargetHint')}</p>
            </div>
            <div className="flex gap-2 justify-end">
              <button className="msm-btn-secondary px-3 py-2 text-sm" onClick={() => setMoveDialog(null)}>
                {t('common.cancel')}
              </button>
              <button className="msm-btn-primary px-3 py-2 text-sm" onClick={() => void submitMove()}>
                {t('common.save')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Hidden close icon to silence unused import in some builds */}
      <X className="hidden" />
    </div>
  )
}
