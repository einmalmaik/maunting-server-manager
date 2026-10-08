/**
 * Das Netzwerk: alle Anfragen seit dem Öffnen mit Filter nach Art und Text,
 * Cache und Drosselung wie in jedem Browser, darunter die Summe. Ein Klick
 * öffnet die Einzelheiten.
 */
import { useMemo, useState } from 'react'
import { RotateCw, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Checkbox, Dropdown, Input } from '@/Singra/UI'

import { Knopf, Leiste } from './Leiste'
import { NetzDetails } from './NetzDetails'
import { TYPEN, typfilter, useNetz, type Anfrage, type Typfilter } from './netzStore'
import { dauer, groesse, name } from './netzText'
import { rufen } from './protokoll'

const LEER: Anfrage[] = []

/** Drosselung: Verzögerung in ms, Durchsatz in Byte/s. */
export const DROSSELUNG: Record<string, { offline: boolean; latency: number; downloadThroughput: number; uploadThroughput: number }> = {
  aus: { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 },
  schnell: { offline: false, latency: 60, downloadThroughput: (9000 * 1024) / 8, uploadThroughput: (1500 * 1024) / 8 },
  langsam: { offline: false, latency: 150, downloadThroughput: (1600 * 1024) / 8, uploadThroughput: (750 * 1024) / 8 },
  sehrLangsam: { offline: false, latency: 400, downloadThroughput: (400 * 1024) / 8, uploadThroughput: (400 * 1024) / 8 },
  offline: { offline: true, latency: 0, downloadThroughput: 0, uploadThroughput: 0 },
}

