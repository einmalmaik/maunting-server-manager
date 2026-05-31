import { describe, expect, it } from 'vitest'

import { labelRole, mapBlueprintPorts } from './portRoles'

describe('port role helpers', () => {
  it('keeps duplicate standard roles addressable', () => {
    const mapped = mapBlueprintPorts([
      { name: 'query', protocol: 'udp' },
      { name: 'query', protocol: 'tcp' },
      { name: 'custom', protocol: 'udp' },
      { name: 'custom', protocol: 'tcp' },
    ])

    expect(mapped.map((p) => p.mappedRole)).toEqual([
      'query',
      'query_2',
      'custom_1',
      'custom_2',
    ])
  })

  it('maps numbered standard roles back to their display base', () => {
    expect(labelRole('query_2')).toBe('query')
    expect(labelRole('custom_2')).toBe('custom_2')
  })
})
