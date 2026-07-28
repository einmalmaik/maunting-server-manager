export type BlueprintCategory = 'steam_game' | 'non_steam_game' | 'voice_server' | 'bot'
export type BlueprintSourceType = 'steam' | 'http' | 'github' | 'dockerOnly' | 'custom' | 'manualUpload'
export type BlueprintUpdateStrategy = 'alwaysValidate' | 'checkBased' | 'none'
export type BlueprintPortName = 'game' | 'query' | 'rcon' | 'voice' | 'web' | 'custom'
export type GuardianApplicationProbeType = 'tcp' | 'http-ping' | 'minecraft-status' | 'minecraft-query' | 'source-query'
export type GuardianRecoveryAction = 'restart' | 'graceful_restart' | 'clear_declared_lock_files' | 'quarantine'
export type GuardianDiagnosticParser = 'linux-oom' | 'java-stacktrace' | 'nodejs-stacktrace' | 'port-conflict' | 'missing-runtime' | 'corrupted-config' | 'startup-pattern'
export type GuardianBuiltinRedactor = 'discord_token' | 'api_key' | 'authorization_header' | 'database_url' | 'jwt'

interface GuardianThresholds {
  id: string
  interval: string
  failure_threshold: number
  success_threshold: number
  required_for_startup: boolean
  required_for_verification: boolean
}

export interface BlueprintHealthProcess extends GuardianThresholds {
  required: boolean
}

export interface BlueprintHealthPort extends GuardianThresholds {
  protocol: 'tcp' | 'udp'
  port: string
  timeout: string
}

export interface BlueprintHealthApplication extends GuardianThresholds {
  type: GuardianApplicationProbeType | ''
  timeout: string
  port?: string
  path?: string
  expected_statuses: number[]
  follow_redirects: boolean
  max_response_bytes: number
}

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
  health?: {
    process?: BlueprintHealthProcess
    port?: BlueprintHealthPort
    application?: BlueprintHealthApplication
    startup?: {
      grace_period_seconds: number
      timeout_seconds: number
      success_patterns: string[]
      failure_patterns: string[]
    }
  }
  logs?: {
    sources: string[]
    redact: string[]
    max_tail_bytes: number
  }
  diagnostics?: {
    parsers: GuardianDiagnosticParser[]
  }
  recovery?: {
    policies: Array<{ match: string; action: GuardianRecoveryAction | '' }>
    safe_lock_files?: Array<{ path: string; reason: string }>
    max_attempts?: number
    attempt_window_seconds?: number
    cooldown_seconds?: number
    verification?: {
      minimum_healthy_duration_seconds?: number
      required_consecutive_successes?: number
      verification_timeout_seconds?: number
    }
  }
  backups?: {
    before_risky_action: boolean
    protected_paths: string[]
  }
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
    health: {
      process: {
        required: true, id: 'process', interval: '15s', failure_threshold: 1, success_threshold: 1,
        required_for_startup: true, required_for_verification: true,
      },
      port: {
        protocol: 'tcp', port: '{{SERVER_PORT}}', timeout: '3s', id: 'port', interval: '30s',
        failure_threshold: 3, success_threshold: 1, required_for_startup: false, required_for_verification: true,
      },
      application: {
        type: '', id: 'application', interval: '30s', timeout: '3s', failure_threshold: 3,
        success_threshold: 1, expected_statuses: [200], follow_redirects: false, max_response_bytes: 4096,
        required_for_startup: false, required_for_verification: true,
      },
      startup: { grace_period_seconds: 30, timeout_seconds: 300, success_patterns: [], failure_patterns: [] },
    },
    logs: {
      sources: [],
      redact: [],
      max_tail_bytes: 65_536,
    },
    diagnostics: {
      parsers: [],
    },
    recovery: {
      policies: [],
      safe_lock_files: [],
      max_attempts: 3,
      attempt_window_seconds: 1800,
      cooldown_seconds: 300,
      verification: {
        minimum_healthy_duration_seconds: 30,
        required_consecutive_successes: 3,
        verification_timeout_seconds: 180,
      },
    },
    backups: {
      before_risky_action: true,
      protected_paths: [],
    },
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

