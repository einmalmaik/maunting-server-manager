/**
 * Ein- und Ausschalten der Werkzeuge für einen Tab, die Verteilung der
 * Protokollereignisse auf die Ansichten und das Aufdecken eines Knotens.
 *
 * Die Bereiche laufen nur, solange das Panel für diesen Tab offen ist
 * (`desktop/entwickler.rs`). Beim Ausschalten fällt alles weg, was die
 * Werkzeuge an der Seite verstellt haben: Gerät, Drosselung, Haltepunkte.
 */
import { create } from 'zustand'

import i18n from '@/i18n'

import { useDom, vorfahren, type ProtokollKnoten } from './domStore'
import { useKonsole } from './konsoleStore'
import { useNetz } from './netzStore'
import { HERVORHEBUNG, rufen } from './protokoll'
import { useQuellen } from './quellenStore'

export type Ansicht = 'elemente' | 'konsole' | 'quellen' | 'netz' | 'anwendung' | 'leistung' | 'geraet'

/** Was die Ansicht „Gerät“ an der Seite verstellt. */
export interface Emulation {
  geraet: string
  breite: number
  hoehe: number
  faktor: number
  mobil: boolean
  medien: string
  schema: string
  bewegung: string
  cpu: number
  ort: string
  zeitzone: string
}

export const OHNE_EMULATION: Emulation = { geraet: 'aus', breite: 390, hoehe: 844, faktor: 3, mobil: true, medien: '', schema: '', bewegung: '', cpu: 1, ort: '', zeitzone: '' }

interface AnsichtZustand {
  ansicht: Ansicht
  /** Elemente in der Seite per Klick wählen. */
  waehlen: boolean
  /** Die Quellen öffnen diese Stelle, sobald sie zu sehen sind. */
  ziel: { url: string; scriptId?: string; zeile: number } | null
  /** Rechtsklick „Untersuchen“: Punkt in der Seite, CSS-Pixel. */
  untersuchen: { tab: string; x: number; y: number } | null
  debugger: Record<string, boolean>
  emulation: Record<string, Emulation>
  setEmulation: (tab: string, e: Emulation) => void
  setAnsicht: (a: Ansicht) => void
  setWaehlen: (an: boolean) => void
  setZiel: (z: AnsichtZustand['ziel']) => void
  setUntersuchen: (u: AnsichtZustand['untersuchen']) => void
}

export const useAnsicht = create<AnsichtZustand>()((set) => ({
  ansicht: 'elemente',
  waehlen: false,
  ziel: null,
  untersuchen: null,
  debugger: {},
  emulation: {},
  setEmulation: (tab, e) => set((s) => ({ emulation: { ...s.emulation, [tab]: e } })),
  setAnsicht: (ansicht) => set({ ansicht }),
  setWaehlen: (waehlen) => set({ waehlen }),
  setZiel: (ziel) => set(ziel ? { ziel, ansicht: 'quellen' } : { ziel }),
  setUntersuchen: (untersuchen) => set({ untersuchen }),
}))

/** Größe der Fläche, in der die Seite steht (CSS-Pixel); daran passt „Gerät“ ein. */
export const useSeitenflaeche = create<{ breite: number; hoehe: number }>(() => ({ breite: 0, hoehe: 0 }))

const leise = (p: Promise<unknown>) => p.catch(() => null)

/**
 * Ein- und Ausschalten eines Tabs laufen nacheinander. Sonst schaltete das
 * `CSS.disable` eines alten Laufs (Tabwechsel, doppeltes Mounten) die Bereiche
 * hinter dem `CSS.enable` des neuen wieder ab, und Stile blieben leer.
 */
const ketten = new Map<string, Promise<void>>()
function nacheinander(tab: string, f: () => Promise<void>): Promise<void> {
  const p = (ketten.get(tab) ?? Promise.resolve()).then(f)
  ketten.set(tab, p.catch(() => undefined))
  return p
}

