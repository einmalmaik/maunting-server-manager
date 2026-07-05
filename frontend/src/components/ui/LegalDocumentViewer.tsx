import { ArrowLeft, FileSignature } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Card, CardContent, CardDescription, CardHeader } from './Card'
import { Badge } from './Badge'

export interface LegalSection {
  heading: string
  body: string
  items?: string[]
}

export interface LegalDocumentData {
  title: string
  intro?: string
  callout?: string
  lastUpdated: string
  version: string
  meta: string
  sections: LegalSection[]
}

interface LegalDocumentViewerProps {
  document: LegalDocumentData
  backTo: string
  backLabel: string
  docLabel: string
  summaryLabel: string
  versionLabel: string
  updatedLabel: string
}

export function LegalDocumentViewer({
  document,
  backTo,
  backLabel,
  docLabel,
  summaryLabel,
  versionLabel,
  updatedLabel,
}: LegalDocumentViewerProps) {
  return (
    <main className="min-h-screen bg-background text-on-surface relative overflow-hidden">
      <div className="absolute inset-0 msm-deep-grid opacity-40" />
      <div className="relative z-10 mx-auto max-w-3xl px-4 py-10 md:py-14">
        <Link
          to={backTo}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-on-surface-variant transition-colors hover:text-on-surface"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          {backLabel}
        </Link>

        <Card className="mt-6 shadow-panel">
          <CardHeader className="gap-3">
            <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-on-surface-variant">
              <FileSignature className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{docLabel}</span>
            </div>
            <h1 className="font-headline text-3xl font-bold leading-tight text-primary">
              {document.title}
            </h1>
            <CardDescription>{document.meta}</CardDescription>
            <div className="flex flex-wrap gap-2">
              <Badge variant="info">{versionLabel} v{document.version}</Badge>
              <Badge variant="default">
                {updatedLabel}: <time dateTime={document.lastUpdated}>{document.lastUpdated}</time>
              </Badge>
            </div>
          </CardHeader>

          <CardContent className="space-y-6">
            {document.intro && (
              <p className="text-base leading-relaxed text-on-surface/90">{document.intro}</p>
            )}

            {document.callout && (
              <aside className="rounded-md border border-primary/30 bg-primary/10 px-4 py-3 text-sm leading-relaxed text-on-surface">
                <span className="font-semibold text-primary">{summaryLabel}</span>{' '}
                {document.callout.replace(/^Kurzfassung:\s*|^Summary:\s*/i, '')}
              </aside>
            )}

            {document.sections.map((section) => (
              <section key={section.heading} className="space-y-2">
                <h2 className="text-xl font-semibold leading-tight text-on-surface">
                  {section.heading}
                </h2>
                {section.body.split('\n\n').map((paragraph, index) => (
                  <p
                    key={index}
                    className="whitespace-pre-line text-sm leading-relaxed text-on-surface-variant"
                  >
                    {paragraph}
                  </p>
                ))}
                {section.items && (
                  <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed text-on-surface-variant">
                    {section.items.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                )}
              </section>
            ))}
          </CardContent>
        </Card>
      </div>
    </main>
  )
}
