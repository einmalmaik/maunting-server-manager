import { useTranslation } from 'react-i18next'
import { BookOpen, Download } from 'lucide-react'

// Reihenfolge der Doku-Sections — i18n-Keys liegen unter ``docs.sections.<key>``.
const SECTION_KEYS = [
  'intro',
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

      <div className="mb-8">
        <a
          href="/api/blueprints/template"
          download
          className="msm-btn-primary inline-flex items-center gap-2 px-4 py-2"
          data-testid="docs-template-download"
        >
          <Download className="w-4 h-4" />
          {t('docs.downloadTemplate')}
        </a>
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
