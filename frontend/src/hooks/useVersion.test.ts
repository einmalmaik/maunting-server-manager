import { describe, expect, it } from 'vitest'
import { formatInstalledVersion } from './useVersion'

describe('formatInstalledVersion', () => {
  it('normalisiert git describe', () => {
    expect(formatInstalledVersion('v1.7.9')).toBe('v1.7.9')
    expect(formatInstalledVersion('v1.7.9-2-gabcdef')).toBe('v1.7.9')
    expect(formatInstalledVersion('1.7.9')).toBe('v1.7.9')
  })
  it('liefert leer bei unknown', () => {
    expect(formatInstalledVersion('unknown')).toBe('')
  })
})