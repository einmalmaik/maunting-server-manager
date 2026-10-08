/**
 * Das Netzwerk eines Tabs: jede Anfrage in einer Zeile, zusammengesetzt aus
 * den Ereignissen von `Network`. Antwortkörper und Nutzlast holt die Ansicht
 * erst bei Bedarf. Nur im Speicher, höchstens `MAX` je Tab.
 */
import { create } from 'zustand'

export const MAX = 2000
const RAHMEN_MAX = 500

export type Koepfe = Record<string, string>

export interface Zeitablauf {
  requestTime: number
  dnsStart: number
  dnsEnd: number
  connectStart: number
  connectEnd: number
  sslStart: number
  sslEnd: number
  sendStart: number
  sendEnd: number
  receiveHeadersEnd: number
}

export interface Rahmen {
  gesendet: boolean
  daten: string
  opcode: number
  zeit: number
}

export interface Anfrage {
  /** `requestId`; eine Weiterleitung bekommt `:1`, `:2` … angehängt. */
  nr: string
  /** `requestId` des Protokolls (für `getResponseBody`). */
  kennung: string
  url: string
  methode: string
  typ: string
  status: number | null
  statusText: string
  mime: string
  anfrageKoepfe: Koepfe
  antwortKoepfe: Koepfe
  /** Was wirklich ging (mit Cookies), aus `…ExtraInfo`. */
  roheAnfrageKoepfe: Koepfe | null
  roheAntwortKoepfe: Koepfe | null
  hatNutzlast: boolean
  nutzlast: string | null
  protokoll: string
  adresse: string
  ausCache: boolean
  /** Sekunden, monoton (nur Abstände zählen). */
  start: number
  ende: number | null
  zeitablauf: Zeitablauf | null
  groesse: number | null
  fehler: string | null
  ausloeser: { type: string; url?: string; lineNumber?: number } | null
  rahmen: Rahmen[]
}

interface NetzZustand {
  anfragen: Record<string, Anfrage[]>
  hauptFrame: Record<string, string>
  beibehalten: boolean
  cacheAus: boolean
  setHauptFrame: (tab: string, frame: string) => void
  /** Liefert die Adresse eines Seitenwechsels im obersten Rahmen, sonst `null`. */
  ereignis: (tab: string, methode: string, daten: Record<string, unknown>) => string | null
  leeren: (tab: string) => void
  setBeibehalten: (an: boolean) => void
  setCacheAus: (an: boolean) => void
  tabWeg: (tab: string) => void
}

/** `…ExtraInfo` kommt manchmal vor der Anfrage selbst. */
const vorab = new Map<string, { anfrage?: Koepfe; antwort?: Koepfe }>()

function koepfe(roh: unknown): Koepfe {
  const aus: Koepfe = {}
  if (roh && typeof roh === 'object') for (const [k, v] of Object.entries(roh)) aus[k] = String(v)
  return aus
}

/** Wer die Anfrage auslöste; bei Skripten steht der Ort nur im Stapel. */
function ausloeser(roh: unknown): Anfrage['ausloeser'] {
  if (!roh || typeof roh !== 'object') return null
  const i = roh as { type: string; url?: string; lineNumber?: number; stack?: { callFrames: { url: string; lineNumber: number }[] } }
  const oben = i.stack?.callFrames.find((r) => r.url)
  return { type: i.type, url: i.url || oben?.url, lineNumber: i.url ? i.lineNumber : oben?.lineNumber }
}

function leer(kennung: string, url: string, methode: string, typ: string, start: number): Anfrage {
  return {
    nr: kennung,
    kennung,
    url,
    methode,
    typ,
    status: null,
    statusText: '',
    mime: '',
    anfrageKoepfe: {},
    antwortKoepfe: {},
    roheAnfrageKoepfe: vorab.get(kennung)?.anfrage ?? null,
    roheAntwortKoepfe: vorab.get(kennung)?.antwort ?? null,
    hatNutzlast: false,
    nutzlast: null,
    protokoll: '',
    adresse: '',
    ausCache: false,
    start,
    ende: null,
    zeitablauf: null,
    groesse: null,
    fehler: null,
    ausloeser: null,
    rahmen: [],
  }
}

