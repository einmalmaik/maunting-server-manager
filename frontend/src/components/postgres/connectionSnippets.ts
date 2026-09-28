/**
 * Verbindungs-Snippets für gängige Treiber und Frameworks.
 *
 * Rein und ohne Zustand, damit ein Test jede Zeile prüfen kann: ein falsch
 * kodiertes Passwort (`@`, `/`, `%` im Passwort) bricht jede URI still.
 */
export interface ConnectionTarget {
  host: string
  port: number
  database: string
  user: string
  /** Leer: Platzhalter statt Passwort. */
  password: string
  sslmode: 'disable' | 'prefer' | 'require'
}

export interface Snippet {
  key: string
  label: string
  code: string
}

export const PASSWORD_PLACEHOLDER = '<PASSWORT>'

const enc = encodeURIComponent

function secret(target: ConnectionTarget): string {
  return target.password || PASSWORD_PLACEHOLDER
}

export function connectionUri(target: ConnectionTarget): string {
  const password = target.password ? enc(target.password) : PASSWORD_PLACEHOLDER
  const ssl = target.sslmode === 'prefer' ? '' : `?sslmode=${target.sslmode}`
  return `postgresql://${enc(target.user)}:${password}@${target.host}:${target.port}/${enc(target.database)}${ssl}`
}

function quoteSingle(value: string): string {
  return `'${value.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`
}

function quoteDouble(value: string): string {
  return JSON.stringify(value)
}

export function buildSnippets(target: ConnectionTarget): Snippet[] {
  const uri = connectionUri(target)
  const pw = secret(target)
  const ssl = target.sslmode
  const nodeSsl = ssl === 'require' ? '{ rejectUnauthorized: false }' : 'false'
  const dsn = `host=${target.host} port=${target.port} dbname=${target.database} user=${target.user} password=${pw} sslmode=${ssl}`
  const jdbcSsl = ssl === 'disable' ? 'sslmode=disable' : `sslmode=${ssl}`
  const sqlalchemy = uri.replace(/^postgresql:\/\//, 'postgresql+psycopg://')
  return [
    { key: 'uri', label: 'URI', code: uri },
    { key: 'dsn', label: 'libpq DSN', code: dsn },
    { key: 'psql', label: 'psql', code: `psql "${uri}"` },
    {
      key: 'prisma',
      label: 'Prisma (.env)',
      code: `DATABASE_URL="${uri}"\n\n// schema.prisma\ndatasource db {\n  provider = "postgresql"\n  url      = env("DATABASE_URL")\n}`,
    },
    {
      key: 'drizzle',
      label: 'Drizzle ORM',
      code: `// drizzle.config.ts\nimport { defineConfig } from 'drizzle-kit'\n\nexport default defineConfig({\n  dialect: 'postgresql',\n  schema: './src/db/schema.ts',\n  dbCredentials: { url: process.env.DATABASE_URL! },\n})\n\n// .env\nDATABASE_URL=${uri}`,
    },
    {
      key: 'node',
      label: 'Node.js (pg)',
      code: `import pg from 'pg'\n\nconst pool = new pg.Pool({\n  host: ${quoteSingle(target.host)},\n  port: ${target.port},\n  database: ${quoteSingle(target.database)},\n  user: ${quoteSingle(target.user)},\n  password: ${quoteSingle(pw)},\n  ssl: ${nodeSsl},\n})`,
    },
    {
      key: 'sqlalchemy',
      label: 'Python (SQLAlchemy)',
      code: `from sqlalchemy import create_engine\n\nengine = create_engine(${quoteDouble(sqlalchemy)})`,
    },
    {
      key: 'asyncpg',
      label: 'Python (asyncpg)',
      code: `import asyncpg\n\nconn = await asyncpg.connect(\n    host=${quoteDouble(target.host)},\n    port=${target.port},\n    database=${quoteDouble(target.database)},\n    user=${quoteDouble(target.user)},\n    password=${quoteDouble(pw)},\n    ssl=${ssl === 'require' ? '"require"' : ssl === 'disable' ? 'False' : '"prefer"'},\n)`,
    },
    {
      key: 'php',
      label: 'PHP (PDO)',
      code: `$pdo = new PDO(\n    ${quoteSingle(`pgsql:host=${target.host};port=${target.port};dbname=${target.database};sslmode=${ssl}`)},\n    ${quoteSingle(target.user)},\n    ${quoteSingle(pw)},\n    [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]\n);`,
    },
    {
      key: 'go',
      label: 'Go (pgx)',
      code: `conn, err := pgx.Connect(context.Background(), ${quoteDouble(uri)})\nif err != nil {\n\tlog.Fatal(err)\n}\ndefer conn.Close(context.Background())`,
    },
    {
      key: 'jdbc',
      label: 'Java (JDBC)',
      code: `String url = ${quoteDouble(`jdbc:postgresql://${target.host}:${target.port}/${target.database}?${jdbcSsl}`)};\nConnection conn = DriverManager.getConnection(url, ${quoteDouble(target.user)}, ${quoteDouble(pw)});`,
    },
  ]
}
