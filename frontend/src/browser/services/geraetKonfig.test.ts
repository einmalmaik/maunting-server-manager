import { describe, expect, it } from 'vitest'

import { schildPausiert, seitenHost } from './geraetKonfig'
import faelle from './schildAusnahmen.faelle.json'

describe('schildPausiert', () => {
  it.each(faelle)('$host mit $ausnahmen: $pausiert', ({ host, ausnahmen, pausiert }) => {
    expect(schildPausiert(host, ausnahmen)).toBe(pausiert)
  })

  it('nimmt den Host wie Rust: ohne www., klein, nur http(s)', () => {
    expect(seitenHost('https://WWW.Seite.Example/a')).toBe('seite.example')
    expect(seitenHost('file:///C:/x')).toBeNull()
  })
})
