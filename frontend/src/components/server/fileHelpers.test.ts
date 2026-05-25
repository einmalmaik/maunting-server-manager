import { describe, expect, it } from 'vitest'
import {
  detectLanguage,
  formatBytes,
  isWithin,
  joinPath,
  parentPath,
  pathSegments,
  sortEntries,
} from './fileHelpers'

describe('joinPath', () => {
  it('liefert nur den Namen wenn parent leer ist', () => {
    expect(joinPath('', 'foo.txt')).toBe('foo.txt')
  })
  it('fuegt Trenner zwischen parent und name ein', () => {
    expect(joinPath('mods', 'a.cfg')).toBe('mods/a.cfg')
  })
  it('frisst doppelte Slashes', () => {
    expect(joinPath('/mods/', '/inner/')).toBe('mods/inner')
  })
})

describe('parentPath', () => {
  it('liefert leeren String fuer Top-Level', () => {
    expect(parentPath('foo.txt')).toBe('')
  })
  it('liefert direktes Verzeichnis fuer verschachtelte Datei', () => {
    expect(parentPath('mods/profiles/main.cfg')).toBe('mods/profiles')
  })
})

describe('pathSegments', () => {
  it('splittet sauber', () => {
    expect(pathSegments('a/b/c')).toEqual(['a', 'b', 'c'])
  })
  it('liefert leer fuer leeren Pfad', () => {
    expect(pathSegments('')).toEqual([])
  })
})

describe('sortEntries', () => {
  it('Verzeichnisse vor Dateien, dann alphabetisch', () => {
    const out = sortEntries([
      { name: 'b.txt', is_dir: false },
      { name: 'mods', is_dir: true },
      { name: 'a.txt', is_dir: false },
      { name: 'Backups', is_dir: true },
    ])
    expect(out.map((e) => e.name)).toEqual(['Backups', 'mods', 'a.txt', 'b.txt'])
  })
})

describe('detectLanguage', () => {
  it('mappt typische Dateinamen', () => {
    expect(detectLanguage('config.json')).toBe('json')
    expect(detectLanguage('docker-compose.yaml')).toBe('yaml')
    expect(detectLanguage('config.yml')).toBe('yaml')
    expect(detectLanguage('manifest.xml')).toBe('xml')
    expect(detectLanguage('README.md')).toBe('markdown')
    expect(detectLanguage('serverDZ.cfg')).toBe('ini')
    expect(detectLanguage('app.properties')).toBe('properties')
    expect(detectLanguage('random.txt')).toBe('plain')
  })
})

describe('formatBytes', () => {
  it('rendert sinnvolle Einheiten', () => {
    expect(formatBytes(0)).toBe('-')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
    expect(formatBytes(3 * 1024 * 1024 * 1024)).toBe('3.00 GB')
  })
})

describe('isWithin', () => {
  it('erkennt Selbst-Reflexion (Move-Schutz)', () => {
    expect(isWithin('outer', 'outer/inner')).toBe(true)
    expect(isWithin('outer', 'outer')).toBe(true)
  })
  it('erkennt unverwandte Pfade', () => {
    expect(isWithin('outer', 'other/inner')).toBe(false)
  })
  it('behandelt leeren base konservativ', () => {
    expect(isWithin('', 'foo')).toBe(false)
  })
})
