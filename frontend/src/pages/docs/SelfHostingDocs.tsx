import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRightLeft,
  Check,
  Clipboard,
  FileArchive,
  GitBranch,
  MonitorSmartphone,
  Network,
  Server,
  ShieldCheck,
  Terminal,
} from 'lucide-react'
import { PageHeader } from '@/Singra/UI/PageHeader'

export const PANEL_BOOTSTRAP_COMMAND = `curl -fsSL https://raw.githubusercontent.com/einmalmaik/maunting-server-manager/main/scripts/bootstrap.sh \\
  | sudo bash -s -- --domain panel.example.com`
export const COMPONENT_MIGRATION_COMMAND = 'sudo /opt/msm/helper-scripts/migrate-panel-components.sh'

const artifacts = [
  'msm-panel-<VERSION>.tar.gz',
  'msm-frontend-<VERSION>.tar.gz',
  'msm-agent-<VERSION>.tar.gz',
  'SHA256SUMS',
] as const

function CommandBlock({ command, label, testId }: { command: string; label: string; testId: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // Die Zwischenablage ist nur Komfort; Installationsfehler werden hier nicht vorgetaeuscht.
    }
  }

  return (
    <div className="relative mt-4 overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest">
      <div className="flex items-center justify-between gap-3 border-b border-outline-variant px-4 py-2.5">
        <span className="inline-flex items-center gap-2 font-label-md text-xs uppercase tracking-wider text-on-surface-variant">
          <Terminal className="h-4 w-4 text-primary" />
          {label}
        </span>
        <button
          type="button"
          onClick={() => void copy()}
          className="msm-btn-secondary inline-flex items-center gap-2 px-3 py-1.5 text-xs"
          aria-label={copied ? t('docsSelfHosting.install.copied') : t('docsSelfHosting.install.copy')}
        >
          {copied ? <Check className="h-3.5 w-3.5 text-status-success" /> : <Clipboard className="h-3.5 w-3.5" />}
          {copied ? t('docsSelfHosting.install.copied') : t('docsSelfHosting.install.copy')}
        </button>
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-xs leading-6 text-on-surface sm:text-sm">
        <code data-testid={testId}>{command}</code>
      </pre>
      <span className="sr-only" role="status" aria-live="polite">
        {copied ? t('docsSelfHosting.install.copied') : ''}
      </span>
    </div>
  )
}

