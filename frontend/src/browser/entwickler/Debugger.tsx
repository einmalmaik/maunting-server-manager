/**
 * Der Debugger der Quellen: Anhalten, Fortsetzen und Schritte, Ausnahmen,
 * und unter dem Code Aufrufstapel, Bereiche, Haltepunkte und Beobachten.
 */
import { useEffect, useState } from 'react'
import { ArrowDownToLine, ArrowUpFromLine, CornerDownRight, Pause, Play, Plus, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Checkbox, Dropdown, Input, TabBar, type TabDef } from '@/Singra/UI'

import { auswerten } from './KonsolenEingabe'
import { Knopf, Leiste } from './Leiste'
import { Wert } from './ObjektBaum'
import { ortText, rufen, type Objekt } from './protokoll'
import { useQuellen } from './quellenStore'
import { useAnsicht } from './werkzeuge'

const leise = (p: Promise<unknown>) => void p.catch(() => null)

export function DebuggerLeiste({ tab }: { tab: string }) {
  const { t } = useTranslation()
  const halt = useQuellen((s) => s.halt[tab])
  const [ausnahmen, setAusnahmen] = useState('none')
  const [aktiv, setAktiv] = useState(true)

  // Tastenkürzel wie in jedem Browser, solange die Quellen offen sind.
  useEffect(() => {
    const taste = (e: KeyboardEvent) => {
      const methode =
        e.key === 'F8' ? (halt ? 'Debugger.resume' : 'Debugger.pause') : !halt ? null : e.key === 'F10' ? 'Debugger.stepOver' : e.key === 'F11' ? (e.shiftKey ? 'Debugger.stepOut' : 'Debugger.stepInto') : null
      if (!methode) return
      e.preventDefault()
      leise(rufen(tab, methode))
    }
    window.addEventListener('keydown', taste)
    return () => window.removeEventListener('keydown', taste)
  }, [tab, halt])

  return (
    <Leiste>
      {halt ? (
        <Knopf text={t('browser.entwickler.quellen.fortsetzen')} onClick={() => leise(rufen(tab, 'Debugger.resume'))}>
          <Play className="h-4 w-4" aria-hidden="true" />
        </Knopf>
      ) : (
        <Knopf text={t('browser.entwickler.quellen.anhalten')} onClick={() => leise(rufen(tab, 'Debugger.pause'))}>
          <Pause className="h-4 w-4" aria-hidden="true" />
        </Knopf>
      )}
      <Knopf text={t('browser.entwickler.quellen.ueberspringen')} disabled={!halt} onClick={() => leise(rufen(tab, 'Debugger.stepOver'))}>
        <CornerDownRight className="h-4 w-4" aria-hidden="true" />
      </Knopf>
      <Knopf text={t('browser.entwickler.quellen.hinein')} disabled={!halt} onClick={() => leise(rufen(tab, 'Debugger.stepInto'))}>
        <ArrowDownToLine className="h-4 w-4" aria-hidden="true" />
      </Knopf>
      <Knopf text={t('browser.entwickler.quellen.heraus')} disabled={!halt} onClick={() => leise(rufen(tab, 'Debugger.stepOut'))}>
        <ArrowUpFromLine className="h-4 w-4" aria-hidden="true" />
      </Knopf>
      <label className="flex items-center gap-1.5 text-label-sm text-on-surface-variant">
        <Checkbox
          checked={aktiv}
          onCheckedChange={(an) => {
            setAktiv(an)
            leise(rufen(tab, 'Debugger.setBreakpointsActive', { active: an }))
          }}
        />
        {t('browser.entwickler.quellen.haltepunkteAktiv')}
      </label>
      <div className="w-44">
        <Dropdown
          aria-label={t('browser.entwickler.quellen.ausnahmen')}
          value={ausnahmen}
          onChange={(v) => {
            setAusnahmen(v)
            leise(rufen(tab, 'Debugger.setPauseOnExceptions', { state: v }))
          }}
          options={['none', 'uncaught', 'all'].map((v) => ({ value: v, label: t(`browser.entwickler.quellen.ausnahme.${v}`) }))}
          buttonClassName="h-7 text-label-sm"
        />
      </div>
      {halt && (
        <span className="rounded bg-status-warning/20 px-2 py-0.5 text-label-sm text-status-warning">
          {t(`browser.entwickler.quellen.grund.${halt.grund}`, { defaultValue: t('browser.entwickler.quellen.grund.other') })}
        </span>
      )}
    </Leiste>
  )
}

type Reiter = 'stapel' | 'haltepunkte' | 'beobachten'

const REITER: TabDef<Reiter>[] = [
  { id: 'stapel', labelKey: 'browser.entwickler.quellen.stapel' },
  { id: 'haltepunkte', labelKey: 'browser.entwickler.quellen.haltepunkte' },
  { id: 'beobachten', labelKey: 'browser.entwickler.quellen.beobachten' },
]

