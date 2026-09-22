/**
 * Serientermine: Regelformat, Prüfung und Ausbreitung — die Clientseite.
 *
 * Spiegel von `backend/services/kalender_serie.py`. Beide Seiten prüfen gegen
 * dieselben Fälle in `tests/fixtures/kalender_serie_vektoren.json`; weichen sie
 * voneinander ab, zeigt die Ansicht etwas anderes an, als die Erinnerung
 * verschickt — und beide Testsuiten sind dabei grün.
 *
 * Zwei Implementierungen sind der Preis der Ende-zu-Ende-Verschlüsselung: die
 * Regel eines E2EE-Termins liegt als `sv-cal-v1:`-Umschlag auf dem Server, der
 * sie nie lesen kann. Ausbreiten kann sie deshalb nur das Gerät.
 *
 * Unterstützte Teilmenge von RFC 5545 (Betreiberentscheid 22.09.2026):
 *
 *     FREQ      DAILY | WEEKLY | MONTHLY | YEARLY
 *     INTERVAL  ganzzahlig >= 1
 *     BYDAY     nur bei WEEKLY, nur MO..SU ohne Zahlpräfix
 *     UNTIL     Datum — oder COUNT, nie beides
 *
 * Alles andere wird abgewiesen statt stillschweigend vereinfacht.
 */

export class SerienRegelFehler extends Error {
  constructor(nachricht: string) {
    super(nachricht)
    this.name = 'SerienRegelFehler'
  }
}

export const FREQUENZEN = ['DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'] as const
export type Frequenz = (typeof FREQUENZEN)[number]

/** RFC-5545-Kürzel. Montag ist 0, wie in `Date.prototype.getUTCDay()` minus eins. */
const WOCHENTAGE: Record<string, number> = {
  MO: 0, TU: 1, WE: 2, TH: 3, FR: 4, SA: 5, SU: 6,
}

const KUERZEL_NACH_NUMMER = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']

export interface Regel {
  freq: Frequenz
  interval: number
  byday: string[]
  until: string | null
  count: number | null
}

export interface Abweichung {
  start?: string
  ende?: string
  titel?: string
}

export interface Serie {
  rrule: string | null
  ausnahmen: string[]
  abweichungen: Record<string, Abweichung>
}

export interface Vorkommen {
  /**
   * Lokales Datum (YYYY-MM-DD) des **ursprünglichen** Vorkommens.
   *
   * Bleibt auch bei einer Abweichung das ursprüngliche Datum — sonst fände
   * eine spätere Änderung ihren eigenen Eintrag nicht wieder.
   */
  schluessel: string
  start: Date
  ende: Date
  istAbweichung: boolean
  titel: string | null
}

export const LEERE_SERIE: Serie = Object.freeze({
  rrule: null,
  ausnahmen: [],
  abweichungen: {},
}) as Serie

/**
 * Obergrenze für die Kandidatenschleife. Greift nur, wenn eine Regel ohne Ende
 * auf ein absurd weites Fenster trifft; im Normalfall beendet das Fenster die
 * Schleife lange vorher.
 */
const MAX_SCHRITTE = 5000

// ── Zeitzonen ─────────────────────────────────────────────────────────────

interface Wanduhr {
  jahr: number
  monat: number
  tag: number
  stunde: number
  minute: number
  sekunde: number
}

const formatierer = new Map<string, Intl.DateTimeFormat>()

function holeFormatierer(zeitzone: string): Intl.DateTimeFormat {
  let f = formatierer.get(zeitzone)
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', {
      timeZone: zeitzone,
      hour12: false,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
    formatierer.set(zeitzone, f)
  }
  return f
}

/** Prüft eine Zeitzonenkennung; unbekannte fallen auf UTC zurück, wie im Backend. */
export function zeitzoneVon(name: string | null | undefined): string {
  const kennung = (name || '').trim()
  if (!kennung) return 'UTC'
  try {
    holeFormatierer(kennung).format(new Date())
    return kennung
  } catch {
    return 'UTC'
  }
}

