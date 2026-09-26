/**
 * Funken und Augenblicke — die Zustandsmaschine, ohne Server.
 *
 * Ein **Augenblick** ist ein Kamerafoto, das als gewöhnliche verschlüsselte
 * Nachricht reist, mit der Marke `augenblick` in der Nutzlast. Ein **Funke**
 * ist die Serie, die entsteht, wenn beide Seiten einander täglich einen
 * Augenblick schicken.
 *
 * ## Warum hier und nicht beim Server
 *
 * Wer mit wem einen Funken hat und wie lange, ist eine Aussage über eine
 * Beziehung. Der Server soll sie nicht kennen, also auch nicht speichern,
 * ändern, löschen oder zurückholen können. Es gibt deshalb keine Tabelle: der
 * Zustand wird auf jedem Gerät aus den Augenblicken selbst gerechnet — aus
 * Nachrichten, die der Server nur als Umschlag sieht. Beide Seiten rechnen
 * mit denselben Ereignissen und kommen zum selben Ergebnis.
 *
 * ## Warum Augenblicke Nachrichten sind und keine Steuerpakete
 *
 * Steuerpakete erreichen die Gegenseite, aber nie die eigenen Zweitgeräte
 * (`relay_blind_envelope` weist Selbstadressiertes ab). Ein am Telefon
 * geschickter Augenblick fehlte dann am Rechner, und der Funke dort erlösche.
 * Eine Nachricht fächert an alle Geräte beider Seiten. Dasselbe gilt für die
 * Wiederherstellung (`funken_rettung`).
 *
 * ## Die Regeln
 *
 * - **Zyklus.** Ab dem Zyklusbeginn haben beide 24 Stunden, je einen
 *   Augenblick zu schicken. Sobald beide haben: Funke +1.
 * - **Höchstens +1 je Tag.** Der nächste Zyklus beginnt frühestens 24 Stunden
 *   nach dem vorigen. Augenblicke davor zählen nicht mehr für heute. Ohne
 *   diese Regel ließen sich in einer Minute zehn „Tage" sammeln.
 * - **Puffer.** Fehlt nach 24 Stunden noch jemand, bleiben weitere 24.
 * - **Verlängerung.** Schickt eine Seite im Puffer, während die andere noch
 *   fehlt, hat die andere ab da 72 Stunden.
 * - **Erlöschen.** Läuft die Frist ab, fällt der Funke auf 0; der alte Stand
 *   bleibt als `verloren` für die Wiederherstellung.
 * - **Wiederherstellung.** Solange kein neuer Funke läuft, einmal je 90 Tage
 *   und Kontakt. Der Abstand gilt für das Paar, nicht je Person.
 * - **Anfang.** Aus dem Stand 0 heraus gibt es keinen Puffer: wer nach dem
 *   ersten Augenblick 24 Stunden keine Antwort bekommt, fängt neu an.
 *
 * ## Zeitpunkte
 *
 * Gerechnet wird mit dem Zeitpunkt aus der unterschriebenen Nutzlast, damit
 * Absender und Empfänger dieselbe Zahl sehen. Damit niemand einen Augenblick
 * zurückdatiert, um einen erloschenen Funken zu retten, wird er an die Ankunft
 * beim Server geklemmt: höchstens eine Stunde davor, höchstens fünf Minuten
 * danach.
 *
 * Diese Datei ist rein: keine Ablage, kein Netz, keine Uhr. `jetzt` kommt
 * immer von außen.
 */

import { zeitAlsZahl } from './nachrichtBezug'

export const STUNDE_MS = 60 * 60 * 1000
export const TAG_MS = 24 * STUNDE_MS
/** Der Puffer nach dem regulären Tag. */
export const PUFFER_MS = 24 * STUNDE_MS
/** Die Frist für die andere Seite, wenn eine im Puffer schickt. */
export const VERLAENGERUNG_MS = 72 * STUNDE_MS
/** So lange liegt zwischen zwei Wiederherstellungen mit demselben Kontakt. */
export const RETTUNG_ABSTAND_MS = 90 * TAG_MS
/** Die Stufen, für die es eine Errungenschaft gibt. */
export const MEILENSTEINE = [10, 100, 1000, 10000] as const

