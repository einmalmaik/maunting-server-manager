export type BlueprintCategory = 'steam_game' | 'non_steam_game' | 'voice_server' | 'bot'
export type BlueprintSourceType = 'steam' | 'http' | 'github' | 'dockerOnly' | 'custom' | 'manualUpload'
export type BlueprintUpdateStrategy = 'alwaysValidate' | 'checkBased' | 'none'
export type BlueprintPortName = 'game' | 'query' | 'rcon' | 'voice' | 'web' | 'custom'

export interface BlueprintDraft {
  version: 1
  meta: { id: string; name: string; category: BlueprintCategory; author?: string; description?: string }
  runtime: {
    image: string; workdir?: string; user?: string; env: Record<string, string>; startup: string
    startupProfiles: Array<{ whenFile: string; startup: string }>; ensureDirs: string[]; requiredFiles: string[]
    configPatches: Array<{ type: 'ini' | 'regex'; file: string; section?: string; key?: string; regex?: string; value: string }>
    stopGracePeriodSeconds: number; startupCheckSeconds: number; enableExec: boolean; execTimeoutSeconds: number
  }
  ports: Array<{ name: BlueprintPortName; protocol: 'tcp' | 'udp' }>
  source: {
    type: BlueprintSourceType; updateStrategy: BlueprintUpdateStrategy
    steam?: { appId: string; platform: 'linux' | 'windows'; compatibility?: 'native' | 'wine' | 'proton'; requiresLogin: boolean; branch?: string; validate: boolean }
    http?: { url: string; archiveType?: 'zip' | 'tar.gz' | 'tgz' | 'tar.xz' | 'txz' | 'tar.bz2' | 'tbz2' | '7z'; extractTo?: string; sha256?: string }
    github?: { repo: string; branch: string; subPath?: string; setupCommands: string[][] }
    manual?: { requiredFiles: string[]; instructions: string; instructionsUrl?: string }
  }
  mods?: {
    supportsMods: boolean; supportsSteamWorkshop: boolean; workshopAppId?: string; filterTags: string[]
    modInjection: 'none' | 'startupArg' | 'file'; modStartupArgumentFormat?: string; modListFilePath?: string
    modListContent: 'workshopIds' | 'postInstallTargetBasenames'
    postInstall: Array<{ operation: 'copy' | 'symlink'; source: string; target: string; required: boolean }>
  }
  backup?: { includePaths: string[] }
}

export interface BlueprintValidationIssue {
  path: string
  key: string
  values?: Record<string, string | number>
}
export type BlueprintCollision = 'native-blocked' | 'community-confirm' | 'none'

export function getBlueprintCollision(entries: Array<{ id: string; origin: 'native' | 'community' }>, id: string, editingExisting: boolean): BlueprintCollision {
  if (editingExisting) return 'none'
  const existing = entries.find(entry => entry.id === id)
  if (existing?.origin === 'native') return 'native-blocked'
  if (existing?.origin === 'community') return 'community-confirm'
  return 'none'
}

export function createBlueprintDraft(): BlueprintDraft {
  return {
    version: 1,
    meta: { id: '', name: '', category: 'steam_game', description: '' },
    runtime: {
      image: 'debian:bookworm-slim', startup: './start-server', env: {}, startupProfiles: [], ensureDirs: [],
      requiredFiles: [], configPatches: [], stopGracePeriodSeconds: 30, startupCheckSeconds: 5,
      enableExec: false, execTimeoutSeconds: 60,
    },
    ports: [{ name: 'game', protocol: 'udp' }],
    source: {
      type: 'steam', updateStrategy: 'checkBased',
      steam: { appId: '', platform: 'linux', compatibility: 'native', requiresLogin: false, validate: true },
    },
    mods: { supportsMods: false, supportsSteamWorkshop: false, filterTags: [], modInjection: 'none', modListContent: 'workshopIds', postInstall: [] },
  }
}

export function changeBlueprintSource(draft: BlueprintDraft, type: BlueprintSourceType): BlueprintDraft {
  const updateStrategy: BlueprintUpdateStrategy = type === 'steam' || type === 'http' || type === 'github' ? 'checkBased' : 'none'
  const source: BlueprintDraft['source'] = { type, updateStrategy }
  if (type === 'steam') source.steam = { appId: '', platform: 'linux', compatibility: 'native', requiresLogin: false, validate: true }
  if (type === 'http') source.http = { url: '' }
  if (type === 'github') source.github = { repo: '', branch: 'main', setupCommands: [] }
  if (type === 'manualUpload') source.manual = { requiredFiles: [], instructions: '' }
  return { ...draft, source }
}

function safeRelativePath(value: string): boolean {
  return Boolean(value) && !value.startsWith('/') && !value.startsWith('~') && !value.includes('\\') && value.split('/').every(part => part !== '' && part !== '.' && part !== '..') && !value.includes('\0')
}

