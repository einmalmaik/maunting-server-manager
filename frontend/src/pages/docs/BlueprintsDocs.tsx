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

export function BlueprintsDocs() {
  const { t, i18n } = useTranslation()

  const TOC = [
    { key: 'intro', title: t('docs.toc.intro') },
    { key: 'quickstart', title: t('docs.toc.quickstart') },
    { key: 'minimal', title: t('docs.toc.minimal') },
    { key: 'reference', title: t('docs.toc.reference') },
    { key: 'howto', title: t('docs.toc.howto') },
    { key: 'updates', title: t('docs.toc.updates') },
    { key: 'backups', title: t('docs.toc.backups') },
    { key: 'troubleshooting', title: t('docs.toc.troubleshooting') },
  ]

  const backupScopeExample = {
    backup: {
      includePaths: [
        'ConanSandbox/Saved/Config',
        'ConanSandbox/Saved/SaveGames',
        'ConanSandbox/Saved/game.db',
      ],
    },
  }

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

  const nonSteamExample = {
    "version": 1,
    "meta": {
      "id": "hytale",
      "name": "Hytale (Dedicated)",
      "category": "non_steam_game"
    },
    "runtime": {
      "image": "ghcr.io/natroutter/egg-hytale:latest",
      "workdir": "/home/container",
      "env": {
        "STARTUP": "./start.sh",
        "SERVER_PORT": "{GAME_PORT}",
        "AUTH_MODE": "AUTHENTICATED",
        "AUTOMATIC_UPDATE": "1",
        "PATCHLINE": "release"
      },
      "startup": "/entrypoint.sh"
    },
    "ports": [
      { "name": "game", "protocol": "udp" }
    ],
    "source": {
      "type": "http",
      "http": {
        "url": "https://downloader.hytale.com/hytale-downloader.zip",
        "archiveType": "zip"
      }
    }
  }

  const steamGameExample = {
    "version": 1,
    "meta": {
      "id": "dayz",
      "name": "DayZ",
      "category": "steam_game"
    },
    "runtime": {
      "image": "cm2network/steamcmd:root",
      "workdir": "/data",
      "env": {},
      "startup": "/data/DayZServer -config=serverDZ.cfg -port={GAME_PORT} -BEpath=battleye -profiles=profiles -dologs -adminlog -netlog -freezecheck",
      "ensureDirs": ["profiles"]
    },
    "ports": [
      { "name": "game", "protocol": "udp" },
      { "name": "query", "protocol": "udp" },
      { "name": "rcon", "protocol": "tcp" }
    ],
    "source": {
      "type": "steam",
      "steam": {
        "appId": "223350",
        "platform": "linux",
        "compatibility": "native",
        "requiresLogin": true
      }
    }
  }

  const botExample = {
    "version": 1,
    "meta": {
      "id": "custom_discord_bot",
      "name": "Custom Discord Bot",
      "category": "bot",
      "author": "Your Name",
      "description": "Bot from GitHub (branch + auto-pull). Startup via startupProfiles."
    },
    "runtime": {
      "image": "node:22-bookworm-slim",
      "workdir": "/data",
      "env": { "NODE_ENV": "production" },
      "startup": "node index.js",
      "startupProfiles": [
        { "whenFile": "package.json", "startup": "npm start" },
        { "whenFile": "bot.py", "startup": "python3 bot.py" }
      ]
    },
    "ports": [],
    "source": {
      "type": "github",
      "updateStrategy": "checkBased",
      "github": {
        "repo": "your-org/your-bot",
        "branch": "main",
        "setupCommands": [["npm", "ci"]]
      }
    }
  }

  const voiceServerExample = {
    "version": 1,
    "meta": {
      "id": "mumble_server",
      "name": "Mumble Server",
      "category": "voice_server"
    },
    "runtime": {
      "image": "mumblevoip/mumble-server:latest",
      "workdir": "/data",
      "env": {},
      "startup": "mumble-server"
    },
    "ports": [
      { "name": "voice", "protocol": "tcp" },
      { "name": "voice", "protocol": "udp" }
    ],
    "source": {
      "type": "dockerOnly"
    }
  }

  const steamLegacyExample = {
    "version": 1,
    "meta": {
      "id": "conan_exiles_legacy",
      "name": "Conan Exiles (Legacy Branch)",
      "category": "steam_game"
    },
    "runtime": {
      "image": "ghcr.io/ptero-eggs/yolks:wine_staging",
      "workdir": "/data",
      "env": {},
      "startup": "wine64 /data/ConanSandboxServer.exe -log"
    },
    "ports": [
      { "name": "game", "protocol": "udp" },
      { "name": "query", "protocol": "udp" },
      { "name": "rcon", "protocol": "tcp" }
    ],
    "source": {
      "type": "steam",
      "updateStrategy": "checkBased",
      "steam": {
        "appId": "443030",
        "platform": "windows",
        "compatibility": "proton",
        "branch": "conan-exiles-legacy",
        "validate": false,
        "requiresLogin": true
      }
    }
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
              <FieldRow field="runtime.ensureDirs" type="list" required={false}>
                {t('docs.reference.runtimeEnsureDirs', 'Relative directories created inside the server directory before each start. Useful for profile, log, cache, or runtime folders expected by startup arguments.')}
              </FieldRow>
              <FieldRow field="runtime.configPatches" type="list" required={false}>{t('docs.reference.runtimeConfigPatches')}</FieldRow>
              {/* v1.4.7+: Exec-Tab-Opt-in. Default false. Erlaubt authentifizierten
                  Usern mit Permission ``server.console.exec``, One-Shot-Befehle
                  im MSM-Container auszufuehren (argv, kein Shell). Siehe
                  BlueprintsDocs-Sektion "Container-Befehle (Exec-Tab)" weiter
                  unten. */}
              <FieldRow field="runtime.enableExec" type="boolean" required={false}>
                {t('docs.reference.runtimeEnableExec', 'Exec-Tab aktivieren. Default: false. Self-Hosted, aber Befehle laufen NUR im MSM-verwalteten Container dieses Servers, NIEMALS auf dem Host oder in fremden Containern.')}
              </FieldRow>
              <FieldRow field="runtime.execTimeoutSeconds" type="integer" required={false}>
                {t('docs.reference.runtimeExecTimeoutSeconds', 'Timeout pro Exec-Aufruf in Sekunden (1..600). Default: 60.')}
              </FieldRow>
            </FieldTable>

            <h4 className="font-semibold text-on-surface-variant mt-3 mb-1 text-sm">{t('docs.reference.configPatchFields')}</h4>
            <FieldTable>
              <FieldRow field="type" type="enum" required={true}>{t('docs.reference.configPatchType')}</FieldRow>
              <FieldRow field="file" type="string" required={true}>{t('docs.reference.configPatchFile')}</FieldRow>
              <FieldRow field="section" type="string" required={false}>{t('docs.reference.configPatchSection')}</FieldRow>
              <FieldRow field="key" type="string" required={false}>{t('docs.reference.configPatchKey')}</FieldRow>
              <FieldRow field="regex" type="string" required={false}>{t('docs.reference.configPatchRegex')}</FieldRow>
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
              <FieldRow field="source.updateStrategy" type="enum" required={false}>{t('docs.reference.sourceUpdateStrategy')}</FieldRow>
              <FieldRow field="source.steam" type="object" required={false}>{t('docs.reference.sourceSteam')}</FieldRow>
              <FieldRow field="source.http" type="object" required={false}>{t('docs.reference.sourceHttp')}</FieldRow>
              <FieldRow field="source.github" type="object" required={false}>{t('docs.reference.sourceGithub')}</FieldRow>
              <FieldRow field="source.manual" type="object" required={false}>{t('docs.reference.sourceManual')}</FieldRow>
            </FieldTable>

            <div className="mt-3 p-3 rounded-md border border-status-info/30 bg-status-info/10 text-sm text-on-surface">
              <strong>{t('docs.reference.githubPrivateTitle', 'Private Repositories')}</strong>
              <p className="mt-1">
                {t('docs.reference.githubPrivateBody',
                  'For private repos, set a GitHub Personal Access Token in Settings → GitHub. Public repos work without a token.')}
              </p>
            </div>

            <h4 className="font-semibold text-on-surface-variant mt-3 mb-1 text-sm">{t('docs.reference.steamFieldsTitle')}</h4>
            <FieldTable>
              <FieldRow field="steam.branch" type="string" required={false}>{t('docs.reference.steamBranch')}</FieldRow>
              <FieldRow field="steam.validate" type="boolean" required={false}>{t('docs.reference.steamValidate')}</FieldRow>
              <FieldRow field="steam.requiresLogin" type="boolean" required={false}>{t('docs.reference.steamRequiresLogin')}</FieldRow>
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
              <FieldRow field="backup.includePaths" type="list" required={false}>{t('docs.reference.backupIncludePaths')}</FieldRow>
            </FieldTable>
          </section>

          <section id="docs-howto" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-2">{t('docs.toc.howto')}</h2>

            <h3 className="font-bold text-on-surface mt-4">{t('docs.howto.h1')}</h3>
            <p className="font-body-md text-body-md text-on-surface-variant mb-2">{t('docs.howto.b1')}</p>

            <h3 className="font-bold text-on-surface mt-6">{t('docs.howto.h2')}</h3>
            <p className="font-body-md text-body-md text-on-surface-variant mb-2">{t('docs.howto.b2')}</p>

            <h3 className="font-bold text-on-surface mt-6">{t('docs.howto.h3')}</h3>
            <p className="font-body-md text-body-md text-on-surface-variant mb-2">{t('docs.howto.b3')}</p>
            <p className="font-body-md text-body-md text-on-surface-variant mb-2">{t('docs.howto.b3_extra')}</p>
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mt-4">
              <div>
                <span className="font-semibold text-xs text-on-surface-variant block mb-1">
                  {t('docs.howto.nonSteamExampleTitle', 'Example: Non-Steam Game (Hytale)')}
                </span>
                <CodeBlock example={nonSteamExample} />
              </div>
              <div>
                <span className="font-semibold text-xs text-on-surface-variant block mb-1">
                  {t('docs.howto.steamExampleTitle', 'Example: Steam Game (DayZ)')}
                </span>
                <CodeBlock example={steamGameExample} />
              </div>
              <div>
                <span className="font-semibold text-xs text-on-surface-variant block mb-1">
                  {t('docs.howto.botExampleTitle')}
                </span>
                <CodeBlock example={botExample} />
              </div>
              <div>
                <span className="font-semibold text-xs text-on-surface-variant block mb-1">
                  {t('docs.howto.voiceExampleTitle', 'Example: Open-Source Voice Server (Mumble)')}
                </span>
                <CodeBlock example={voiceServerExample} />
              </div>
              <div className="xl:col-span-2">
                <span className="font-semibold text-xs text-on-surface-variant block mb-1">
                  {t('docs.howto.steamLegacyExampleTitle')}
                </span>
                <CodeBlock example={steamLegacyExample} />
              </div>
            </div>

            <h3 className="font-bold text-on-surface mt-8">{t('docs.howto.h4')}</h3>
            <p className="font-body-md text-body-md text-on-surface-variant mb-2 whitespace-pre-line">{t('docs.howto.b4')}</p>

            <h3 className="font-bold text-on-surface mt-8">{t('docs.howto.h5')}</h3>
            <p className="font-body-md text-body-md text-on-surface-variant mb-2">{t('docs.howto.b5')}</p>
          </section>

          {/* v1.4.7+: Exec-Tab Sektion. Erklaert das Sicherheitsmodell und
              wie Blueprint-Autoren den Tab pro Server-Typ aktivieren. */}
          <section id="docs-exec" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-2">
              {t('docs.toc.exec', 'Container-Befehle (Exec-Tab)')}
            </h2>
            <p className="font-body-md text-body-md text-on-surface-variant mb-3">
              {t('docs.exec.intro', 'Mit dem Exec-Tab koennen authentifizierte User mit der Permission server.console.exec einmalige Befehle im MSM-verwalteten Container des Servers ausfuehren -- z. B. docker compose logs, npm run build, ls -la /data. Self-Hosted, aber die Befehle laufen NUR im MSM-Container dieses einen Servers, niemals auf dem Host oder in fremden Containern.')}
            </p>

            <h3 className="font-bold text-on-surface mt-4">{t('docs.exec.safetyTitle', 'Sicherheits-Modell')}</h3>
            <ul className="list-disc ml-6 font-body-md text-body-md text-on-surface-variant mb-3">
              <li>{t('docs.exec.safety1', 'argv statt Shell-String: Wir reichen das Befehls-Array 1:1 an docker exec weiter. Es gibt keine Shell dazwischen, also koennen Shell-Metazeichen (;, |, $(), Backticks) nicht eskaliert werden -- sie sind literaler Text.')}</li>
              <li>{t('docs.exec.safety2', 'Container-Name kommt nur aus container_name_for(server.id): Es gibt kein Request-Feld, mit dem der User den Zielcontainer beeinflussen kann. Host-Exec oder Container-eines-anderen-Servers ist strukturell ausgeschlossen.')}</li>
              <li>{t('docs.exec.safety3', 'Blueprint-Gate: runtime.enableExec muss true sein, sonst lehnt der Endpoint auch fuer Owner mit Permission ab (403). Default ist false.')}</li>
              <li>{t('docs.exec.safety4', 'Separate Permission server.console.exec (nicht console.write). Wer Exec bekommt, bekommt es explizit.')}</li>
              <li>{t('docs.exec.safety5', 'Limits: 1..32 Argumente, je max 4096 Zeichen. Timeout 1..600 Sekunden aus Blueprint. Output gedeckelt auf 256 KiB mit [truncated]-Marker.')}</li>
              <li>{t('docs.exec.safety6', 'Audit-Log: Server-ID, User-ID und argv werden geloggt. Output (kann sensible Daten enthalten) wird NICHT geloggt.')}</li>
            </ul>

            <h3 className="font-bold text-on-surface mt-6">{t('docs.exec.activateTitle', 'Exec-Tab pro Blueprint aktivieren')}</h3>
            <p className="font-body-md text-body-md text-on-surface-variant mb-2">
              {t('docs.exec.activateBody', 'Im Blueprint unter runtime:')}
            </p>
            <CodeBlock example={`"runtime": {
  "image": "node:22-bookworm-slim",
  "startup": "./start.sh",
  "enableExec": true,
  "execTimeoutSeconds": 120
}`} />
            <p className="font-body-md text-body-md text-on-surface-variant mt-3 mb-2">
              {t('docs.exec.useTitle', 'Im Panel:')}
            </p>
            <ol className="list-decimal ml-6 font-body-md text-body-md text-on-surface-variant mb-3">
              <li>{t('docs.exec.useStep1', 'Oeffne den Server-Detail-Dialog.')}</li>
              <li>{t('docs.exec.useStep2', 'Klicke auf den Tab "Exec" (neben "Konsole").')}</li>
              <li>{t('docs.exec.useStep3', 'Tippe einen Befehl ein, z. B. ls -la /data oder npm run build:api.')}</li>
              <li>{t('docs.exec.useStep4', 'Klicke "Ausfuehren". Output erscheint im Stream darunter.')}</li>
            </ol>
          </section>

          <section id="docs-updates" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-2">{t('docs.toc.updates')}</h2>
            <p className="font-body-md text-body-md text-on-surface-variant mb-4">{t('docs.updates.body')}</p>
            <h3 className="font-bold text-on-surface mb-2">{t('docs.updates.tableTitle')}</h3>
            <div className="overflow-x-auto my-4 border border-outline-variant/30 rounded-md">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-outline-variant/30 bg-surface-container-lowest text-on-surface-variant">
                  <tr>
                    <th className="px-4 py-2 font-medium">{t('docs.updates.colWhat')}</th>
                    <th className="px-4 py-2 font-medium">{t('docs.updates.colDetect')}</th>
                    <th className="px-4 py-2 font-medium">{t('docs.updates.colInstall')}</th>
                    <th className="px-4 py-2 font-medium">{t('docs.updates.colAutoRestart')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-outline-variant/10 text-on-surface">
                  <tr>
                    <td className="px-4 py-3 font-medium">{t('docs.updates.modsWhat')}</td>
                    <td className="px-4 py-3">{t('docs.updates.modsDetect')}</td>
                    <td className="px-4 py-3">{t('docs.updates.modsInstall')}</td>
                    <td className="px-4 py-3">{t('docs.updates.modsAutoRestart')}</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-3 font-medium">{t('docs.updates.gameWhat')}</td>
                    <td className="px-4 py-3">{t('docs.updates.gameDetect')}</td>
                    <td className="px-4 py-3">{t('docs.updates.gameInstall')}</td>
                    <td className="px-4 py-3">{t('docs.updates.gameAutoRestart')}</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-3 font-medium">{t('docs.updates.githubWhat')}</td>
                    <td className="px-4 py-3">{t('docs.updates.githubDetect')}</td>
                    <td className="px-4 py-3">{t('docs.updates.githubInstall')}</td>
                    <td className="px-4 py-3">{t('docs.updates.githubAutoRestart')}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <Alert type="info" title={t('docs.reference.steamBranch')}>
              {t('docs.updates.branchNote')}
            </Alert>
          </section>

          <section id="docs-backups" className="msm-card p-6 scroll-mt-20">
            <h2 className="font-headline text-headline-sm font-bold text-on-surface mb-2">{t('docs.toc.backups')}</h2>
            <p className="font-body-md text-body-md text-on-surface-variant mb-4 whitespace-pre-line">{t('docs.backups.body')}</p>
            <span className="font-semibold text-xs text-on-surface-variant block mb-1">{t('docs.backups.exampleTitle')}</span>
            <CodeBlock example={backupScopeExample} />
            <Alert type="info" title={t('docs.backups.restoreTitle')}>
              {t('docs.backups.restoreNote')}
            </Alert>
            <p className="font-body-md text-body-md text-on-surface-variant mt-4">{t('docs.backups.panelNote')}</p>
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
              <Alert type="warning" title={t('docs.troubleshooting.err9Title', 'Hytale OAuth or downloader access failed')}>
                {t('docs.troubleshooting.err9Body', 'On the first Hytale start, the console shows an OAuth link. Open it, enter the code, and use a Hytale account with server-download access. If you see 403 or Unauthorized, refresh the login flow or remove the local Hytale downloader credential file in that server directory.')}
              </Alert>
              <Alert type="warning" title={t('docs.troubleshooting.err10Title')}>
                {t('docs.troubleshooting.err10Body')}
              </Alert>
            </div>
          </section>

        </div>
      </div>
    </div>
  )
}
