import { describe, expect, it } from 'vitest'
import { matchPositions } from './FileEditorWorkspace'

describe('file editor search', () => {
  it('finds all non-overlapping matches with case control', () => {
    expect(matchPositions('Alpha alpha ALPHA', 'alpha', false)).toEqual([
      { from: 0, to: 5 },
      { from: 6, to: 11 },
      { from: 12, to: 17 },
    ])
    expect(matchPositions('Alpha alpha ALPHA', 'alpha', true)).toEqual([{ from: 6, to: 11 }])
  })

  it('returns no synthetic match for an empty query', () => {
    expect(matchPositions('content', '', false)).toEqual([])
  })
})
