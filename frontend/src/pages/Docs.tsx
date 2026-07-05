import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { BookOpen, KeyRound, ArrowRight, BookOpenCheck, FileText, ExternalLink } from 'lucide-react'
import { usePublicLegalSettings } from '@/hooks/usePublicLegalSettings'

export function Docs() {
  const { t } = useTranslation()
  const legal = usePublicLegalSettings()
  const imprintUrl = legal.imprint_enabled ? legal.imprint_url : ''

  return (
    <div className="container mx-auto px-4 py-8 max-w-5xl">
      <div className="flex items-center gap-3 mb-2">
        <BookOpen className="w-8 h-8 text-primary" />
        <h1 className="font-headline text-display-sm font-extrabold text-on-surface">
          {t('docsIndex.title')}
        </h1>
      </div>
      <p className="font-body-md text-body-md text-on-surface-variant mb-8">
        {t('docsIndex.subtitle')}
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <DocCard
          to="/docs/blueprints"
          icon={<BookOpenCheck className="w-6 h-6" />}
          title={t('docsIndex.blueprintsTitle')}
          description={t('docsIndex.blueprintsDesc')}
          cta={t('docsIndex.blueprintsLink')}
        />
        <DocCard
          to="/docs/oauth"
          icon={<KeyRound className="w-6 h-6" />}
          title={t('docsIndex.oauthTitle')}
          description={t('docsIndex.oauthDesc')}
          cta={t('docsIndex.oauthLink')}
        />
        <LegalCard imprintUrl={imprintUrl} />
      </div>
    </div>
  )
}

function DocCard({
  to, icon, title, description, cta,
}: {
  to: string
  icon: React.ReactNode
  title: string
  description: string
  cta: string
}) {
  return (
    <Link
      to={to}
      className="msm-card p-6 hover:border-secondary transition-colors group flex flex-col"
    >
      <div className="flex items-center gap-3 mb-3">
        <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center text-secondary">
          {icon}
        </div>
        <h2 className="font-headline text-headline-sm text-primary">{title}</h2>
      </div>
      <p className="font-body-md text-body-md text-on-surface-variant flex-1 mb-4">
        {description}
      </p>
      <span className="inline-flex items-center gap-2 text-secondary group-hover:text-mint-accent font-label-md text-label-md">
        {cta}
        <ArrowRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform" />
      </span>
    </Link>
  )
}

function LegalCard({ imprintUrl }: { imprintUrl: string }) {
  const { t } = useTranslation()

  return (
    <div className="msm-card p-6 md:col-span-2">
      <div className="flex items-center gap-3 mb-3">
        <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center text-secondary">
          <FileText className="w-6 h-6" />
        </div>
        <div>
          <h2 className="font-headline text-headline-sm text-primary">{t('docsIndex.legalTitle')}</h2>
          <p className="font-body-md text-sm text-on-surface-variant">{t('docsIndex.legalDesc')}</p>
        </div>
      </div>
      <div className="flex flex-wrap gap-3 pt-3">
        <Link to="/privacy" className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2">
          <FileText className="w-4 h-4" />
          {t('docsIndex.privacyLink')}
        </Link>
        {imprintUrl && (
          <a
            href={imprintUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="msm-btn-secondary px-4 py-2 inline-flex items-center gap-2"
          >
            <ExternalLink className="w-4 h-4" />
            {t('docsIndex.imprintLink')}
          </a>
        )}
      </div>
    </div>
  )
}