export function SelfHostingDocs() {
  const { t } = useTranslation()

  const deploymentUnits = [
    {
      icon: <ShieldCheck className="h-5 w-5" />,
      title: t('docsSelfHosting.units.panel.title'),
      artifact: 'msm-panel-<VERSION>.tar.gz',
      description: t('docsSelfHosting.units.panel.description'),
    },
    {
      icon: <MonitorSmartphone className="h-5 w-5" />,
      title: t('docsSelfHosting.units.frontend.title'),
      artifact: 'msm-frontend-<VERSION>.tar.gz',
      description: t('docsSelfHosting.units.frontend.description'),
    },
    {
      icon: <Server className="h-5 w-5" />,
      title: t('docsSelfHosting.units.node.title'),
      artifact: 'msm-agent-<VERSION>.tar.gz',
      description: t('docsSelfHosting.units.node.description'),
    },
  ]

  return (
    <main className="msm-page mx-auto max-w-6xl">
      <PageHeader
        eyebrow={t('docsSelfHosting.eyebrow')}
        title={t('docsSelfHosting.title')}
        description={t('docsSelfHosting.subtitle')}
        status={<Network className="h-6 w-6 text-primary" aria-hidden="true" />}
      />

      <nav className="sticky top-16 z-10 -mx-1 mb-6 flex gap-2 overflow-x-auto bg-surface/95 px-1 py-2 backdrop-blur lg:hidden" aria-label={t('docsSelfHosting.navigation.label')}>
        {[
          ['deployment-units', t('docsSelfHosting.units.title')],
          ['panel-install', t('docsSelfHosting.install.title')],
          ['topology', t('docsSelfHosting.topology.title')],
          ['component-migration', t('docsSelfHosting.migration.title')],
          ['enrollment', t('docsSelfHosting.enrollment.title')],
          ['artifacts', t('docsSelfHosting.artifacts.title')],
        ].map(([id, label]) => (
          <a key={id} href={`#${id}`} className="msm-btn-secondary shrink-0 px-3 py-2 text-xs">{label}</a>
        ))}
      </nav>

      <section aria-labelledby="deployment-units" className="mb-10">
        <div className="mb-4 flex items-center gap-2">
          <GitBranch className="h-5 w-5 text-primary" />
          <h2 id="deployment-units" className="font-headline text-headline-md text-on-surface">
            {t('docsSelfHosting.units.title')}
          </h2>
        </div>
        <p className="mb-5 max-w-3xl text-sm leading-6 text-on-surface-variant">
          {t('docsSelfHosting.units.intro')}
        </p>
        <div className="grid gap-4 lg:grid-cols-3">
          {deploymentUnits.map((unit) => (
            <article key={unit.artifact} className="msm-card p-5">
              <div className="mb-4 flex items-start justify-between gap-3">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-container-highest text-secondary">
                  {unit.icon}
                </span>
                <span className="rounded-md border border-outline-variant bg-surface-container-low px-2 py-1 font-mono text-[11px] text-primary">
                  {unit.artifact}
                </span>
              </div>
              <h3 className="font-headline text-body-lg font-semibold text-on-surface">{unit.title}</h3>
              <p className="mt-2 text-sm leading-6 text-on-surface-variant">{unit.description}</p>
            </article>
          ))}
        </div>
      </section>

      <section aria-labelledby="panel-install" className="msm-card mb-10 p-5 sm:p-6">
        <div className="flex items-start gap-3">
          <Terminal className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
          <div>
            <h2 id="panel-install" className="font-headline text-headline-md text-on-surface">
              {t('docsSelfHosting.install.title')}
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-on-surface-variant">
              {t('docsSelfHosting.install.description')}
            </p>
          </div>
        </div>
        <CommandBlock
          command={PANEL_BOOTSTRAP_COMMAND}
          label={t('docsSelfHosting.install.commandLabel')}
          testId="panel-bootstrap-command"
        />
        <p className="mt-4 border-l-2 border-primary/50 pl-4 text-sm leading-6 text-on-surface-variant">
          {t('docsSelfHosting.install.releaseNote')}
        </p>
      </section>

      <section aria-labelledby="topology" className="mb-10">
        <div className="mb-4 flex items-center gap-2">
          <Network className="h-5 w-5 text-primary" />
          <h2 id="topology" className="font-headline text-headline-md text-on-surface">
            {t('docsSelfHosting.topology.title')}
          </h2>
        </div>
        <div className="msm-card overflow-hidden p-0">
          <div className="grid lg:grid-cols-[0.85fr,1.15fr]">
            <div className="border-b border-outline-variant p-5 lg:border-b-0 lg:border-r sm:p-6">
              <p className="font-mono text-xs uppercase tracking-wider text-primary">01 / {t('docsSelfHosting.topology.controlLabel')}</p>
              <p className="mt-3 text-sm leading-6 text-on-surface-variant">{t('docsSelfHosting.topology.control')}</p>
            </div>
            <div className="p-5 sm:p-6">
              <p className="font-mono text-xs uppercase tracking-wider text-primary">02–20 / {t('docsSelfHosting.topology.nodesLabel')}</p>
              <p className="mt-3 text-sm leading-6 text-on-surface-variant">{t('docsSelfHosting.topology.nodes')}</p>
              <div className="mt-4 grid grid-cols-5 gap-2 sm:grid-cols-10" aria-label={t('docsSelfHosting.topology.nodeGridLabel')}>
                {Array.from({ length: 19 }, (_, index) => (
                  <span
                    key={index}
                    className="flex aspect-square items-center justify-center rounded-md border border-outline-variant bg-surface-container-low font-mono text-[10px] text-on-surface-variant"
                  >
                    {index + 2}
                  </span>
                ))}
              </div>
            </div>
          </div>
          <p className="border-t border-outline-variant bg-surface-container-low px-5 py-4 text-sm leading-6 text-on-surface-variant sm:px-6">
            {t('docsSelfHosting.topology.split')}
          </p>
        </div>
      </section>

      <section aria-labelledby="component-migration" className="msm-card mb-10 p-5 sm:p-6">
        <div className="flex items-start gap-3">
          <ArrowRightLeft className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
          <div>
            <h2 id="component-migration" className="font-headline text-headline-md text-on-surface">
              {t('docsSelfHosting.migration.title')}
            </h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-on-surface-variant">
              {t('docsSelfHosting.migration.description')}
            </p>
          </div>
        </div>
        <CommandBlock
          command={COMPONENT_MIGRATION_COMMAND}
          label={t('docsSelfHosting.migration.commandLabel')}
          testId="component-migration-command"
        />
        <ol className="mt-5 grid gap-px overflow-hidden rounded-xl border border-outline-variant bg-outline-variant lg:grid-cols-3">
          {Array.from({ length: 3 }, (_, index) => (
            <li key={index} className="bg-surface-container p-4 sm:p-5">
              <span className="font-mono text-xs font-semibold text-primary">0{index + 1}</span>
              <h3 className="mt-2 text-sm font-semibold text-on-surface">
                {t(`docsSelfHosting.migration.steps.${index + 1}.title`)}
              </h3>
              <p className="mt-1 text-sm leading-6 text-on-surface-variant">
                {t(`docsSelfHosting.migration.steps.${index + 1}.description`)}
              </p>
            </li>
          ))}
        </ol>
        <div className="mt-4 flex gap-3 rounded-xl border border-status-warning/30 bg-status-warning/10 p-4 text-status-warning">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <p className="text-sm leading-6">{t('docsSelfHosting.migration.externalBoundary')}</p>
        </div>
        <p className="mt-4 text-sm leading-6 text-on-surface-variant">
          {t('docsSelfHosting.migration.rollback')}
        </p>
      </section>

      <section aria-labelledby="enrollment" className="mb-10">
        <div className="mb-4 flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-primary" />
          <h2 id="enrollment" className="font-headline text-headline-md text-on-surface">
            {t('docsSelfHosting.enrollment.title')}
          </h2>
        </div>
        <p className="mb-5 max-w-3xl text-sm leading-6 text-on-surface-variant">
          {t('docsSelfHosting.enrollment.intro')}
        </p>
        <ol className="grid gap-px overflow-hidden rounded-xl border border-outline-variant bg-outline-variant md:grid-cols-2">
          {Array.from({ length: 6 }, (_, index) => (
            <li key={index} className="flex gap-4 bg-surface-container p-4 sm:p-5">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-primary/35 bg-primary/10 font-mono text-xs font-semibold text-primary">
                {index + 1}
              </span>
              <div>
                <h3 className="text-sm font-semibold text-on-surface">
                  {t(`docsSelfHosting.enrollment.steps.${index + 1}.title`)}
                </h3>
                <p className="mt-1 text-sm leading-6 text-on-surface-variant">
                  {t(`docsSelfHosting.enrollment.steps.${index + 1}.description`)}
                </p>
              </div>
            </li>
          ))}
        </ol>
        <p className="mt-4 text-sm leading-6 text-on-surface-variant">{t('docsSelfHosting.enrollment.fallback')}</p>
      </section>

      <section aria-labelledby="artifacts" className="mb-10">
        <div className="mb-4 flex items-center gap-2">
          <FileArchive className="h-5 w-5 text-primary" />
          <h2 id="artifacts" className="font-headline text-headline-md text-on-surface">
            {t('docsSelfHosting.artifacts.title')}
          </h2>
        </div>
        <div className="overflow-hidden rounded-xl border border-outline-variant text-sm">
          <div className="hidden grid-cols-[minmax(220px,0.8fr)_1.2fr] border-b border-outline-variant bg-surface-container-lowest text-on-surface-variant sm:grid">
            <span className="px-4 py-3 font-label-md text-xs uppercase tracking-wider">{t('docsSelfHosting.artifacts.file')}</span>
            <span className="px-4 py-3 font-label-md text-xs uppercase tracking-wider">{t('docsSelfHosting.artifacts.contains')}</span>
          </div>
          <dl className="divide-y divide-outline-variant bg-surface-container">
            {artifacts.map((artifact) => (
              <div key={artifact} className="grid gap-1 px-4 py-3 sm:grid-cols-[minmax(220px,0.8fr)_1.2fr] sm:gap-0 sm:px-0 sm:py-0">
                <dt className="break-all font-mono text-xs text-primary sm:px-4 sm:py-3">{artifact}</dt>
                <dd className="leading-6 text-on-surface-variant sm:px-4 sm:py-3">
                  {t(`docsSelfHosting.artifacts.descriptions.${artifact === 'SHA256SUMS' ? 'sums' : artifact.split('-')[1]}`)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <aside className="mb-8 flex gap-3 rounded-xl border border-status-warning/30 bg-status-warning/10 p-4 text-status-warning" aria-labelledby="database-compatibility">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
        <div>
          <h2 id="database-compatibility" className="font-semibold">{t('docsSelfHosting.compatibility.title')}</h2>
          <p className="mt-1 text-sm leading-6 opacity-90">{t('docsSelfHosting.compatibility.description')}</p>
        </div>
      </aside>

      <nav className="flex flex-col gap-3 border-t border-outline-variant pt-6 sm:flex-row" aria-label={t('docsSelfHosting.navigation.label')}>
        <Link to="/admin/nodes" className="msm-btn-primary inline-flex items-center justify-center gap-2 px-4 py-2.5">
          <Server className="h-4 w-4" />
          {t('docsSelfHosting.navigation.nodes')}
        </Link>
        <Link to="/docs" className="msm-btn-secondary inline-flex items-center justify-center gap-2 px-4 py-2.5">
          <ArrowLeft className="h-4 w-4" />
          {t('docsSelfHosting.navigation.docs')}
        </Link>
      </nav>
    </main>
  )
}