/**
 * So lange bleibt ein Ereignis einzeln in der Akte, bevor es in die Basis
 * gefaltet wird.
 *
 * Die längste Frist ist Tag + Puffer + Verlängerung, fünf Tage. Acht lassen
 * Raum für eine Nachricht, die verspätet aus der Mailbox kommt: was später als
 * das eintrifft, zählt nicht mehr.
 */
export const NACHLAUF_MS = 8 * TAG_MS
/** Mehr Einzelereignisse hält eine Akte nicht; die ältesten falten zuerst. */
export const EREIGNIS_DECKEL = 150
const RUECKDATIERUNG_MS = 60 * 60 * 1000
const VORDATIERUNG_MS = 5 * 60 * 1000
/** Obergrenze für jede Zahl aus fremder Hand: gut 270 Jahre. */
const STAND_DECKEL = 100_000

/** Der Zustand des Paares, aus keiner Seite gesehen — beide rechnen denselben. */
export interface FunkenKern {
  stand: number
  /** Beginn des laufenden Zyklus (ms). `null`: es läuft keiner. */
  zyklusStart: number | null
  /** Konto → Zeitpunkt seines ersten Augenblicks im laufenden Zyklus. */
  gesendet: Record<string, number>
  /** Die verlängerte Frist (ms), solange eine läuft. */
  frist: number | null
  /** Der Stand vor dem Erlöschen, für die Wiederherstellung. */
  verloren: number
  verlorenAm: number | null
  /** Die letzte Wiederherstellung dieses Paares. */
  gerettetAm: number | null
  /** Der höchste je erreichte Stand. Trägt die Errungenschaften. */
  rekord: number
}

export type FunkenEreignisArt = 'augenblick' | 'rettung'

export interface FunkenEreignis {
  /** `<konto>:<logische Kennung>` — dieselbe Nachricht zählt einmal. */
  kennung: string
  von: number
  at: number
  art: FunkenEreignisArt
  /**
   * Wie der Absender den Funken **vor** diesem Ereignis sah.
   *
   * Nur für ein Gerät, das noch nichts weiß (neu angemeldet, Ablage geleert):
   * die Mailbox gibt die letzten hundert Umschläge her, ein Funke von 400
   * Tagen ließe sich daraus nicht nachrechnen. Ein Gerät mit eigener Akte
   * rechnet selbst.
   */
  vorher?: FunkenKern
}

/** Was ein Gerät über den Funken mit einem Kontakt ablegt. */
export interface FunkenAkte {
  partnerId: number
  /** Der Stand bei `bis`, mit allen Ereignissen bis dahin. */
  basis: FunkenKern
  /** Bis hierher ist gefaltet (ms). `0`: noch nie. Älteres zählt nicht mehr. */
  bis: number
  /** Die jüngeren Ereignisse, einzeln und nach Zeit sortiert. */
  ereignisse: FunkenEreignis[]
  /**
   * Bis hierher ist alles vergessen (ms): Freundschaft beendet oder blockiert.
   *
   * Eine Grenze statt einer gelöschten Zeile. Die Augenblicke liegen weiter in
   * Verlauf und Mailbox; ohne Grenze baute der nächste Abruf den Funken daraus
   * wieder auf, samt Stand aus `vorher`.
   */
  vergessenBis: number
}

export type FunkenStatus = 'inactive' | 'pending' | 'active' | 'grace' | 'extended' | 'expired'

/** Was die Oberfläche über einen Funken wissen muss. */
export interface FunkenZustand {
  partnerId: number
  status: FunkenStatus
  streakCount: number
  /** Der erloschene Stand, der sich wiederherstellen ließe. */
  lostCount: number
  /** Bis wann gehandelt werden muss (ms), sonst `null`. */
  deadlineAt: number | null
  cycleStartAt: number | null
  meSent: boolean
  partnerSent: boolean
  /** Heute ist schon geschafft; der nächste Tag beginnt bei `cycleStartAt`. */
  erledigt: boolean
  lastRestoredAt: number | null
  canRestore: boolean
  /** Ab wann wieder wiederhergestellt werden darf, falls gerade nicht. */
  restoreAvailableAt: number | null
  record: number
}

