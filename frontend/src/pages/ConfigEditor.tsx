import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, Save, FileText, Settings } from 'lucide-react'

interface ConfigFile {
  name: string
  path: string
}

interface ConfigSchema {
  key: string
  label: string
  type: 'text' | 'number' | 'boolean'
  default?: any
  description?: string
  required?: boolean
}

export function ConfigEditor() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const [files, setFiles] = useState<ConfigFile[]>([])
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [content, setContent] = useState('')
  const [schema, setSchema] = useState<ConfigSchema[]>([])
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  useEffect(() => {
    if (!id) return
    loadFiles()
    loadSchema()
  }, [id])

  useEffect(() => {
    if (selectedFile) {
      loadFileContent(selectedFile)
    }
  }, [selectedFile])

  const loadFiles = async () => {
    try {
      const res = await fetch(`/api/config/${id}/files`)
      if (!res.ok) throw new Error()
      setFiles(await res.json())
    } catch {
      setMessage({ type: 'error', text: t('config.loadError', 'Konnte Config-Dateien nicht laden') })
    }
  }

  const loadSchema = async () => {
    try {
      const res = await fetch(`/api/config/${id}/schema`)
      if (!res.ok) throw new Error()
      setSchema(await res.json())
    } catch {
      // Schema ist optional
    }
  }

  const loadFileContent = async (fileName: string) => {
    try {
      const res = await fetch(`/api/config/${id}/files/${fileName}`)
      if (!res.ok) throw new Error()
      const data = await res.json()
      setContent(data.content || '')
    } catch {
      setMessage({ type: 'error', text: t('config.fileLoadError', 'Konnte Datei nicht laden') })
    }
  }

  const saveFile = async () => {
    if (!selectedFile) return
    setSaving(true)
    try {
      const res = await fetch(`/api/config/${id}/files/${selectedFile}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      })
      if (!res.ok) throw new Error()
      setMessage({ type: 'success', text: t('config.saved', 'Config gespeichert') })
    } catch {
      setMessage({ type: 'error', text: t('config.saveFailed', 'Speichern fehlgeschlagen') })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate(`/servers/${id}`)}
            className="p-2 rounded-md border border-outline bg-surface-container-highest hover:bg-surface-container text-on-surface transition-colors"
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="font-headline text-headline-sm text-primary">{t('config.title')}</h1>
            <p className="font-body-md text-sm text-on-surface-variant">{t('config.subtitle')}</p>
          </div>
        </div>
        {selectedFile && (
          <button
            onClick={saveFile}
            disabled={saving}
            className="msm-btn-primary flex items-center gap-2 px-4 py-2 disabled:opacity-50"
          >
            <Save className="w-4 h-4" />
            {saving ? t('common.loading') : t('common.save')}
          </button>
        )}
      </div>

      {message && (
        <div className={`mb-4 p-3 rounded-md border text-sm font-body-md ${
          message.type === 'success'
            ? 'bg-status-success/10 border-status-success/30 text-status-success'
            : 'bg-status-error/10 border-status-error/30 text-status-error'
        }`}>
          {message.text}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Sidebar: Dateiliste */}
        <div className="lg:col-span-1 space-y-4">
          <div className="msm-card p-4">
            <h3 className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-3 flex items-center gap-2">
              <FileText className="w-4 h-4" />
              {t('config.configFiles')}
            </h3>
            <div className="space-y-1">
              {files.map((file) => (
                <button
                  key={file.name}
                  onClick={() => setSelectedFile(file.name)}
                  className={`w-full text-left px-3 py-2 rounded-md text-sm font-body-md transition-colors ${
                    selectedFile === file.name
                      ? 'bg-primary/10 border border-mint-accent/50 text-mint-accent'
                      : 'hover:bg-surface-container text-on-surface-variant border border-transparent'
                  }`}
                >
                  {file.name}
                </button>
              ))}
            </div>
          </div>

          {/* Schema-Felder */}
          {schema.length > 0 && (
            <div className="msm-card p-4">
              <h3 className="font-label-md text-label-md text-on-surface-variant uppercase tracking-wider mb-3 flex items-center gap-2">
                <Settings className="w-4 h-4" />
                {t('config.quickConfig')}
              </h3>
              <div className="space-y-3">
                {schema.map((field) => (
                  <div key={field.key}>
                    <label className="font-body-md text-xs text-on-surface-variant block mb-1">
                      {field.label}
                      {field.required && <span className="text-status-error ml-1">*</span>}
                    </label>
                    {field.type === 'boolean' ? (
                      <input
                        type="checkbox"
                        defaultChecked={field.default}
                        className="rounded border-outline bg-surface-container-highest text-secondary focus:ring-secondary"
                      />
                    ) : field.type === 'number' ? (
                      <input
                        type="number"
                        defaultValue={field.default}
                        className="msm-input"
                      />
                    ) : (
                      <input
                        type="text"
                        defaultValue={field.default}
                        className="msm-input"
                      />
                    )}
                    {field.description && (
                      <p className="font-body-md text-xs text-on-surface-variant mt-1">{field.description}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Main: Editor */}
        <div className="lg:col-span-3">
          {selectedFile ? (
            <div className="msm-card p-6">
              <div className="mb-4">
                <h2 className="font-headline text-body-lg text-on-surface">{selectedFile}</h2>
                <p className="font-body-md text-sm text-on-surface-variant">{t('config.fileContent')}</p>
              </div>
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                className="w-full h-96 px-4 py-3 bg-surface-darkest border border-outline rounded-md text-on-surface font-mono text-sm focus:outline-none focus:ring-2 focus:ring-mint-accent resize-none"
                placeholder={t('config.filePlaceholder', 'Config-Inhalt wird hier angezeigt...')}
                spellCheck={false}
              />
            </div>
          ) : (
            <div className="msm-card p-12 text-center">
              <FileText className="w-12 h-12 text-on-surface-variant mx-auto mb-4" />
              <h3 className="font-headline text-body-lg text-on-surface mb-2">{t('config.noFileSelected')}</h3>
              <p className="font-body-md text-sm text-on-surface-variant">{t('config.selectFile')}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}