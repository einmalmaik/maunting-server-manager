/**
 * Die Blob-Routen des Tresors (`/api/vault/blobs`). Hier gehen nur Chiffrat,
 * Kennungen und Größen über die Leitung; was ein Blob enthält, weiß nur der
 * Tresor-Eintrag.
 */

import { api, apiStream } from '@/api/client'

export interface TresorSpeicher {
  belegt: number
  quote: number
  in_loeschung: number
}

export interface BlobStand {
  state: 'offen' | 'fertig' | 'geloescht'
  chunk_count: number
  vorhanden: number[]
}

export function speicherAbfragen(): Promise<TresorSpeicher> {
  return api<TresorSpeicher>('/api/vault/speicher')
}

export async function blobReservieren(id: string, chunkAnzahl: number, bytes: number, loeschPruefwert: string): Promise<void> {
  await api('/api/vault/blobs', {
    method: 'POST',
    body: JSON.stringify({ id, chunk_count: chunkAnzahl, bytes_total: bytes, delete_verifier: loeschPruefwert }),
  })
}

export function blobStand(id: string): Promise<BlobStand> {
  return api<BlobStand>(`/api/vault/blobs/${id}/status`)
}

export async function chunkHochladen(id: string, index: number, chiffrat: Uint8Array): Promise<void> {
  await apiStream(`/api/vault/blobs/${id}/chunks/${index}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/octet-stream', Accept: '*/*' },
    body: chiffrat as BodyInit,
  })
}

export async function blobFertig(id: string): Promise<void> {
  await api(`/api/vault/blobs/${id}/fertig`, { method: 'POST' })
}

export async function chunkLaden(id: string, index: number, signal?: AbortSignal): Promise<Uint8Array> {
  const antwort = await apiStream(`/api/vault/blobs/${id}/chunks/${index}`, {
    method: 'GET',
    headers: { Accept: 'application/octet-stream' },
    signal,
  })
  return new Uint8Array(await antwort.arrayBuffer())
}

/**
 * Bis zu 100 einteilige kleine Blobs (Miniaturen) in einer Anfrage. Fehlt
 * einer, steht er nicht in der Antwort.
 */
export async function kleineLaden(ids: string[]): Promise<Map<string, Uint8Array>> {
  const antwort = await apiStream('/api/vault/blobs/klein', {
    method: 'POST',
    headers: { Accept: 'application/octet-stream' },
    body: JSON.stringify({ ids }),
  })
  const daten = new Uint8Array(await antwort.arrayBuffer())
  const sicht = new DataView(daten.buffer, daten.byteOffset, daten.byteLength)
  const ergebnis = new Map<string, Uint8Array>()
  let pos = 0
  for (const id of ids) {
    if (pos + 4 > daten.length) break
    const laenge = sicht.getUint32(pos)
    pos += 4
    if (pos + laenge > daten.length) break
    if (laenge > 0) ergebnis.set(id, daten.slice(pos, pos + laenge))
    pos += laenge
  }
  return ergebnis
}

export async function blobLoeschen(id: string, loeschen: string): Promise<void> {
  await api(`/api/vault/blobs/${id}`, { method: 'DELETE', body: JSON.stringify({ schluessel: loeschen }) })
}