function validGuardianRegex(value: string): boolean {
  if (!value || value.length > 256 || /[\0\n\r]/.test(value)) return false
  if (/\\[1-9]|\(\?[=!<]|\(\?P|\(\?>|\(\?\(/.test(value)) return false
  if (/(?:\*|\+|\{\d+(?:,\d*)?\})\s*(?:\*|\+|\{)/.test(value)) return false
  if (/\([^)]*(?:\*|\+|\{\d+(?:,\d*)?\})[^)]*\)(?:\*|\+|\{)/.test(value)) return false
  try {
    new RegExp(value)
    return true
  } catch {
    return false
  }
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

  // Autopilot/Guardian Validation
  if (draft.health?.port && !draft.health.port.port.trim()) add('health.port.port', 'blueprintBuilder.validation.healthPortEmpty')
  if (draft.health?.port && !draft.health.port.timeout.trim()) add('health.port.timeout', 'blueprintBuilder.validation.healthTimeoutEmpty')
  if (draft.health?.application?.type) {
    const supportedTypes: GuardianApplicationProbeType[] = ['tcp', 'http-ping', 'minecraft-status', 'minecraft-query', 'source-query']
    if (!supportedTypes.includes(draft.health.application.type)) {
      add('health.application.type', 'blueprintBuilder.validation.healthAppTypeUnsupported')
    } else if (draft.health.application.type === 'http-ping') {
      const path = draft.health.application.path?.trim() ?? ''
      if (!path.startsWith('/') || path.startsWith('//') || path.includes('://') || path.includes('#')) {
        add('health.application.path', 'blueprintBuilder.validation.healthAppPathInvalid')
      }
    } else if (draft.health.application.path !== undefined) {
      add('health.application.path', 'blueprintBuilder.validation.healthAppPathOnlyHttp')
    }
  }
  const durationPattern = /^\d+(?:\.\d+)?(?:ms|s|m)$/
  const healthChecks = [draft.health?.process, draft.health?.port, draft.health?.application].filter(Boolean) as GuardianThresholds[]
  const checkIds = new Set<string>()
  healthChecks.forEach((check) => {
    if (!/^[a-z][a-z0-9_-]{0,63}$/.test(check.id)) add('health', 'blueprintBuilder.validation.healthCheckId')
    if (checkIds.has(check.id)) add('health', 'blueprintBuilder.validation.healthCheckIdDuplicate')
    checkIds.add(check.id)
    if (!durationPattern.test(check.interval)) add('health', 'blueprintBuilder.validation.healthDuration')
    if (check.failure_threshold < 1 || check.failure_threshold > 20 || check.success_threshold < 1 || check.success_threshold > 20) {
      add('health', 'blueprintBuilder.validation.healthThreshold')
    }
  })
  if (draft.health?.port && !durationPattern.test(draft.health.port.timeout)) add('health.port.timeout', 'blueprintBuilder.validation.healthDuration')
  if (draft.health?.application) {
    if (!durationPattern.test(draft.health.application.timeout)) add('health.application.timeout', 'blueprintBuilder.validation.healthDuration')
    if (draft.health.application.expected_statuses.length < 1 || draft.health.application.expected_statuses.length > 16 || draft.health.application.expected_statuses.some(status => !Number.isInteger(status) || status < 100 || status > 599)) add('health.application.expected_statuses', 'blueprintBuilder.validation.healthStatuses')
    if (draft.health.application.follow_redirects) add('health.application.follow_redirects', 'blueprintBuilder.validation.healthRedirects')
    if (draft.health.application.max_response_bytes < 1 || draft.health.application.max_response_bytes > 1_048_576) add('health.application.max_response_bytes', 'blueprintBuilder.validation.healthResponseBytes')
    if ((draft.health.application.path?.length ?? 0) > 512) add('health.application.path', 'blueprintBuilder.validation.healthAppPathLength')
    if (draft.health.application.port !== undefined && !draft.health.application.port.trim()) add('health.application.port', 'blueprintBuilder.validation.healthPortEmpty')
  }
  if (draft.health?.startup) {
    if (draft.health.startup.grace_period_seconds < 0 || draft.health.startup.grace_period_seconds > 600 || draft.health.startup.timeout_seconds < 1 || draft.health.startup.timeout_seconds > 3600 || draft.health.startup.timeout_seconds <= draft.health.startup.grace_period_seconds) {
      add('health.startup.timeout_seconds', 'blueprintBuilder.validation.healthStartupTimeout')
    }
    if (draft.health.startup.success_patterns.length > 16 || draft.health.startup.failure_patterns.length > 16) add('health.startup', 'blueprintBuilder.validation.guardianArrayLimit')
    if ([...draft.health.startup.success_patterns, ...draft.health.startup.failure_patterns].some(pattern => !validGuardianRegex(pattern))) add('health.startup', 'blueprintBuilder.validation.guardianRegex')
  }
  if (draft.logs && (draft.logs.max_tail_bytes < 1024 || draft.logs.max_tail_bytes > 1_048_576)) add('logs.max_tail_bytes', 'blueprintBuilder.validation.logsTailBytes')
  if (draft.logs) {
    if (draft.logs.sources.length > 16 || draft.logs.redact.length > 32) add('logs', 'blueprintBuilder.validation.guardianArrayLimit')
    draft.logs.sources.forEach(source => {
      const parts = source.split('/')
      const validSource = source === 'stdout' || (
        safeRelativePath(source)
        && !source.includes('**')
        && parts.every((part, index) => !/[?\[]/.test(part) && (!part.includes('*') || (index === parts.length - 1 && part.split('*').length === 2)))
      )
      if (!validSource) add('logs.sources', 'blueprintBuilder.validation.logsSource')
    })
    const builtinRedactors: GuardianBuiltinRedactor[] = ['discord_token', 'api_key', 'authorization_header', 'database_url', 'jwt']
    draft.logs.redact.forEach(redactor => {
      if (!builtinRedactors.includes(redactor as GuardianBuiltinRedactor) && (!redactor.startsWith('regex:') || !validGuardianRegex(redactor.slice(6)))) {
        add('logs.redact', 'blueprintBuilder.validation.logsRedactor')
      }
    })
  }
  if (draft.diagnostics) {
    const supportedParsers: GuardianDiagnosticParser[] = ['linux-oom', 'java-stacktrace', 'nodejs-stacktrace', 'port-conflict', 'missing-runtime', 'corrupted-config', 'startup-pattern']
    if (draft.diagnostics.parsers.length > 16 || draft.diagnostics.parsers.some(parser => !supportedParsers.includes(parser))) {
      add('diagnostics.parsers', 'blueprintBuilder.validation.diagnosticsParser')
    }
  }
  if (draft.recovery?.policies) {
    const supportedActions: GuardianRecoveryAction[] = ['restart', 'graceful_restart', 'clear_declared_lock_files', 'quarantine']
    const supportedMatches = new Set([
      'process_not_running', 'tcp_connect_failed', 'udp_mapping_missing', 'http_redirect_rejected',
      'http_response_too_large', 'http_unexpected_status', 'http_request_failed',
      'minecraft_query_failed', 'minecraft_status_failed', 'source_query_failed', 'linux-oom',
      'port-conflict', 'java-stacktrace', 'nodejs-stacktrace', 'missing-runtime',
      'corrupted-config', 'startup-pattern', 'probe_failed',
    ])
    if (draft.recovery.policies.length > 16) add('recovery.policies', 'blueprintBuilder.validation.guardianArrayLimit')
    draft.recovery.policies.forEach((policy, index) => {
      if (!/^[a-z][a-z0-9_-]{0,63}$/.test(policy.match)) add(`recovery.policies.${index}`, 'blueprintBuilder.validation.recoveryMatchEmpty')
      else if (!supportedMatches.has(policy.match)) add(`recovery.policies.${index}`, 'blueprintBuilder.validation.recoveryMatchUnsupported')
      if (!policy.action.trim()) add(`recovery.policies.${index}`, 'blueprintBuilder.validation.recoveryActionEmpty')
      else if (!supportedActions.includes(policy.action as GuardianRecoveryAction)) add(`recovery.policies.${index}`, 'blueprintBuilder.validation.recoveryActionUnsupported')
    })
    if (draft.recovery.policies.some(policy => policy.action === 'clear_declared_lock_files') && !draft.recovery.safe_lock_files?.length) {
      add('recovery.safe_lock_files', 'blueprintBuilder.validation.recoveryLockFilesRequired')
    }
  }
  if (draft.recovery?.safe_lock_files) {
    if (draft.recovery.safe_lock_files.length > 32) add('recovery.safe_lock_files', 'blueprintBuilder.validation.guardianArrayLimit')
    const seenLockPaths = new Set<string>()
    draft.recovery.safe_lock_files.forEach((entry, index) => {
      if (!safeRelativePath(entry.path) || /[*?\[]/.test(entry.path)) add(`recovery.safe_lock_files.${index}`, 'blueprintBuilder.validation.recoveryLockPath')
      if (!entry.reason.trim() || entry.reason.length > 256 || entry.path.length > 256) add(`recovery.safe_lock_files.${index}`, 'blueprintBuilder.validation.recoveryLockReason')
      if (seenLockPaths.has(entry.path)) add(`recovery.safe_lock_files.${index}`, 'blueprintBuilder.validation.recoveryLockDuplicate')
      seenLockPaths.add(entry.path)
    })
  }
  if (draft.recovery) {
    if ((draft.recovery.max_attempts ?? 3) < 1 || (draft.recovery.max_attempts ?? 3) > 10) add('recovery.max_attempts', 'blueprintBuilder.validation.recoveryBounds')
    if ((draft.recovery.attempt_window_seconds ?? 1800) < 60 || (draft.recovery.attempt_window_seconds ?? 1800) > 86_400) add('recovery.attempt_window_seconds', 'blueprintBuilder.validation.recoveryBounds')
    if ((draft.recovery.cooldown_seconds ?? 300) < 1 || (draft.recovery.cooldown_seconds ?? 300) > 3600) add('recovery.cooldown_seconds', 'blueprintBuilder.validation.recoveryBounds')
    const verification = draft.recovery.verification
    if (verification && (
      (verification.minimum_healthy_duration_seconds ?? 30) < 0 || (verification.minimum_healthy_duration_seconds ?? 30) > 600
      || (verification.required_consecutive_successes ?? 3) < 1 || (verification.required_consecutive_successes ?? 3) > 20
      || (verification.verification_timeout_seconds ?? 180) < 5 || (verification.verification_timeout_seconds ?? 180) > 3600
    )) add('recovery.verification', 'blueprintBuilder.validation.recoveryBounds')
  }
  if (draft.backups?.protected_paths) {
    draft.backups.protected_paths.forEach((path) => {
      const strippedPath = path.endsWith('/') ? path.slice(0, -1) : path
      if (strippedPath && (!safeRelativePath(strippedPath) || /[*?\[]/.test(strippedPath))) add('paths', 'blueprintBuilder.validation.unsafePath', { path: path || '—' })
    })
  }

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

  // Autopilot/Guardian normalization
  if (clean.health) {
    if (clean.health.application) {
      if (!clean.health.application.port?.trim()) delete clean.health.application.port
      if (clean.health.application.type !== 'http-ping') delete clean.health.application.path
    }
    if (clean.health.startup) {
      clean.health.startup.success_patterns = normalizeLines(clean.health.startup.success_patterns)
      clean.health.startup.failure_patterns = normalizeLines(clean.health.startup.failure_patterns)
    }
    const hasProcess = Boolean(clean.health.process)
    const hasPort = Boolean(clean.health.port?.port?.trim())
    const hasApp = Boolean(clean.health.application?.type?.trim())
    const hasStartup = Boolean(
      clean.health.startup?.success_patterns?.length
      || clean.health.startup?.failure_patterns?.length
      || clean.health.startup?.grace_period_seconds !== 30
      || clean.health.startup?.timeout_seconds !== 300
    )

    if (!hasProcess) delete clean.health.process
    if (!hasPort) delete clean.health.port
    if (!hasApp) delete clean.health.application
    if (!hasStartup) delete clean.health.startup

    if (!hasProcess && !hasPort && !hasApp && !hasStartup) {
      delete clean.health
    }
  }

  if (clean.logs) {
    clean.logs.sources = normalizeLines(clean.logs.sources)
    clean.logs.redact = normalizeLines(clean.logs.redact)
    if (clean.logs.sources.length === 0 && clean.logs.redact.length === 0 && clean.logs.max_tail_bytes === 65_536) {
      delete clean.logs
    }
  }

  if (clean.diagnostics) {
    clean.diagnostics.parsers = clean.diagnostics.parsers.map(value => value.trim() as GuardianDiagnosticParser).filter(Boolean)
    if (clean.diagnostics.parsers.length === 0) {
      delete clean.diagnostics
    }
  }

  if (clean.recovery) {
    clean.recovery.policies = clean.recovery.policies
      .map(p => ({ match: p.match.trim(), action: p.action.trim() as GuardianRecoveryAction | '' }))
      .filter(p => p.match && p.action)
    if (clean.recovery.safe_lock_files) {
      clean.recovery.safe_lock_files = clean.recovery.safe_lock_files
        .map(entry => ({ path: entry.path.trim(), reason: entry.reason.trim() }))
        .filter(entry => entry.path)
      if (clean.recovery.safe_lock_files.length === 0) {
        delete clean.recovery.safe_lock_files
      }
    }
    const hasPolicies = clean.recovery.policies.length > 0
    const hasLockFiles = clean.recovery.safe_lock_files && clean.recovery.safe_lock_files.length > 0
    const hasOtherKeys = clean.recovery.max_attempts !== undefined ||
                         clean.recovery.attempt_window_seconds !== undefined ||
                         clean.recovery.cooldown_seconds !== undefined ||
                         clean.recovery.verification !== undefined
    if (!hasPolicies && !hasLockFiles && !hasOtherKeys) {
      delete clean.recovery
    }
  }

  if (clean.backups) {
    clean.backups.protected_paths = normalizeLines(clean.backups.protected_paths)
    if (clean.backups.protected_paths.length === 0 && clean.backups.before_risky_action) {
      delete clean.backups
    }
  }

  return clean
}