function Beobachten({ tab }: { tab: string }) {
  const { t } = useTranslation()
  const halt = useQuellen((s) => s.halt[tab])
  const [ausdruecke, setAusdruecke] = useState<string[]>([])
  const [neu, setNeu] = useState('')
  const [werte, setWerte] = useState<Record<string, Objekt>>({})

  useEffect(() => {
    let gilt = true
    void Promise.all(ausdruecke.map((a) => auswerten(tab, a).then((r) => [a, r.wert] as const, () => [a, { type: 'string', value: '…' }] as const))).then(
      (liste) => gilt && setWerte(Object.fromEntries(liste)),
    )
    return () => {
      gilt = false
    }
  }, [tab, ausdruecke, halt])

  return (
    <div className="p-2 font-mono text-label-sm">
      {ausdruecke.map((a) => (
        <div key={a} className="flex items-start gap-1">
          <span className="min-w-0 flex-1">{werte[a] ? <Wert tab={tab} o={werte[a]} name={a} /> : a}</span>
          <button type="button" aria-label={t('browser.entwickler.quellen.beobachtenWeg', { ausdruck: a })} onClick={() => setAusdruecke(ausdruecke.filter((x) => x !== a))} className="text-on-surface-variant hover:text-on-surface">
            <X className="h-3 w-3" aria-hidden="true" />
          </button>
        </div>
      ))}
      <form
        className="mt-1 flex items-center gap-1"
        onSubmit={(e) => {
          e.preventDefault()
          const a = neu.trim()
          if (a && !ausdruecke.includes(a)) setAusdruecke([...ausdruecke, a])
          setNeu('')
        }}
      >
        <Plus className="h-3 w-3 shrink-0 text-on-surface-variant" aria-hidden="true" />
        <Input aria-label={t('browser.entwickler.quellen.beobachtenNeu')} placeholder={t('browser.entwickler.quellen.beobachtenNeu')} value={neu} onChange={(e) => setNeu(e.target.value)} className="h-6 font-mono text-label-sm" />
      </form>
    </div>
  )
}

export function DebuggerSeite({ tab, haltepunktWeg }: { tab: string; haltepunktWeg: (id: string) => void }) {
  const { t } = useTranslation()
  const [reiter, setReiter] = useState<Reiter>('stapel')
  const halt = useQuellen((s) => s.halt[tab])
  const haltepunkte = useQuellen((s) => s.haltepunkte[tab])
  const rahmenWaehlen = useQuellen((s) => s.rahmenWaehlen)
  const setZiel = useAnsicht((s) => s.setZiel)
  const rahmen = halt?.rahmen[halt.gewaehlt]

  return (
    <div className="flex min-h-[7rem] flex-[2] flex-col border-t border-outline-variant">
      <div className="shrink-0 border-b border-outline-variant px-2 py-1">
        <TabBar tabs={REITER} active={reiter} onChange={setReiter} embedded kompakt ariaLabel={t('browser.entwickler.quellen.debugger')} />
      </div>
      <div className="min-h-0 flex-1 select-text overflow-y-auto text-label-sm">
        {reiter === 'stapel' &&
          (!halt ? (
            <p className="p-3 text-on-surface-variant">{t('browser.entwickler.quellen.nichtAngehalten')}</p>
          ) : (
            <>
              <ul aria-label={t('browser.entwickler.quellen.stapel')}>
                {halt.rahmen.map((r, i) => (
                  <li key={r.callFrameId}>
                    <button
                      type="button"
                      aria-current={i === halt.gewaehlt}
                      onClick={() => {
                        rahmenWaehlen(tab, i)
                        setZiel({ url: r.url, scriptId: r.location.scriptId, zeile: r.location.lineNumber })
                      }}
                      className={`flex w-full gap-2 px-3 py-0.5 text-left ${i === halt.gewaehlt ? 'bg-primary/15' : 'hover:bg-surface-container-high'}`}
                    >
                      <span className="min-w-0 flex-1 truncate font-mono">{r.functionName || t('browser.entwickler.quellen.anonym')}</span>
                      <span className="shrink-0 text-on-surface-variant">{r.url ? ortText(r.url, r.location.lineNumber) : ''}</span>
                    </button>
                  </li>
                ))}
              </ul>
              {rahmen && (
                <div className="border-t border-outline-variant/50 p-2 font-mono">
                  {rahmen.scopeChain.map((b, i) => (
                    <div key={`${halt.gewaehlt}-${i}`}>
                      <Wert tab={tab} o={b.object} aufgeklappt={i === 0} name={t(`browser.entwickler.quellen.bereich.${b.type}`, { defaultValue: b.type }) + (b.name ? ` (${b.name})` : '')} />
                    </div>
                  ))}
                  <Wert tab={tab} o={rahmen.this} name="this" />
                </div>
              )}
            </>
          ))}
        {reiter === 'haltepunkte' &&
          (!haltepunkte?.length ? (
            <p className="p-3 text-on-surface-variant">{t('browser.entwickler.quellen.keineHaltepunkte')}</p>
          ) : (
            <ul>
              {haltepunkte.map((h) => (
                <li key={h.id} className="flex items-center gap-2 px-3 py-0.5">
                  <button type="button" className="min-w-0 flex-1 truncate text-left font-mono hover:text-primary" onClick={() => setZiel({ url: h.url, zeile: h.zeile })}>
                    {ortText(h.url, h.zeile)}
                    {h.bedingung && <span className="text-on-surface-variant"> · {h.bedingung}</span>}
                  </button>
                  <button type="button" aria-label={t('browser.entwickler.quellen.haltepunktWeg', { zeile: h.zeile + 1 })} onClick={() => haltepunktWeg(h.id)} className="text-on-surface-variant hover:text-on-surface">
                    <X className="h-3 w-3" aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          ))}
        {reiter === 'beobachten' && <Beobachten tab={tab} />}
      </div>
    </div>
  )
}
