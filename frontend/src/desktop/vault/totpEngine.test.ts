import { describe, it, expect } from 'vitest'
import {
  base32Decode,
  generateTotpCode,
  getTotpSecondsRemaining,
  parseOtpauthUri,
} from './totpEngine'

describe('totpEngine', () => {
  it('decodes Base32 strings accurately', () => {
    // RFC 4648 test vectors
    // "JBSWY3DPEHPK3PXP" is standard test key "Hello!\xde\xad\xbe\xef"
    const decoded = base32Decode('JBSWY3DPEHPK3PXP')
    expect(decoded.length).toBe(10)
  })

  it('generates 6-digit TOTP codes for standard secret', async () => {
    const secret = 'JBSWY3DPEHPK3PXP'
    const code = await generateTotpCode(secret, 30, 1600000000000)
    expect(code).toMatch(/^\d{6}$/)
  })

  it('calculates remaining seconds within 30s window', () => {
    const rem = getTotpSecondsRemaining(30)
    expect(rem).toBeGreaterThanOrEqual(1)
    expect(rem).toBeLessThanOrEqual(30)
  })

  it('parses otpauth URIs and extracts secret, issuer, and label', () => {
    const uri = 'otpauth://totp/GitHub:einmalmaik?secret=JBSWY3DPEHPK3PXP&issuer=GitHub'
    const parsed = parseOtpauthUri(uri)
    expect(parsed).not.toBeNull()
    expect(parsed?.secret).toBe('JBSWY3DPEHPK3PXP')
    expect(parsed?.issuer).toBe('GitHub')
    expect(parsed?.label).toBe('einmalmaik')
  })

  it('handles raw secret input directly', () => {
    const parsed = parseOtpauthUri('JBSWY3DPEHPK3PXP')
    expect(parsed).not.toBeNull()
    expect(parsed?.secret).toBe('JBSWY3DPEHPK3PXP')
  })
})
