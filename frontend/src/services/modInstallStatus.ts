export type ModInstallStatus = 'pending' | 'installing' | 'installed' | 'error'
export type ModInstallAction = 'install' | 'update' | string

export interface ModInstallState {
  install_status?: ModInstallStatus | string | null
  install_action?: ModInstallAction | null
  install_progress?: number | null
  install_eta_seconds?: number | null
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
      detail: t('mods.statusErrorHint'),
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
