import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BookOpen, Check, Copy, Download, ExternalLink, Info, AlertTriangle } from 'lucide-react'
import { Link } from 'react-router-dom'

interface CodeBlockProps {
  example: string | object
}

function CodeBlock({ example }: CodeBlockProps) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const text = typeof example === 'string' ? example : JSON.stringify(example, null, 2)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // Kein Toast — clipboard ist Komfort, kein kritischer Pfad.
    }
  }

  return (
    <div className="relative mt-4">
      <button
        type="button"
        onClick={copy}
        className="absolute top-2 right-2 msm-btn-secondary px-2 py-1 text-xs inline-flex items-center gap-1.5"
      >
        {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
        {copied ? t('docs.copiedExample') : t('docs.copyExample')}
      </button>
      <pre className="bg-surface-container-lowest border border-outline rounded-md p-4 overflow-auto font-mono text-xs text-on-surface whitespace-pre">
        {text}
      </pre>
    </div>
  )
}

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

function FieldTable({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto my-4 border border-outline-variant/30 rounded-md">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-outline-variant/30 bg-surface-container-lowest text-on-surface-variant">
          <tr>
            <th className="px-4 py-2 font-medium">Feld</th>
            <th className="px-4 py-2 font-medium">Typ</th>
            <th className="px-4 py-2 font-medium">Pflicht?</th>
            <th className="px-4 py-2 font-medium">Beschreibung</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-outline-variant/10">
          {children}
        </tbody>
      </table>
    </div>
  )
}

function FieldRow({ field, type, required, children }: { field: string, type: string, required: boolean, children: React.ReactNode }) {
  const { t } = useTranslation()
  return (
    <tr>
      <td className="px-4 py-3 font-mono text-xs text-primary">{field}</td>
      <td className="px-4 py-3 font-mono text-xs text-on-surface-variant">{type}</td>
      <td className="px-4 py-3 text-xs">
        {required ? (
          <span className="text-warning font-bold">{t('docs.reference.yes')}</span>
        ) : (
          <span className="text-on-surface-variant">{t('docs.reference.no')}</span>
        )}
      </td>
      <td className="px-4 py-3 text-on-surface">{children}</td>
    </tr>
  )
}

