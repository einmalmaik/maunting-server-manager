import { beforeEach, describe, expect, it } from 'vitest'

import { herkunft } from './Anwendung'
import { elementName, selektor, useDom, type ProtokollKnoten } from './domStore'
import { zeilen } from './DomBaum'
import { abstaende } from './ElementInfo'
import { einpassen } from './Geraet'
import { blasenSeite } from './Leiste'
import { dateienAus } from './Quellen'
import { stilText, ueberdeckt } from './Stile'

const TAB = 'tab-dom'

const element = (nodeId: number, localName: string, attributes: string[] = [], children?: ProtokollKnoten[]): ProtokollKnoten => ({
  nodeId,
  backendNodeId: nodeId + 100,
  nodeType: 1,
  nodeName: localName.toUpperCase(),
  localName,
  nodeValue: '',
  attributes,
  childNodeCount: children?.length ?? 0,
  children,
})
const text = (nodeId: number, wert: string): ProtokollKnoten => ({ nodeId, backendNodeId: nodeId + 100, nodeType: 3, nodeName: '#text', localName: '', nodeValue: wert })

function laden() {
  useDom.getState().dokument(TAB, {
    nodeId: 1,
    backendNodeId: 101,
    nodeType: 9,
    nodeName: '#document',
    localName: '',
    nodeValue: '',
    childNodeCount: 1,
    children: [
      element(2, 'html', [], [
        element(3, 'body', ['class', 'a  b'], [element(4, 'li', [], [text(5, 'eins')]), element(6, 'li', [], [text(7, 'zwei')]), element(8, 'div', ['id', 'app'], [])]),
      ]),
    ],
  })
}

const baum = () => useDom.getState().baeume[TAB]

describe('DOM-Baum', () => {
  beforeEach(() => {
    useDom.getState().tabWeg(TAB)
    laden()
  })

  it('zeigt Dokument und <html> offen, das Dokument ohne eigene Zeile', () => {
    expect(zeilen(baum()).map((z) => `${z.tiefe}${z.art}:${z.k.localName}`)).toEqual(['0auf:html', '1auf:body', '0zu:html'])
  })

  it('stellt kurzen Text in die Zeile seines Elements', () => {
    useDom.getState().oeffnen(TAB, 3, true)
    useDom.getState().oeffnen(TAB, 4, true)
    const z = zeilen(baum()).map((x) => `${x.art}:${x.k.localName || x.k.nodeName}`)
    expect(z).toEqual(['auf:html', 'auf:body', 'auf:li', 'auf:li', 'auf:div', 'zu:body', 'zu:html'])
  })

  it('übernimmt Änderungen der Seite', () => {
    useDom.getState().ereignis(TAB, 'DOM.attributeModified', { nodeId: 8, name: 'class', value: 'neu' })
    useDom.getState().ereignis(TAB, 'DOM.childNodeInserted', { parentNodeId: 3, previousNodeId: 4, node: element(9, 'p') })
    useDom.getState().waehlen(TAB, 6)
    useDom.getState().ereignis(TAB, 'DOM.childNodeRemoved', { parentNodeId: 3, nodeId: 6 })
    const b = baum()
    expect(b.knoten[8].attribute).toEqual([
      ['id', 'app'],
      ['class', 'neu'],
    ])
    expect(b.knoten[3].kinder).toEqual([4, 9, 8])
    expect(b.knoten[6]).toBeUndefined()
    expect(b.gewaehlt).toBe(3)
  })

  it('baut Selektor und Kurznamen', () => {
    expect(selektor(baum(), 6)).toBe('html > body > li:nth-of-type(2)')
    expect(selektor(baum(), 8)).toBe('#app')
    expect(elementName(baum().knoten[3])).toBe('body.a.b')
  })

  it('vergisst den Baum, wenn das Dokument neu ist', () => {
    useDom.getState().ereignis(TAB, 'DOM.documentUpdated', {})
    expect(baum().wurzel).toBeNull()
  })
})

describe('Stile', () => {
  const p = (name: string, value: string, mehr: Record<string, unknown> = {}) => ({ name, value, ...mehr })

  it('schreibt den Stiltext nach einer Änderung', () => {
    const props = [p('color', 'red'), p('margin', '0', { important: true })]
    expect(stilText(props, 0, { name: 'color', value: 'blue' })).toBe('color: blue; margin: 0 !important;')
    expect(stilText(props, 1, null)).toBe('color: red;')
    expect(stilText(props, 0, { name: 'color', value: 'red', disabled: true })).toBe('/* color: red; */ margin: 0 !important;')
    expect(stilText(props, 2, { name: 'gap', value: '4px' })).toBe('color: red; margin: 0 !important; gap: 4px;')
  })

  it('streicht Überdecktes, !important gewinnt auch aus einer schwächeren Regel', () => {
    const stark = p('color', 'red')
    const wichtig = p('color', 'blue', { important: true })
    const aus = p('margin', '1px', { disabled: true })
    const ergebnis = ueberdeckt([
      { cssProperties: [stark, aus] },
      { cssProperties: [wichtig] },
    ])
    expect(ergebnis.has(stark)).toBe(true)
    expect(ergebnis.has(wichtig)).toBe(false)
    expect(ergebnis.has(aus)).toBe(false)
  })
})

describe('Kleinigkeiten', () => {
  it('misst die Abstände des Box-Modells', () => {
    // Außen 0,0 bis 100,50; innen 10,5 bis 90,45.
    expect(abstaende([0, 0, 100, 0, 100, 50, 0, 50], [10, 5, 90, 5, 90, 45, 10, 45])).toEqual([5, 10, 5, 10])
  })

  it('verkleinert ein Gerät, bis es ganz in die Fläche passt', () => {
    expect(einpassen({ breite: 393, hoehe: 852 }, { breite: 300, hoehe: 732 })).toBeCloseTo(300 / 393)
    expect(einpassen({ breite: 375, hoehe: 667 }, { breite: 1000, hoehe: 500 })).toBeCloseTo(500 / 667)
    expect(einpassen({ breite: 375, hoehe: 667 }, { breite: 1000, hoehe: 900 })).toBe(1)
    expect(einpassen({ breite: 375, hoehe: 667 }, { breite: 0, hoehe: 0 })).toBe(1)
  })

  it('hängt die Kurzinfo eines Knopfs am Panelrand bündig an', () => {
    const bereich = new DOMRect(700, 0, 400, 800)
    expect(blasenSeite(new DOMRect(708, 0, 28, 28), bereich)).toBe('anfang')
    expect(blasenSeite(new DOMRect(900, 0, 28, 28), bereich)).toBe('mitte')
    expect(blasenSeite(new DOMRect(1064, 0, 28, 28), bereich)).toBe('ende')
  })

  it('kennt nur http und https als Herkunft', () => {
    expect(herkunft('https://a.example/x?y')).toBe('https://a.example')
    expect(herkunft('about:blank')).toBeNull()
    expect(herkunft('file:///C:/x')).toBeNull()
  })

  it('sammelt die Dateien aller Rahmen', () => {
    const dateien = dateienAus({
      frame: { id: 'F1', url: 'https://a.example/' },
      resources: [{ url: 'https://a.example/x.css', type: 'Stylesheet', mimeType: 'text/css' }],
      childFrames: [{ frame: { id: 'F2', url: 'https://b.example/' }, resources: [] }],
    })
    expect(dateien.map((d) => [d.url, d.frameId])).toEqual([
      ['https://a.example/', 'F1'],
      ['https://a.example/x.css', 'F1'],
      ['https://b.example/', 'F2'],
    ])
  })
})
