import { describe, expect, it } from 'vitest'
import { getModInstallPresentation, hasActiveModInstall } from './modInstallStatus'

const t = (key: string, options?: Record<string, unknown>) =>
  options?.count ? `${key}:${options.count}` : key

describe('modInstallStatus', () => {
  it('marks pending and installing mods as active jobs', () => {
    expect(hasActiveModInstall({ install_status: 'pending' })).toBe(true)
    expect(hasActiveModInstall({ install_status: 'installing' })).toBe(true)
    expect(hasActiveModInstall({ install_status: 'installed' })).toBe(false)
  })

  it('builds a running download presentation with remaining-time text', () => {
    const presentation = getModInstallPresentation(
      { install_status: 'installing', install_action: 'update', install_progress: 42, install_eta_seconds: 125 },
      t,
    )

    expect(presentation.kind).toBe('info')
    expect(presentation.label).toBe('mods.statusUpdating')
    expect(presentation.detail).toBe('mods.etaMinutes:3')
    expect(presentation.progress).toBe(42)
    expect(presentation.showProgress).toBe(true)
  })

  it('keeps progress inside the visible active range', () => {
    expect(getModInstallPresentation({ install_status: 'installing', install_progress: 130 }, t).progress).toBe(99)
    expect(getModInstallPresentation({ install_status: 'installing', install_progress: -4 }, t).progress).toBe(0)
  })

  it('shows update status when no install job is active', () => {
    expect(getModInstallPresentation({ install_status: 'installed', update_status: 'outdated' }, t).label).toBe(
      'mods.statusOutdated',
    )
    expect(
      getModInstallPresentation(
        { install_status: 'installed', update_status: 'unknown', update_reason: 'steam_api_key_missing' },
        t,
      ).detail,
    ).toBe('mods.statusUnknownNoSteamKeyHint')
  })
})