export function leererKern(): FunkenKern {
  return {
    stand: 0,
    zyklusStart: null,
    gesendet: {},
    frist: null,
    verloren: 0,
    verlorenAm: null,
    gerettetAm: null,
    rekord: 0,
  }
}

export function leereAkte(partnerId: number): FunkenAkte {
  return { partnerId, basis: leererKern(), bis: 0, ereignisse: [], vergessenBis: 0 }
}

/** Ob in diesem Kern überhaupt etwas steht, das ein anderes Gerät bräuchte. */
export function kernHatInhalt(k: FunkenKern): boolean {
  return k.stand > 0 || k.verloren > 0 || k.gerettetAm !== null || k.rekord > 0
}

/** Das Ende des laufenden Zyklus. Nur mit laufendem Zyklus sinnvoll. */
function endeVon(k: FunkenKern): number {
  if (k.frist !== null) return k.frist
  const start = k.zyklusStart ?? 0
  return start + (k.stand > 0 ? TAG_MS + PUFFER_MS : TAG_MS)
}

/** Hat im laufenden Zyklus schon jemand anderes als `von` geschickt? */
function andererHat(k: FunkenKern, von: number): boolean {
  return Object.keys(k.gesendet).some((konto) => konto !== String(von))
}

/** Lässt einen abgelaufenen Zyklus erlöschen, gemessen an `t`. */
export function verfalle(k: FunkenKern, t: number): FunkenKern {
  if (k.zyklusStart === null) return k
  const ende = endeVon(k)
  if (t < ende) return k
  if (k.stand > 0) {
    return {
      ...k,
      verloren: k.stand,
      verlorenAm: ende,
      stand: 0,
      zyklusStart: null,
      gesendet: {},
      frist: null,
    }
  }
  // Ein Anfang, auf den niemand geantwortet hat: es gab nichts zu verlieren.
  return { ...k, zyklusStart: null, gesendet: {}, frist: null }
}

export function rettungMoeglich(k: FunkenKern, t: number): boolean {
  return (
    k.stand === 0 &&
    k.verloren > 0 &&
    (k.gerettetAm === null || t - k.gerettetAm >= RETTUNG_ABSTAND_MS)
  )
}

function wendeAugenblickAn(vorher: FunkenKern, von: number, t: number): FunkenKern {
  const k = verfalle(vorher, t)
  if (k.zyklusStart === null) {
    return { ...k, zyklusStart: t, gesendet: { [String(von)]: t }, frist: null }
  }
  // Heute ist schon geschafft; der nächste Tag hat noch nicht begonnen.
  if (t < k.zyklusStart) return k

  const imPuffer = k.stand > 0 && k.frist === null && t >= k.zyklusStart + TAG_MS
  if (k.gesendet[String(von)] !== undefined) {
    // Ein zweiter Augenblick derselben Seite zählt nicht doppelt. Im Puffer
    // löst er aber die Verlängerung aus, solange die andere Seite fehlt.
    if (imPuffer && !andererHat(k, von)) return { ...k, frist: t + VERLAENGERUNG_MS }
    return k
  }

  if (andererHat(k, von)) {
    const stand = k.stand + 1
    return {
      ...k,
      stand,
      // Frühestens morgen geht es weiter. Wer im Puffer rettet, beginnt den
      // nächsten Tag mit der Rettung.
      zyklusStart: Math.max(t, k.zyklusStart + TAG_MS),
      gesendet: {},
      frist: null,
      rekord: Math.max(k.rekord, stand),
      // Ein neuer Funke ersetzt den erloschenen; zurückholen lässt sich der
      // alte dann nicht mehr.
      verloren: 0,
      verlorenAm: null,
    }
  }

  const gesendet = { ...k.gesendet, [String(von)]: t }
  if (imPuffer) return { ...k, gesendet, frist: t + VERLAENGERUNG_MS }
  return { ...k, gesendet }
}

