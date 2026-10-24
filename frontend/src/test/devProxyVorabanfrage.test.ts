// @vitest-environment node
/**
 * Der Dev-Server von :3000 darf Vorabanfragen nicht selbst beantworten.
 *
 * Vite setzte bis 22.10.2026 auf OPTIONS nur `Access-Control-Allow-Origin` und
 * nie `Access-Control-Allow-Credentials`. Der Client schickt bei einer
 * absoluten API-Adresse `credentials: "include"` (MSS, Pontis, Handy); der
 * Browser verlangt `Allow-Credentials: true` auch in der Vorabanfrage und
 * bricht sonst mit „Failed to fetch“ ab, bevor die Anfrage beim Backend ist.
 * Pontis auf `http://localhost:1450` konnte deshalb den Kopplungscode nicht
 * einlösen, die Adressprüfung davor (schlichtes GET) ging (AGENTS.md 178).
 */
import { describe, expect, it } from 'vitest'

import konfig from '../../vite.config'

interface DevServer {
  cors?: unknown
  proxy?: Record<string, unknown>
}

const server = (konfig as unknown as { server?: DevServer }).server

describe('Dev-Server und Vorabanfragen', () => {
  it('überlässt die CORS-Köpfe dem Backend', () => {
    expect(server?.cors).toBe(false)
  })

  it('reicht /api an das Backend weiter, damit dessen Antwort gilt', () => {
    expect(server?.proxy?.['/api']).toBeTruthy()
  })
})
