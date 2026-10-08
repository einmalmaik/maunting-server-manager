/** Kleine Helfer der Netzwerkansicht, ohne React (getestet in `netz.test.ts`). */
import type { Anfrage, Koepfe } from './netzStore'

export function groesse(bytes: number | null): string {
  if (bytes === null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}

export function dauer(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms) || ms < 0) return ''
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`
}

/** Dateiname und Rest einer Adresse für die Namensspalte. */
export function name(url: string): { datei: string; rest: string } {
  try {
    const u = new URL(url)
    if (u.protocol === 'data:') return { datei: url.slice(0, 40), rest: '' }
    const teile = u.pathname.split('/').filter(Boolean)
    // `…/ordner/` heißt wie in jedem Browser „ordner/“, die Wurzel nach dem Host.
    const letzter = teile.pop()
    const datei = letzter ? letzter + (u.pathname.endsWith('/') ? '/' : '') : u.host
    return { datei: datei + u.search, rest: u.host + (teile.length ? `/${teile.join('/')}` : '') }
  } catch {
    return { datei: url, rest: '' }
  }
}

/** Kopfzeilen, wie sie wirklich gingen, wenn bekannt. */
export function anfrageKoepfe(a: Anfrage): Koepfe {
  return a.roheAnfrageKoepfe ?? a.anfrageKoepfe
}

export function antwortKoepfe(a: Anfrage): Koepfe {
  return a.roheAntwortKoepfe ?? a.antwortKoepfe
}

export function kopf(k: Koepfe, name: string): string | undefined {
  const gesucht = name.toLowerCase()
  return Object.entries(k).find(([n]) => n.toLowerCase() === gesucht)?.[1]
}

const shell = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`

/** Die Anfrage als `curl`-Befehl (bash). Pseudo-Kopfzeilen von HTTP/2 fallen weg. */
export function alsCurl(a: Anfrage, nutzlast: string | null): string {
  const teile = [`curl ${shell(a.url)}`]
  if (a.methode !== 'GET' && !(a.methode === 'POST' && nutzlast !== null)) teile.push(`-X ${a.methode}`)
  for (const [n, w] of Object.entries(anfrageKoepfe(a))) if (!n.startsWith(':')) teile.push(`-H ${shell(`${n}: ${w}`)}`)
  if (nutzlast !== null) teile.push(`--data-raw ${shell(nutzlast)}`)
  return teile.join(' \\\n  ')
}

/** Die Anfrage als `fetch()`. */
export function alsFetch(a: Anfrage, nutzlast: string | null): string {
  const headers = Object.fromEntries(Object.entries(anfrageKoepfe(a)).filter(([n]) => !n.startsWith(':') && n.toLowerCase() !== 'cookie'))
  const optionen: Record<string, unknown> = { method: a.methode, headers }
  if (nutzlast !== null) optionen.body = nutzlast
  return `fetch(${JSON.stringify(a.url)}, ${JSON.stringify(optionen, null, 2)});`
}

/** `name=wert; …` aus der Cookie-Kopfzeile. */
export function cookiesDerAnfrage(a: Anfrage): [string, string][] {
  const roh = kopf(anfrageKoepfe(a), 'cookie')
  if (!roh) return []
  return roh.split(/;\s*/).filter(Boolean).map((c) => {
    const i = c.indexOf('=')
    return i < 0 ? [c, ''] : [c.slice(0, i), c.slice(i + 1)]
  })
}

/** Jede `Set-Cookie`-Zeile der Antwort (das Protokoll trennt sie mit Zeilenumbruch). */
export function cookiesDerAntwort(a: Anfrage): string[] {
  return (kopf(antwortKoepfe(a), 'set-cookie') ?? '').split('\n').filter(Boolean)
}

/** Abschnitte des Zeitablaufs in ms; `null`, wo die Phase nicht stattfand. */
export function phasen(a: Anfrage): { name: string; ms: number }[] {
  const z = a.zeitablauf
  if (!z) return []
  const spanne = (von: number, bis: number) => (von >= 0 && bis >= von ? bis - von : null)
  const aus: { name: string; ms: number | null }[] = [
    { name: 'dns', ms: spanne(z.dnsStart, z.dnsEnd) },
    { name: 'verbinden', ms: spanne(z.connectStart, z.sslStart >= 0 ? z.sslStart : z.connectEnd) },
    { name: 'tls', ms: spanne(z.sslStart, z.sslEnd) },
    { name: 'senden', ms: spanne(z.sendStart, z.sendEnd) },
    { name: 'warten', ms: spanne(z.sendEnd, z.receiveHeadersEnd) },
    { name: 'laden', ms: a.ende !== null ? (a.ende - z.requestTime) * 1000 - z.receiveHeadersEnd : null },
  ]
  return aus.filter((p): p is { name: string; ms: number } => p.ms !== null && p.ms >= 0)
}
