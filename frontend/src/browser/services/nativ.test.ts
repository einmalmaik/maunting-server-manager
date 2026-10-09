import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('nativ.ts', () => {
  // `main.tsx` lädt die Panel-Adresse über `nativ.ts`, bevor `config/api.ts`
  // sie beim Laden des Moduls liest. Ein Import aus der App käme ihm zuvor.
  it('importiert nur Tauri', () => {
    const quelle = readFileSync(resolve(process.cwd(), 'src/browser/services/nativ.ts'), 'utf-8')
    const importe = [...quelle.matchAll(/^import .* from '([^']+)'/gm)].map((m) => m[1])
    expect(importe.length).toBeGreaterThan(0)
    expect(importe.filter((i) => !i.startsWith('@tauri-apps/'))).toEqual([])
  })
})