export function validateBlueprintDraft(draft: BlueprintDraft): BlueprintValidationIssue[] {
  const issues: BlueprintValidationIssue[] = []
  const add = (path: string, key: string, values?: Record<string, string | number>) => issues.push({ path, key, values })
  if (!/^[a-z0-9_]{1,64}$/.test(draft.meta.id)) add('meta.id', 'blueprintBuilder.validation.metaId')
  if (!draft.meta.name.trim() || draft.meta.name.length > 128) add('meta.name', 'blueprintBuilder.validation.metaName')
  if (!/^[A-Za-z0-9._/:@-]{1,256}$/.test(draft.runtime.image)) add('runtime.image', 'blueprintBuilder.validation.image')
  if (!draft.runtime.startup.trim() || draft.runtime.startup.length > 2048) add('runtime.startup', 'blueprintBuilder.validation.startup')
  if (/[$`\n\r]/.test(draft.runtime.startup) || draft.runtime.startup.includes('&&') || draft.runtime.startup.includes('||')) add('runtime.startup', 'blueprintBuilder.validation.shellSyntax')
  if (draft.runtime.workdir && (!draft.runtime.workdir.startsWith('/') || draft.runtime.workdir.split('/').includes('..'))) add('runtime.workdir', 'blueprintBuilder.validation.workdir')
  if (draft.runtime.user && !/^[1-9]\d{0,9}:[1-9]\d{0,9}$/.test(draft.runtime.user)) add('runtime.user', 'blueprintBuilder.validation.user')
  if (draft.runtime.stopGracePeriodSeconds < 5 || draft.runtime.stopGracePeriodSeconds > 600) add('runtime.stopGracePeriodSeconds', 'blueprintBuilder.validation.stopGrace')
  if (draft.runtime.startupCheckSeconds < 0 || draft.runtime.startupCheckSeconds > 300) add('runtime.startupCheckSeconds', 'blueprintBuilder.validation.startCheck')
  if (draft.runtime.execTimeoutSeconds < 1 || draft.runtime.execTimeoutSeconds > 600) add('runtime.execTimeoutSeconds', 'blueprintBuilder.validation.execTimeout')
  Object.entries(draft.runtime.env).forEach(([key, value]) => {
    if (!/^[A-Z][A-Z0-9_]*$/.test(key)) add('runtime.env', 'blueprintBuilder.validation.envName', { name: key })
    if (/[$`]/.test(value) || value.includes('&&') || value.includes('||')) add('runtime.env', 'blueprintBuilder.validation.envValue', { name: key })
  })
  if (draft.runtime.ensureDirs.length > 16) add('runtime.ensureDirs', 'blueprintBuilder.validation.maxLines', { count: 16 })
  if (draft.runtime.requiredFiles.length > 16) add('runtime.requiredFiles', 'blueprintBuilder.validation.maxLines', { count: 16 })
  if (draft.runtime.startupProfiles.length > 8) add('runtime.startupProfiles', 'blueprintBuilder.validation.maxProfiles')
  draft.runtime.startupProfiles.forEach((profile, index) => {
    if (!safeRelativePath(profile.whenFile)) add(`runtime.startupProfiles.${index}`, 'blueprintBuilder.validation.markerFile')
    if (!profile.startup.trim() || /[$`\n\r]/.test(profile.startup)) add(`runtime.startupProfiles.${index}`, 'blueprintBuilder.validation.profileStartup')
  })
  draft.runtime.configPatches.forEach((patch, index) => {
    if (!safeRelativePath(patch.file)) add(`runtime.configPatches.${index}`, 'blueprintBuilder.validation.patchFile')
    if (!patch.value) add(`runtime.configPatches.${index}`, 'blueprintBuilder.validation.patchValue')
    if (patch.type === 'ini' && (!patch.section || !patch.key)) add(`runtime.configPatches.${index}`, 'blueprintBuilder.validation.patchIni')
    if (patch.type === 'regex' && !patch.regex) add(`runtime.configPatches.${index}`, 'blueprintBuilder.validation.patchRegex')
  })
  if (draft.ports.length > 32) add('ports', 'blueprintBuilder.validation.maxPorts')
  const seenPorts = new Set<string>()
  draft.ports.forEach((port, index) => {
    const key = `${port.name}/${port.protocol}`
    if (port.name !== 'custom' && seenPorts.has(key)) add(`ports.${index}`, 'blueprintBuilder.validation.duplicatePort')
    seenPorts.add(key)
  })
  if (draft.source.type === 'steam' && !/^\d{1,10}$/.test(draft.source.steam?.appId ?? '')) add('source.steam.appId', 'blueprintBuilder.validation.steamAppId')
  if (draft.source.type === 'steam' && draft.source.steam?.platform === 'windows' && !['wine', 'proton'].includes(draft.source.steam.compatibility ?? '')) add('source.steam.compatibility', 'blueprintBuilder.validation.windowsCompatibility')
  if (draft.source.type === 'http' && !(draft.source.http?.url ?? '').startsWith('https://')) add('source.http.url', 'blueprintBuilder.validation.httpsUrl')
  if (draft.source.type === 'github' && !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(draft.source.github?.repo ?? '')) add('source.github.repo', 'blueprintBuilder.validation.githubRepo')
  if (draft.source.http?.sha256 && !/^[0-9a-f]{64}$/.test(draft.source.http.sha256)) add('source.http.sha256', 'blueprintBuilder.validation.sha256')
  if (draft.source.github?.subPath && !safeRelativePath(draft.source.github.subPath)) add('source.github.subPath', 'blueprintBuilder.validation.relativePath')
  if ((draft.source.github?.setupCommands.length ?? 0) > 8) add('source.github.setupCommands', 'blueprintBuilder.validation.maxSetupCommands')
  draft.source.github?.setupCommands.forEach((command, index) => {
    if (command.length === 0 || command.length > 32 || command.some(argument => !argument.trim())) add(`source.github.setupCommands.${index}`, 'blueprintBuilder.validation.setupCommand')
  })
  if (draft.source.type === 'manualUpload') {
    if (!(draft.source.manual?.instructions ?? '').trim()) add('source.manual.instructions', 'blueprintBuilder.validation.manualInstructions')
    if (!(draft.source.manual?.requiredFiles.length)) add('source.manual.requiredFiles', 'blueprintBuilder.validation.manualFiles')
  }
  ;[...draft.runtime.ensureDirs, ...draft.runtime.requiredFiles, ...(draft.backup?.includePaths ?? [])].forEach((path) => {
    if (!safeRelativePath(path)) add('paths', 'blueprintBuilder.validation.unsafePath', { path: path || '—' })
  })
  if (draft.mods?.supportsSteamWorkshop && !/^\d{1,10}$/.test(draft.mods.workshopAppId ?? '')) add('mods.workshopAppId', 'blueprintBuilder.validation.workshopAppId')
  if (draft.mods?.modInjection === 'startupArg' && !(draft.mods.modStartupArgumentFormat ?? '').includes('{mods}')) add('mods.modStartupArgumentFormat', 'blueprintBuilder.validation.modArgument')
  if (draft.mods?.modInjection === 'file' && !safeRelativePath(draft.mods.modListFilePath ?? '')) add('mods.modListFilePath', 'blueprintBuilder.validation.modListPath')
  if (draft.mods?.modListContent === 'postInstallTargetBasenames' && !draft.mods.postInstall.length) add('mods.postInstall', 'blueprintBuilder.validation.postInstall')
  return issues
}

export function normalizeBlueprintDraft(draft: BlueprintDraft): BlueprintDraft {
  const clean = structuredClone(draft)
  const normalizeLines = (values: string[]) => values.map(value => value.trim()).filter(Boolean)
  clean.runtime.ensureDirs = normalizeLines(clean.runtime.ensureDirs)
  clean.runtime.requiredFiles = normalizeLines(clean.runtime.requiredFiles)
  clean.runtime.env = Object.fromEntries(Object.entries(clean.runtime.env).map(([key, value]) => [key.trim(), value]))
  if (!clean.meta.author) delete clean.meta.author
  if (!clean.meta.description) delete clean.meta.description
  if (!clean.runtime.workdir) delete clean.runtime.workdir
  if (!clean.runtime.user) delete clean.runtime.user
  if (clean.source.steam && !clean.source.steam.branch) delete clean.source.steam.branch
  if (clean.source.http && !clean.source.http.archiveType) delete clean.source.http.archiveType
  if (clean.source.http && !clean.source.http.extractTo) delete clean.source.http.extractTo
  if (clean.source.http && !clean.source.http.sha256) delete clean.source.http.sha256
  if (clean.source.github && !clean.source.github.subPath) delete clean.source.github.subPath
  if (clean.source.github) clean.source.github.setupCommands = clean.source.github.setupCommands.map(command => command.map(argument => argument.trim()))
  if (clean.source.manual && !clean.source.manual.instructionsUrl) delete clean.source.manual.instructionsUrl
  if (clean.source.manual) clean.source.manual.requiredFiles = normalizeLines(clean.source.manual.requiredFiles)
  if (clean.mods && !clean.mods.workshopAppId) delete clean.mods.workshopAppId
  if (clean.mods) clean.mods.filterTags = normalizeLines(clean.mods.filterTags)
  if (clean.mods && clean.mods.modInjection !== 'startupArg') delete clean.mods.modStartupArgumentFormat
  if (clean.mods && clean.mods.modInjection !== 'file') delete clean.mods.modListFilePath
  if (clean.mods) {
    const isDefaultModsBlock = !clean.mods.supportsMods
      && !clean.mods.supportsSteamWorkshop
      && clean.mods.filterTags.length === 0
      && clean.mods.modInjection === 'none'
      && clean.mods.modListContent === 'workshopIds'
      && clean.mods.postInstall.length === 0
      && !clean.mods.workshopAppId
      && !clean.mods.modStartupArgumentFormat
      && !clean.mods.modListFilePath
    if (isDefaultModsBlock) delete clean.mods
  }
  if (clean.backup) clean.backup.includePaths = normalizeLines(clean.backup.includePaths)
  if (!clean.backup?.includePaths.length) delete clean.backup
  return clean
}
