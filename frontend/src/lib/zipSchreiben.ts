/**
 * Ein Zip-Archiv ohne Kompression, für den Datenexport.
 *
 * Absichtlich klein statt einer Bibliothek: nur Dateien ablegen, kein Lesen,
 * kein Deflate, kein Zip64. Jedes Betriebssystem öffnet das Ergebnis. Grenzen
 * sind 65.535 Dateien und 4 GiB, darüber wirft die Funktion.
 */

export interface ZipEintrag {
  pfad: string
  inhalt: Uint8Array | string
}

const CRC_TABELLE = (() => {
  const tabelle = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    tabelle[n] = c >>> 0
  }
  return tabelle
})()

/** CRC-32 über `daten`; mit `bisher` läuft sie über mehrere Stücke weiter. */
export function crc32(daten: Uint8Array, bisher = 0): number {
  let crc = (bisher ^ 0xffffffff) >>> 0
  for (let i = 0; i < daten.length; i++) crc = CRC_TABELLE[(crc ^ daten[i]) & 0xff] ^ (crc >>> 8)
  return (crc ^ 0xffffffff) >>> 0
}

function dosZeit(datum: Date): { zeit: number; tag: number } {
  return {
    zeit: (datum.getHours() << 11) | (datum.getMinutes() << 5) | Math.floor(datum.getSeconds() / 2),
    tag: ((datum.getFullYear() - 1980) << 9) | ((datum.getMonth() + 1) << 5) | datum.getDate(),
  }
}

export function zipSchreiben(eintraege: ZipEintrag[], datum = new Date()): Blob {
  if (eintraege.length > 0xffff) throw new Error('Zu viele Dateien für ein Zip ohne Zip64.')
  const kodierer = new TextEncoder()
  const { zeit, tag } = dosZeit(datum)
  const teile: Uint8Array[] = []
  const verzeichnis: Uint8Array[] = []
  let versatz = 0

  for (const eintrag of eintraege) {
    const name = kodierer.encode(eintrag.pfad)
    const inhalt = typeof eintrag.inhalt === 'string' ? kodierer.encode(eintrag.inhalt) : eintrag.inhalt
    const pruefsumme = crc32(inhalt)

    const kopf = new DataView(new ArrayBuffer(30))
    kopf.setUint32(0, 0x04034b50, true)
    kopf.setUint16(4, 20, true)
    kopf.setUint16(6, 0x0800, true) // Namen in UTF-8
    kopf.setUint16(8, 0, true) // gespeichert, nicht komprimiert
    kopf.setUint16(10, zeit, true)
    kopf.setUint16(12, tag, true)
    kopf.setUint32(14, pruefsumme, true)
    kopf.setUint32(18, inhalt.length, true)
    kopf.setUint32(22, inhalt.length, true)
    kopf.setUint16(26, name.length, true)
    kopf.setUint16(28, 0, true)

    const zentral = new DataView(new ArrayBuffer(46))
    zentral.setUint32(0, 0x02014b50, true)
    zentral.setUint16(4, 20, true)
    zentral.setUint16(6, 20, true)
    zentral.setUint16(8, 0x0800, true)
    zentral.setUint16(10, 0, true)
    zentral.setUint16(12, zeit, true)
    zentral.setUint16(14, tag, true)
    zentral.setUint32(16, pruefsumme, true)
    zentral.setUint32(20, inhalt.length, true)
    zentral.setUint32(24, inhalt.length, true)
    zentral.setUint16(28, name.length, true)
    zentral.setUint32(42, versatz, true)

    teile.push(new Uint8Array(kopf.buffer), name, inhalt)
    verzeichnis.push(new Uint8Array(zentral.buffer), name)
    versatz += 30 + name.length + inhalt.length
    if (versatz > 0xffffffff) throw new Error('Das Archiv wird größer als 4 GiB.')
  }

  const verzeichnisGroesse = verzeichnis.reduce((summe, teil) => summe + teil.length, 0)
  const ende = new DataView(new ArrayBuffer(22))
  ende.setUint32(0, 0x06054b50, true)
  ende.setUint16(8, eintraege.length, true)
  ende.setUint16(10, eintraege.length, true)
  ende.setUint32(12, verzeichnisGroesse, true)
  ende.setUint32(16, versatz, true)

  return new Blob([...teile, ...verzeichnis, new Uint8Array(ende.buffer)] as BlobPart[], {
    type: 'application/zip',
  })
}

export interface ZipStromEintrag {
  pfad: string
  /** Länge des Inhalts in Bytes, vorher bekannt. */
  groesse: number
  datum?: Date
  /** Liefert den Inhalt Stück für Stück, erst wenn der Eintrag an der Reihe ist. */
  teile: () => AsyncIterable<Uint8Array>
}

