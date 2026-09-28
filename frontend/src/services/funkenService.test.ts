import { describe, expect, it } from 'vitest'

import {
  EREIGNIS_DECKEL,
  NACHLAUF_MS,
  RETTUNG_ABSTAND_MS,
  STUNDE_MS,
  TAG_MS,
  baueAugenblickNutzlast,
  berechneFunkenZustand,
  ereignisAusNachricht,
  erreichteMeilensteine,
  falte,
  leereAkte,
  leseAkte,
  leseAugenblickMarke,
  leseKern,
  nimmAuf,
  ohneEreignis,
  vergesseneAkte,
  zaehlzeit,
  type FunkenAkte,
  type FunkenEreignis,
  type FunkenKern,
} from './funkenService'

const ICH = 1
const DU = 2
const T0 = Date.parse('2026-09-01T08:00:00Z')
const SEIT = T0 - 400 * TAG_MS

let lfd = 0
function augenblick(von: number, at: number, vorher?: FunkenKern): FunkenEreignis {
  return { kennung: `${von}:m${++lfd}`, von, at, art: 'augenblick', ...(vorher ? { vorher } : {}) }
}
function rettung(von: number, at: number): FunkenEreignis {
  return { kennung: `${von}:r${++lfd}`, von, at, art: 'rettung' }
}

function akteMit(...ereignisse: FunkenEreignis[]): FunkenAkte {
  // `jetzt` weit genug vorn, dass nichts gefaltet wird: die Tests prüfen die
  // Regeln, die Verdichtung hat eigene.
  return nimmAuf(leereAkte(DU), ereignisse, ereignisse.length ? ereignisse[0].at : T0, SEIT)
}

function zustand(akte: FunkenAkte, jetzt: number) {
  return berechneFunkenZustand(akte, { ich: ICH, partner: DU, seit: SEIT, jetzt })
}

/** Ein laufender Funke mit `tage` Tagen, zuletzt geschafft um `T0`. */
function funkeVon(tage: number): FunkenEreignis[] {
  const raus: FunkenEreignis[] = []
  for (let i = tage - 1; i >= 0; i--) {
    const tag = T0 - i * TAG_MS
    raus.push(augenblick(ICH, tag - STUNDE_MS), augenblick(DU, tag))
  }
  return raus
}

