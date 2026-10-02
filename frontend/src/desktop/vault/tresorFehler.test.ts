import { describe, expect, it } from 'vitest'

import { SanitizedApiError } from '@/api/client'
import { fehlerText, TresorFehler } from './tresorFehler'

describe('fehlerText', () => {
  it('lässt nur Texte für die Oberfläche durch', () => {
    expect(fehlerText(new TresorFehler('Der Tresor ist gesperrt.'), 'ersatz')).toBe('Der Tresor ist gesperrt.')
    expect(fehlerText(new SanitizedApiError('Speicher voll', { status: 507 }), 'ersatz')).toBe('Speicher voll')
    expect(fehlerText(new TypeError('Failed to fetch'), 'ersatz')).toBe('ersatz')
    expect(fehlerText(new Error('Chunk hat die falsche Länge'), 'ersatz')).toBe('ersatz')
    expect(fehlerText('kaputt', 'ersatz')).toBe('ersatz')
  })
})