/** Das Archiv sprengt die Grenzen eines Zip ohne Zip64. */
export class ZipZuGross extends Error {
  constructor(readonly grund: 'dateien' | 'groesse') {
    super(grund === 'dateien' ? 'Zu viele Dateien für ein Zip ohne Zip64.' : 'Das Archiv wird größer als 4 GiB.')
  }
}

/** Wirft `ZipZuGross`, bevor etwas geschrieben ist, wenn das Archiv nicht passt. */
export function zipGrenzenPruefen(eintraege: Pick<ZipStromEintrag, 'pfad' | 'groesse'>[]): void {
  if (eintraege.length > 0xffff) throw new ZipZuGross('dateien')
  const kodierer = new TextEncoder()
  let summe = 22
  for (const e of eintraege) summe += 30 + 16 + 46 + 2 * kodierer.encode(e.pfad).length + e.groesse
  if (summe > 0xffffffff) throw new ZipZuGross('groesse')
}

/**
 * Schreibt ein Zip ohne Kompression als Strom, für Inhalte, die nie ganz im
 * Speicher liegen sollen (Tresor-Dateien). Die Prüfsumme ist erst nach dem
 * Inhalt bekannt; sie steht deshalb im Datenbeschreiber hinter jedem Eintrag
 * (Bit 3) und im zentralen Verzeichnis am Ende, das jedes Entpackprogramm liest.
 */
export async function* zipStrom(eintraege: ZipStromEintrag[]): AsyncGenerator<Uint8Array> {
  zipGrenzenPruefen(eintraege)
  const kodierer = new TextEncoder()
  const verzeichnis: Uint8Array[] = []
  let versatz = 0

  for (const eintrag of eintraege) {
    const name = kodierer.encode(eintrag.pfad)
    const { zeit, tag } = dosZeit(eintrag.datum ?? new Date())

    const kopf = new DataView(new ArrayBuffer(30))
    kopf.setUint32(0, 0x04034b50, true)
    kopf.setUint16(4, 20, true)
    kopf.setUint16(6, 0x0808, true) // Namen in UTF-8, Prüfsumme im Datenbeschreiber
    kopf.setUint16(8, 0, true)
    kopf.setUint16(10, zeit, true)
    kopf.setUint16(12, tag, true)
    kopf.setUint16(26, name.length, true)
    yield new Uint8Array(kopf.buffer)
    yield name

    let pruefsumme = 0
    let laenge = 0
    for await (const teil of eintrag.teile()) {
      pruefsumme = crc32(teil, pruefsumme)
      laenge += teil.length
      yield teil
    }
    // Eine abweichende Länge hieße: Größe und Inhalt passen nicht zusammen.
    if (laenge !== eintrag.groesse) throw new Error(`Inhalt von ${eintrag.pfad} hat ${laenge} statt ${eintrag.groesse} Bytes.`)

    const beschreiber = new DataView(new ArrayBuffer(16))
    beschreiber.setUint32(0, 0x08074b50, true)
    beschreiber.setUint32(4, pruefsumme, true)
    beschreiber.setUint32(8, laenge, true)
    beschreiber.setUint32(12, laenge, true)
    yield new Uint8Array(beschreiber.buffer)

    const zentral = new DataView(new ArrayBuffer(46))
    zentral.setUint32(0, 0x02014b50, true)
    zentral.setUint16(4, 20, true)
    zentral.setUint16(6, 20, true)
    zentral.setUint16(8, 0x0808, true)
    zentral.setUint16(12, zeit, true)
    zentral.setUint16(14, tag, true)
    zentral.setUint32(16, pruefsumme, true)
    zentral.setUint32(20, laenge, true)
    zentral.setUint32(24, laenge, true)
    zentral.setUint16(28, name.length, true)
    zentral.setUint32(42, versatz, true)
    verzeichnis.push(new Uint8Array(zentral.buffer), name)
    versatz += 30 + name.length + laenge + 16
  }

  const verzeichnisGroesse = verzeichnis.reduce((summe, teil) => summe + teil.length, 0)
  for (const teil of verzeichnis) yield teil
  const ende = new DataView(new ArrayBuffer(22))
  ende.setUint32(0, 0x06054b50, true)
  ende.setUint16(8, eintraege.length, true)
  ende.setUint16(10, eintraege.length, true)
  ende.setUint32(12, verzeichnisGroesse, true)
  ende.setUint32(16, versatz, true)
  yield new Uint8Array(ende.buffer)
}