describe('Funken-Zustandsmaschine', () => {
  it('beide schicken im ersten Tag: Funke 1', () => {
    const akte = akteMit(augenblick(ICH, T0), augenblick(DU, T0 + 3 * STUNDE_MS))
    const z = zustand(akte, T0 + 4 * STUNDE_MS)
    expect(z.status).toBe('active')
    expect(z.streakCount).toBe(1)
    expect(z.erledigt).toBe(true)
  })

  it('ein einseitiger Anfang wartet und verfällt nach 24 h ohne Puffer', () => {
    const akte = akteMit(augenblick(ICH, T0))
    expect(zustand(akte, T0 + 23 * STUNDE_MS).status).toBe('pending')
    expect(zustand(akte, T0 + 23 * STUNDE_MS).meSent).toBe(true)
    const spaeter = zustand(akte, T0 + 25 * STUNDE_MS)
    expect(spaeter.status).toBe('inactive')
    expect(spaeter.lostCount).toBe(0)
  })

  it('höchstens +1 je Tag: zehn Augenblicke hin und her in einer Stunde zählen einmal', () => {
    const ereignisse: FunkenEreignis[] = []
    for (let i = 0; i < 10; i++) {
      ereignisse.push(augenblick(ICH, T0 + i * 60_000), augenblick(DU, T0 + i * 60_000 + 30_000))
    }
    const z = zustand(akteMit(...ereignisse), T0 + STUNDE_MS)
    expect(z.streakCount).toBe(1)
    // Der nächste Tag beginnt 24 h nach dem ersten.
    expect(z.cycleStartAt).toBe(T0 + TAG_MS)
  })

  it('regulärer Zyklus: jeden Tag beide, der Funke wächst', () => {
    const z = zustand(akteMit(...funkeVon(5)), T0 + 2 * STUNDE_MS)
    expect(z.streakCount).toBe(5)
    expect(z.record).toBe(5)
  })

  it('nach 24 h ohne beide: Puffer', () => {
    const akte = akteMit(...funkeVon(3))
    // Jeder Tag beginnt 24 h nach dem vorigen, und der erste mit dem ersten
    // Augenblick (T0 - 2 Tage - 1 h). Der vierte also bei T0 + 23 h.
    const vierter = T0 + 23 * STUNDE_MS
    expect(zustand(akte, vierter + 23 * STUNDE_MS).status).toBe('active')
    const z = zustand(akte, vierter + 25 * STUNDE_MS)
    expect(z.status).toBe('grace')
    expect(z.streakCount).toBe(3)
    expect(z.deadlineAt).toBe(vierter + 48 * STUNDE_MS)
  })

  it('die fehlende Seite schickt im Puffer: gerettet, +1', () => {
    const start = T0 + TAG_MS
    const akte = akteMit(
      ...funkeVon(3),
      augenblick(ICH, start + 2 * STUNDE_MS),
      augenblick(DU, start + 30 * STUNDE_MS),
    )
    const z = zustand(akte, start + 31 * STUNDE_MS)
    expect(z.status).toBe('active')
    expect(z.streakCount).toBe(4)
    // Wer im Puffer rettet, beginnt den nächsten Tag mit der Rettung.
    expect(z.cycleStartAt).toBe(start + 30 * STUNDE_MS)
  })

  it('eine Seite schickt im Puffer, die andere fehlt: genau 72 h Verlängerung', () => {
    const start = T0 + TAG_MS
    const imPuffer = start + 30 * STUNDE_MS
    const akte = akteMit(...funkeVon(3), augenblick(ICH, imPuffer))
    const z = zustand(akte, imPuffer + STUNDE_MS)
    expect(z.status).toBe('extended')
    expect(z.deadlineAt).toBe(imPuffer + 72 * STUNDE_MS)
    expect(z.meSent).toBe(true)
    expect(z.partnerSent).toBe(false)
    // Nach 48 h ab Zyklusbeginn wäre ohne Verlängerung Schluss gewesen.
    expect(zustand(akte, start + 60 * STUNDE_MS).status).toBe('extended')
  })

  it('die andere antwortet innerhalb der 72 h: Funke gerettet, +1', () => {
    const start = T0 + TAG_MS
    const imPuffer = start + 30 * STUNDE_MS
    const antwort = imPuffer + 70 * STUNDE_MS
    const akte = akteMit(...funkeVon(3), augenblick(ICH, imPuffer), augenblick(DU, antwort))
    const z = zustand(akte, antwort + STUNDE_MS)
    expect(z.status).toBe('active')
    expect(z.streakCount).toBe(4)
  })

  it('niemand schickt 48 h: erloschen, der alte Stand bleibt für die Rettung', () => {
    const akte = akteMit(...funkeVon(12))
    const z = zustand(akte, T0 + TAG_MS + 48 * STUNDE_MS)
    expect(z.status).toBe('expired')
    expect(z.streakCount).toBe(0)
    expect(z.lostCount).toBe(12)
    expect(z.canRestore).toBe(true)
  })

  it('die Verlängerung läuft ohne Antwort ab: erloschen', () => {
    const start = T0 + TAG_MS
    const imPuffer = start + 30 * STUNDE_MS
    const akte = akteMit(...funkeVon(7), augenblick(ICH, imPuffer))
    const z = zustand(akte, imPuffer + 72 * STUNDE_MS)
    expect(z.status).toBe('expired')
    expect(z.lostCount).toBe(7)
  })

  it('eine Antwort nach Ablauf zählt nicht als Rettung, sondern als neuer Anfang', () => {
    const start = T0 + TAG_MS
    const zuSpaet = start + 50 * STUNDE_MS
    const akte = akteMit(...funkeVon(7), augenblick(DU, zuSpaet))
    const z = zustand(akte, zuSpaet + STUNDE_MS)
    expect(z.streakCount).toBe(0)
    expect(z.lostCount).toBe(7)
    expect(z.partnerSent).toBe(true)
  })

  it('Wiederherstellung stellt den verlorenen Stand her und startet einen neuen Tag', () => {
    const erloschen = T0 + TAG_MS + 48 * STUNDE_MS
    const r = erloschen + 5 * STUNDE_MS
    const akte = akteMit(...funkeVon(12), rettung(DU, r))
    const z = zustand(akte, r + STUNDE_MS)
    expect(z.status).toBe('active')
    expect(z.streakCount).toBe(12)
    expect(z.lastRestoredAt).toBe(r)
    expect(z.cycleStartAt).toBe(r)
    expect(z.lostCount).toBe(0)
  })

  it('eine zweite Wiederherstellung vor Ablauf von 90 Tagen wird abgewiesen', () => {
    const erloschen = T0 + TAG_MS + 48 * STUNDE_MS
    const r1 = erloschen + STUNDE_MS
    // Nach der Rettung wieder niemand: erlischt 48 h später erneut.
    const zweitesErloeschen = r1 + 48 * STUNDE_MS
    const r2 = zweitesErloeschen + STUNDE_MS
    const akte = akteMit(...funkeVon(12), rettung(ICH, r1), rettung(ICH, r2))
    const z = zustand(akte, r2 + STUNDE_MS)
    expect(z.status).toBe('expired')
    expect(z.streakCount).toBe(0)
    expect(z.canRestore).toBe(false)
    expect(z.restoreAvailableAt).toBe(r1 + RETTUNG_ABSTAND_MS)
  })

  it('nach 90 Tagen geht die Wiederherstellung wieder', () => {
    const erloschen = T0 + TAG_MS + 48 * STUNDE_MS
    const r1 = erloschen + STUNDE_MS
    const r2 = r1 + RETTUNG_ABSTAND_MS
    const akte = akteMit(...funkeVon(12), rettung(ICH, r1), rettung(DU, r2))
    const z = zustand(akte, r2 + STUNDE_MS)
    expect(z.streakCount).toBe(12)
    expect(z.lastRestoredAt).toBe(r2)
  })

  it('gleichzeitige Rettung beider Seiten zählt einmal', () => {
    const erloschen = T0 + TAG_MS + 48 * STUNDE_MS
    const akte = akteMit(...funkeVon(12), rettung(ICH, erloschen + 1000), rettung(DU, erloschen + 2000))
    expect(zustand(akte, erloschen + 3000).streakCount).toBe(12)
    expect(zustand(akte, erloschen + 3000).lastRestoredAt).toBe(erloschen + 1000)
  })

  it('ein neuer Funke ersetzt den erloschenen: danach keine Rettung mehr', () => {
    const erloschen = T0 + TAG_MS + 48 * STUNDE_MS
    const akte = akteMit(
      ...funkeVon(12),
      augenblick(ICH, erloschen + STUNDE_MS),
      augenblick(DU, erloschen + 2 * STUNDE_MS),
    )
    const z = zustand(akte, erloschen + 3 * STUNDE_MS)
    expect(z.streakCount).toBe(1)
    expect(z.lostCount).toBe(0)
    expect(z.canRestore).toBe(false)
    expect(z.record).toBe(12)
  })

  it('Fremde zählen nicht', () => {
    expect(ereignisAusNachricht({ clientUuid: 'x', senderId: 3, createdAt: '2026-09-01T08:00:00Z', augenblick: { zeitpunkt: '2026-09-01T08:00:00Z' } }, ICH, DU)).toBeNull()
  })

  it('dieselbe Nachricht zählt einmal', () => {
    const e = augenblick(ICH, T0)
    const akte = nimmAuf(nimmAuf(leereAkte(DU), [e], T0, SEIT), [e, { ...e }], T0, SEIT)
    expect(akte.ereignisse).toHaveLength(1)
  })
})