export function Docs() {
  const { t, i18n } = useTranslation()

  const TOC = [
    { key: 'intro', title: t('docs.toc.intro') },
    { key: 'quickstart', title: t('docs.toc.quickstart') },
    { key: 'minimal', title: t('docs.toc.minimal') },
    { key: 'reference', title: t('docs.toc.reference') },
    { key: 'howto', title: t('docs.toc.howto') },
    { key: 'troubleshooting', title: t('docs.toc.troubleshooting') },
  ]

  const minimalExample = {
    "version": 1,
    "meta": {
      "id": "minimal_server",
      "name": "Minimal Server",
      "category": "non_steam_game",
    },
    "runtime": {
      "image": "ubuntu:24.04",
      "startup": "echo 'Hello World'"
    },
    "ports": [],
    "source": { "type": "dockerOnly" }
  }

  return (
    <div className="container mx-auto px-4 py-8 max-w-6xl">
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
          href={`/api/blueprints/template?lang=${i18n.language}`}
          download
          data-testid="docs-template-download"
          className="msm-btn-primary inline-flex items-center gap-2 px-4 py-2"
        >
          <Download className="w-4 h-4" />
          {t('docs.downloadTemplate')}
        </a>
        <Link
          to="/blueprints"
          data-testid="docs-link-blueprints"
          className="msm-btn-secondary inline-flex items-center gap-2 px-4 py-2"
        >
          <ExternalLink className="w-4 h-4" />
          {t('docs.manageBlueprints')}
        </Link>
        <span className="font-body-md text-xs text-on-surface-variant basis-full">
          {t('docs.manageBlueprintsHint')}
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[220px,1fr] gap-8">
        <nav className="lg:sticky lg:top-20 lg:self-start">
          <h2 className="font-headline text-label-lg uppercase tracking-wide text-on-surface-variant mb-3">
            {t('docs.tocTitle')}
          </h2>
          <ul className="space-y-1.5">
            {TOC.map(({ key, title }) => (
              <li key={key}>
                <a
                  href={`#docs-${key}`}
                  className="block text-body-sm text-on-surface-variant hover:text-on-surface transition-colors"
                >
                  {title}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="space-y-6 min-w-0">
          
          <section id="docs-intro" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-2">{t('docs.toc.intro')}</h2>
            <p className="font-body-md text-body-md text-on-surface-variant whitespace-pre-line">
              {t('docs.intro.body')}
            </p>
          </section>

          <section id="docs-quickstart" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-2">{t('docs.toc.quickstart')}</h2>
            <div className="font-body-md text-body-md text-on-surface-variant space-y-4">
              <p>{t('docs.quickstart.p1')}</p>
              <ol className="list-decimal pl-5 space-y-2">
                <li>{t('docs.quickstart.s1')}</li>
                <li>{t('docs.quickstart.s2')}</li>
                <li>{t('docs.quickstart.s3')}</li>
                <li>{t('docs.quickstart.s4')}</li>
              </ol>
            </div>
            <Alert type="info" title={t('docs.quickstart.alertTitle')}>
              {t('docs.quickstart.alertBody')}
            </Alert>
          </section>

          <section id="docs-minimal" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-2">{t('docs.toc.minimal')}</h2>
            <p className="font-body-md text-body-md text-on-surface-variant">
              {t('docs.minimal.body')}
            </p>
            <CodeBlock example={minimalExample} />
          </section>

          <section id="docs-reference" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-2">{t('docs.toc.reference')}</h2>
            <p className="font-body-md text-body-md text-on-surface-variant mb-4">
              {t('docs.reference.body')}
            </p>
            
            <h3 className="font-bold text-primary mt-6 mb-2">meta</h3>
            <FieldTable>
              <FieldRow field="meta.id" type="string" required={true}>{t('docs.reference.metaId')}</FieldRow>
              <FieldRow field="meta.name" type="string" required={true}>{t('docs.reference.metaName')}</FieldRow>
              <FieldRow field="meta.category" type="enum" required={true}>{t('docs.reference.metaCategory')}</FieldRow>
              <FieldRow field="meta.author" type="string" required={false}>{t('docs.reference.metaAuthor')}</FieldRow>
              <FieldRow field="meta.description" type="string" required={false}>{t('docs.reference.metaDescription')}</FieldRow>
            </FieldTable>

            <h3 className="font-bold text-primary mt-6 mb-2">runtime</h3>
            <FieldTable>
              <FieldRow field="runtime.image" type="string" required={true}>{t('docs.reference.runtimeImage')}</FieldRow>
              <FieldRow field="runtime.workdir" type="string" required={false}>{t('docs.reference.runtimeWorkdir')}</FieldRow>
              <FieldRow field="runtime.env" type="dict" required={false}>{t('docs.reference.runtimeEnv')}</FieldRow>
              <FieldRow field="runtime.startup" type="string" required={true}>{t('docs.reference.runtimeStartup')}</FieldRow>
              <FieldRow field="runtime.configPatches" type="list" required={false}>{t('docs.reference.runtimeConfigPatches')}</FieldRow>
            </FieldTable>

            <h4 className="font-semibold text-on-surface-variant mt-3 mb-1 text-sm">{t('docs.reference.configPatchFields')}</h4>
            <FieldTable>
              <FieldRow field="type" type="enum" required={true}>{t('docs.reference.configPatchType')}</FieldRow>
              <FieldRow field="file" type="string" required={true}>{t('docs.reference.configPatchFile')}</FieldRow>
              <FieldRow field="section" type="string" required={true}>{t('docs.reference.configPatchSection')}</FieldRow>
              <FieldRow field="key" type="string" required={true}>{t('docs.reference.configPatchKey')}</FieldRow>
              <FieldRow field="value" type="string" required={true}>{t('docs.reference.configPatchValue')}</FieldRow>
            </FieldTable>


            <h3 className="font-bold text-primary mt-6 mb-2">ports</h3>
            <p className="text-sm text-on-surface-variant mb-2">{t('docs.reference.portsDesc')}</p>
            <FieldTable>
              <FieldRow field="ports[].name" type="enum" required={true}>{t('docs.reference.portsName')}</FieldRow>
              <FieldRow field="ports[].protocol" type="enum" required={true}>{t('docs.reference.portsProtocol')}</FieldRow>
            </FieldTable>

            <h3 className="font-bold text-primary mt-6 mb-2">source</h3>
            <FieldTable>
              <FieldRow field="source.type" type="enum" required={true}>{t('docs.reference.sourceType')}</FieldRow>
              <FieldRow field="source.steam" type="object" required={false}>{t('docs.reference.sourceSteam')}</FieldRow>
              <FieldRow field="source.http" type="object" required={false}>{t('docs.reference.sourceHttp')}</FieldRow>
              <FieldRow field="source.manual" type="object" required={false}>{t('docs.reference.sourceManual')}</FieldRow>
            </FieldTable>

            <h3 className="font-bold text-primary mt-6 mb-2">mods</h3>
            <FieldTable>
              <FieldRow field="mods.supportsMods" type="boolean" required={false}>{t('docs.reference.modsSupportsMods')}</FieldRow>
              <FieldRow field="mods.supportsSteamWorkshop" type="boolean" required={false}>{t('docs.reference.modsSupportsSteamWorkshop')}</FieldRow>
              <FieldRow field="mods.workshopAppId" type="string" required={false}>{t('docs.reference.modsWorkshopAppId')}</FieldRow>
              <FieldRow field="mods.filterTags" type="list" required={false}>{t('docs.reference.modsFilterTags')}</FieldRow>
              <FieldRow field="mods.modInjection" type="enum" required={false}>{t('docs.reference.modsModInjection')}</FieldRow>
              <FieldRow field="mods.modStartupArgumentFormat" type="string" required={false}>{t('docs.reference.modsModStartupArgumentFormat')}</FieldRow>
              <FieldRow field="mods.modListFilePath" type="string" required={false}>{t('docs.reference.modsModListFilePath')}</FieldRow>
              <FieldRow field="mods.modListContent" type="enum" required={false}>{t('docs.reference.modsModListContent')}</FieldRow>
              <FieldRow field="mods.postInstall" type="list" required={false}>{t('docs.reference.modsPostInstall')}</FieldRow>
            </FieldTable>
          </section>

          <section id="docs-howto" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-2">{t('docs.toc.howto')}</h2>
            
            <h3 className="font-bold text-on-surface mt-4">{t('docs.howto.h1')}</h3>
            <p className="font-body-md text-body-md text-on-surface-variant mb-2">{t('docs.howto.b1')}</p>
            
            <h3 className="font-bold text-on-surface mt-6">{t('docs.howto.h2')}</h3>
            <p className="font-body-md text-body-md text-on-surface-variant mb-2">{t('docs.howto.b2')}</p>
          </section>

          <section id="docs-troubleshooting" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-2">{t('docs.toc.troubleshooting')}</h2>
            <div className="space-y-4">
              <Alert type="warning" title={t('docs.troubleshooting.err1Title')}>
                {t('docs.troubleshooting.err1Body')}
              </Alert>
              <Alert type="warning" title={t('docs.troubleshooting.err2Title')}>
                {t('docs.troubleshooting.err2Body')}
              </Alert>
              <Alert type="warning" title={t('docs.troubleshooting.err3Title')}>
                {t('docs.troubleshooting.err3Body')}
              </Alert>
              <Alert type="warning" title={t('docs.troubleshooting.err4Title')}>
                {t('docs.troubleshooting.err4Body')}
              </Alert>
              <Alert type="warning" title={t('docs.troubleshooting.err5Title')}>
                {t('docs.troubleshooting.err5Body')}
              </Alert>
              <Alert type="warning" title={t('docs.troubleshooting.err6Title')}>
                {t('docs.troubleshooting.err6Body')}
              </Alert>
              <Alert type="warning" title={t('docs.troubleshooting.err7Title')}>
                {t('docs.troubleshooting.err7Body')}
              </Alert>
              <Alert type="warning" title={t('docs.troubleshooting.err8Title')}>
                {t('docs.troubleshooting.err8Body')}
              </Alert>
            </div>
          </section>

        </div>
      </div>
    </div>
  )
}