function wendeRettungAn(vorher: FunkenKern, t: number): FunkenKern {
  const k = verfalle(vorher, t)
  // Zu früh, schon gerettet, oder die Gegenseite war im selben Augenblick
  // schneller: dann ist der Funke schon da, und dieses Paket bleibt folgenlos.
  if (!rettungMoeglich(k, t)) return k
  return {
    ...k,
    stand: k.verloren,
    verloren: 0,
    verlorenAm: null,
    gerettetAm: t,
    zyklusStart: t,
    gesendet: {},
    frist: null,
    rekord: Math.max(k.rekord, k.verloren),
  }
}

export function wendeAn(k: FunkenKern, e: FunkenEreignis): FunkenKern {
  return e.art === 'rettung' ? wendeRettungAn(k, e.at) : wendeAugenblickAn(k, e.von, e.at)
}

function ordneEreignisse(a: FunkenEreignis, b: FunkenEreignis): number {
  return a.at - b.at || (a.kennung < b.kennung ? -1 : a.kennung > b.kennung ? 1 : 0)
}

/** Die Untergrenze, ab der Ereignisse für diese Freundschaft zählen. */
function untergrenzeVon(akte: FunkenAkte, seit: number): number {
  return Math.max(akte.vergessenBis, seit)
}

/**
 * Rechnet den Kern einer Akte aus, ohne ihn an `jetzt` verfallen zu lassen.
 *
 * `seit` ist der Beginn der Freundschaft. Gehört die gefaltete Basis zu einer
 * früheren (Freundschaft beendet und neu geschlossen), fängt die Rechnung von
 * vorn an; ebenso nach `vergessenBis`.
 */
export function falte(akte: FunkenAkte, seit: number): FunkenKern {
  const grenze = untergrenzeVon(akte, seit)
  const basisGilt = akte.bis > 0 && akte.bis >= grenze
  const ereignisse = akte.ereignisse.filter((e) => e.at > grenze && e.at > akte.bis)

  let kern = basisGilt ? akte.basis : leererKern()
  let ab = 0
  if (!basisGilt) {
    // Ein Gerät ohne eigene Geschichte fängt beim ersten Ereignis an, das den
    // Stand des Absenders mitbringt. Was davor liegt, steckt darin schon.
    const saat = ereignisse.findIndex((e) => e.vorher !== undefined)
    if (saat >= 0) {
      kern = klemmeKern(ereignisse[saat].vorher as FunkenKern, ereignisse[saat].at, seit)
      ab = saat
    }
  }
  for (let i = ab; i < ereignisse.length; i++) kern = wendeAn(kern, ereignisse[i])
  return kern
}

/** Die Zahl, die ein Funke zu diesem Zeitpunkt höchstens haben kann. */
function hoechstmoeglich(at: number, seit: number): number {
  if (!seit || at <= seit) return seit ? 1 : STAND_DECKEL
  return Math.min(STAND_DECKEL, Math.floor((at - seit) / TAG_MS) + 1)
}

/**
 * Hält einen fremden Stand in den Grenzen des Möglichen.
 *
 * Ein Funke wächst höchstens um eins am Tag. Wer behauptet, seit gestern
 * befreundet zu sein und 400 Tage Funke zu haben, bekommt zwei.
 */
function klemmeKern(k: FunkenKern, at: number, seit: number): FunkenKern {
  const max = hoechstmoeglich(at, seit)
  return {
    ...k,
    stand: Math.min(k.stand, max),
    verloren: Math.min(k.verloren, max),
    rekord: Math.min(Math.max(k.rekord, k.stand), max),
  }
}

/**
 * Nimmt neue Ereignisse in eine Akte auf.
 *
 * Doppeltes zählt einmal, zu Altes gar nicht. Was älter als `NACHLAUF_MS` ist,
 * wandert in die Basis — so bleibt die Akte klein, auch nach tausend Tagen.
 */
export function nimmAuf(
  akte: FunkenAkte,
  neue: readonly FunkenEreignis[],
  jetzt: number,
  seit = 0,
): FunkenAkte {
  const grenze = Math.max(akte.bis, akte.vergessenBis)
  const bekannt = new Set(akte.ereignisse.map((e) => e.kennung))
  const zusatz: FunkenEreignis[] = []
  for (const e of neue) {
    if (e.at <= grenze || bekannt.has(e.kennung)) continue
    bekannt.add(e.kennung)
    zusatz.push(e)
  }
  if (zusatz.length === 0 && akte.ereignisse.length <= EREIGNIS_DECKEL) {
    return verdichte(akte, jetzt, seit)
  }
  return verdichte({ ...akte, ereignisse: [...akte.ereignisse, ...zusatz].sort(ordneEreignisse) }, jetzt, seit)
}

