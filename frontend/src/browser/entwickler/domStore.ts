/**
 * Der DOM-Baum eines Tabs, wie das Protokoll ihn stückweise schickt: erst das
 * Dokument mit wenigen Ebenen, dann Kinder auf Anfrage (`DOM.requestChildNodes`)
 * und jede Änderung der Seite als Ereignis. Die Kennungen gelten nur bis zum
 * nächsten `DOM.getDocument`.
 */
import { create } from 'zustand'

export interface Knoten {
  nodeId: number
  backendNodeId: number
  eltern: number | null
  nodeType: number
  nodeName: string
  localName: string
  nodeValue: string
  attribute: [string, string][]
  kinderZahl: number
  /** `null`: noch nicht geladen. */
  kinder: number[] | null
  /** Shadow-Root, Dokument eines Rahmens, `::before`/`::after`. */
  extra: number[]
  pseudo?: string
  schatten?: string
}

/** Was `DOM.Node` liefert. */
export interface ProtokollKnoten {
  nodeId: number
  backendNodeId: number
  parentId?: number
  nodeType: number
  nodeName: string
  localName: string
  nodeValue: string
  attributes?: string[]
  childNodeCount?: number
  children?: ProtokollKnoten[]
  shadowRoots?: ProtokollKnoten[]
  contentDocument?: ProtokollKnoten
  pseudoElements?: ProtokollKnoten[]
  pseudoType?: string
  shadowRootType?: string
}

interface Baum {
  wurzel: number | null
  knoten: Record<number, Knoten>
  offen: Record<number, true>
  gewaehlt: number | null
}

interface DomZustand {
  baeume: Record<string, Baum>
  /** Stylesheets: Kennung → Adresse (für den Ort einer Regel). */
  blaetter: Record<string, Record<string, string>>
  dokument: (tab: string, wurzel: ProtokollKnoten) => void
  ereignis: (tab: string, methode: string, daten: Record<string, unknown>) => void
  oeffnen: (tab: string, nodeId: number, offen: boolean) => void
  waehlen: (tab: string, nodeId: number | null) => void
  tabWeg: (tab: string) => void
}

const LEER: Baum = { wurzel: null, knoten: {}, offen: {}, gewaehlt: null }

function paare(attribute: string[] | undefined): [string, string][] {
  const aus: [string, string][] = []
  for (let i = 0; attribute && i + 1 < attribute.length; i += 2) aus.push([attribute[i], attribute[i + 1]])
  return aus
}

/** Nimmt einen Knoten samt allem Mitgeschickten in `ziel` auf. */
function aufnehmen(ziel: Record<number, Knoten>, k: ProtokollKnoten, eltern: number | null): number {
  const extra = [...(k.shadowRoots ?? []), ...(k.contentDocument ? [k.contentDocument] : []), ...(k.pseudoElements ?? [])]
  ziel[k.nodeId] = {
    nodeId: k.nodeId,
    backendNodeId: k.backendNodeId,
    eltern,
    nodeType: k.nodeType,
    nodeName: k.nodeName,
    localName: k.localName,
    nodeValue: k.nodeValue,
    attribute: paare(k.attributes),
    kinderZahl: k.childNodeCount ?? k.children?.length ?? 0,
    kinder: k.children ? k.children.map((c) => aufnehmen(ziel, c, k.nodeId)) : (ziel[k.nodeId]?.kinder ?? null),
    extra: extra.map((c) => aufnehmen(ziel, c, k.nodeId)),
    pseudo: k.pseudoType,
    schatten: k.shadowRootType,
  }
  return k.nodeId
}

