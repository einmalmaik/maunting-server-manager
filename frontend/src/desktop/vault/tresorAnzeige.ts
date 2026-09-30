/**
 * Was die App mit einer entschlüsselten Datei anfangen kann: selbst zeigen
 * oder auf dem Gerät speichern. Gemeinsam für Dateien und Fotos.
 */
import { inDerAppSpeichern } from '@/lib/geraetSpeichern'
import { inDerApp } from '@/services/passkeyService'
import { useVaultStore } from './vaultStore'
import type { BlobKopf } from './tresorDatei'
import { ansichtOeffnen, ansichtSchliessen, klartextTeile } from './tresorDateien'

/** Was der Tresor selbst zeigen kann. Alles andere wird gespeichert. */
export function anzeigeArt(typ: string): 'bild' | 'video' | 'audio' | 'text' | null {
  if (typ.startsWith('image/') && typ !== 'image/svg+xml') return 'bild'
  if (typ.startsWith('video/')) return 'video'
  if (typ.startsWith('audio/')) return 'audio'
  if (typ.startsWith('text/') || typ === 'application/json') return 'text'
  return null
}

/**
 * Speichert eine Tresor-Datei auf dem Gerät. In der App fragt Rust nach dem
 * Ziel und schreibt Chunk für Chunk (auch nach `content://` auf Android); im
 * JavaScript-Speicher liegt immer nur ein Chunk Klartext, dazu seine
 * Base64-Fassung für den Weg nach Rust, bis sie eingesammelt wird. `false`
 * heißt: der Mensch hat den Dialog abgebrochen.
 */
export async function aufGeraetSpeichern(
  kopf: BlobKopf,
  eintragId: string,
  userKey: CryptoKey,
  name: string,
  typ: string,
): Promise<boolean> {
  if (!inDerApp()) {
    const teile: Blob[] = []
    for await (const klartext of klartextTeile(kopf, eintragId, userKey)) teile.push(new Blob([klartext as BlobPart]))
    if (useVaultStore.getState().userKey !== userKey) return false
    const url = ansichtOeffnen(new Blob(teile, { type: typ }))
    const link = document.createElement('a')
    link.href = url
    link.download = name
    link.rel = 'noopener'
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => ansichtSchliessen(url), 60_000)
    return true
  }
  return inDerAppSpeichern(name, bisGesperrt(klartextTeile(kopf, eintragId, userKey), userKey))
}

/** Gesperrt: nichts mehr herausgeben, die halbe Datei fällt weg. */
async function* bisGesperrt(teile: AsyncIterable<Uint8Array>, userKey: CryptoKey): AsyncGenerator<Uint8Array> {
  for await (const teil of teile) {
    if (useVaultStore.getState().userKey !== userKey) throw new Error('gesperrt')
    yield teil
  }
}