/**
 * Faltet, was alt genug ist, in die Basis.
 *
 * Nach Alter nur, wenn der Beginn der Freundschaft bekannt ist. Sonst läge in
 * der Basis womöglich eine frühere Freundschaft mit demselben Menschen, und
 * die ließe sich hinterher nicht mehr heraustrennen. Der Deckel gilt immer.
 */
function verdichte(akte: FunkenAkte, jetzt: number, seit: number): FunkenAkte {
  const schnitt = seit > 0 ? jetzt - NACHLAUF_MS : -Infinity
  let anzahl = 0
  while (anzahl < akte.ereignisse.length && akte.ereignisse[anzahl].at < schnitt) anzahl++
  anzahl = Math.max(anzahl, akte.ereignisse.length - EREIGNIS_DECKEL)
  if (anzahl <= 0) return akte
  const alt = akte.ereignisse.slice(0, anzahl)
  const bis = Math.max(akte.bis, alt[alt.length - 1].at)
  const basis = falte({ ...akte, ereignisse: alt }, seit)
  return { ...akte, basis, bis, ereignisse: akte.ereignisse.slice(anzahl) }
}

/** Entfernt ein Ereignis, das nie hinausging (Versand gescheitert). */
export function ohneEreignis(akte: FunkenAkte, kennung: string): FunkenAkte {
  if (!akte.ereignisse.some((e) => e.kennung === kennung)) return akte
  return { ...akte, ereignisse: akte.ereignisse.filter((e) => e.kennung !== kennung) }
}

/** Die Akte nach „Freundschaft beendet" oder „blockiert": leer, mit Grenze. */
export function vergesseneAkte(partnerId: number, jetzt: number): FunkenAkte {
  return { ...leereAkte(partnerId), vergessenBis: jetzt }
}

export interface Blickwinkel {
  ich: number
  partner: number
  /** Beginn der Freundschaft (ms). `0`: unbekannt. */
  seit: number
  jetzt: number
}

/** Der Funke, wie ihn die Oberfläche zeigt. */
export function berechneFunkenZustand(
  akte: FunkenAkte | undefined,
  { ich, partner, seit, jetzt }: Blickwinkel,
): FunkenZustand {
  const kern = verfalle(akte ? falte(akte, seit) : leererKern(), jetzt)
  const laeuft = kern.zyklusStart !== null
  const erledigt = laeuft && jetzt < (kern.zyklusStart as number)

  let status: FunkenStatus
  if (kern.stand === 0) {
    status = kern.verloren > 0 ? 'expired' : laeuft ? 'pending' : 'inactive'
  } else if (kern.frist !== null) {
    status = 'extended'
  } else if (!erledigt && jetzt >= (kern.zyklusStart as number) + TAG_MS) {
    status = 'grace'
  } else {
    status = 'active'
  }

  const kannRetten = rettungMoeglich(kern, jetzt)
  return {
    partnerId: partner,
    status,
    streakCount: kern.stand,
    lostCount: kern.verloren,
    deadlineAt: laeuft ? endeVon(kern) : null,
    cycleStartAt: kern.zyklusStart,
    meSent: kern.gesendet[String(ich)] !== undefined,
    partnerSent: kern.gesendet[String(partner)] !== undefined,
    erledigt,
    lastRestoredAt: kern.gerettetAm,
    canRestore: kannRetten,
    restoreAvailableAt:
      !kannRetten && kern.verloren > 0 && kern.gerettetAm !== null
        ? kern.gerettetAm + RETTUNG_ABSTAND_MS
        : null,
    record: kern.rekord,
  }
}

export function istWiederherstellungMoeglich(zustand: FunkenZustand): boolean {
  return zustand.canRestore
}

