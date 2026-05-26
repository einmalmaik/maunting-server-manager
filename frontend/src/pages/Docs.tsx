import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BookOpen, Check, Copy, Download, ExternalLink } from 'lucide-react'
import { Link } from 'react-router-dom'

/** Reihenfolge der Doku-Sections. i18n-Keys liegen unter ``docs.sections.<key>``.
 *  Reihenfolge ist die TOC-Reihenfolge — Examples ganz am Ende.
 *  Sections mit ``example`` rendern unter dem Body einen Code-Block. */
type Section = { key: string; example?: object }

const SECTIONS: readonly Section[] = [
  { key: 'intro' },
  { key: 'workflow' },
  { key: 'addServer' },
  { key: 'addBlueprintSteam' },
  { key: 'addBlueprintCustom' },
  { key: 'location' },
  { key: 'schema' },
  { key: 'runtime' },
  { key: 'ports' },
  { key: 'source' },
  { key: 'httpSecurity' },
  { key: 'mods' },
  { key: 'import' },
  {
    key: 'exampleMinecraftVersion',
    example: {
      version: 1,
      meta: {
        id: 'minecraft_paper_1_20_4',
        name: 'Minecraft (Paper 1.20.4)',
        category: 'non_steam_game',
        author: 'community',
        description: 'Paper-Server fixiert auf 1.20.4 — abgeleitet von der Native Blueprint minecraft_paper.',
      },
      runtime: {
        image: 'itzg/minecraft-server:latest',
        workdir: '/data',
        env: {
          EULA: 'TRUE',
          TYPE: 'PAPER',
          VERSION: '1.20.4',
          SERVER_PORT: '{GAME_PORT}',
          ENABLE_RCON: 'true',
          RCON_PORT: '{RCON_PORT}',
        },
        startup: '/start',
      },
      ports: [
        { name: 'game', protocol: 'tcp' },
        { name: 'query', protocol: 'udp' },
        { name: 'rcon', protocol: 'tcp' },
      ],
      source: { type: 'dockerOnly' },
      mods: {
        supportsMods: true,
        supportsSteamWorkshop: false,
        modInjection: 'none',
      },
    },
  },
  {
    key: 'exampleWine',
    example: {
      version: 1,
      meta: {
        id: 'my_windows_server',
        name: 'My Windows Server (Wine)',
        category: 'non_steam_game',
        author: 'community',
        description: 'Windows-Dedicated-Server in selbst gebautem Wine-Image. Beispiel — bitte Image, Startup und Ports anpassen.',
      },
      runtime: {
        image: 'registry.example.com/my-wine-server:latest',
        workdir: '/data',
        env: {
          WINEPREFIX: '/data/.wine',
          WINEARCH: 'win64',
          WINEDEBUG: '-all',
        },
        startup: 'wine64 MyServer.exe -port {GAME_PORT} -log /data/server.log',
      },
      ports: [
        { name: 'game', protocol: 'udp' },
        { name: 'query', protocol: 'udp' },
      ],
      source: { type: 'dockerOnly' },
      mods: null,
    },
  },
] as const

interface CodeBlockProps {
  example: object
}

/** Kleiner Code-Block fuer Beispiel-JSON. Copy-Button setzt die JSON in die
 *  Zwischenablage. Bewusst kein syntax highlighter — KISS, kein neuer Lib-Pull. */
function CodeBlock({ example }: CodeBlockProps) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const text = JSON.stringify(example, null, 2)

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
        data-testid="docs-copy-example"
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

/** Doku-Seite. Anchor-Sidebar links, Sections rechts. Upload-/Verwaltungspfad
 *  liegt jetzt komplett auf /blueprints — diese Seite ist read-only. */
export function Docs() {
  const { t } = useTranslation()

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
          href="/api/blueprints/template"
          download
          className="msm-btn-primary inline-flex items-center gap-2 px-4 py-2"
          data-testid="docs-template-download"
        >
          <Download className="w-4 h-4" />
          {t('docs.downloadTemplate')}
        </a>
        <Link
          to="/blueprints"
          className="msm-btn-secondary inline-flex items-center gap-2 px-4 py-2"
          data-testid="docs-link-blueprints"
        >
          <ExternalLink className="w-4 h-4" />
          {t('docs.manageBlueprints')}
        </Link>
        <span className="font-body-md text-xs text-on-surface-variant basis-full">
          {t('docs.manageBlueprintsHint')}
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[220px,1fr] gap-8">
        <nav
          aria-label={t('docs.tocTitle')}
          className="lg:sticky lg:top-20 lg:self-start"
          data-testid="docs-toc"
        >
          <h2 className="font-headline text-label-lg uppercase tracking-wide text-on-surface-variant mb-3">
            {t('docs.tocTitle')}
          </h2>
          <ul className="space-y-1.5">
            {SECTIONS.map(({ key }) => (
              <li key={key}>
                <a
                  href={`#docs-${key}`}
                  className="block text-body-sm text-on-surface-variant hover:text-on-surface transition-colors"
                  data-testid={`docs-toc-${key}`}
                >
                  {t(`docs.sections.${key}.title`)}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="space-y-6 min-w-0">
          {SECTIONS.map(({ key, example }) => (
            <section
              key={key}
              id={`docs-${key}`}
              className="msm-card p-6 scroll-mt-20"
              data-testid={`docs-section-${key}`}
            >
              <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-2">
                {t(`docs.sections.${key}.title`)}
              </h2>
              <p className="font-body-md text-body-md text-on-surface-variant whitespace-pre-line">
                {t(`docs.sections.${key}.body`)}
              </p>
              {example && <CodeBlock example={example} />}
            </section>
          ))}
        </div>
      </div>
    </div>
  )
}