interface Antwort {
  status: number
  statusText: string
  mimeType: string
  headers: Record<string, unknown>
  protocol?: string
  remoteIPAddress?: string
  remotePort?: number
  fromDiskCache?: boolean
  fromServiceWorker?: boolean
  fromPrefetchCache?: boolean
  timing?: Zeitablauf
  encodedDataLength?: number
}

function mitAntwort(a: Anfrage, r: Antwort): Anfrage {
  return {
    ...a,
    status: r.status,
    statusText: r.statusText,
    mime: r.mimeType,
    antwortKoepfe: koepfe(r.headers),
    protokoll: r.protocol ?? a.protokoll,
    adresse: r.remoteIPAddress ? `${r.remoteIPAddress.includes(':') ? `[${r.remoteIPAddress}]` : r.remoteIPAddress}:${r.remotePort ?? ''}` : a.adresse,
    ausCache: a.ausCache || !!(r.fromDiskCache || r.fromServiceWorker || r.fromPrefetchCache),
    zeitablauf: r.timing ?? a.zeitablauf,
  }
}

export const useNetz = create<NetzZustand>()((set, get) => {
  /** Ändert die jüngste Zeile mit dieser Kennung. */
  const aendern = (tab: string, kennung: string, f: (a: Anfrage) => Anfrage) =>
    set((s) => {
      const liste = s.anfragen[tab]
      if (!liste) return s
      for (let i = liste.length - 1; i >= 0; i--) {
        if (liste[i].kennung === kennung) {
          const neu = liste.slice()
          neu[i] = f(liste[i])
          return { anfragen: { ...s.anfragen, [tab]: neu } }
        }
      }
      return s
    })
  const anhaengen = (tab: string, a: Anfrage) =>
    set((s) => {
      const neu = [...(s.anfragen[tab] ?? []), a]
      return { anfragen: { ...s.anfragen, [tab]: neu.length > MAX ? neu.slice(neu.length - MAX) : neu } }
    })

  return {
    anfragen: {},
    hauptFrame: {},
    beibehalten: false,
    cacheAus: false,
    setHauptFrame: (tab, frame) => set((s) => ({ hauptFrame: { ...s.hauptFrame, [tab]: frame } })),
    ereignis: (tab, methode, d) => {
      const kennung = String(d.requestId ?? '')
      switch (methode) {
        case 'Network.requestWillBeSent': {
          const r = d.request as { url: string; method: string; headers: Record<string, unknown>; hasPostData?: boolean; postData?: string; urlFragment?: string }
          const umleitung = d.redirectResponse as Antwort | undefined
          if (umleitung) {
            // Die alte Zeile endet mit der Weiterleitung und bekommt eine eigene Nummer.
            const zahl = (get().anfragen[tab] ?? []).filter((a) => a.kennung === kennung).length
            aendern(tab, kennung, (a) => ({ ...mitAntwort(a, umleitung), nr: `${kennung}:${zahl}`, kennung: `${kennung}:${zahl}`, ende: Number(d.timestamp) }))
          }
          const typ = String(d.type ?? 'Other')
          const seitenwechsel = typ === 'Document' && d.requestId === d.loaderId && !umleitung && d.frameId === get().hauptFrame[tab]
          if (seitenwechsel && !get().beibehalten) set((s) => ({ anfragen: { ...s.anfragen, [tab]: [] } }))
          const a = leer(kennung, r.url + (r.urlFragment ?? ''), r.method, typ, Number(d.timestamp))
          anhaengen(tab, {
            ...a,
            anfrageKoepfe: koepfe(r.headers),
            hatNutzlast: !!r.hasPostData,
            nutzlast: r.postData ?? null,
            ausloeser: ausloeser(d.initiator),
          })
          return seitenwechsel ? r.url : null
        }
        case 'Network.requestWillBeSentExtraInfo': {
          const k = koepfe(d.headers)
          vorab.set(kennung, { ...vorab.get(kennung), anfrage: k })
          aendern(tab, kennung, (a) => ({ ...a, roheAnfrageKoepfe: k }))
          break
        }
        case 'Network.responseReceivedExtraInfo': {
          const k = koepfe(d.headers)
          vorab.set(kennung, { ...vorab.get(kennung), antwort: k })
          aendern(tab, kennung, (a) => ({ ...a, roheAntwortKoepfe: k }))
          break
        }
        case 'Network.responseReceived':
          aendern(tab, kennung, (a) => ({ ...mitAntwort(a, d.response as Antwort), typ: String(d.type ?? a.typ) }))
          break
        case 'Network.requestServedFromCache':
          aendern(tab, kennung, (a) => ({ ...a, ausCache: true }))
          break
        case 'Network.loadingFinished':
          vorab.delete(kennung)
          aendern(tab, kennung, (a) => ({ ...a, ende: Number(d.timestamp), groesse: Number(d.encodedDataLength ?? 0) }))
          break
        case 'Network.loadingFailed':
          vorab.delete(kennung)
          aendern(tab, kennung, (a) => ({
            ...a,
            ende: Number(d.timestamp),
            fehler: d.canceled ? 'canceled' : String(d.blockedReason ?? d.errorText ?? 'failed'),
          }))
          break
        case 'Network.webSocketCreated':
          anhaengen(tab, { ...leer(kennung, String(d.url), 'GET', 'WebSocket', 0), ausloeser: ausloeser(d.initiator) })
          break
        case 'Network.webSocketFrameSent':
        case 'Network.webSocketFrameReceived': {
          const r = d.response as { opcode: number; payloadData: string }
          const rahmen: Rahmen = { gesendet: methode.endsWith('Sent'), daten: r.payloadData, opcode: r.opcode, zeit: Number(d.timestamp) }
          aendern(tab, kennung, (a) => ({
            ...a,
            status: a.status ?? 101,
            start: a.start || rahmen.zeit,
            rahmen: [...a.rahmen, rahmen].slice(-RAHMEN_MAX),
          }))
          break
        }
        case 'Network.webSocketClosed':
          aendern(tab, kennung, (a) => ({ ...a, ende: Number(d.timestamp) }))
          break
      }
      return null
    },
    leeren: (tab) => set((s) => ({ anfragen: { ...s.anfragen, [tab]: [] } })),
    setBeibehalten: (an) => set({ beibehalten: an }),
    setCacheAus: (an) => set({ cacheAus: an }),
    tabWeg: (tab) =>
      set((s) => {
        const anfragen = { ...s.anfragen }
        const hauptFrame = { ...s.hauptFrame }
        delete anfragen[tab]
        delete hauptFrame[tab]
        return { anfragen, hauptFrame }
      }),
  }
})

/** Filter der Liste, wie in jedem Browser. */
export const TYPEN = ['alle', 'fetch', 'doc', 'css', 'js', 'schrift', 'bild', 'medien', 'ws', 'sonst'] as const
export type Typfilter = (typeof TYPEN)[number]

export function typfilter(typ: string): Exclude<Typfilter, 'alle'> {
  switch (typ) {
    case 'XHR':
    case 'Fetch':
    case 'EventSource':
      return 'fetch'
    case 'Document':
      return 'doc'
    case 'Stylesheet':
      return 'css'
    case 'Script':
      return 'js'
    case 'Font':
      return 'schrift'
    case 'Image':
      return 'bild'
    case 'Media':
      return 'medien'
    case 'WebSocket':
      return 'ws'
    default:
      return 'sonst'
  }
}
