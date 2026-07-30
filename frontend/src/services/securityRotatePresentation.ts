/**
 * Reine Hilfsfunktionen fuer die msm_admin-Rotation in den Panel-Settings.
 * Kein Passwort-State — nur sichere Erfolgs-/Fehlertexte.
 */

export interface RotateAdminResult {
  ok: boolean
  admin_user: string
  nodes_updated: number[]
  nodes_skipped: number[]
}

/**
 * Prueft, ob ein API-Objekt unerwartet Passwort-Felder enthaelt.
 */
export function rotateResultLeaksSecret(payload: unknown): boolean {
  if (!payload || typeof payload !== 'object') return false
  const obj = payload as Record<string, unknown>
  for (const key of Object.keys(obj)) {
    if (/password|secret|token/i.test(key) && key !== 'admin_user') {
      const val = obj[key]
      if (typeof val === 'string' && val.length > 0) return true
    }
  }
  return false
}

/**
 * Mappt die API-Antwort der Cluster-Admin-Rotation; wirft bei ungueltiger Form.
 */
export function mapRotateAdminResult(payload: unknown): RotateAdminResult {
  if (!payload || typeof payload !== 'object') {
    throw new Error('Ungueltige Antwort der Admin-Rotation.')
  }
  if (rotateResultLeaksSecret(payload)) {
    throw new Error('Sicherheitsfehler: Antwort enthaelt unerwartete Secret-Felder.')
  }
  const raw = payload as Record<string, unknown>
  const nodes_updated = Array.isArray(raw.nodes_updated)
    ? raw.nodes_updated.map(Number).filter((n) => Number.isFinite(n))
    : []
  const nodes_skipped = Array.isArray(raw.nodes_skipped)
    ? raw.nodes_skipped.map(Number).filter((n) => Number.isFinite(n))
    : []
  return {
    ok: Boolean(raw.ok),
    admin_user: typeof raw.admin_user === 'string' ? raw.admin_user : 'msm_admin',
    nodes_updated,
    nodes_skipped,
  }
}

/**
 * Baut einen nutzerlesbaren Erfolgstext ohne Secrets.
 */
export function formatRotateSuccessSummary(result: RotateAdminResult): string {
  const updated = result.nodes_updated.length
  const skipped = result.nodes_skipped.length
  return `Cluster-Admin (${result.admin_user}) rotiert. Nodes aktualisiert: ${updated}, uebersprungen: ${skipped}. Das neue Passwort wird nicht angezeigt.`
}
