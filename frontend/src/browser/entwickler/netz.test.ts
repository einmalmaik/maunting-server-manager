import { beforeEach, describe, expect, it } from 'vitest'

import { useNetz, typfilter, type Anfrage } from './netzStore'
import { alsCurl, alsFetch, cookiesDerAnfrage, cookiesDerAntwort, dauer, groesse, name, phasen } from './netzText'

const TAB = 'tab-netz'

function senden(kennung: string, url: string, extra: Record<string, unknown> = {}) {
  return useNetz.getState().ereignis(TAB, 'Network.requestWillBeSent', {
    requestId: kennung,
    loaderId: 'L1',
    frameId: 'F1',
    type: 'Fetch',
    timestamp: 1,
    request: { url, method: 'GET', headers: { Accept: '*/*' } },
    ...extra,
  })
}

const liste = () => useNetz.getState().anfragen[TAB] ?? []

describe('Netzwerk', () => {
  beforeEach(() => {
    useNetz.getState().tabWeg(TAB)
    useNetz.getState().setBeibehalten(false)
    useNetz.getState().setHauptFrame(TAB, 'F1')
  })

  it('setzt eine Anfrage aus ihren Ereignissen zusammen', () => {
    senden('1', 'https://a.example/api/daten.json?x=1')
    useNetz.getState().ereignis(TAB, 'Network.responseReceived', {
      requestId: '1',
      type: 'Fetch',
      response: { status: 200, statusText: 'OK', mimeType: 'application/json', headers: { 'Content-Type': 'application/json' }, remoteIPAddress: '::1', remotePort: 443 },
    })
    useNetz.getState().ereignis(TAB, 'Network.loadingFinished', { requestId: '1', timestamp: 1.25, encodedDataLength: 2048 })
    const [a] = liste()
    expect(a).toMatchObject({ status: 200, mime: 'application/json', groesse: 2048, ende: 1.25, adresse: '[::1]:443' })
  })

  it('nimmt die rohen Kopfzeilen, auch wenn sie vor der Anfrage kommen', () => {
    useNetz.getState().ereignis(TAB, 'Network.requestWillBeSentExtraInfo', { requestId: '2', headers: { Cookie: 'a=1; b=2' } })
    senden('2', 'https://a.example/')
    const [a] = liste()
    expect(cookiesDerAnfrage(a)).toEqual([
      ['a', '1'],
      ['b', '2'],
    ])
  })

  it('nimmt den Ort des Auslösers aus dem Stapel', () => {
    senden('6', 'https://a.example/d.json', {
      initiator: { type: 'script', stack: { callFrames: [{ url: '', lineNumber: 0 }, { url: 'https://a.example/x.js', lineNumber: 32 }] } },
    })
    expect(liste()[0].ausloeser).toEqual({ type: 'script', url: 'https://a.example/x.js', lineNumber: 32 })
  })

  it('gibt einer Weiterleitung eine eigene Zeile', () => {
    senden('3', 'http://a.example/')
    senden('3', 'https://a.example/', {
      redirectResponse: { status: 301, statusText: 'Moved', mimeType: '', headers: { Location: 'https://a.example/' } },
    })
    expect(liste().map((a) => [a.nr, a.status])).toEqual([
      ['3:1', 301],
      ['3', null],
    ])
  })

  it('leert beim Seitenwechsel im obersten Rahmen, nur ohne „beibehalten“', () => {
    senden('4', 'https://a.example/bild.png')
    const url = senden('L1', 'https://b.example/', { type: 'Document', loaderId: 'L1' })
    expect(url).toBe('https://b.example/')
    expect(liste()).toHaveLength(1)

    useNetz.getState().setBeibehalten(true)
    senden('L2', 'https://c.example/', { requestId: 'L2', type: 'Document', loaderId: 'L2' })
    expect(liste()).toHaveLength(2)
  })

  it('ein Seitenwechsel in einem eingebetteten Rahmen leert nichts', () => {
    senden('5', 'https://a.example/x.js')
    expect(senden('L9', 'https://werbung.example/', { type: 'Document', loaderId: 'L9', frameId: 'F2' })).toBeNull()
    expect(liste()).toHaveLength(2)
  })

  it('sammelt WebSocket-Nachrichten an der Verbindung', () => {
    useNetz.getState().ereignis(TAB, 'Network.webSocketCreated', { requestId: 'w', url: 'wss://a.example/ws' })
    useNetz.getState().ereignis(TAB, 'Network.webSocketFrameSent', { requestId: 'w', timestamp: 2, response: { opcode: 1, payloadData: 'hallo' } })
    useNetz.getState().ereignis(TAB, 'Network.webSocketFrameReceived', { requestId: 'w', timestamp: 3, response: { opcode: 1, payloadData: 'zurück' } })
    const [a] = liste()
    expect(a.status).toBe(101)
    expect(a.rahmen.map((r) => [r.gesendet, r.daten])).toEqual([
      [true, 'hallo'],
      [false, 'zurück'],
    ])
  })

  it('ordnet Typen den Filtern zu', () => {
    expect(typfilter('XHR')).toBe('fetch')
    expect(typfilter('Stylesheet')).toBe('css')
    expect(typfilter('WebSocket')).toBe('ws')
  })
})

