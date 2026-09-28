import { describe, expect, it } from 'vitest'
import { buildSnippets, connectionUri, PASSWORD_PLACEHOLDER, type ConnectionTarget } from './connectionSnippets'

const base: ConnectionTarget = {
  host: 'db.example.com',
  port: 25432,
  database: 'app',
  user: 'app_owner',
  password: '',
  sslmode: 'require',
}

describe('Verbindungs-Snippets', () => {
  it('kodiert Sonderzeichen im Passwort, sonst bricht die URI', () => {
    const password = ['p@ss', 'w/rd', '%1', '#x'].join('')
    const uri = connectionUri({ ...base, password })
    expect(uri).toBe('postgresql://app_owner:p%40ssw%2Frd%251%23x@db.example.com:25432/app?sslmode=require')
    expect(decodeURIComponent(new URL(uri.replace('postgresql', 'http')).password)).toBe(password)
  })

  it('zeigt ohne abgerufenes Passwort nur den Platzhalter', () => {
    for (const snippet of buildSnippets(base)) {
      expect(snippet.code).toContain(PASSWORD_PLACEHOLDER)
    }
  })

  it('setzt sslmode je Ziel', () => {
    expect(connectionUri({ ...base, sslmode: 'prefer' })).not.toContain('sslmode')
    const jdbc = buildSnippets({ ...base, sslmode: 'disable' }).find((s) => s.key === 'jdbc')!
    expect(jdbc.code).toContain('sslmode=disable')
  })

  it('quotet Passwörter in Code-Snippets als Literal', () => {
    const password = ["it's", '"quoted"'].join(' ')
    const snippets = buildSnippets({ ...base, password })
    expect(snippets.find((s) => s.key === 'node')!.code).toContain("password: 'it\\'s \"quoted\"'")
    expect(snippets.find((s) => s.key === 'asyncpg')!.code).toContain('password="it\'s \\"quoted\\""')
  })

  it('liefert alle zugesagten Treiber', () => {
    expect(buildSnippets(base).map((s) => s.key)).toEqual(['uri', 'dsn', 'psql', 'prisma', 'drizzle', 'node', 'sqlalchemy', 'asyncpg', 'php', 'go', 'jdbc'])
  })
})
