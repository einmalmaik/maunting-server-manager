/**
 * Die Elemente: DOM-Baum mit Suche und Pfadleiste, darunter Stile und alles
 * Weitere zum gewählten Knoten. Das Dokument lädt beim Öffnen und nach jedem
 * Seitenwechsel neu.
 */
import { useEffect, useId, useState } from 'react'
import { ChevronDown, ChevronUp, MoreHorizontal } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { ActionMenu, Button, Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, Input, prompt, TabBar, Textarea, type TabDef } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { elementName, selektor, useDom, vorfahren, type Knoten } from './domStore'
import { DomBaum } from './DomBaum'
import { Barrierefreiheit, Berechnet, Ereignisse, Eigenschaften, Layout } from './ElementInfo'
import { Knopf, Leiste } from './Leiste'
import { rufen } from './protokoll'
import { Stile } from './Stile'
import { dokumentLaden, zeigen } from './werkzeuge'

type Unten = 'stile' | 'berechnet' | 'layout' | 'ereignisse' | 'eigenschaften' | 'ax'

const UNTEN: TabDef<Unten>[] = [
  { id: 'stile', labelKey: 'browser.entwickler.elemente.stile' },
  { id: 'berechnet', labelKey: 'browser.entwickler.elemente.berechnet' },
  { id: 'layout', labelKey: 'browser.entwickler.elemente.layout' },
  { id: 'ereignisse', labelKey: 'browser.entwickler.elemente.ereignisse' },
  { id: 'eigenschaften', labelKey: 'browser.entwickler.elemente.eigenschaften' },
  { id: 'ax', labelKey: 'browser.entwickler.elemente.barrierefreiheit' },
]

function aendern(tab: string, methode: string, parameter: Record<string, unknown>) {
  return rufen(tab, 'DOM.markUndoableState').then(() => rufen(tab, methode, parameter))
}

function Suche({ tab }: { tab: string }) {
  const { t } = useTranslation()
  const [begriff, setBegriff] = useState('')
  const [stand, setStand] = useState<{ id: string; zahl: number; i: number } | null>(null)

  useEffect(() => {
    setStand(null)
  }, [begriff])

  const springen = async (richtung: 1 | -1) => {
    let s = stand
    if (!s) {
      if (!begriff.trim()) return
      const r = await rufen<{ searchId: string; resultCount: number }>(tab, 'DOM.performSearch', { query: begriff }).catch(() => null)
      if (!r) return
      s = { id: r.searchId, zahl: r.resultCount, i: -1 }
    }
    if (s.zahl === 0) return setStand(s)
    const i = (s.i + richtung + s.zahl) % s.zahl
    setStand({ ...s, i })
    const r = await rufen<{ nodeIds: number[] }>(tab, 'DOM.getSearchResults', { searchId: s.id, fromIndex: i, toIndex: i + 1 }).catch(() => null)
    if (r?.nodeIds?.[0]) await zeigen(tab, r.nodeIds[0])
  }

  return (
    <>
      <div className="w-40 min-w-32 flex-1">
        <Input
          aria-label={t('browser.entwickler.elemente.suchen')}
          placeholder={t('browser.entwickler.elemente.suchenPlatzhalter')}
          value={begriff}
          onChange={(e) => setBegriff(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              void springen(e.shiftKey ? -1 : 1)
            }
          }}
          className="h-7 text-label-sm"
        />
      </div>
      {stand && <span className="text-label-sm tabular-nums text-on-surface-variant">{stand.zahl ? `${stand.i + 1}/${stand.zahl}` : t('browser.entwickler.keineTreffer')}</span>}
      <Knopf text={t('browser.entwickler.zurueck')} onClick={() => void springen(-1)} disabled={!begriff.trim()}>
        <ChevronUp className="h-4 w-4" aria-hidden="true" />
      </Knopf>
      <Knopf text={t('browser.entwickler.weiter')} onClick={() => void springen(1)} disabled={!begriff.trim()}>
        <ChevronDown className="h-4 w-4" aria-hidden="true" />
      </Knopf>
    </>
  )
}

