import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import { passkeyBestaetigen } from './passkeyService'

const OPTIONEN = { challenge: 'YWJj', rpId: 'localhost', allowCredentials: [] }

describe('passkeyService — Fehlertexte', () => {
  afterEach(async () => {
    vi.unstubAllGlobals()
    await i18n.changeLanguage('de')
  })

  // Bis 09/2026 standen die Meldungen fest auf Deutsch, auch in der englischen Oberfläche.
  it('meldet fehlende Unterstützung in der Sprache der Oberfläche', async () => {
    await i18n.changeLanguage('en')
    await expect(passkeyBestaetigen(OPTIONEN)).rejects.toThrow(i18n.t('auth.passkeyErrors.unsupported'))
    expect(i18n.t('auth.passkeyErrors.unsupported')).not.toMatch(/unterstützt/)
  })

  it('meldet einen Abbruch in der Sprache der Oberfläche', async () => {
    await i18n.changeLanguage('en')
    vi.stubGlobal('PublicKeyCredential', function PublicKeyCredential() {})
    vi.stubGlobal('navigator', {
      ...navigator,
      credentials: { get: vi.fn().mockRejectedValue(Object.assign(new Error('x'), { name: 'NotAllowedError' })) },
    })
    await expect(passkeyBestaetigen(OPTIONEN)).rejects.toThrow(i18n.t('auth.passkeyErrors.cancelled'))
    expect(i18n.t('auth.passkeyErrors.cancelled')).not.toMatch(/abgebrochen/)
  })
})
