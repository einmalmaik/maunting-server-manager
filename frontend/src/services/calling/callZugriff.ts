/**
 * Der eine Zugriff auf die LiveKit-Kapsel.
 *
 * `livekit-client` ist ueber 500 kB gross; das Modul kommt erst beim ersten
 * Verbindungsaufbau. Wer es danach braucht, darf es voraussetzen.
 */

import type * as LivekitRaum from '@/services/livekitRaum'

let livekitModul: typeof LivekitRaum | null = null

export async function ladeLivekit(): Promise<typeof LivekitRaum> {
  livekitModul ??= await import('@/services/livekitRaum')
  return livekitModul
}

export function lk(): typeof LivekitRaum {
  if (!livekitModul) throw new Error('LiveKit ist noch nicht geladen')
  return livekitModul
}