describe('Zeitpunkte', () => {
  it('ein zurückdatierter Augenblick wird an die Ankunft geklemmt', () => {
    const eingang = '2026-09-03T12:00:00Z'
    expect(zaehlzeit('2026-09-01T12:00:00Z', eingang)).toBe(Date.parse(eingang) - STUNDE_MS)
    expect(zaehlzeit('2026-09-03T13:00:00Z', eingang)).toBe(Date.parse(eingang) + 5 * 60_000)
    expect(zaehlzeit('2026-09-03T11:59:00Z', eingang)).toBe(Date.parse('2026-09-03T11:59:00Z'))
  })

  it('ein Serverzeitpunkt ohne Zone ist UTC', () => {
    expect(zaehlzeit('2026-09-03T11:59:00Z', '2026-09-03T12:00:00')).toBe(Date.parse('2026-09-03T11:59:00Z'))
  })
})

describe('Freundschaft', () => {
  it('nach dem Vergessen zählt nichts von davor, auch nicht aus der Mailbox', () => {
    const vergessen = vergesseneAkte(DU, T0 + 5 * STUNDE_MS)
    const akte = nimmAuf(vergessen, funkeVon(12), T0 + 6 * STUNDE_MS, SEIT)
    expect(akte.ereignisse).toHaveLength(0)
    expect(zustand(akte, T0 + 6 * STUNDE_MS).status).toBe('inactive')
  })

  it('eine neue Freundschaft verwirft die Basis der alten', () => {
    // Alte Freundschaft mit 12 Tagen, längst gefaltet.
    const alt = nimmAuf(leereAkte(DU), funkeVon(12), T0 + 20 * TAG_MS, SEIT)
    expect(alt.bis).toBeGreaterThan(0)
    const neueFreundschaft = T0 + 21 * TAG_MS
    const z = berechneFunkenZustand(alt, { ich: ICH, partner: DU, seit: neueFreundschaft, jetzt: neueFreundschaft + 1 })
    expect(z.status).toBe('inactive')
    expect(z.lostCount).toBe(0)
  })
})

