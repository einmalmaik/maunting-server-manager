import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { BookOpen, KeyRound, ArrowRight, BookOpenCheck, FileText, ExternalLink, Network } from 'lucide-react'
import { usePublicLegalSettings } from '@/hooks/usePublicLegalSettings'
import { PageHeader } from '@/Singra/UI/PageHeader'

export function Docs() {
  const { t } = useTranslation()
  const legal = usePublicLegalSettings()
  const imprintUrl = legal.imprint_enabled ? legal.imprint_url : ''

  return (
    <div className="msm-page mx-auto max-w-5xl">
      <PageHeader
        eyebrow={t('pageContext.help', 'Help & guidance')}
        title={t('docsIndex.title')}
        description={t('docsIndex.subtitle')}
        status={<BookOpen className="h-6 w-6 text-primary" aria-hidden="true" />}
      />

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
        <DocCard
          to="/docs/self-hosting"
          icon={<Network className="w-6 h-6" />}
          title={t('docsIndex.selfHostingTitle')}
          description={t('docsIndex.selfHostingDesc')}
          cta={t('docsIndex.selfHostingLink')}
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
    <div className="msm-card p-6">
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