export const useDom = create<DomZustand>()((set) => {
  const baum = (tab: string, f: (b: Baum) => Baum) => set((s) => ({ baeume: { ...s.baeume, [tab]: f(s.baeume[tab] ?? LEER) } }))
  const knotenAendern = (tab: string, nodeId: number, f: (k: Knoten) => Knoten) =>
    baum(tab, (b) => (b.knoten[nodeId] ? { ...b, knoten: { ...b.knoten, [nodeId]: f(b.knoten[nodeId]) } } : b))

  return {
    baeume: {},
    blaetter: {},
    dokument: (tab, wurzel) => {
      const knoten: Record<number, Knoten> = {}
      aufnehmen(knoten, wurzel, null)
      // Dokument und <html> stehen offen, wie in jedem Browser.
      const offen: Record<number, true> = { [wurzel.nodeId]: true }
      const html = wurzel.children?.find((c) => c.nodeType === 1)
      if (html) offen[html.nodeId] = true
      baum(tab, () => ({ wurzel: wurzel.nodeId, knoten, offen, gewaehlt: null }))
    },
    ereignis: (tab, methode, d) => {
      switch (methode) {
        case 'DOM.documentUpdated':
          baum(tab, () => LEER)
          break
        case 'DOM.setChildNodes':
          baum(tab, (b) => {
            const eltern = Number(d.parentId)
            if (!b.knoten[eltern]) return b
            const knoten = { ...b.knoten }
            const kinder = (d.nodes as ProtokollKnoten[]).map((k) => aufnehmen(knoten, k, eltern))
            knoten[eltern] = { ...knoten[eltern], kinder, kinderZahl: kinder.length }
            return { ...b, knoten }
          })
          break
        case 'DOM.childNodeInserted':
          baum(tab, (b) => {
            const eltern = b.knoten[Number(d.parentNodeId)]
            if (!eltern) return b
            const knoten = { ...b.knoten }
            const neu = aufnehmen(knoten, d.node as ProtokollKnoten, eltern.nodeId)
            const kinder = (eltern.kinder ?? []).filter((k) => k !== neu)
            const davor = Number(d.previousNodeId)
            kinder.splice(davor ? kinder.indexOf(davor) + 1 : 0, 0, neu)
            knoten[eltern.nodeId] = { ...eltern, kinder: eltern.kinder ? kinder : null, kinderZahl: Math.max(eltern.kinderZahl + 1, kinder.length) }
            return { ...b, knoten }
          })
          break
        case 'DOM.childNodeRemoved':
          baum(tab, (b) => {
            const eltern = b.knoten[Number(d.parentNodeId)]
            const weg = Number(d.nodeId)
            if (!eltern) return b
            const kinder = eltern.kinder?.filter((k) => k !== weg) ?? null
            const knoten = { ...b.knoten, [eltern.nodeId]: { ...eltern, kinder, kinderZahl: Math.max(0, eltern.kinderZahl - 1) } }
            delete knoten[weg]
            return { ...b, knoten, gewaehlt: b.gewaehlt === weg ? eltern.nodeId : b.gewaehlt }
          })
          break
        case 'DOM.childNodeCountUpdated':
          knotenAendern(tab, Number(d.nodeId), (k) => ({ ...k, kinderZahl: Number(d.childNodeCount), kinder: null }))
          break
        case 'DOM.attributeModified':
          knotenAendern(tab, Number(d.nodeId), (k) => {
            const name = String(d.name)
            const wert = String(d.value)
            const da = k.attribute.some(([n]) => n === name)
            return { ...k, attribute: da ? k.attribute.map(([n, w]) => [n, n === name ? wert : w]) : [...k.attribute, [name, wert]] }
          })
          break
        case 'DOM.attributeRemoved':
          knotenAendern(tab, Number(d.nodeId), (k) => ({ ...k, attribute: k.attribute.filter(([n]) => n !== d.name) }))
          break
        case 'DOM.characterDataModified':
          knotenAendern(tab, Number(d.nodeId), (k) => ({ ...k, nodeValue: String(d.characterData) }))
          break
        case 'CSS.styleSheetAdded': {
          const kopf = d.header as { styleSheetId: string; sourceURL: string }
          set((s) => ({ blaetter: { ...s.blaetter, [tab]: { ...s.blaetter[tab], [kopf.styleSheetId]: kopf.sourceURL } } }))
          break
        }
      }
    },
    oeffnen: (tab, nodeId, offen) =>
      baum(tab, (b) => {
        const neu = { ...b.offen }
        if (offen) neu[nodeId] = true
        else delete neu[nodeId]
        return { ...b, offen: neu }
      }),
    waehlen: (tab, nodeId) => baum(tab, (b) => ({ ...b, gewaehlt: nodeId })),
    tabWeg: (tab) =>
      set((s) => {
        const baeume = { ...s.baeume }
        const blaetter = { ...s.blaetter }
        delete baeume[tab]
        delete blaetter[tab]
        return { baeume, blaetter }
      }),
  }
})

/** Die Vorfahren eines Knotens, vom Dokument abwärts. */
export function vorfahren(b: { knoten: Record<number, Knoten> }, nodeId: number): Knoten[] {
  const aus: Knoten[] = []
  let k: Knoten | undefined = b.knoten[nodeId]
  while (k) {
    aus.unshift(k)
    k = k.eltern === null ? undefined : b.knoten[k.eltern]
  }
  return aus
}

/** Ein CSS-Selektor, der den Knoten von `<html>` aus trifft. */
export function selektor(b: { knoten: Record<number, Knoten> }, nodeId: number): string {
  const teile: string[] = []
  for (const k of vorfahren(b, nodeId)) {
    if (k.nodeType !== 1) continue
    const id = k.attribute.find(([n]) => n === 'id')?.[1]
    if (id && /^[A-Za-z][\w-]*$/.test(id)) {
      teile.length = 0
      teile.push(`#${id}`)
      continue
    }
    const eltern = k.eltern !== null ? b.knoten[k.eltern] : undefined
    const gleiche = eltern?.kinder?.map((c) => b.knoten[c]).filter((c) => c?.nodeType === 1 && c.localName === k.localName) ?? []
    teile.push(gleiche.length > 1 ? `${k.localName}:nth-of-type(${gleiche.indexOf(k) + 1})` : k.localName)
  }
  return teile.join(' > ')
}

/** Kurzname eines Elements wie in der Pfadleiste: `div#app.kopf`. */
export function elementName(k: Knoten): string {
  if (k.nodeType !== 1) return k.nodeName.toLowerCase()
  const id = k.attribute.find(([n]) => n === 'id')?.[1]
  const klassen = (k.attribute.find(([n]) => n === 'class')?.[1] ?? '').split(/\s+/).filter(Boolean)
  return `${k.localName}${id ? `#${id}` : ''}${klassen.map((c) => `.${c}`).join('')}`
}
