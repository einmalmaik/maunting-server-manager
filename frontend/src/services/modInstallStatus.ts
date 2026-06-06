export type ModInstallStatus = 'pending' | 'installing' | 'installed' | 'error'
export type ModInstallAction = 'install' | 'update' | string
export type ModUpdateStatus = 'missing' | 'outdated' | 'up_to_date' | 'unknown' | 'failed'

export interface ModInstallState {
  install_status?: ModInstallStatus | string | null
  install_action?: ModInstallAction | null
  install_progress?: number | null
  install_eta_seconds?: number | null
  update_status?: ModUpdateStatus | string | null
  update_reason?: string | null
}

type Translate = (key: string, options?: Record<string, unknown>) => string

export interface ModInstallPresentation {
  kind: 'success' | 'warning' | 'info' | 'error'
  label: string
  detail: string | null
  progress: number | null
  showProgress: boolean
}

export function hasActiveModInstall(mod: ModInstallState): boolean {
  return mod.install_status === 'pending' || mod.install_status === 'installing'
}

export function getModInstallPresentation(mod: ModInstallState, t: Translate): ModInstallPresentation {
  const action = mod.install_action === 'update' ? 'update' : 'install'
  const progress = clampProgress(mod.install_progress)
  const rawError = typeof mod.install_error === 'string' ? mod.install_error.trim() : ''
  // Bis zu 240 Zeichen echten Fehlertext zeigen, damit der User bei
  // "Installation fehlgeschlagen" sehen kann, was wirklich passiert ist
  // (z. B. SteamCMD 0x202, fehlender Steam-Account, Netzwerkfehler). Fallback
  // auf den generischen Hint, wenn das Backend keinen Fehlertext geliefert hat.
  const errorDetail = rawError
    ? `${t('mods.statusErrorHint')} — ${rawError.slice(0, 240)}`
    : t('mods.statusErrorHint')

  if (mod.install_status === 'pending') {
    return {
      kind: 'warning',
      label: t(action === 'update' ? 'mods.statusUpdatePending' : 'mods.statusPending'),
      detail: t('mods.statusPendingHint'),
      progress: 0,
      showProgress: false,
    }
  }

  if (mod.install_status === 'installing') {
    return {
      kind: 'info',
      label: t(action === 'update' ? 'mods.statusUpdating' : 'mods.statusInstalling'),
      detail: formatEta(mod.install_eta_seconds, t),
      progress,
      showProgress: true,
    }
  }

  if (mod.install_status === 'error') {
    return {
      kind: 'error',
      label: t('mods.statusError'),
      detail: errorDetail,
      progress: null,
      showProgress: false,
    }
  }

  if (mod.update_status === 'missing') {
    return {
      kind: 'warning',
      label: t('mods.statusMissing'),
      detail: t('mods.statusMissingHint'),
      progress: null,
      showProgress: false,
    }
  }

  if (mod.update_status === 'outdated') {
    return {
      kind: 'warning',
      label: t('mods.statusOutdated'),
      detail: t('mods.statusOutdatedHint'),
      progress: null,
      showProgress: false,
    }
  }

  if (mod.update_status === 'unknown') {
    return {
      kind: 'info',
      label: t('mods.statusUnknown'),
      detail: t(
        mod.update_reason === 'steam_api_key_missing'
          ? 'mods.statusUnknownNoSteamKeyHint'
          : 'mods.statusUnknownHint',
      ),
      progress: null,
      showProgress: false,
    }
  }

  if (mod.update_status === 'failed') {
    return {
      kind: 'error',
      label: t('mods.statusFailed'),
      detail: t('mods.statusFailedHint'),
      progress: null,
      showProgress: false,
    }
  }

  return {
    kind: 'success',
    label: t('mods.statusInstalled'),
    detail: null,
    progress: 100,
    showProgress: false,
  }
}

function clampProgress(value: number | null | undefined): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 0
  return Math.max(0, Math.min(99, Math.round(value)))
}

function formatEta(seconds: number | null | undefined, t: Translate): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) {
    return t('mods.etaCalculating')
  }
  if (seconds <= 0) {
    return t('mods.etaAlmostDone')
  }
  if (seconds < 60) {
    return t('mods.etaLessThanMinute')
  }
  if (seconds < 3600) {
    return t('mods.etaMinutes', { count: Math.ceil(seconds / 60) })
  }
  return t('mods.etaHours', { count: Math.ceil(seconds / 3600) })
}