/** Die Wanduhrzeit eines UTC-Zeitpunkts in der gegebenen Zone. */
function wanduhr(zeitpunkt: Date, zeitzone: string): Wanduhr {
  const teile = holeFormatierer(zeitzone).formatToParts(zeitpunkt)
  const lies = (art: string): number => {
    const t = teile.find((p) => p.type === art)
    return t ? parseInt(t.value, 10) : 0
  }
  return {
    jahr: lies('year'),
    monat: lies('month'),
    tag: lies('day'),
    // Manche Laufzeiten melden Mitternacht als "24" statt "00".
    stunde: lies('hour') % 24,
    minute: lies('minute'),
    sekunde: lies('second'),
  }
}

/** Der UTC-Zeitpunkt, an dem in der Zone diese Wanduhrzeit gilt. */
function nachUtc(uhr: Wanduhr, zeitzone: string): Date {
  const angenommen = Date.UTC(uhr.jahr, uhr.monat - 1, uhr.tag, uhr.stunde, uhr.minute, uhr.sekunde)
  // Zwei Durchgänge: der erste schätzt den Versatz, der zweite korrigiert ihn
  // am Zeitumstellungstag, an dem der Versatz selbst vom Ergebnis abhängt.
  let zeitpunkt = angenommen
  for (let i = 0; i < 2; i++) {
    const gemessen = wanduhr(new Date(zeitpunkt), zeitzone)
    const alsUtc = Date.UTC(
      gemessen.jahr, gemessen.monat - 1, gemessen.tag,
      gemessen.stunde, gemessen.minute, gemessen.sekunde,
    )
    const versatz = alsUtc - zeitpunkt
    const neu = angenommen - versatz
    if (neu === zeitpunkt) break
    zeitpunkt = neu
  }
  return new Date(zeitpunkt)
}

function tageImMonat(jahr: number, monat: number): number {
  return new Date(Date.UTC(jahr, monat, 0)).getUTCDate()
}