describe('Netzwerk-Texte', () => {
  const anfrage = (teil: Partial<Anfrage>): Anfrage => ({
    nr: '1',
    kennung: '1',
    url: 'https://a.example/x',
    methode: 'GET',
    typ: 'Fetch',
    status: 200,
    statusText: '',
    mime: '',
    anfrageKoepfe: {},
    antwortKoepfe: {},
    roheAnfrageKoepfe: null,
    roheAntwortKoepfe: null,
    hatNutzlast: false,
    nutzlast: null,
    protokoll: '',
    adresse: '',
    ausCache: false,
    start: 0,
    ende: null,
    zeitablauf: null,
    groesse: null,
    fehler: null,
    ausloeser: null,
    rahmen: [],
    ...teil,
  })

  it('formatiert Größe und Dauer', () => {
    expect([groesse(null), groesse(512), groesse(2048), groesse(3 * 1024 * 1024), groesse(5 * 1024 ** 3)]).toEqual(['', '512 B', '2.0 kB', '3.0 MB', '5.0 GB'])
    expect([dauer(null), dauer(-1), dauer(12.4), dauer(1500)]).toEqual(['', '', '12 ms', '1.50 s'])
  })

  it('trennt Dateiname und Ort', () => {
    expect(name('https://a.example/pfad/zu/datei.js?v=2')).toEqual({ datei: 'datei.js?v=2', rest: 'a.example/pfad/zu' })
    expect(name('https://a.example/ordner/')).toEqual({ datei: 'ordner/', rest: 'a.example' })
    expect(name('https://a.example/')).toEqual({ datei: 'a.example', rest: 'a.example' })
    expect(name('kein url')).toEqual({ datei: 'kein url', rest: '' })
  })

  it('maskiert Anführungszeichen in cURL und lässt HTTP/2-Pseudoköpfe weg', () => {
    const a = anfrage({ methode: 'POST', anfrageKoepfe: { ':authority': 'a.example', 'X-Wert': "it's" } })
    const curl = alsCurl(a, '{"a":1}')
    expect(curl).toContain(`-H 'X-Wert: it'\\''s'`)
    expect(curl).not.toContain(':authority')
    expect(curl).not.toContain('-X POST')
    expect(alsCurl(anfrage({ methode: 'DELETE' }), null)).toContain('-X DELETE')
  })

  it('schreibt kein Cookie in fetch()', () => {
    const text = alsFetch(anfrage({ anfrageKoepfe: { Cookie: 'geheim=1', Accept: 'text/html' } }), null)
    expect(text).not.toContain('geheim')
    expect(text).toContain('text/html')
  })

  it('trennt mehrere Set-Cookie-Zeilen', () => {
    expect(cookiesDerAntwort(anfrage({ antwortKoepfe: { 'set-cookie': 'a=1\nb=2' } }))).toEqual(['a=1', 'b=2'])
  })

  it('rechnet die Phasen des Zeitablaufs und lässt fehlende weg', () => {
    const z = { requestTime: 10, dnsStart: -1, dnsEnd: -1, connectStart: -1, connectEnd: -1, sslStart: -1, sslEnd: -1, sendStart: 1, sendEnd: 2, receiveHeadersEnd: 50 }
    expect(phasen(anfrage({ zeitablauf: z, ende: 10.1 }))).toEqual([
      { name: 'senden', ms: 1 },
      { name: 'warten', ms: 48 },
      { name: 'laden', ms: expect.closeTo(50, 5) },
    ])
  })
})