/** Die Meilensteine, die dieser Rekord erreicht hat. */
export function erreichteMeilensteine(rekord: number): number[] {
  return MEILENSTEINE.filter((m) => rekord >= m)
}

// ---------------------------------------------------------------------------
// Nutzlast und Nachricht
// ---------------------------------------------------------------------------

/** Was ein Augenblick in der Nutzlast und in der Nachricht trägt. */
export interface AugenblickMarke {
  zeitpunkt: string
  vorher?: FunkenKern
}

/** Was eine Wiederherstellung trägt. */
export interface RettungsMarke {
  zeitpunkt: string
  /** Der Stand, den der Absender zurückholt — für die Zeile im Verlauf. */
  verloren: number
  vorher?: FunkenKern
}

/** Die Felder für die Nutzlast eines Augenblicks. */
export function baueAugenblickNutzlast(zeitpunkt: string, vorher: FunkenKern | null): AugenblickMarke {
  return vorher && kernHatInhalt(vorher) ? { zeitpunkt, vorher } : { zeitpunkt }
}

/** Die Felder für die Nutzlast einer Wiederherstellung. */
export function baueWiederherstellungNutzlast(zeitpunkt: string, vorher: FunkenKern): RettungsMarke {
  return { zeitpunkt, verloren: vorher.verloren, vorher }
}

function ganzzahl(roh: unknown, max = STAND_DECKEL): number | null {
  const n = Number(roh)
  return Number.isSafeInteger(n) && n >= 0 && n <= max ? n : null
}

function zeitOderNull(roh: unknown): number | null | undefined {
  if (roh === null || roh === undefined) return null
  const n = Number(roh)
  return Number.isFinite(n) && n >= 0 ? n : undefined
}

/**
 * Liest einen Kern aus fremder Hand. Alles, was nicht passt, macht ihn ungültig.
 *
 * Eine Nutzlast ist unterschrieben, aber unterschrieben heißt nur: von der
 * Gegenseite. Die kann sich verzählen, veraltet sein oder schummeln.
 */
export function leseKern(roh: unknown): FunkenKern | undefined {
  if (!roh || typeof roh !== 'object') return undefined
  const r = roh as Record<string, unknown>
  const stand = ganzzahl(r.stand)
  const verloren = ganzzahl(r.verloren)
  const rekord = ganzzahl(r.rekord)
  const zyklusStart = zeitOderNull(r.zyklusStart)
  const frist = zeitOderNull(r.frist)
  const verlorenAm = zeitOderNull(r.verlorenAm)
  const gerettetAm = zeitOderNull(r.gerettetAm)
  if (
    stand === null ||
    verloren === null ||
    rekord === null ||
    zyklusStart === undefined ||
    frist === undefined ||
    verlorenAm === undefined ||
    gerettetAm === undefined
  ) {
    return undefined
  }
  const gesendet: Record<string, number> = {}
  if (r.gesendet && typeof r.gesendet === 'object') {
    const eintraege = Object.entries(r.gesendet as Record<string, unknown>)
    if (eintraege.length > 2) return undefined
    for (const [konto, wann] of eintraege) {
      const t = Number(wann)
      if (!/^\d+$/.test(konto) || !Number.isFinite(t) || t < 0) return undefined
      gesendet[konto] = t
    }
  }
  return { stand, zyklusStart, gesendet, frist, verloren, verlorenAm, gerettetAm, rekord }
}

/** Die Marke eines Augenblicks aus einer Nutzlast; sonst `undefined`. */
export function leseAugenblickMarke(roh: unknown): AugenblickMarke | undefined {
  if (!roh || typeof roh !== 'object') return undefined
  const r = roh as Record<string, unknown>
  if (typeof r.zeitpunkt !== 'string' || !zeitAlsZahl(r.zeitpunkt)) return undefined
  const vorher = leseKern(r.vorher)
  return vorher ? { zeitpunkt: r.zeitpunkt, vorher } : { zeitpunkt: r.zeitpunkt }
}

