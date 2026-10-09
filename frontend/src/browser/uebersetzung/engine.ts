/**
 * Die Engine im Worker (`engine.worker.js`), von der Oberfläche aus. Ein
 * Worker für alle Tabs; er bekommt WASM und Modelle als Bytes aus
 * `modelle.ts` und holt selbst nichts. Stirbt er, fängt der nächste Aufruf
 * mit einem neuen an.
 */
import glue from './bergamot/bergamot-translator.js?url'
import { WASM, dateienVon, holen } from './modelle'

type Antwort = { nr: number; ergebnis?: unknown; fehler?: string }

let worker: Worker | null = null
let start: Promise<void> | null = null
let naechste = 0
const offen = new Map<number, { fertig: (e: unknown) => void; fehler: (f: Error) => void }>()

function beenden(grund: string) {
  worker?.terminate()
  worker = null
  start = null
  for (const o of offen.values()) o.fehler(new Error(grund))
  offen.clear()
}

function senden<T>(nachricht: Record<string, unknown>, transfer: Transferable[] = []): Promise<T> {
  if (!worker) {
    worker = new Worker(new URL('./engine.worker.js', import.meta.url))
    worker.onmessage = ({ data }: MessageEvent<Antwort>) => {
      const o = offen.get(data.nr)
      offen.delete(data.nr)
      if (data.fehler) o?.fehler(new Error(data.fehler))
      else o?.fertig(data.ergebnis)
    }
    worker.onerror = () => beenden('engine')
  }
  const nr = ++naechste
  return new Promise<T>((fertig, fehler) => {
    offen.set(nr, { fertig: fertig as (e: unknown) => void, fehler })
    worker!.postMessage({ ...nachricht, nr }, transfer)
  })
}

function starten(fortschritt: (bytes: number) => void): Promise<void> {
  start ??= holen(WASM, fortschritt)
    .then((wasm) => senden<void>({ art: 'start', glue: new URL(glue, location.href).href, wasm }, [wasm]))
    .catch((f) => {
      start = null
      throw f
    })
  return start
}

/** Lädt Engine und Modelle der Paare, soweit nötig; `fortschritt` zählt neu geladene Bytes. */
export async function bereitmachen(paare: string[], fortschritt: (bytes: number) => void, signal?: AbortSignal): Promise<void> {
  await starten(fortschritt)
  for (const paar of paare) {
    if (await senden<boolean>({ art: 'hat', paar })) continue
    const { dateien, name } = dateienVon(paar)
    const bytes: Record<string, ArrayBuffer> = {}
    for (const [art, d] of Object.entries(dateien)) bytes[art] = await holen(d, fortschritt)
    signal?.throwIfAborted()
    await senden({ art: 'modell', paar, dateien: bytes, name }, Object.values(bytes))
  }
}

/** Übersetzt Texte über die Paare (`weg` in `modelle.ts`), in derselben Reihenfolge. */
export function uebersetzen(paare: string[], texte: string[]): Promise<string[]> {
  return senden<string[]>({ art: 'uebersetzen', paare, texte })
}

/** Gibt den Speicher der Engine frei, etwa nachdem die Modelle entfernt wurden. */
export function engineBeenden(): void {
  beenden('beendet')
}
