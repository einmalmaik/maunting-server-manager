import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { BookOpen, KeyRound, ArrowRight, BookOpenCheck } from 'lucide-react'

export function Docs() {
  const { t } = useTranslation()
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
