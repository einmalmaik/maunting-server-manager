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

  it('surfaces the real install_error text instead of the generic hint', () => {
    // Regressions-Test: Vorher zeigte das UI nur den generischen
    // 'mods.statusErrorHint' ("Installation fehlgeschlagen"), obwohl das
    // Backend den konkreten Fehler in install_error ablegt (SteamCMD-Output,
    // fehlender Steam-Account, Netzwerkfehler). User konnte so nie selbst
    // diagnostizieren, warum ein Reinstall scheitert. Mit dem Fix wird der
    // echte Fehlertext bis 240 Zeichen mit angehaengt.
    const longError =
      'SteamCMD meldet App-State 0x202 nach dem Update-Job. Moegliche Ursachen (nicht verifiziert): unvollstaendige App-Konfiguration, Plattenplatz/Quota, Berechtigungen oder paralleler Zugriff auf Install-/Cache-Daten.'
    const presentation = getModInstallPresentation(
      { install_status: 'error', install_error: longError },
      t,
    )

    expect(presentation.kind).toBe('error')
    expect(presentation.label).toBe('mods.statusError')
    // Generischer Hint bleibt Prefix, konkreter Fehlertext folgt.
    expect(presentation.detail).toContain('mods.statusErrorHint')
    expect(presentation.detail).toContain('0x202')
    // Nicht endlos lange Fehler rendern — auf 240 Zeichen begrenzt.
    expect(presentation.detail?.length ?? 0).toBeLessThanOrEqual(240 + 'mods.statusErrorHint'.length + 4)
  })

  it('falls back to the generic error hint when install_error is missing', () => {
    const presentation = getModInstallPresentation({ install_status: 'error' }, t)
    expect(presentation.kind).toBe('error')
    expect(presentation.detail).toBe('mods.statusErrorHint')
  })
})