/** Die Marke einer Wiederherstellung aus einer Nutzlast; sonst `undefined`. */
export function leseRettungsMarke(roh: unknown): RettungsMarke | undefined {
  if (!roh || typeof roh !== 'object') return undefined
  const r = roh as Record<string, unknown>
  const verloren = ganzzahl(r.verloren)
  if (typeof r.zeitpunkt !== 'string' || !zeitAlsZahl(r.zeitpunkt) || verloren === null) return undefined
  const vorher = leseKern(r.vorher)
  return vorher ? { zeitpunkt: r.zeitpunkt, verloren, vorher } : { zeitpunkt: r.zeitpunkt, verloren }
}

/** So viel braucht es von einer Nachricht, um ein Ereignis daraus zu machen. */
export interface FunkenNachricht {
  clientUuid?: string
  senderId: number
  createdAt: string
  augenblick?: AugenblickMarke
  funkenRettung?: RettungsMarke
}

/**
 * Der Zeitpunkt, an dem ein Ereignis zählt.
 *
 * Die Angabe des Absenders, geklemmt an die Ankunft beim Server. Beim
 * Absender selbst ist `createdAt` die eigene Uhr, und die Klemme greift nicht.
 */
export function zaehlzeit(zeitpunkt: string, createdAt: string): number {
  const behauptet = zeitAlsZahl(zeitpunkt)
  const eingang = zeitAlsZahl(createdAt)
  if (!eingang) return behauptet
  if (!behauptet) return eingang
  return Math.min(Math.max(behauptet, eingang - RUECKDATIERUNG_MS), eingang + VORDATIERUNG_MS)
}

/**
 * Macht aus einer Nachricht ein Ereignis — oder nichts.
 *
 * Nur die beiden Menschen dieses Gesprächs zählen. Eine Nachricht ohne
 * Kennung zählt nicht: ohne sie ließe sie sich nicht von ihrer Kopie auf
 * einem zweiten Gerät unterscheiden und zählte doppelt.
 */
export function ereignisAusNachricht(
  msg: FunkenNachricht,
  ich: number,
  partner: number,
): FunkenEreignis | null {
  const von = Number(msg.senderId)
  if (von !== ich && von !== partner) return null
  if (!msg.clientUuid) return null
  const marke = msg.augenblick ?? msg.funkenRettung
  if (!marke) return null
  const at = zaehlzeit(marke.zeitpunkt, msg.createdAt)
  if (!at) return null
  return {
    kennung: `${von}:${msg.clientUuid}`,
    von,
    at,
    art: msg.augenblick ? 'augenblick' : 'rettung',
    ...(marke.vorher ? { vorher: marke.vorher } : {}),
  }
}

export function ereignisseAusNachrichten(
  nachrichten: readonly FunkenNachricht[],
  ich: number,
  partner: number,
): FunkenEreignis[] {
  const raus: FunkenEreignis[] = []
  for (const m of nachrichten) {
    const e = ereignisAusNachricht(m, ich, partner)
    if (e) raus.push(e)
  }
  return raus
}

/** Die Akte aus der Ablage, geprüft. Eine kaputte Zeile gibt `null`. */
export function leseAkte(roh: unknown): FunkenAkte | null {
  if (!roh || typeof roh !== 'object') return null
  const r = roh as Record<string, unknown>
  const partnerId = Number(r.partnerId)
  const basis = leseKern(r.basis)
  const bis = zeitOderNull(r.bis)
  const vergessenBis = zeitOderNull(r.vergessenBis)
  if (!Number.isSafeInteger(partnerId) || partnerId <= 0 || !basis || bis == null || vergessenBis == null) {
    return null
  }
  const ereignisse: FunkenEreignis[] = []
  for (const e of Array.isArray(r.ereignisse) ? r.ereignisse : []) {
    if (!e || typeof e !== 'object') continue
    const x = e as Record<string, unknown>
    const at = Number(x.at)
    const von = Number(x.von)
    if (typeof x.kennung !== 'string' || !Number.isFinite(at) || !Number.isSafeInteger(von)) continue
    if (x.art !== 'augenblick' && x.art !== 'rettung') continue
    const vorher = leseKern(x.vorher)
    ereignisse.push({ kennung: x.kennung, von, at, art: x.art, ...(vorher ? { vorher } : {}) })
  }
  ereignisse.sort(ordneEreignisse)
  return { partnerId, basis, bis, ereignisse, vergessenBis }
}