export function einschalten(tab: string): Promise<void> {
  return nacheinander(tab, () => einschaltenJetzt(tab))
}

async function einschaltenJetzt(tab: string): Promise<void> {
  // `Runtime.enable` schickt alle Meldungen der Seite noch einmal; ohne Leeren stünden sie doppelt da.
  useKonsole.getState().leeren(tab)
  // Erst aus, dann an: war ein Bereich noch an (Oberfläche neu geladen), schickte er sonst
  // nichts nach, weder Meldungen noch Skripte, und alte Knotenkennungen gälten weiter.
  for (const bereich of ['Runtime', 'Log', 'Network', 'DOM', 'CSS', 'Overlay', 'Page']) {
    await leise(rufen(tab, `${bereich}.disable`))
    await leise(rufen(tab, `${bereich}.enable`))
  }
  useDom.getState().tabWeg(tab)
  if (useNetz.getState().cacheAus) await leise(rufen(tab, 'Network.setCacheDisabled', { cacheDisabled: true }))
  const baum = await rufen<{ frameTree?: { frame: { id: string } } }>(tab, 'Page.getFrameTree').catch(() => null)
  if (baum?.frameTree) useNetz.getState().setHauptFrame(tab, baum.frameTree.frame.id)
}

/** Der Debugger läuft erst, wenn jemand die Quellen öffnet. */
export async function debuggerAn(tab: string): Promise<void> {
  if (useAnsicht.getState().debugger[tab]) return
  useAnsicht.setState((s) => ({ debugger: { ...s.debugger, [tab]: true } }))
  await leise(rufen(tab, 'Debugger.disable'))
  await leise(rufen(tab, 'Debugger.enable', { maxScriptsCacheSize: 100_000_000 }))
  await leise(rufen(tab, 'Overlay.setPausedInDebuggerMessage', { message: i18n.t('browser.entwickler.quellen.angehaltenSeite') }))
}

export function ausschalten(tab: string): Promise<void> {
  return nacheinander(tab, () => ausschaltenJetzt(tab))
}

