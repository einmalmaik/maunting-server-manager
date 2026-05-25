import { api } from '@/api/client'

// Schwelle, ab der wir vom einfachen Multipart-Upload auf den
// chunked-resumable-Modus wechseln. ~5 MB ist genau der Bereich, in dem
// klassische Uploads laut Bug-Report kippen.
export const SINGLE_SHOT_LIMIT_BYTES = 5 * 1024 * 1024

// Chunk-Groesse. Bewusst klein genug, dass Reverse-Proxies (nginx default 1MB
// body, viele Setups ~10MB) nicht stoeren, aber gross genug fuer brauchbaren
// Durchsatz. Backend akzeptiert bis 64 MB pro Chunk.
export const CHUNK_SIZE_BYTES = 4 * 1024 * 1024

function getCsrfToken(): string | null {
  const match = document.cookie.match(new RegExp('(^| )__Secure-csrf_token=([^;]+)'))
  return match ? decodeURIComponent(match[2]) : null
}

interface UploadOptions {
  serverId: number
  /** Pfad innerhalb des Server-Roots (z.B. "mods" oder "" fuer Root). */
  destinationPath: string
  file: File
  /** 0..1, Fortschritt fuer UI. */
  onProgress?: (fraction: number) => void
  /** Bricht den laufenden Upload ab (Aufrufer setzt seinen Zustand). */
  signal?: AbortSignal
}

interface InitResponse {
  upload_id: string
}

/** Single-Shot-Upload fuer kleine Dateien (<= 5 MB). */
async function uploadSingle({ serverId, destinationPath, file, signal }: UploadOptions): Promise<void> {
  const formData = new FormData()
  formData.append('file', file)
  await api(`/files/${serverId}/upload?path=${encodeURIComponent(destinationPath)}`, {
    method: 'POST',
    body: formData,
    signal,
  })
}

/** Chunked-resumable Upload fuer grosse Dateien.
 *
 *  Wir benutzen rohes `fetch()` fuer die Chunk-Anfragen, weil der zentrale
 *  `api()`-Client die Antworten als JSON parsed und wir an dieser Stelle nur
 *  einen `2xx`-Check brauchen. CSRF + Cookies werden manuell mitgegeben.
 */
async function uploadChunked({
  serverId,
  destinationPath,
  file,
  onProgress,
  signal,
}: UploadOptions): Promise<void> {
  const init = await api<InitResponse>(`/files/${serverId}/upload/init`, {
    method: 'POST',
    body: JSON.stringify({
      path: destinationPath,
      filename: file.name,
      total_size: file.size,
    }),
    signal,
  })

  const uploadId = init.upload_id
  const csrf = getCsrfToken()
  const totalChunks = Math.max(1, Math.ceil(file.size / CHUNK_SIZE_BYTES))

  try {
    for (let idx = 0; idx < totalChunks; idx++) {
      if (signal?.aborted) {
        throw new DOMException('Upload abgebrochen', 'AbortError')
      }
      const start = idx * CHUNK_SIZE_BYTES
      const end = Math.min(file.size, start + CHUNK_SIZE_BYTES)
      const blob = file.slice(start, end)

      const formData = new FormData()
      formData.append('chunk', blob, file.name)

      const res = await fetch(
        `/api/files/${serverId}/upload/${uploadId}/chunk`,
        {
          method: 'PUT',
          credentials: 'include',
          body: formData,
          headers: csrf ? { 'X-CSRF-Token': csrf } : undefined,
          signal,
        },
      )
      if (!res.ok) {
        const text = await res.text().catch(() => '')
        throw new Error(text || `Chunk ${idx + 1}/${totalChunks} fehlgeschlagen`)
      }
      onProgress?.(end / file.size)
    }

    await api(`/files/${serverId}/upload/${uploadId}/finalize`, {
      method: 'POST',
      signal,
    })
  } catch (err) {
    // Best-effort cleanup auf dem Server. Schweigt — der naechste Init
    // wuerde sonst ueber Storage-Reste klagen.
    try {
      await api(`/files/${serverId}/upload/${uploadId}`, { method: 'DELETE' })
    } catch {
      // ignore
    }
    throw err
  }
}

/** Einheitlicher Einstiegspunkt fuer File-Uploads. Waehlt die richtige
 *  Strategie anhand der Dateigroesse und melde Fortschritt an die UI.
 */
export async function uploadFile(options: UploadOptions): Promise<void> {
  if (options.file.size <= SINGLE_SHOT_LIMIT_BYTES) {
    await uploadSingle(options)
    options.onProgress?.(1)
    return
  }
  await uploadChunked(options)
}