describe('Neues Gerät', () => {
  it('fängt beim ersten mitgebrachten Stand an', () => {
    const vorher: FunkenKern = {
      stand: 40,
      zyklusStart: T0,
      gesendet: { [String(ICH)]: T0 + STUNDE_MS },
      frist: null,
      verloren: 0,
      verlorenAm: null,
      gerettetAm: null,
      rekord: 40,
    }
    const akte = nimmAuf(leereAkte(DU), [augenblick(DU, T0 + 2 * STUNDE_MS, vorher)], T0 + 2 * STUNDE_MS, SEIT)
    const z = zustand(akte, T0 + 3 * STUNDE_MS)
    expect(z.streakCount).toBe(41)
  })

  it('ein behaupteter Stand über dem Möglichen wird geklemmt', () => {
    const seitGestern = T0 - TAG_MS
    const vorher: FunkenKern = {
      stand: 400,
      zyklusStart: T0,
      gesendet: { [String(ICH)]: T0 },
      frist: null,
      verloren: 0,
      verlorenAm: null,
      gerettetAm: null,
      rekord: 400,
    }
    const akte = nimmAuf(leereAkte(DU), [augenblick(DU, T0 + STUNDE_MS, vorher)], T0, seitGestern)
    const z = berechneFunkenZustand(akte, { ich: ICH, partner: DU, seit: seitGestern, jetzt: T0 + 2 * STUNDE_MS })
    expect(z.streakCount).toBeLessThanOrEqual(3)
  })

  it('ein Gerät mit eigener Geschichte ignoriert mitgebrachte Stände', () => {
    const lang = nimmAuf(leereAkte(DU), funkeVon(20), T0 + STUNDE_MS, SEIT)
    // Nach der Verdichtung ist `bis` gesetzt.
    const spaeter = T0 + NACHLAUF_MS + TAG_MS
    const verdichtet = nimmAuf(lang, [], spaeter, SEIT)
    expect(verdichtet.bis).toBeGreaterThan(0)
    const gelogen: FunkenKern = { ...leererKernMit(999) }
    const mitLuege = nimmAuf(verdichtet, [augenblick(DU, T0 + 2 * STUNDE_MS, gelogen)], spaeter, SEIT)
    expect(falte(mitLuege, SEIT).stand).toBe(20)
  })
})