function HtmlBearbeiten({ tab, k, schliessen }: { tab: string; k: Knoten; schliessen: () => void }) {
  const { t } = useTranslation()
  const titel = useId()
  const [html, setHtml] = useState<string | null>(null)
  useEffect(() => {
    rufen<{ outerHTML: string }>(tab, 'DOM.getOuterHTML', { nodeId: k.nodeId })
      .then((r) => setHtml(r.outerHTML ?? ''))
      .catch(() => setHtml(''))
  }, [tab, k.nodeId])
  const speichern = () => {
    if (html === null) return
    void aendern(tab, 'DOM.setOuterHTML', { nodeId: k.nodeId, outerHTML: html }).catch(() => toast.error(t('browser.entwickler.elemente.htmlFehler')))
    schliessen()
  }
  return (
    <Dialog open onOpenChange={(o) => !o && schliessen()}>
      <DialogContent aria-labelledby={titel} className="max-w-2xl">
        <DialogHeader>
          <DialogTitle id={titel}>{t('browser.entwickler.elemente.alsHtml')}</DialogTitle>
        </DialogHeader>
        <Textarea
          aria-label={t('browser.entwickler.elemente.alsHtml')}
          value={html ?? ''}
          onChange={(e) => setHtml(e.target.value)}
          rows={16}
          spellCheck={false}
          className="font-mono text-label-sm"
        />
        <DialogFooter>
          <Button variant="ghost" onClick={schliessen}>
            {t('common.cancel')}
          </Button>
          <Button onClick={speichern} disabled={html === null}>
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function Elemente({ tab }: { tab: string }) {
  const { t } = useTranslation()
  const baum = useDom((s) => s.baeume[tab])
  const [unten, setUnten] = useState<Unten>('stile')
  const [html, setHtml] = useState<Knoten | null>(null)
  const wurzel = baum?.wurzel ?? null
  const gewaehlt = baum && baum.gewaehlt !== null ? baum.knoten[baum.gewaehlt] : undefined

  // Ohne Dokument (Öffnen, Seitenwechsel) wird es geholt.
  useEffect(() => {
    if (wurzel === null) void dokumentLaden(tab).catch(() => null)
  }, [tab, wurzel])

  const kopieren = (text: string) =>
    void navigator.clipboard.writeText(text).then(
      () => toast.success(t('browser.entwickler.kopiert')),
      () => toast.error(t('browser.entwickler.kopierenFehler')),
    )
  const loeschen = (k: Knoten) => {
    if (k.nodeType === 9 || k.localName === 'html') return
    void aendern(tab, 'DOM.removeNode', { nodeId: k.nodeId }).catch(() => null)
  }

  const menue = gewaehlt
    ? [
        { key: 'html', label: t('browser.entwickler.elemente.alsHtml'), onSelect: () => setHtml(gewaehlt) },
        {
          key: 'attribut',
          label: t('browser.entwickler.elemente.attributNeu'),
          onSelect: () =>
            void prompt({ message: t('browser.entwickler.elemente.attributNeuText'), placeholder: 'name="wert"' }).then((text) => {
              if (text?.trim()) void aendern(tab, 'DOM.setAttributesAsText', { nodeId: gewaehlt.nodeId, text }).catch(() => null)
            }),
        },
        {
          key: 'doppeln',
          label: t('browser.entwickler.elemente.duplizieren'),
          onSelect: () => {
            const eltern = gewaehlt.eltern !== null ? baum?.knoten[gewaehlt.eltern] : undefined
            const naechster = eltern?.kinder?.[eltern.kinder.indexOf(gewaehlt.nodeId) + 1]
            if (eltern) void aendern(tab, 'DOM.copyTo', { nodeId: gewaehlt.nodeId, targetNodeId: eltern.nodeId, insertBeforeNodeId: naechster }).catch(() => null)
          },
        },
        { key: 'sicht', label: t('browser.entwickler.elemente.inSicht'), onSelect: () => void rufen(tab, 'DOM.scrollIntoViewIfNeeded', { nodeId: gewaehlt.nodeId }).catch(() => null) },
        {
          key: 'outer',
          label: t('browser.entwickler.elemente.kopierenHtml'),
          separatorBefore: true,
          onSelect: () => void rufen<{ outerHTML: string }>(tab, 'DOM.getOuterHTML', { nodeId: gewaehlt.nodeId }).then((r) => kopieren(r.outerHTML), () => null),
        },
        { key: 'sel', label: t('browser.entwickler.elemente.kopierenSelektor'), onSelect: () => baum && kopieren(selektor(baum, gewaehlt.nodeId)) },
        { key: 'js', label: t('browser.entwickler.elemente.kopierenJs'), onSelect: () => baum && kopieren(`document.querySelector(${JSON.stringify(selektor(baum, gewaehlt.nodeId))})`) },
        { key: 'weg', label: t('browser.entwickler.elemente.loeschen'), destructive: true, separatorBefore: true, onSelect: () => loeschen(gewaehlt) },
      ]
    : []

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Leiste>
        <Suche tab={tab} />
        <ActionMenu label={t('browser.entwickler.elemente.aktionen')} icon={<MoreHorizontal className="h-4 w-4" aria-hidden="true" />} items={menue} disabled={!gewaehlt} compact align="end" />
      </Leiste>
      <div className="flex min-h-[8rem] flex-[3] flex-col">
        <DomBaum tab={tab} loeschen={loeschen} />
        {baum && gewaehlt && (
          <nav aria-label={t('browser.entwickler.elemente.pfad')} className="flex shrink-0 gap-1 overflow-x-auto border-t border-outline-variant px-2 py-0.5 font-mono text-label-sm msm-ohne-rollbalken">
            {vorfahren(baum, gewaehlt.nodeId)
              .filter((k) => k.nodeType === 1)
              .map((k) => (
                <button
                  key={k.nodeId}
                  type="button"
                  onClick={() => useDom.getState().waehlen(tab, k.nodeId)}
                  className={`shrink-0 rounded px-1 ${k.nodeId === gewaehlt.nodeId ? 'bg-primary/15 text-on-surface' : 'text-on-surface-variant hover:text-on-surface'}`}
                >
                  {elementName(k)}
                </button>
              ))}
          </nav>
        )}
      </div>
      <div className="flex min-h-[8rem] flex-[2] flex-col border-t border-outline-variant">
        <div className="shrink-0 border-b border-outline-variant px-2 py-1">
          <TabBar tabs={UNTEN} active={unten} onChange={setUnten} embedded kompakt ariaLabel={t('browser.entwickler.elemente.details')} />
        </div>
        {!gewaehlt || gewaehlt.nodeType !== 1 ? (
          <p className="p-3 text-label-sm text-on-surface-variant">{t('browser.entwickler.elemente.nichtsGewaehlt')}</p>
        ) : unten === 'stile' ? (
          <Stile key={gewaehlt.nodeId} tab={tab} nodeId={gewaehlt.nodeId} />
        ) : unten === 'berechnet' ? (
          <Berechnet key={gewaehlt.nodeId} tab={tab} nodeId={gewaehlt.nodeId} />
        ) : unten === 'layout' ? (
          <Layout key={gewaehlt.nodeId} tab={tab} nodeId={gewaehlt.nodeId} />
        ) : unten === 'ereignisse' ? (
          <Ereignisse key={gewaehlt.nodeId} tab={tab} nodeId={gewaehlt.nodeId} />
        ) : unten === 'eigenschaften' ? (
          <Eigenschaften key={gewaehlt.nodeId} tab={tab} nodeId={gewaehlt.nodeId} />
        ) : (
          <Barrierefreiheit key={gewaehlt.nodeId} tab={tab} nodeId={gewaehlt.nodeId} />
        )}
      </div>
      {html && <HtmlBearbeiten tab={tab} k={html} schliessen={() => setHtml(null)} />}
    </div>
  )
}
