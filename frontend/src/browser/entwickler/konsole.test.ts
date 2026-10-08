import { beforeEach, describe, expect, it } from 'vitest'

import { MAX, useKonsole } from './konsoleStore'
import { zerlegen } from './KonsolenEingabe'

const TAB = 'tab-konsole'
const liste = () => useKonsole.getState().eintraege[TAB] ?? []
const log = (typ: string, wert: string, extra: Record<string, unknown> = {}) =>
  useKonsole.getState().ereignis(TAB, 'Runtime.consoleAPICalled', { type: typ, args: [{ type: 'string', value: wert }], ...extra })

describe('Konsole', () => {
  beforeEach(() => {
    useKonsole.getState().tabWeg(TAB)
    useKonsole.getState().setBeibehalten(false)
  })

  it('ordnet die Stufen zu und zählt gleiche Meldungen hoch', () => {
    log('log', 'a')
    log('log', 'a')
    log('warning', 'b')
    log('error', 'c')
    expect(liste().map((e) => [e.stufe, e.anzahl])).toEqual([
      ['log', 2],
      ['warn', 1],
      ['error', 1],
    ])
  })

  it('rückt Gruppen ein und hört mit endGroup wieder auf', () => {
    log('startGroupCollapsed', 'Gruppe')
    log('log', 'innen')
    useKonsole.getState().ereignis(TAB, 'Runtime.consoleAPICalled', { type: 'endGroup', args: [] })
    log('log', 'außen')
    expect(liste().map((e) => [e.ebene, e.gruppe])).toEqual([
      [0, 'zu'],
      [1, undefined],
      [0, undefined],
    ])
  })

  it('console.clear leert und sagt es', () => {
    log('log', 'a')
    useKonsole.getState().ereignis(TAB, 'Runtime.consoleAPICalled', { type: 'clear', args: [] })
    expect(liste().map((e) => e.art)).toEqual(['geleert'])
  })

  it('zeigt eine Ausnahme mit Ort', () => {
    useKonsole.getState().ereignis(TAB, 'Runtime.exceptionThrown', {
      exceptionDetails: { text: 'Uncaught', lineNumber: 4, url: 'https://a.example/x.js', exception: { type: 'object', subtype: 'error', description: 'Error: kaputt' } },
    })
    expect(liste()[0]).toMatchObject({ stufe: 'error', quelle: { url: 'https://a.example/x.js', zeile: 4 } })
  })

  it('ein Seitenwechsel leert, außer mit „beibehalten“', () => {
    log('log', 'a')
    useKonsole.getState().ereignis(TAB, 'Runtime.executionContextsCleared', {})
    expect(liste()).toHaveLength(0)

    useKonsole.getState().setBeibehalten(true)
    log('log', 'b')
    useKonsole.getState().ereignis(TAB, 'Runtime.executionContextsCleared', {})
    useKonsole.getState().navigiert(TAB, 'https://b.example/')
    expect(liste().map((e) => e.art)).toEqual(['meldung', 'navigiert'])
  })

  it('behält höchstens MAX Einträge', () => {
    for (let i = 0; i < MAX + 5; i++) log('log', String(i))
    expect(liste()).toHaveLength(MAX)
    expect(liste()[0].werte[0].value).toBe('5')
  })

  it('merkt sich Eingaben ohne Doppel', () => {
    useKonsole.getState().eingabe(TAB, '1+1')
    useKonsole.getState().eingabe(TAB, 'x')
    useKonsole.getState().eingabe(TAB, '1+1')
    expect(useKonsole.getState().verlauf.slice(-2)).toEqual(['x', '1+1'])
  })
})

describe('Vorschläge der Eingabe', () => {
  it('trennt Objektpfad und angefangenen Namen', () => {
    expect(zerlegen('docu')).toEqual({ pfad: '', anfang: 'docu' })
    expect(zerlegen('document.bo')).toEqual({ pfad: 'document', anfang: 'bo' })
    expect(zerlegen('let a = window . document .')).toEqual({ pfad: 'window.document', anfang: '' })
    expect(zerlegen('1 + ')).toBeNull()
  })
})
