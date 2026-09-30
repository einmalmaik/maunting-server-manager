/**
 * Legt Bytes in der App dort ab, wo der Mensch es im Speichern-Dialog sagt
 * (`datei_speichern.rs`): Tresor-Dateien und den Datenexport.
 *
 * Die Teile reisen als Base64 im JSON. Android kennt keinen rohen IPC-Körper
 * (Tauri liefert dort immer JSON), roh angekommen wäre dort nichts.
 */

function zuBase64(bytes: Uint8Array): string {
  let binaer = ''
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binaer += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  }
  return btoa(binaer)
}

/** Teilt einen Blob für `inDerAppSpeichern` in Stücke von 1 MiB. */
export async function* blobTeile(blob: Blob): AsyncGenerator<Uint8Array> {
  for (let ab = 0; ab < blob.size; ab += TEIL) {
    yield new Uint8Array(await blob.slice(ab, ab + TEIL).arrayBuffer())
  }
}

const TEIL = 1024 * 1024

/**
 * Fragt nach dem Ziel und schreibt Teil für Teil. `false` heißt: der Mensch hat
 * den Dialog abgebrochen. Wirft `teile`, wird die halbe Datei verworfen.
 */
export async function inDerAppSpeichern(name: string, teile: AsyncIterable<Uint8Array>): Promise<boolean> {
  const { invoke } = await import('@tauri-apps/api/core')
  const vorgang = await invoke<number | null>('datei_speichern_start', { name })
  if (vorgang === null) return false
  try {
    for await (const teil of teile) {
      await invoke('datei_speichern_teil', { vorgang, teil: zuBase64(teil) })
    }
  } catch (fehler) {
    await invoke('datei_speichern_ende', { vorgang, abbrechen: true }).catch(() => {})
    throw fehler
  }
  await invoke('datei_speichern_ende', { vorgang, abbrechen: false })
  return true
}