export function Netzwerk({ tab }: { tab: string }) {
  const { t } = useTranslation()
  const anfragen = useNetz((s) => s.anfragen[tab] ?? LEER)
  const beibehalten = useNetz((s) => s.beibehalten)
  const cacheAus = useNetz((s) => s.cacheAus)
  const [typ, setTyp] = useState<Typfilter>('alle')
  const [suche, setSuche] = useState('')
  const [drossel, setDrossel] = useState('aus')
  const [offen, setOffen] = useState<string | null>(null)

  const sichtbar = useMemo(() => {
    const s = suche.trim().toLowerCase()
    return anfragen.filter((a) => (typ === 'alle' || typfilter(a.typ) === typ) && (!s || a.url.toLowerCase().includes(s)))
  }, [anfragen, typ, suche])

  const zeitachse = useMemo(() => {
    const starts = anfragen.map((a) => a.start).filter((z) => z > 0)
    const von = starts.length ? Math.min(...starts) : 0
    const bis = Math.max(von, ...anfragen.map((a) => a.ende ?? a.start))
    return { von, spanne: Math.max(0.001, bis - von) }
  }, [anfragen])

  const summe = useMemo(() => {
    const uebertragen = sichtbar.reduce((n, a) => n + (a.ausCache ? 0 : (a.groesse ?? 0)), 0)
    return { zahl: sichtbar.length, uebertragen, zeit: zeitachse.spanne * 1000 }
  }, [sichtbar, zeitachse])

  const gewaehlt = offen ? anfragen.find((a) => a.nr === offen) : undefined
  if (gewaehlt) return <NetzDetails tab={tab} a={gewaehlt} schliessen={() => setOffen(null)} />

  const cacheSchalten = (an: boolean) => {
    useNetz.getState().setCacheAus(an)
    void rufen(tab, 'Network.setCacheDisabled', { cacheDisabled: an }).catch(() => null)
  }
  const drosseln = (wert: string) => {
    setDrossel(wert)
    void rufen(tab, 'Network.emulateNetworkConditions', DROSSELUNG[wert]).catch(() => null)
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Leiste>
        <Knopf text={t('browser.entwickler.netz.leeren')} onClick={() => useNetz.getState().leeren(tab)}>
          <Trash2 className="h-4 w-4" aria-hidden="true" />
        </Knopf>
        <Knopf text={t('browser.entwickler.neuLaden')} onClick={() => void rufen(tab, 'Page.reload', { ignoreCache: cacheAus }).catch(() => null)}>
          <RotateCw className="h-4 w-4" aria-hidden="true" />
        </Knopf>
        <div className="w-36 min-w-32 flex-1">
          <Input aria-label={t('browser.entwickler.filter')} placeholder={t('browser.entwickler.filter')} value={suche} onChange={(e) => setSuche(e.target.value)} className="h-7 text-label-sm" />
        </div>
        <label className="flex items-center gap-1.5 text-label-sm text-on-surface-variant">
          <Checkbox checked={beibehalten} onCheckedChange={(an) => useNetz.getState().setBeibehalten(an)} />
          {t('browser.entwickler.beibehalten')}
        </label>
        <label className="flex items-center gap-1.5 text-label-sm text-on-surface-variant">
          <Checkbox checked={cacheAus} onCheckedChange={cacheSchalten} />
          {t('browser.entwickler.netz.cacheAus')}
        </label>
        <div className="w-40">
          <Dropdown
            aria-label={t('browser.entwickler.netz.drosselung')}
            value={drossel}
            onChange={drosseln}
            options={Object.keys(DROSSELUNG).map((k) => ({ value: k, label: t(`browser.entwickler.netz.drossel.${k}`) }))}
            buttonClassName="h-7 text-label-sm"
          />
        </div>
      </Leiste>
      <div role="group" aria-label={t('browser.entwickler.netz.arten')} className="flex shrink-0 gap-1 overflow-x-auto border-b border-outline-variant px-2 py-1 msm-ohne-rollbalken">
        {TYPEN.map((f) => (
          <button
            key={f}
            type="button"
            aria-pressed={typ === f}
            onClick={() => setTyp(f)}
            className={`h-6 shrink-0 rounded px-2 text-label-sm [@media(pointer:coarse)]:h-11 ${typ === f ? 'bg-primary/15 text-on-surface' : 'text-on-surface-variant hover:bg-surface-container-high'}`}
          >
            {t(`browser.entwickler.netz.typ.${f}`)}
          </button>
        ))}
      </div>
      {anfragen.length === 0 ? (
        <p className="p-3 text-body-sm text-on-surface-variant">{t('browser.entwickler.netz.leer')}</p>
      ) : (
        <div role="table" aria-label={t('browser.entwickler.netz.titel')} className="min-h-0 flex-1 select-text overflow-y-auto text-label-sm">
          {/* Im schmalen Panel fallen Typ und Verlauf weg, sonst bliebe für den Namen nichts. */}
          <div role="row" className="sticky top-0 z-[1] grid grid-cols-[1fr_3rem_4.5rem_4rem_3.5rem_4rem] max-sm:grid-cols-[1fr_3rem_4rem_3.5rem] gap-2 border-b border-outline-variant bg-surface-container-low px-3 py-1 text-on-surface-variant">
            {(['name', 'status', 'typ', 'groesse', 'zeit', 'verlauf'] as const).map((s) => (
              <span key={s} role="columnheader" className={s === 'groesse' || s === 'zeit' ? 'text-right' : s === 'typ' || s === 'verlauf' ? 'max-sm:hidden' : ''}>
                {t(`browser.entwickler.netz.spalte.${s}`)}
              </span>
            ))}
          </div>
          {sichtbar.map((a) => {
            const fehler = a.fehler !== null || (a.status !== null && a.status >= 400)
            const n = name(a.url)
            const links = a.start ? ((a.start - zeitachse.von) / zeitachse.spanne) * 100 : 0
            const breite = a.start && a.ende ? Math.max(1, ((a.ende - a.start) / zeitachse.spanne) * 100) : 1
            return (
              <div
                role="row"
                tabIndex={0}
                aria-label={a.url}
                key={a.nr}
                onClick={() => setOffen(a.nr)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    setOffen(a.nr)
                  }
                }}
                className={`grid w-full grid-cols-[1fr_3rem_4.5rem_4rem_3.5rem_4rem] max-sm:grid-cols-[1fr_3rem_4rem_3.5rem] cursor-pointer items-center gap-2 border-b border-outline-variant/40 px-3 py-0.5 text-left hover:bg-surface-container-high focus-visible:bg-surface-container-high focus-visible:outline-none ${fehler ? 'text-status-destructive' : ''}`}
              >
                <span role="cell" className="min-w-0">
                  <span className="block truncate">{n.datei}</span>
                  {n.rest && <span className="block truncate text-label-sm text-on-surface-variant">{n.rest}</span>}
                </span>
                <span role="cell" className="truncate tabular-nums">
                  {a.fehler ? t('browser.entwickler.netz.fehlgeschlagen') : (a.status ?? '…')}
                </span>
                <span role="cell" className="truncate text-on-surface-variant max-sm:hidden">{a.typ}</span>
                <span role="cell" className="truncate text-right tabular-nums text-on-surface-variant">
                  {a.ausCache ? t('browser.entwickler.netz.cacheKurz') : groesse(a.groesse)}
                </span>
                <span role="cell" className="truncate text-right tabular-nums text-on-surface-variant">
                  {a.ende !== null && a.start ? dauer((a.ende - a.start) * 1000) : ''}
                </span>
                <span role="cell" className="relative h-2 max-sm:hidden" aria-hidden="true">
                  <span className={`absolute top-0 h-2 rounded-sm ${fehler ? 'bg-status-destructive/70' : 'bg-primary/60'}`} style={{ left: `${links}%`, width: `${Math.min(breite, 100 - links)}%` }} />
                </span>
              </div>
            )
          })}
        </div>
      )}
      <div className="shrink-0 border-t border-outline-variant px-3 py-1 text-label-sm text-on-surface-variant">
        {t('browser.entwickler.netz.summe', { zahl: summe.zahl, groesse: groesse(summe.uebertragen) || '0 B', zeit: dauer(summe.zeit) || '0 ms' })}
      </div>
    </div>
  )
}
