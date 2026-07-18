import { useTranslation } from 'react-i18next'
import { KeyRound, Info, AlertTriangle, ExternalLink } from 'lucide-react'
import { Link } from 'react-router-dom'
import { PageHeader } from '@/Singra/UI/PageHeader'

function Alert({ type = 'info', title, children }: { type?: 'info' | 'warning', title: string, children: React.ReactNode }) {
  const styles = {
    info: 'bg-primary/10 text-primary border-primary/20',
    warning: 'bg-warning/10 text-warning border-warning/20',
  }
  const icons = {
    info: <Info className="w-5 h-5 shrink-0" />,
    warning: <AlertTriangle className="w-5 h-5 shrink-0" />,
  }

  return (
    <div className={`p-4 my-4 border rounded-md flex gap-3 ${styles[type]}`}>
      {icons[type]}
      <div>
        <h4 className="font-bold mb-1">{title}</h4>
        <div className="text-sm opacity-90">{children}</div>
      </div>
    </div>
  )
}

export function OAuthDocs() {
  const { t } = useTranslation()

  const TOC = [
    { key: 'intro', title: t('docsOAuth.toc.intro') },
    { key: 'presets', title: t('docsOAuth.toc.presets') },
    { key: 'create', title: t('docsOAuth.toc.create') },
    { key: 'security', title: t('docsOAuth.toc.security') },
    { key: 'troubleshooting', title: t('docsOAuth.toc.troubleshooting') },
  ]

  return (
    <div className="msm-page mx-auto max-w-6xl">
      <PageHeader
        eyebrow={t('pageContext.help', 'Help & guidance')}
        title={t('docsOAuth.title')}
        description={t('docsOAuth.subtitle')}
        status={<KeyRound className="h-6 w-6 text-primary" aria-hidden="true" />}
      />

      <div className="mb-8">
        <Link
          to="/settings"
          className="msm-btn-secondary inline-flex items-center gap-2 px-4 py-2"
        >
          <ExternalLink className="w-4 h-4" />
          {t('docs.manageBlueprints').replace('Blueprints', 'OAuth')}
        </Link>
      </div>

      <details className="msm-card mb-5 p-4 lg:hidden">
        <summary className="cursor-pointer font-label-md text-sm font-semibold text-on-surface">
          {t('docsOAuth.tocTitle')}
        </summary>
        <nav className="mt-3 border-t border-outline-variant pt-3" aria-label={t('docsOAuth.tocTitle')}>
          <ul className="grid gap-1.5 sm:grid-cols-2">
            {TOC.map(({ key, title }) => (
              <li key={key}><a href={`#oauth-docs-${key}`} className="block min-h-11 py-2 text-sm text-on-surface-variant hover:text-on-surface">{title}</a></li>
            ))}
          </ul>
        </nav>
      </details>

      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[220px,1fr]">
        <nav className="hidden lg:sticky lg:top-20 lg:block lg:self-start" aria-label={t('docsOAuth.tocTitle')}>
          <h2 className="font-headline text-label-lg uppercase tracking-wide text-on-surface-variant mb-3">
            {t('docsOAuth.tocTitle')}
          </h2>
          <ul className="space-y-1.5">
            {TOC.map(({ key, title }) => (
              <li key={key}>
                <a
                  href={`#oauth-docs-${key}`}
                  className="block text-body-sm text-on-surface-variant hover:text-on-surface transition-colors"
                >
                  {title}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="space-y-6 min-w-0">
          <section id="oauth-docs-intro" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-2">
              {t('docsOAuth.toc.intro')}
            </h2>
            <p className="font-body-md text-body-md text-on-surface-variant whitespace-pre-line">
              {t('docsOAuth.intro.body')}
            </p>
          </section>

          <section id="oauth-docs-presets" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-4">
              {t('docsOAuth.presets.title')}
            </h2>
            <div className="space-y-4">
              {(['google', 'discord', 'github', 'microsoft', 'twitter', 'custom_oidc', 'custom_oauth2'] as const).map((p) => (
                <div key={p} className="flex gap-3">
                  <div className="w-24 shrink-0">
                    <span className="font-mono text-sm text-primary">{t(`settings.oauth.preset.${p}` as any, p)}</span>
                  </div>
                  <p className="font-body-md text-sm text-on-surface-variant flex-1">
                    {t(`docsOAuth.presets.${p}` as any)}
                  </p>
                </div>
              ))}
            </div>
          </section>

          <section id="oauth-docs-create" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-4">
              {t('docsOAuth.create.title')}
            </h2>
            <ol className="font-body-md text-body-md text-on-surface-variant list-decimal pl-5 space-y-2">
              <li>{t('docsOAuth.create.step1')}</li>
              <li>{t('docsOAuth.create.step2')}</li>
              <li>{t('docsOAuth.create.step3')}</li>
              <li>{t('docsOAuth.create.step4')}</li>
              <li>{t('docsOAuth.create.step5')}</li>
              <li>{t('docsOAuth.create.step6')}</li>
            </ol>
          </section>

          <section id="oauth-docs-security" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-4">
              {t('docsOAuth.security.title')}
            </h2>
            <ul className="font-body-md text-body-md text-on-surface-variant list-disc pl-5 space-y-2">
              <li>{t('docsOAuth.security.rule1')}</li>
              <li>{t('docsOAuth.security.rule2')}</li>
              <li>{t('docsOAuth.security.rule3')}</li>
              <li>{t('docsOAuth.security.rule4')}</li>
              <li>{t('docsOAuth.security.rule5')}</li>
              <li>{t('docsOAuth.security.rule6')}</li>
              <li>{t('docsOAuth.security.rule7')}</li>
              <li>{t('docsOAuth.security.rule8')}</li>
            </ul>
          </section>

          <section id="oauth-docs-troubleshooting" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-4">
              {t('docsOAuth.toc.troubleshooting')}
            </h2>
            <div className="space-y-4">
              <Alert type="warning" title={t('docsOAuth.troubleshooting.err1Title')}>
                {t('docsOAuth.troubleshooting.err1Body')}
              </Alert>
              <Alert type="warning" title={t('docsOAuth.troubleshooting.err2Title')}>
                {t('docsOAuth.troubleshooting.err2Body')}
              </Alert>
              <Alert type="warning" title={t('docsOAuth.troubleshooting.err3Title')}>
                {t('docsOAuth.troubleshooting.err3Body')}
              </Alert>
              <Alert type="warning" title={t('docsOAuth.troubleshooting.err4Title')}>
                {t('docsOAuth.troubleshooting.err4Body')}
              </Alert>
              <Alert type="warning" title={t('docsOAuth.troubleshooting.err5Title')}>
                {t('docsOAuth.troubleshooting.err5Body')}
              </Alert>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