async function ausschaltenJetzt(tab: string): Promise<void> {
  useAnsicht.setState((s) => {
    const emulation = { ...s.emulation }
    delete emulation[tab]
    return { waehlen: false, debugger: { ...s.debugger, [tab]: false }, emulation }
  })
  await leise(rufen(tab, 'Overlay.setInspectMode', { mode: 'none', highlightConfig: {} }))
  await leise(rufen(tab, 'Overlay.hideHighlight'))
  await leise(rufen(tab, 'Debugger.disable'))
  await leise(rufen(tab, 'Performance.disable'))
  // Was die Werkzeuge an der Seite verstellt haben.
  await leise(rufen(tab, 'Emulation.clearDeviceMetricsOverride'))
  await leise(rufen(tab, 'Emulation.setTouchEmulationEnabled', { enabled: false }))
  await leise(rufen(tab, 'Emulation.setUserAgentOverride', { userAgent: '' }))
  await leise(rufen(tab, 'Emulation.setEmulatedMedia', { media: '', features: [] }))
  await leise(rufen(tab, 'Emulation.setCPUThrottlingRate', { rate: 1 }))
  await leise(rufen(tab, 'Emulation.clearGeolocationOverride'))
  await leise(rufen(tab, 'Emulation.setTimezoneOverride', { timezoneId: '' }))
  await leise(rufen(tab, 'Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 }))
  await leise(rufen(tab, 'Network.setCacheDisabled', { cacheDisabled: false }))
  for (const m of ['CSS.disable', 'DOM.disable', 'Overlay.disable', 'Page.disable', 'Network.disable', 'Log.disable', 'Runtime.disable']) {
    await leise(rufen(tab, m))
  }
  useDom.getState().tabWeg(tab)
  useQuellen.getState().zuruecksetzen(tab)
}

export async function dokumentLaden(tab: string): Promise<void> {
  const { root } = await rufen<{ root: ProtokollKnoten }>(tab, 'DOM.getDocument', { depth: 2 })
  if (root) useDom.getState().dokument(tab, root)
}

/** Zeigt einen Knoten der Seite in den Elementen: Vorfahren auf, gewählt, sichtbar. */
export async function aufdecken(tab: string, ziel: { backendNodeId?: number; objectId?: string }): Promise<void> {
  if (!useDom.getState().baeume[tab]?.wurzel) await dokumentLaden(tab)
  let nodeId: number | undefined
  if (ziel.objectId) {
    nodeId = (await rufen<{ nodeId?: number }>(tab, 'DOM.requestNode', { objectId: ziel.objectId })).nodeId
  } else if (ziel.backendNodeId) {
    nodeId = (await rufen<{ nodeIds?: number[] }>(tab, 'DOM.pushNodesByBackendIdsToFrontend', { backendNodeIds: [ziel.backendNodeId] })).nodeIds?.[0]
  }
  if (nodeId) await zeigen(tab, nodeId)
}

/** Öffnet die Vorfahren eines bekannten Knotens und wählt ihn. */
export async function zeigen(tab: string, nodeId: number): Promise<void> {
  // Den Weg dorthin schickt die Seite als `DOM.setChildNodes`, getrennt von der Antwort.
  for (let i = 0; i < 40 && !useDom.getState().baeume[tab]?.knoten[nodeId]; i++) {
    await new Promise((r) => setTimeout(r, 25))
  }
  const baum = useDom.getState().baeume[tab]
  if (!baum?.knoten[nodeId]) return
  const { oeffnen, waehlen } = useDom.getState()
  for (const k of vorfahren(baum, nodeId).slice(0, -1)) oeffnen(tab, k.nodeId, true)
  waehlen(tab, nodeId)
  useAnsicht.getState().setAnsicht('elemente')
}

export async function waehlenUmschalten(tab: string, an: boolean): Promise<void> {
  useAnsicht.getState().setWaehlen(an)
  if (an && !useDom.getState().baeume[tab]?.wurzel) await dokumentLaden(tab)
  await leise(rufen(tab, 'Overlay.setInspectMode', { mode: an ? 'searchForNode' : 'none', highlightConfig: HERVORHEBUNG }))
}

/** Verteilt ein Ereignis des Protokolls auf die Ansichten. */
export function protokollEreignis(tab: string, methode: string, daten: Record<string, unknown>): void {
  if (methode.startsWith('Network.')) {
    const seite = useNetz.getState().ereignis(tab, methode, daten)
    if (seite) useKonsole.getState().navigiert(tab, seite)
  } else if (methode.startsWith('DOM.') || methode === 'CSS.styleSheetAdded') {
    useDom.getState().ereignis(tab, methode, daten)
  } else if (methode.startsWith('Debugger.')) {
    useQuellen.getState().ereignis(tab, methode, daten)
  } else if (methode === 'Runtime.executionContextsCleared') {
    useKonsole.getState().ereignis(tab, methode, daten)
    useQuellen.getState().ereignis(tab, methode, daten)
  } else if (methode.startsWith('Runtime.') || methode.startsWith('Log.')) {
    useKonsole.getState().ereignis(tab, methode, daten)
  } else if (methode === 'Overlay.inspectNodeRequested') {
    void waehlenUmschalten(tab, false)
    void aufdecken(tab, { backendNodeId: Number(daten.backendNodeId) })
  } else if (methode === 'Overlay.inspectModeCanceled') {
    useAnsicht.getState().setWaehlen(false)
  }
}

/** Ein Tab ist zu: alles, was die Werkzeuge über ihn wissen, fällt weg. */
export function tabWeg(tab: string): void {
  ketten.delete(tab)
  useKonsole.getState().tabWeg(tab)
  useNetz.getState().tabWeg(tab)
  useDom.getState().tabWeg(tab)
  useQuellen.getState().tabWeg(tab)
}