function leererKernMit(stand: number): FunkenKern {
  return { stand, zyklusStart: T0, gesendet: {}, frist: null, verloren: 0, verlorenAm: null, gerettetAm: null, rekord: stand }
}

describe('Verdichtung', () => {
  it('bleibt beim selben Ergebnis, auch über 100 Tage', () => {
    const ereignisse = funkeVon(100)
    // Schritt für Schritt aufgenommen, wie im Alltag: jeden Tag neu gespeichert.
    let akte = leereAkte(DU)
    for (const e of ereignisse) akte = nimmAuf(akte, [e], e.at, SEIT)
    expect(akte.ereignisse.length).toBeLessThan(40)
    expect(zustand(akte, T0 + STUNDE_MS).streakCount).toBe(100)
    expect(zustand(akte, T0 + STUNDE_MS).record).toBe(100)
  })

  it('hält den Deckel ein', () => {
    const viele: FunkenEreignis[] = []
    for (let i = 0; i < EREIGNIS_DECKEL + 50; i++) viele.push(augenblick(ICH, T0 + i * 1000))
    const akte = nimmAuf(leereAkte(DU), viele, T0, SEIT)
    expect(akte.ereignisse.length).toBeLessThanOrEqual(EREIGNIS_DECKEL)
  })

  it('ohne bekannten Freundschaftsbeginn wird nicht nach Alter gefaltet', () => {
    const akte = nimmAuf(leereAkte(DU), funkeVon(3), T0 + 30 * TAG_MS, 0)
    expect(akte.bis).toBe(0)
    expect(akte.ereignisse).toHaveLength(6)
  })
})

describe('Nutzlast und Ablage', () => {
  it('ein leerer Stand reist nicht mit', () => {
    expect(baueAugenblickNutzlast('2026-09-01T08:00:00Z', leereAkte(DU).basis)).toEqual({
      zeitpunkt: '2026-09-01T08:00:00Z',
    })
  })

  it('verwirft einen Kern mit falschen Feldern', () => {
    expect(leseKern({ stand: -1, verloren: 0, rekord: 0 })).toBeUndefined()
    expect(leseKern({ stand: 1.5, verloren: 0, rekord: 0 })).toBeUndefined()
    expect(leseKern({ stand: 1, verloren: 0, rekord: 1, gesendet: { a: 1 } })).toBeUndefined()
    expect(leseKern({ stand: 1, verloren: 0, rekord: 1, gesendet: { 1: 1, 2: 2, 3: 3 } })).toBeUndefined()
    expect(leseKern({ stand: 1, verloren: 0, rekord: 1 })).toMatchObject({ stand: 1, zyklusStart: null })
  })

  it('eine Marke ohne lesbaren Zeitpunkt ist keine', () => {
    expect(leseAugenblickMarke({ zeitpunkt: 'gestern' })).toBeUndefined()
    expect(leseAugenblickMarke({ zeitpunkt: '2026-09-01T08:00:00Z' })).toEqual({ zeitpunkt: '2026-09-01T08:00:00Z' })
  })

  it('liest eine Akte zurück und lässt Kaputtes aus', () => {
    const akte = nimmAuf(leereAkte(DU), funkeVon(2), T0, SEIT)
    const roh = JSON.parse(JSON.stringify({ ...akte, ereignisse: [...akte.ereignisse, { kennung: 5 }] }))
    const gelesen = leseAkte(roh)
    expect(gelesen?.ereignisse).toHaveLength(4)
    expect(leseAkte({ partnerId: 'x' })).toBeNull()
  })

  it('ein gescheiterter Versand nimmt sein Ereignis wieder heraus', () => {
    const e = augenblick(ICH, T0)
    const akte = nimmAuf(leereAkte(DU), [e], T0, SEIT)
    expect(ohneEreignis(akte, e.kennung).ereignisse).toHaveLength(0)
  })

  it('Meilensteine', () => {
    expect(erreichteMeilensteine(9)).toEqual([])
    expect(erreichteMeilensteine(100)).toEqual([10, 100])
  })
})
