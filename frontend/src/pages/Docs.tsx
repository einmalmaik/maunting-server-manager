import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BookOpen, Download, Upload } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useHasPermission } from '@/hooks/useHasPermission'

// Reihenfolge der Doku-Sections — i18n-Keys liegen unter ``docs.sections.<key>``.
const SECTION_KEYS = [
  'intro',
  'workflow',
  'addServer',
  'addBlueprintSteam',
  'addBlueprintCustom',
  'location',
  'schema',
  'runtime',
  'ports',
  'source',
  'httpSecurity',
  'mods',
  'import',
] as const

export function Docs() {
  const { t } = useTranslation()
  const canImport = useHasPermission('panel.settings.write')
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [uploading, setUploading] = useState(false)

  const handleFileSelected = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    // Input nach Auswahl wieder leeren, damit dieselbe Datei erneut hochgeladen
    // werden kann (z. B. nach Korrektur).
    event.target.value = ''
    if (!file) return

    setUploading(true)
    try {
      const text = await file.text()
      let body: unknown
      try {
        body = JSON.parse(text)
      } catch {
        toast.error(t('docs.uploadInvalidJson'))
        return
      }
      const res = await api<{ id: string }>('/blueprints/import', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      toast.success(t('docs.uploadSuccess', { id: res.id }))
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : t('docs.uploadFailed')
      toast.error(message)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="container mx-auto px-4 py-8 max-w-4xl">
      <div className="flex items-center gap-3 mb-2">
        <BookOpen className="w-8 h-8 text-primary" />
        <h1 className="font-headline text-display-sm font-extrabold text-on-surface">
          {t('docs.pageTitle')}
        </h1>
      </div>
      <p className="font-body-md text-body-md text-on-surface-variant mb-6">
        {t('docs.pageSubtitle')}
      </p>

      <div className="mb-8 flex flex-wrap items-center gap-3">
        <a
          href="/api/blueprints/template"
          download
          className="msm-btn-primary inline-flex items-center gap-2 px-4 py-2"
          data-testid="docs-template-download"
        >
          <Download className="w-4 h-4" />
          {t('docs.downloadTemplate')}
        </a>

        {canImport && (
          <>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="msm-btn-secondary inline-flex items-center gap-2 px-4 py-2 disabled:opacity-50"
              data-testid="docs-blueprint-upload"
            >
              {uploading ? (
                <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <Upload className="w-4 h-4" />
              )}
              {t('docs.uploadBlueprint')}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={handleFileSelected}
              data-testid="docs-blueprint-upload-input"
            />
            <span className="font-body-md text-xs text-on-surface-variant basis-full">
              {t('docs.uploadHint')}
            </span>
          </>
        )}
      </div>

      <div className="space-y-6">
        {SECTION_KEYS.map((key) => (
          <section
            key={key}
            className="msm-card p-6"
            data-testid={`docs-section-${key}`}
          >
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-2">
              {t(`docs.sections.${key}.title`)}
            </h2>
            <p className="font-body-md text-body-md text-on-surface-variant whitespace-pre-line">
              {t(`docs.sections.${key}.body`)}
            </p>
          </section>
        ))}
      </div>
    </div>
  )
}
