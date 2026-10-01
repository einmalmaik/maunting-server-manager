/**
 * Liest, was in einem Archiv steckt, ohne etwas zu entpacken: Zip (auch Zip64,
 * jar, apk), tar und tar.gz. Für die Inhaltsliste im Tresor.
 *
 * Absichtlich klein statt einer Bibliothek: nur Namen und Größen. gz entpackt
 * die Plattform selbst (`DecompressionStream`).
 */

export interface ArchivEintrag {
  pfad: string
  groesse: number
  ordner: boolean
}

/** Mehr Einträge zeigt keine Liste sinnvoll an. */
export const ARCHIV_EINTRAEGE_HOECHSTENS = 10_000

const utf8 = new TextDecoder('utf-8')
// Namen ohne UTF-8-Kennzeichen stammen meist aus alten Windows-Packern (CP437);
// latin1 trifft die Buchstaben nicht immer, macht aber nie einen Fehler.
const latin1 = new TextDecoder('latin1')

function zip64Werte(sicht: DataView, start: number, laenge: number): bigint[] {
  const werte: bigint[] = []
  let p = start
  while (p + 4 <= start + laenge) {
    const kennung = sicht.getUint16(p, true)
    const groesse = sicht.getUint16(p + 2, true)
    if (kennung === 0x0001) {
      for (let q = p + 4; q + 8 <= p + 4 + groesse; q += 8) werte.push(sicht.getBigUint64(q, true))
      break
    }
    p += 4 + groesse
  }
  return werte
}

export function zipInhalt(daten: Uint8Array): ArchivEintrag[] {
  const sicht = new DataView(daten.buffer, daten.byteOffset, daten.byteLength)
  let ende = -1
  for (let p = daten.length - 22; p >= Math.max(0, daten.length - 22 - 0xffff); p--) {
    if (sicht.getUint32(p, true) === 0x06054b50) {
      ende = p
      break
    }
  }
  if (ende < 0) throw new Error('Kein Zip-Verzeichnis gefunden.')
  let anzahl = sicht.getUint16(ende + 10, true)
  let versatz = sicht.getUint32(ende + 16, true)
  // Zip64: der Locator steht direkt vor dem Ende-Satz.
  if ((anzahl === 0xffff || versatz === 0xffffffff) && ende >= 20 && sicht.getUint32(ende - 20, true) === 0x07064b50) {
    const satz = Number(sicht.getBigUint64(ende - 12, true))
    if (sicht.getUint32(satz, true) === 0x06064b50) {
      anzahl = Number(sicht.getBigUint64(satz + 32, true))
      versatz = Number(sicht.getBigUint64(satz + 48, true))
    }
  }
  const eintraege: ArchivEintrag[] = []
  let p = versatz
  for (let i = 0; i < anzahl && eintraege.length < ARCHIV_EINTRAEGE_HOECHSTENS; i++) {
    if (p + 46 > daten.length || sicht.getUint32(p, true) !== 0x02014b50) throw new Error('Zip-Verzeichnis ist beschädigt.')
    const flags = sicht.getUint16(p + 8, true)
    let groesse = sicht.getUint32(p + 24, true)
    const namenLaenge = sicht.getUint16(p + 28, true)
    const extraLaenge = sicht.getUint16(p + 30, true)
    const kommentarLaenge = sicht.getUint16(p + 32, true)
    const name = daten.subarray(p + 46, p + 46 + namenLaenge)
    const pfad = (flags & 0x0800 ? utf8 : latin1).decode(name)
    if (groesse === 0xffffffff) groesse = Number(zip64Werte(sicht, p + 46 + namenLaenge, extraLaenge)[0] ?? 0)
    eintraege.push({ pfad, groesse, ordner: pfad.endsWith('/') })
    p += 46 + namenLaenge + extraLaenge + kommentarLaenge
  }
  return eintraege
}

function tarText(daten: Uint8Array, von: number, laenge: number): string {
  const feld = daten.subarray(von, von + laenge)
  const null_ = feld.indexOf(0)
  return utf8.decode(null_ < 0 ? feld : feld.subarray(0, null_))
}

export function tarInhalt(daten: Uint8Array): ArchivEintrag[] {
  const eintraege: ArchivEintrag[] = []
  let p = 0
  let langerName: string | null = null
  while (p + 512 <= daten.length && eintraege.length < ARCHIV_EINTRAEGE_HOECHSTENS) {
    const start = p
    const kopf = daten.subarray(start, start + 512)
    if (kopf.every((b) => b === 0)) break
    const groesse = parseInt(tarText(daten, start + 124, 12).trim() || '0', 8) || 0
    // Eine negative Größe führte zurück auf denselben Kopf, und die Schleife lief ewig.
    if (groesse < 0 || !Number.isSafeInteger(groesse)) break
    const art = String.fromCharCode(kopf[156])
    const inhaltVon = start + 512
    p = inhaltVon + Math.ceil(groesse / 512) * 512
    if (art === 'L' || art === 'x') {
      // GNU-Langname bzw. PAX-Kopf: der Name gilt für den nächsten Eintrag.
      const text = tarText(daten, inhaltVon, groesse)
      langerName = art === 'L' ? text : (/(?:^|\n)\d+ path=([^\n]*)/.exec(text)?.[1] ?? null)
      continue
    }
    if (art === 'g') continue
    const vorsatz = tarText(daten, start + 345, 155)
    const name = langerName ?? (vorsatz ? `${vorsatz}/` : '') + tarText(daten, start, 100)
    langerName = null
    eintraege.push({ pfad: name, groesse: art === '5' ? 0 : groesse, ordner: art === '5' || name.endsWith('/') })
  }
  return eintraege
}

/**
 * So viel entpackt `gunzip` höchstens. Ein .gz von wenigen Kilobyte kann sonst
 * Gigabytes ergeben und die App aus dem Speicher werfen. Für die Inhaltsliste
 * reicht der Anfang; ein abgeschnittenes tar liest `tarInhalt` bis dorthin.
 */
export const GUNZIP_HOECHSTENS = 64 * 1024 * 1024

export async function gunzip(daten: Uint8Array, hoechstens = GUNZIP_HOECHSTENS): Promise<Uint8Array> {
  const leser = new Blob([daten as BlobPart]).stream().pipeThrough(new DecompressionStream('gzip')).getReader()
  const teile: Uint8Array[] = []
  let laenge = 0
  while (laenge < hoechstens) {
    const { done, value } = await leser.read()
    if (done) break
    const stueck = value.subarray(0, hoechstens - laenge)
    teile.push(stueck)
    laenge += stueck.length
  }
  await leser.cancel().catch(() => {})
  const ergebnis = new Uint8Array(laenge)
  let p = 0
  for (const teil of teile) {
    ergebnis.set(teil, p)
    p += teil.length
  }
  return ergebnis
}

/** Erkennt das Archiv an seinen ersten Bytes und liest den Inhalt. */
export async function archivInhalt(daten: Uint8Array): Promise<ArchivEintrag[]> {
  if (daten[0] === 0x50 && daten[1] === 0x4b) return zipInhalt(daten)
  // Ein .gz kann auch eine einzelne gepackte Datei sein; nur ein tar hat Einträge.
  const roh = daten[0] === 0x1f && daten[1] === 0x8b ? await gunzip(daten) : daten
  if (utf8.decode(roh.subarray(257, 262)) === 'ustar') return tarInhalt(roh)
  throw new Error('Unbekanntes Archivformat.')
}
