import { describe, expect, it } from 'vitest'
import { changeBlueprintSource, createBlueprintDraft, getBlueprintCollision, normalizeBlueprintDraft, validateBlueprintDraft } from './contract'

describe('Blueprint builder contract', () => {
  it('uses the safe backend defaults for a new Steam blueprint', () => {
    const draft = createBlueprintDraft()
    expect(draft.version).toBe(1)
    expect(draft.source.updateStrategy).toBe('checkBased')
    expect(draft.runtime.enableExec).toBe(false)
  })

  it('replaces source-dependent objects instead of serializing extras', () => {
    const changed = changeBlueprintSource(createBlueprintDraft(), 'github')
    expect(changed.source).toEqual({ type: 'github', updateStrategy: 'checkBased', github: { repo: '', branch: 'main', setupCommands: [] } })
  })

  it('blocks unsafe identifiers, shell chaining, duplicate ports and insecure URLs', () => {
    const draft = changeBlueprintSource(createBlueprintDraft(), 'http')
    draft.meta.id = 'Unsafe-ID'
    draft.runtime.startup = './server && $(rm)'
    draft.ports.push({ name: 'game', protocol: 'udp' })
    draft.source.http!.url = 'http://example.invalid/server.zip'
    expect(validateBlueprintDraft(draft).map(issue => issue.path)).toEqual(expect.arrayContaining(['meta.id', 'runtime.startup', 'ports.1', 'source.http.url']))
  })

  it('omits optional empty blocks without changing the schema version', () => {
    const normalized = normalizeBlueprintDraft(createBlueprintDraft())
    expect(normalized.version).toBe(1)
    expect(normalized).not.toHaveProperty('mods')
    expect(normalized).not.toHaveProperty('backup')
  })

  it('never silently replaces native or community entries in create mode', () => {
    const entries = [{ id: 'native_bp', origin: 'native' as const }, { id: 'community_bp', origin: 'community' as const }]
    expect(getBlueprintCollision(entries, 'native_bp', false)).toBe('native-blocked')
    expect(getBlueprintCollision(entries, 'community_bp', false)).toBe('community-confirm')
    expect(getBlueprintCollision(entries, 'community_bp', true)).toBe('none')
  })
})