function alsDatum(uhr: Wanduhr): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${uhr.jahr}-${p(uhr.monat)}-${p(uhr.tag)}`
}

/** Wochentag (Montag = 0) eines Kalenderdatums. */
function wochentag(jahr: number, monat: number, tag: number): number {
  return (new Date(Date.UTC(jahr, monat - 1, tag)).getUTCDay() + 6) % 7
}

function tagPlus(jahr: number, monat: number, tag: number, tage: number): [number, number, number] {
  const d = new Date(Date.UTC(jahr, monat - 1, tag + tage))
  return [d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate()]
}

function datumKleinerGleich(a: [number, number, number], b: [number, number, number]): boolean {
  if (a[0] !== b[0]) return a[0] < b[0]
  if (a[1] !== b[1]) return a[1] < b[1]
  return a[2] <= b[2]
}

// ── Regel lesen und schreiben ─────────────────────────────────────────────

function untilLesen(roh: string): string {
  let wert = roh.trim().toUpperCase()
  // DATE-TIME (20260110T235959Z) und DATE (20260110). Bei DATE-TIME zählt nur
  // der Tag: UNTIL ist einschließlich, und eine Uhrzeit darin hätte in der
  // Teilmenge keine Bedeutung, die ein Mensch eingestellt hätte.
  if (wert.includes('T')) wert = wert.split('T')[0]
  if (!/^\d{8}$/.test(wert)) {
    throw new SerienRegelFehler(`UNTIL ist kein Datum: ${JSON.stringify(roh)}`)
  }
  const jahr = parseInt(wert.slice(0, 4), 10)
  const monat = parseInt(wert.slice(4, 6), 10)
  const tag = parseInt(wert.slice(6, 8), 10)
  if (monat < 1 || monat > 12 || tag < 1 || tag > tageImMonat(jahr, monat)) {
    throw new SerienRegelFehler(`UNTIL ist kein Datum: ${JSON.stringify(roh)}`)
  }
  const p = (n: number) => String(n).padStart(2, '0')
  return `${jahr}-${p(monat)}-${p(tag)}`
}

/** Liest und **prüft** eine RRULE. Wirft bei allem außerhalb der Teilmenge. */
export function regelLesen(rrule: string): Regel {
  let text = (rrule || '').trim()
  if (text.toUpperCase().startsWith('RRULE:')) text = text.slice(6).trim()
  if (!text) throw new SerienRegelFehler('Leere Wiederholungsregel.')

  const teile: Record<string, string> = {}
  for (const roh of text.split(';')) {
    const stueck = roh.trim()
    if (!stueck) continue
    if (!stueck.includes('=')) {
      throw new SerienRegelFehler(`Bestandteil ohne Wert: ${JSON.stringify(stueck)}`)
    }
    const schnitt = stueck.indexOf('=')
    const schluessel = stueck.slice(0, schnitt).trim().toUpperCase()
    if (schluessel in teile) {
      throw new SerienRegelFehler(`${schluessel} steht doppelt in der Regel.`)
    }
    teile[schluessel] = stueck.slice(schnitt + 1).trim()
  }

  if (Object.keys(teile).length === 0) {
    throw new SerienRegelFehler('Leere Wiederholungsregel.')
  }

  const erlaubt = new Set(['FREQ', 'INTERVAL', 'BYDAY', 'UNTIL', 'COUNT'])
  const unbekannt = Object.keys(teile).filter((k) => !erlaubt.has(k)).sort()
  if (unbekannt.length) {
    throw new SerienRegelFehler(
      `Nicht unterstützt: ${unbekannt.join(', ')}. Erlaubt sind FREQ, INTERVAL, BYDAY, UNTIL, COUNT.`,
    )
  }

  const freq = (teile.FREQ || '').toUpperCase()
  if (!freq) throw new SerienRegelFehler('FREQ fehlt.')
  if (!(FREQUENZEN as readonly string[]).includes(freq)) {
    throw new SerienRegelFehler(`FREQ=${freq} gehört nicht zur Teilmenge (${FREQUENZEN.join(', ')}).`)
  }

  let interval = 1
  if ('INTERVAL' in teile) {
    if (!/^-?\d+$/.test(teile.INTERVAL)) {
      throw new SerienRegelFehler(`INTERVAL ist keine Zahl: ${JSON.stringify(teile.INTERVAL)}`)
    }
    interval = parseInt(teile.INTERVAL, 10)
    if (interval < 1) throw new SerienRegelFehler('INTERVAL muss mindestens 1 sein.')
  }

  let byday: string[] = []
  if ('BYDAY' in teile) {
    if (freq !== 'WEEKLY') {
      throw new SerienRegelFehler('BYDAY wird nur bei FREQ=WEEKLY unterstützt.')
    }
    const roh = teile.BYDAY.split(',').map((t) => t.trim().toUpperCase()).filter(Boolean)
    if (!roh.length) throw new SerienRegelFehler('BYDAY ist leer.')
    for (const tag of roh) {
      if (!(tag in WOCHENTAGE)) {
        throw new SerienRegelFehler(
          `BYDAY=${tag} wird nicht unterstützt (erlaubt: MO, TU, WE, TH, FR, SA, SU ohne Zahlpräfix).`,
        )
      }
    }
    // Sortiert und ohne Dubletten, damit dieselbe Regel immer denselben Text
    // ergibt — sonst hinge der Ciphertext an der Reihenfolge der Eingabe.
    byday = [...new Set(roh)].sort((a, b) => WOCHENTAGE[a] - WOCHENTAGE[b])
  }

  if ('UNTIL' in teile && 'COUNT' in teile) {
    throw new SerienRegelFehler('UNTIL und COUNT schließen einander aus.')
  }

  const until = 'UNTIL' in teile ? untilLesen(teile.UNTIL) : null

  let count: number | null = null
  if ('COUNT' in teile) {
    if (!/^-?\d+$/.test(teile.COUNT)) {
      throw new SerienRegelFehler(`COUNT ist keine Zahl: ${JSON.stringify(teile.COUNT)}`)
    }
    count = parseInt(teile.COUNT, 10)
    if (count < 1) throw new SerienRegelFehler('COUNT muss mindestens 1 sein.')
  }

  return { freq: freq as Frequenz, interval, byday, until, count }
}

/** Kanonische Textform — dieselbe Regel ergibt immer denselben String. */
export function regelSchreiben(regel: Regel): string {
  const stuecke = [`FREQ=${regel.freq}`]
  if (regel.interval !== 1) stuecke.push(`INTERVAL=${regel.interval}`)
  if (regel.byday.length) stuecke.push(`BYDAY=${regel.byday.join(',')}`)
  if (regel.until) stuecke.push(`UNTIL=${regel.until.replace(/-/g, '')}`)
  if (regel.count !== null) stuecke.push(`COUNT=${regel.count}`)
  return stuecke.join(';')
}

// ── Das Dokument im Feld `recurrence` ─────────────────────────────────────

function datumSchluessel(roh: unknown): string {
  let text = String(roh ?? '').trim()
  if (text.includes('T')) text = text.split('T')[0]
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    throw new SerienRegelFehler(`Kein gültiges Datum: ${JSON.stringify(roh)}`)
  }
  const [j, m, t] = text.split('-').map((n) => parseInt(n, 10))
  if (m < 1 || m > 12 || t < 1 || t > tageImMonat(j, m)) {
    throw new SerienRegelFehler(`Kein gültiges Datum: ${JSON.stringify(roh)}`)
  }
  return text
}

/**
 * Liest das entschlüsselte Dokument.
 *
 * Nachsichtig: alte Termine ohne Dokument, leere Strings, noch verschlüsselte
 * Umschläge und unlesbares JSON gelten als "kein Serientermin". Ein Termin ist
 * wichtiger als seine Wiederholung — wer hier wirft, lässt einen ganzen
 * Kalender verschwinden, weil eine einzige Zeile verbogen ist.
 */
export function serieLesen(klartext: string | null | undefined): Serie {
  const text = (klartext || '').trim()
  if (!text) return LEERE_SERIE

  let roh: unknown
  try {
    roh = JSON.parse(text)
  } catch {
    return LEERE_SERIE
  }
  if (!roh || typeof roh !== 'object' || Array.isArray(roh)) return LEERE_SERIE
  const dokument = roh as Record<string, unknown>

  let rrule: string | null = null
  if (dokument.rrule != null) {
    const kandidat = String(dokument.rrule).trim()
    if (kandidat) {
      try {
        // Kanonisieren, damit eine von Hand geschriebene Regel dieselbe
        // Gestalt bekommt wie eine erzeugte.
        rrule = regelSchreiben(regelLesen(kandidat))
      } catch {
        // Unlesbare Regel: der Termin bleibt, die Wiederholung fällt weg.
        return LEERE_SERIE
      }
    }
  }

  const ausnahmen: string[] = []
  const rohAusnahmen = dokument.ausnahmen
  if (Array.isArray(rohAusnahmen)) {
    for (const eintrag of rohAusnahmen) {
      try {
        const tag = datumSchluessel(eintrag)
        if (!ausnahmen.includes(tag)) ausnahmen.push(tag)
      } catch {
        // einzelner unbrauchbarer Eintrag, der Rest gilt weiter
      }
    }
  }

  const abweichungen: Record<string, Abweichung> = {}
  const rohAbweichungen = dokument.abweichungen
  if (rohAbweichungen && typeof rohAbweichungen === 'object' && !Array.isArray(rohAbweichungen)) {
    for (const [schluessel, wert] of Object.entries(rohAbweichungen as Record<string, unknown>)) {
      if (!wert || typeof wert !== 'object' || Array.isArray(wert)) continue
      try {
        abweichungen[datumSchluessel(schluessel)] = wert as Abweichung
      } catch {
        // dito
      }
    }
  }

  return { rrule, ausnahmen: ausnahmen.sort(), abweichungen }
}

/** Kanonische Klartextform des Dokuments, bereit zum Verschlüsseln. */
export function serieSchreiben(serie: Serie): string {
  // Schlüssel alphabetisch und ohne Leerzeichen — dieselbe Reihenfolge, die
  // `json.dumps(..., sort_keys=True, separators=(",", ":"))` im Backend
  // erzeugt, damit dasselbe Dokument auf beiden Seiten gleich aussieht.
  const teile: string[] = []
  if (Object.keys(serie.abweichungen || {}).length) {
    const sortiert: Record<string, Abweichung> = {}
    for (const k of Object.keys(serie.abweichungen).sort()) sortiert[k] = serie.abweichungen[k]
    teile.push(`"abweichungen":${JSON.stringify(sortiert)}`)
  }
  if ((serie.ausnahmen || []).length) {
    teile.push(`"ausnahmen":${JSON.stringify([...serie.ausnahmen].sort())}`)
  }
  teile.push(`"rrule":${JSON.stringify(serie.rrule ?? null)}`)
  return `{${teile.join(',')}}`
}

export const LEERES_DOKUMENT = serieSchreiben(LEERE_SERIE)

/** Baut eine Serie aus Takt und Zubehör — der Weg, den das Formular nimmt. */
export function serieBauen(
  takt: Frequenz | null,
  optionen: { intervall?: number; wochentage?: string[]; bis?: string | null; anzahl?: number | null } = {},
  vorhandene?: Serie,
): Serie {
  if (!takt) {
    return { rrule: null, ausnahmen: [], abweichungen: {} }
  }
  const stuecke = [`FREQ=${takt}`]
  if (optionen.intervall && optionen.intervall > 1) stuecke.push(`INTERVAL=${optionen.intervall}`)
  if (takt === 'WEEKLY' && optionen.wochentage?.length) {
    stuecke.push(`BYDAY=${optionen.wochentage.join(',')}`)
  }
  if (optionen.bis) stuecke.push(`UNTIL=${optionen.bis.replace(/-/g, '')}`)
  else if (optionen.anzahl) stuecke.push(`COUNT=${optionen.anzahl}`)

  return {
    rrule: regelSchreiben(regelLesen(stuecke.join(';'))),
    // Ausnahmen und Abweichungen überleben eine Taktänderung: wer den Takt
    // korrigiert, will nicht auch abgesagte Einzeltermine zurückholen.
    ausnahmen: vorhandene?.ausnahmen ?? [],
    abweichungen: vorhandene?.abweichungen ?? {},
  }
}

// ── Ausbreitung ───────────────────────────────────────────────────────────

interface Kandidat {
  datum: string
  start: Date
}

function* lokaleKandidaten(
  regel: Regel,
  startUhr: Wanduhr,
  zeitzone: string,
  grenze: [number, number, number] | null,
): Generator<Kandidat> {
  const uhrzeit = { stunde: startUhr.stunde, minute: startUhr.minute, sekunde: startUhr.sekunde }
  let geliefert = 0

  const baue = (jahr: number, monat: number, tag: number): Kandidat => {
    const uhr: Wanduhr = { jahr, monat, tag, ...uhrzeit }
    return { datum: alsDatum(uhr), start: nachUtc(uhr, zeitzone) }
  }

  // Das Fenster begrenzt nur, wenn kein COUNT mitzählt: bei COUNT muss bis zum
  // letzten Vorkommen gezählt werden, auch weit hinter dem sichtbaren Bereich,
  // sonst stimmt die Zahl nicht.
  const fertig = (jahr: number, monat: number, tag: number): boolean => {
    if (regel.until) {
      const [uj, um, ut] = regel.until.split('-').map((n) => parseInt(n, 10))
      if (!datumKleinerGleich([jahr, monat, tag], [uj, um, ut])) return true
    }
    if (regel.count !== null && geliefert >= regel.count) return true
    if (regel.count === null && grenze && !datumKleinerGleich([jahr, monat, tag], grenze)) return true
    return false
  }

  if (regel.freq === 'DAILY' || regel.freq === 'WEEKLY') {
    if (regel.freq === 'WEEKLY' && regel.byday.length) {
      const gewuenscht = regel.byday.map((t) => WOCHENTAGE[t]).sort((a, b) => a - b)
      // Anker ist der Montag der Woche, in der DTSTART liegt (WKST=MO).
      const versatz = wochentag(startUhr.jahr, startUhr.monat, startUhr.tag)
      const [aj, am, at] = tagPlus(startUhr.jahr, startUhr.monat, startUhr.tag, -versatz)
      for (let n = 0; n < MAX_SCHRITTE; n++) {
        const [wj, wm, wt] = tagPlus(aj, am, at, n * 7 * regel.interval)
        if (regel.count === null && grenze && !datumKleinerGleich([wj, wm, wt], grenze)) return
        if (wj > 9999) return
        for (const tagNr of gewuenscht) {
          const [j, m, t] = tagPlus(wj, wm, wt, tagNr)
          if (!datumKleinerGleich([startUhr.jahr, startUhr.monat, startUhr.tag], [j, m, t])) continue
          if (fertig(j, m, t)) return
          geliefert++
          yield baue(j, m, t)
        }
      }
      return
    }

    const schrittTage = regel.interval * (regel.freq === 'WEEKLY' ? 7 : 1)
    for (let n = 0; n < MAX_SCHRITTE; n++) {
      const [j, m, t] = tagPlus(startUhr.jahr, startUhr.monat, startUhr.tag, n * schrittTage)
      if (j > 9999 || Number.isNaN(j)) return
      if (fertig(j, m, t)) return
      geliefert++
      yield baue(j, m, t)
    }
    return
  }

  if (regel.freq === 'MONTHLY') {
    const tag = startUhr.tag
    for (let n = 0; n < MAX_SCHRITTE; n++) {
      const gesamt = (startUhr.jahr * 12 + (startUhr.monat - 1)) + n * regel.interval
      const jahr = Math.floor(gesamt / 12)
      const monat = (gesamt % 12) + 1
      // Hinter dem Jahr 9999 gibt es keinen Kalender mehr.
      if (jahr > 9999) return
      const letzter = tageImMonat(jahr, monat)
      if (tag > letzter) {
        // Den 31. gibt es im Februar nicht. RFC 5545: überspringen, nicht
        // verschieben — sonst stünde die Miete im Februar am 28. und im März
        // wieder am 31.
        if (regel.count === null && grenze && !datumKleinerGleich([jahr, monat, letzter], grenze)) return
        continue
      }
      if (fertig(jahr, monat, tag)) return
      geliefert++
      yield baue(jahr, monat, tag)
    }
    return
  }

  // YEARLY
  const { monat, tag } = startUhr
  for (let n = 0; n < MAX_SCHRITTE; n++) {
    const jahr = startUhr.jahr + n * regel.interval
    if (jahr > 9999) return
    const letzter = tageImMonat(jahr, monat)
    if (tag > letzter) {
      // Der 29. Februar, außerhalb der Schaltjahre.
      if (regel.count === null && grenze && !datumKleinerGleich([jahr, monat, letzter], grenze)) return
      continue
    }
    if (fertig(jahr, monat, tag)) return
    geliefert++
    yield baue(jahr, monat, tag)
  }
}

function zeitpunktLesen(roh: unknown): Date | null {
  const text = String(roh ?? '').trim()
  if (!text) return null
  const d = new Date(text.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(text) ? text : `${text}Z`)
  return Number.isNaN(d.getTime()) ? null : d
}

export interface AusbreitungsOptionen {
  ganztaegig?: boolean
  zeitzone?: string | null
  fensterVon?: Date | null
  fensterBis?: Date | null
}

/**
 * Rechnet die Vorkommen einer Serie im Fenster aus.
 *
 * Ein Vorkommen gehört dazu, wenn es den Bereich berührt — ein dreitägiger
 * Termin erscheint auch, wenn nur sein Mittelteil im Fenster liegt.
 */
export function ausbreiten(
  serie: Serie,
  start: Date,
  ende: Date,
  optionen: AusbreitungsOptionen = {},
): Vorkommen[] {
  const zeitzone = zeitzoneVon(optionen.zeitzone)
  const ganztaegig = !!optionen.ganztaegig
  const fensterVon = optionen.fensterVon ?? null
  const fensterBis = optionen.fensterBis ?? null

  let dauer = ende.getTime() - start.getTime()
  if (dauer < 0) dauer = 0
  // Ganztägige Termine rechnen in Tagen, nicht in Stunden: über einen
  // Zeitumstellungstag ist ein Tag 23 oder 25 Stunden lang, und der Termin
  // soll trotzdem um Mitternacht enden.
  const ganzeTage = ganztaegig ? Math.max(1, Math.round(dauer / 86400000)) : 0

  const startUhr = wanduhr(start, zeitzone)
  const ergebnis: Vorkommen[] = []

  const imFenster = (a: Date, b: Date): boolean => {
    if (fensterBis && a.getTime() > fensterBis.getTime()) return false
    if (fensterVon && b.getTime() < fensterVon.getTime()) return false
    return true
  }

  const eintragen = (schluessel: string, rohStart: Date, rohEnde: Date): void => {
    if (serie.ausnahmen.includes(schluessel)) return
    let a = rohStart
    let b = rohEnde
    let istAbweichung = false
    let titel: string | null = null

    const abweichung = serie.abweichungen[schluessel]
    if (abweichung) {
      const neuA = zeitpunktLesen(abweichung.start)
      const neuB = zeitpunktLesen(abweichung.ende)
      if (neuA) {
        b = neuB ?? new Date(neuA.getTime() + (b.getTime() - a.getTime()))
        a = neuA
      } else if (neuB) {
        b = neuB
      }
      titel = abweichung.titel ? String(abweichung.titel) : null
      istAbweichung = true
    }

    if (!imFenster(a, b)) return
    ergebnis.push({ schluessel, start: a, ende: b, istAbweichung, titel })
  }

  const endeFuer = (beginn: Date): Date => {
    if (!ganztaegig) return new Date(beginn.getTime() + dauer)
    const uhr = wanduhr(beginn, zeitzone)
    const [j, m, t] = tagPlus(uhr.jahr, uhr.monat, uhr.tag, ganzeTage)
    return nachUtc({ ...uhr, jahr: j, monat: m, tag: t }, zeitzone)
  }

  if (!serie.rrule) {
    eintragen(alsDatum(startUhr), start, ende)
    return ergebnis
  }

  let regel: Regel
  try {
    regel = regelLesen(serie.rrule)
  } catch {
    // `serieLesen` fängt das schon ab; hier steht es für den Fall, dass jemand
    // eine Serie von Hand zusammensetzt.
    eintragen(alsDatum(startUhr), start, ende)
    return ergebnis
  }

  let grenze: [number, number, number] | null = null
  if (fensterBis) {
    const u = wanduhr(fensterBis, zeitzone)
    grenze = tagPlus(u.jahr, u.monat, u.tag, 1)
  }

  for (const kandidat of lokaleKandidaten(regel, startUhr, zeitzone, grenze)) {
    eintragen(kandidat.datum, kandidat.start, endeFuer(kandidat.start))
  }

  // Eine Abweichung kann ein Vorkommen nach hinten verschieben; sortieren,
  // damit die Ansicht nicht springt.
  ergebnis.sort((a, b) => a.start.getTime() - b.start.getTime())
  return ergebnis
}

// ── Für Menschen ──────────────────────────────────────────────────────────

const KURZ_TAKT: Record<Frequenz, [string, string]> = {
  DAILY: ['täglich', 'alle {n} Tage'],
  WEEKLY: ['wöchentlich', 'alle {n} Wochen'],
  MONTHLY: ['monatlich', 'alle {n} Monate'],
  YEARLY: ['jährlich', 'alle {n} Jahre'],
}

const KURZ_TAGE: Record<string, string> = {
  MO: 'Mo', TU: 'Di', WE: 'Mi', TH: 'Do', FR: 'Fr', SA: 'Sa', SU: 'So',
}

/** Eine Zeile, die ein Mensch lesen kann. Spiegelt `kalender_serie.kurzform`. */
export function kurzform(serie: Serie): string {
  if (!serie.rrule) return 'einmalig'
  let regel: Regel
  try {
    regel = regelLesen(serie.rrule)
  } catch {
    return 'einmalig'
  }

  const [einfach, mehrfach] = KURZ_TAKT[regel.freq]
  let text = regel.interval === 1 ? einfach : mehrfach.replace('{n}', String(regel.interval))

  if (regel.byday.length) {
    text += ` (${regel.byday.map((t) => KURZ_TAGE[t]).join(', ')})`
  }

  if (regel.until) {
    const [j, m, t] = regel.until.split('-')
    text += ` bis ${t}.${m}.${j}`
  } else if (regel.count !== null) {
    text += `, ${regel.count} Termine`
  } else {
    text += ', ohne Ende'
  }

  if (serie.ausnahmen.length) text += `, ${serie.ausnahmen.length} ausgenommen`
  const anzahlAbweichungen = Object.keys(serie.abweichungen).length
  if (anzahlAbweichungen) text += `, ${anzahlAbweichungen} verschoben`
  return text
}

/** Die Bestandteile einer Regel für das Formular. */
export function regelZerlegen(serie: Serie): {
  takt: Frequenz | null
  intervall: number
  wochentage: string[]
  bis: string | null
  anzahl: number | null
} {
  if (!serie.rrule) {
    return { takt: null, intervall: 1, wochentage: [], bis: null, anzahl: null }
  }
  try {
    const r = regelLesen(serie.rrule)
    return { takt: r.freq, intervall: r.interval, wochentage: r.byday, bis: r.until, anzahl: r.count }
  } catch {
    return { takt: null, intervall: 1, wochentage: [], bis: null, anzahl: null }
  }
}

export { KUERZEL_NACH_NUMMER as WOCHENTAG_KUERZEL }
