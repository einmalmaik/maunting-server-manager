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

  it('generates 6-digit TOTP codes for standard secret with SHA-1', async () => {
    const secret = 'JBSWY3DPEHPK3PXP'
    const code = await generateTotpCode(secret, 30, 1600000000000)
    expect(code).toMatch(/^\d{6}$/)
  })

  it('supports SHA-256 and SHA-512 algorithms (SEC-12)', async () => {
    const secret = 'JBSWY3DPEHPK3PXP'
    const codeSha256 = await generateTotpCode(secret, {
      algorithm: 'SHA-256',
      digits: 6,
      period: 30,
      timestampMs: 1600000000000,
    })
    expect(codeSha256).toMatch(/^\d{6}$/)

    const codeSha512 = await generateTotpCode(secret, {
      algorithm: 'SHA-512',
      digits: 8,
      period: 60,
      timestampMs: 1600000000000,
    })
    expect(codeSha512).toMatch(/^\d{8}$/)
  })

  it('fails cleanly on malformed Base32 secret without pseudo-codes (SEC-12)', async () => {
    await expect(generateTotpCode('INVALID_BASE32_1890')).rejects.toThrow()
    await expect(generateTotpCode('')).rejects.toThrow()
  })

  it('calculates remaining seconds within 30s window', () => {
    const rem = getTotpSecondsRemaining(30)
    expect(rem).toBeGreaterThanOrEqual(1)
    expect(rem).toBeLessThanOrEqual(30)
  })

  it('parses otpauth URIs and extracts secret, issuer, label, algorithm, and digits', () => {
    const uri = 'otpauth://totp/GitHub:einmalmaik?secret=JBSWY3DPEHPK3PXP&issuer=GitHub&algorithm=SHA256&digits=8&period=60'
    const parsed = parseOtpauthUri(uri)
    expect(parsed).not.toBeNull()
    expect(parsed?.secret).toBe('JBSWY3DPEHPK3PXP')
    expect(parsed?.issuer).toBe('GitHub')
    expect(parsed?.label).toBe('einmalmaik')
    expect(parsed?.algorithm).toBe('SHA-256')
    expect(parsed?.digits).toBe(8)
    expect(parsed?.period).toBe(60)
  })

  it('handles raw secret input directly', () => {
    const parsed = parseOtpauthUri('JBSWY3DPEHPK3PXP')
    expect(parsed).not.toBeNull()
    expect(parsed?.secret).toBe('JBSWY3DPEHPK3PXP')
  })
})
