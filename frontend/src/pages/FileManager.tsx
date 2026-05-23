import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import {
  ChevronLeft,
  Folder,
  File,
  Upload,
  Download,
  Trash2,
  FolderPlus,
  Save,
  Archive,
  ChevronRight,
} from 'lucide-react'

interface FileEntry {
  name: string
  is_dir: boolean
  size: number
  modified: number
}

export function FileManager() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const serverId = parseInt(id || '0')

  const [currentPath, setCurrentPath] = useState('')
  const [entries, setEntries] = useState<FileEntry[]>([])
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState('')
  const [originalContent, setOriginalContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)
  const [showMkdir, setShowMkdir] = useState(false)
  const [newDirName, setNewDirName] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const fetchEntries = useCallback(async () => {
    try {
      const data = await api<{ path: string; entries: FileEntry[]; exists: boolean }>(
        `/files/${serverId}/browse?path=${encodeURIComponent(currentPath)}`
      )
      setEntries(data.entries || [])
    } catch {
      setEntries([])
    } finally {
      setLoading(false)
    }
  }, [serverId, currentPath])

  useEffect(() => {
    setSelectedFile(null)
    setFileContent('')
    setOriginalContent('')
    setLoading(true)
    fetchEntries()
  }, [fetchEntries])

  const openDir = (name: string) => {
    setCurrentPath(currentPath ? `${currentPath}/${name}` : name)
  }

  const goUp = () => {
    const parts = currentPath.split('/')
    parts.pop()
    setCurrentPath(parts.join('/'))
  }

  const openFile = async (name: string) => {
    const filePath = currentPath ? `${currentPath}/${name}` : name
    try {
      const data = await api<{ content: string }>(`/files/${serverId}/read?path=${encodeURIComponent(filePath)}`)
      setSelectedFile(filePath)
      setFileContent(data.content)
      setOriginalContent(data.content)
    } catch (err: any) {
      toast.error(err.message)
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
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    for (const file of Array.from(files)) {
      const formData = new FormData()
      formData.append('file', file)
      try {
        await fetch(`/api/files/${serverId}/upload?path=${encodeURIComponent(currentPath)}`, {
          method: 'POST',
          body: formData,
          credentials: 'include',
        })
        toast.success(`${file.name} ${t('files.uploaded')}`)
      } catch (err: any) {
        toast.error(`${file.name}: ${err.message}`)
      }
    }
    fetchEntries()
  }

  const handleDelete = async (name: string, isDir: boolean) => {
    const filePath = currentPath ? `${currentPath}/${name}` : name
    if (!confirm(isDir ? t('files.confirmDeleteDir') : t('files.confirmDeleteFile'))) return
    try {
      await api(`/files/${serverId}/delete?path=${encodeURIComponent(filePath)}`, { method: 'DELETE' })
      toast.success(t('files.deleted'))
      if (selectedFile === filePath) {
        setSelectedFile(null)
        setFileContent('')
      }
      fetchEntries()
    } catch (err: any) {
      toast.error(err.message)
    }
  }

  const handleMkdir = async () => {
    if (!newDirName.trim()) return
    try {
      await api(`/files/${serverId}/mkdir?path=${encodeURIComponent(currentPath)}`, {
        method: 'POST',
        body: JSON.stringify({ name: newDirName.trim() }),
      })
      setNewDirName('')
      setShowMkdir(false)
      fetchEntries()
    } catch (err: any) {
      toast.error(err.message)
    }
  }

  const handleExtract = async (name: string) => {
    const filePath = currentPath ? `${currentPath}/${name}` : name
    try {
      await api(`/files/${serverId}/extract?path=${encodeURIComponent(filePath)}`, { method: 'POST' })
      toast.success(t('files.extracted'))
      fetchEntries()
    } catch (err: any) {
      toast.error(err.message)
    }
  }

  const downloadFile = (name: string) => {
    const filePath = currentPath ? `${currentPath}/${name}` : name
    window.open(`/api/files/${serverId}/download?path=${encodeURIComponent(filePath)}`, '_blank')
  }

  const formatSize = (bytes: number) => {
    if (bytes === 0) return '-'
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  const breadcrumbs = currentPath ? currentPath.split('/') : []
  const hasUnsavedChanges = selectedFile && fileContent !== originalContent

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate(`/servers/${serverId}`)}
            className="p-2 rounded-md border border-outline bg-surface-container-highest hover:bg-surface-container text-on-surface transition-colors"
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="font-headline text-headline-sm text-primary">{t('files.title')}</h1>
            <p className="font-body-md text-sm text-on-surface-variant">{t('files.subtitle')}</p>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowMkdir(true)}
            className="msm-btn-secondary flex items-center gap-2 px-3 py-2 text-sm"
          >
            <FolderPlus className="w-4 h-4" />
            {t('files.newFolder')}
          </button>
          <button
            onClick={() => fileInputRef.current?.click()}
            className="msm-btn-primary flex items-center gap-2 px-3 py-2 text-sm"
          >
            <Upload className="w-4 h-4" />
            {t('files.upload')}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => handleUpload(e.target.files)}
          />
        </div>
      </div>

      {/* Mkdir dialog */}
      {showMkdir && (
        <div className="msm-card p-4 flex gap-3 items-end">
          <div className="flex-1">
            <label className="block font-label-md text-label-md text-on-surface-variant mb-1 uppercase tracking-wider">
              {t('files.folderName')}
            </label>
            <input
              type="text"
              value={newDirName}
              onChange={(e) => setNewDirName(e.target.value)}
              className="msm-input"
              placeholder="new-folder"
              onKeyDown={(e) => e.key === 'Enter' && handleMkdir()}
            />
          </div>
          <button onClick={handleMkdir} className="msm-btn-primary px-4 py-2">{t('common.save')}</button>
          <button onClick={() => { setShowMkdir(false); setNewDirName('') }} className="msm-btn-secondary px-4 py-2">{t('common.cancel')}</button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* File tree */}
        <div
          className={`lg:col-span-1 msm-card overflow-hidden ${dragOver ? 'border-secondary' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); handleUpload(e.dataTransfer.files) }}
        >
          {/* Breadcrumb */}
          <div className="p-3 border-b border-outline flex items-center gap-1 text-sm font-body-md overflow-x-auto">
            <button
              onClick={() => setCurrentPath('')}
              className="text-secondary hover:underline whitespace-nowrap"
            >
              /
            </button>
            {breadcrumbs.map((part, i) => (
              <span key={i} className="flex items-center gap-1">
                <ChevronRight className="w-3 h-3 text-on-surface-variant" />
                <button
                  onClick={() => setCurrentPath(breadcrumbs.slice(0, i + 1).join('/'))}
                  className="text-secondary hover:underline whitespace-nowrap"
                >
                  {part}
                </button>
              </span>
            ))}
          </div>

          {/* Entries */}
          <div className="max-h-[600px] overflow-y-auto">
            {currentPath && (
              <button
                onClick={goUp}
                className="w-full text-left px-3 py-2 flex items-center gap-2 text-sm font-body-md hover:bg-surface-container-highest transition-colors text-on-surface-variant"
              >
                <Folder className="w-4 h-4" />
                ..
              </button>
            )}
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <span className="w-5 h-5 border-2 border-secondary border-t-transparent rounded-full animate-spin" />
              </div>
            ) : entries.length === 0 ? (
              <p className="text-center text-sm text-on-surface-variant py-8">{t('files.empty')}</p>
            ) : (
              entries.map((entry) => (
                <div
                  key={entry.name}
                  className={`w-full text-left px-3 py-2 flex items-center gap-2 text-sm font-body-md hover:bg-surface-container-highest transition-colors group ${
                    selectedFile === (currentPath ? `${currentPath}/${entry.name}` : entry.name)
                      ? 'bg-primary/10 border-l-2 border-mint-accent'
                      : ''
                  }`}
                >
                  <button
                    className="flex items-center gap-2 flex-1 min-w-0"
                    onClick={() => entry.is_dir ? openDir(entry.name) : openFile(entry.name)}
                  >
                    {entry.is_dir ? (
                      <Folder className="w-4 h-4 text-secondary shrink-0" />
                    ) : (
                      <File className="w-4 h-4 text-on-surface-variant shrink-0" />
                    )}
                    <span className="truncate text-on-surface">{entry.name}</span>
                    {!entry.is_dir && (
                      <span className="text-xs text-on-surface-variant ml-auto shrink-0">{formatSize(entry.size)}</span>
                    )}
                  </button>
                  <div className="hidden group-hover:flex items-center gap-1 shrink-0">
                    {!entry.is_dir && (
                      <button onClick={() => downloadFile(entry.name)} className="p-1 hover:text-secondary" title="Download">
                        <Download className="w-3 h-3" />
                      </button>
                    )}
                    {!entry.is_dir && entry.name.toLowerCase().endsWith('.zip') && (
                      <button onClick={() => handleExtract(entry.name)} className="p-1 hover:text-secondary" title="Entpacken">
                        <Archive className="w-3 h-3" />
                      </button>
                    )}
                    <button onClick={() => handleDelete(entry.name, entry.is_dir)} className="p-1 hover:text-status-error" title="Löschen">
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Drag & Drop hint */}
          {dragOver && (
            <div className="p-4 text-center text-sm text-secondary border-t border-outline">
              {t('files.dropHere')}
            </div>
          )}
        </div>

        {/* Editor */}
        <div className="lg:col-span-2 msm-card overflow-hidden flex flex-col">
          {selectedFile ? (
            <>
              <div className="p-3 border-b border-outline flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  <File className="w-4 h-4 text-on-surface-variant shrink-0" />
                  <span className="font-mono text-sm text-on-surface truncate">{selectedFile}</span>
                  {hasUnsavedChanges && (
                    <span className="text-xs text-status-warning">●</span>
                  )}
                </div>
                <button
                  onClick={saveFile}
                  disabled={saving || !hasUnsavedChanges}
                  className="msm-btn-primary flex items-center gap-2 px-3 py-1.5 text-sm disabled:opacity-50"
                >
                  <Save className="w-3 h-3" />
                  {saving ? t('common.loading') : t('common.save')}
                </button>
              </div>
              <textarea
                value={fileContent}
                onChange={(e) => setFileContent(e.target.value)}
                className="flex-1 min-h-[500px] bg-surface-darkest p-4 font-mono text-xs text-on-surface-variant resize-none focus:outline-none"
                spellCheck={false}
              />
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center min-h-[500px]">
              <p className="text-sm text-on-surface-variant">{t('files.selectFile')}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
