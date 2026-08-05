/**
 * Reine Hilfsfunktionen fuer die Admin-Audit-UI.
 * Keine API-Calls, keine Secrets — nur sichere Darstellung.
 */

export interface AuditLogRow {
  id: number
  user_id: number | null
  action: string
  target_type: string | null
  target_id: number | null
  origin: 'direct' | 'ai' | 'external' | 'system'
  correlation_id: string | null
  details: string | null
  created_at: string | null
}

const SECRET_ASSIGN = /(password|passwd|token|secret|authorization)\s*[:=]\s*\S+/gi

/**
 * Prueft, ob ein Text secret-aehnliche Fragmente enthaelt (Test/Anzeige-Schutz).
 */
export function containsSecretFragment(text: string | null | undefined): boolean {
  if (!text) return false
  return /(password|passwd|token|secret|authorization)\s*[:=]/i.test(text)
}

/**
 * Formatiert Zieltyp + ID fuer die Tabelle (z. B. "server #12").
 */
export function formatAuditTarget(row: Pick<AuditLogRow, 'target_type' | 'target_id'>): string {
  if (!row.target_type && row.target_id == null) return '—'
  if (row.target_type && row.target_id != null) return `${row.target_type} #${row.target_id}`
  if (row.target_type) return row.target_type
  return `#${row.target_id}`
}

/**
 * Formatiert den Zeitstempel lesbar; leere/ungueltige Werte bleiben klar markiert.
 */
export function formatAuditTime(iso: string | null | undefined, locale = 'de-DE'): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString(locale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

/**
 * Mappt unbekannte API-Antworten auf typsichere Zeilen; ungueltige Eintraege werden verworfen.
 */
export function mapAuditApiRows(payload: unknown): AuditLogRow[] {
  if (!Array.isArray(payload)) return []
  const rows: AuditLogRow[] = []
  for (const item of payload) {
    if (!item || typeof item !== 'object') continue
    const raw = item as Record<string, unknown>
    const id = Number(raw.id)
    if (!Number.isFinite(id)) continue
    const action = typeof raw.action === 'string' ? raw.action : ''
    if (!action) continue
    rows.push({
      id,
      user_id: raw.user_id == null ? null : Number(raw.user_id),
      action,
      target_type: typeof raw.target_type === 'string' ? raw.target_type : null,
      target_id: raw.target_id == null ? null : Number(raw.target_id),
      origin: raw.origin === 'ai' || raw.origin === 'external' || raw.origin === 'system'
        ? raw.origin
        : 'direct',
      correlation_id: typeof raw.correlation_id === 'string' ? raw.correlation_id : null,
      details: typeof raw.details === 'string' ? raw.details : raw.details == null ? null : String(raw.details),
      created_at: typeof raw.created_at === 'string' ? raw.created_at : null,
    })
  }
  return rows
}

/**
 * Kuerzt Details fuer die Tabelle und maskiert offensichtliche Secret-Fragmente.
 */
export function safeAuditDetails(details: string | null | undefined, maxLen = 160): string {
  if (!details) return '—'
  let text = details.replace(SECRET_ASSIGN, '$1=[redacted]')
  if (text.length > maxLen) text = `${text.slice(0, maxLen)}…`
  return text
}
