/**
 * Eine Anfrage im Einzelnen: Kopfzeilen, Nutzlast, Vorschau, Antwort, Cookies,
 * Zeitablauf und bei WebSockets die Nachrichten. Körper holt die Ansicht erst,
 * wenn ihr Reiter offen ist (`Network.getResponseBody`, `getRequestPostData`).
 */
import { useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, Copy, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { ActionMenu, TabBar, type TabDef } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { Knopf } from './Leiste'
import type { Anfrage, Koepfe } from './netzStore'
import { alsCurl, alsFetch, anfrageKoepfe, antwortKoepfe, cookiesDerAnfrage, cookiesDerAntwort, dauer, groesse, kopf, phasen } from './netzText'
import { JsonWert } from './ObjektBaum'
import { ortText, rufen } from './protokoll'
import { useAnsicht } from './werkzeuge'

type Reiter = 'koepfe' | 'nutzlast' | 'vorschau' | 'antwort' | 'cookies' | 'zeit' | 'nachrichten'

/** So viel Antwort zeigt die Ansicht als Text; der Rest steht beim Kopieren bereit. */
const TEXT_MAX = 400_000

function Liste({ titel, eintraege }: { titel: string; eintraege: [string, string][] }) {
  if (eintraege.length === 0) return null
  return (
    <section className="border-b border-outline-variant/50 py-1.5">
      {titel && <h3 className="px-3 pb-1 text-label-sm font-semibold text-on-surface">{titel}</h3>}
      <dl className="grid grid-cols-[minmax(6rem,auto)_1fr] gap-x-3 gap-y-0.5 px-3 font-mono text-label-sm">
        {eintraege.map(([n, w], i) => (
          <div key={`${n}-${i}`} className="contents">
            <dt className="text-secondary">{n}</dt>
            <dd className="min-w-0 break-all text-on-surface">{w}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

const zeilen = (k: Koepfe) => Object.entries(k).sort(([a], [b]) => a.localeCompare(b))

function useKoerper(tab: string, a: Anfrage, an: boolean) {
  const [koerper, setKoerper] = useState<{ text: string; base64: boolean } | null | 'fehlt'>(null)
  useEffect(() => {
    if (!an || koerper !== null) return
    rufen<{ body: string; base64Encoded: boolean }>(tab, 'Network.getResponseBody', { requestId: a.kennung })
      .then((r) => setKoerper({ text: r.body ?? '', base64: !!r.base64Encoded }))
      .catch(() => setKoerper('fehlt'))
  }, [tab, a.kennung, an, koerper])
  return koerper
}

function useNutzlast(tab: string, a: Anfrage) {
  const [nutzlast, setNutzlast] = useState<string | null>(a.nutzlast)
  useEffect(() => {
    if (!a.hatNutzlast || nutzlast !== null) return
    rufen<{ postData: string }>(tab, 'Network.getRequestPostData', { requestId: a.kennung })
      .then((r) => setNutzlast(r.postData ?? ''))
      .catch(() => null)
  }, [tab, a.kennung, a.hatNutzlast, nutzlast])
  return nutzlast
}

function text(k: { text: string; base64: boolean }): string {
  if (!k.base64) return k.text
  try {
    return new TextDecoder().decode(Uint8Array.from(atob(k.text), (c) => c.charCodeAt(0)))
  } catch {
    return k.text
  }
}

function json(roh: string): unknown {
  try {
    return JSON.parse(roh)
  } catch {
    return undefined
  }
}

export function NetzDetails({ tab, a, schliessen }: { tab: string; a: Anfrage; schliessen: () => void }) {
  const { t } = useTranslation()
  const [reiter, setReiter] = useState<Reiter>('koepfe')
  const koerper = useKoerper(tab, a, reiter === 'vorschau' || reiter === 'antwort')
  const nutzlast = useNutzlast(tab, a)
  const setZiel = useAnsicht((s) => s.setZiel)
  const ws = a.typ === 'WebSocket'
  const query = (() => {
    try {
      return [...new URL(a.url).searchParams.entries()]
    } catch {
      return []
    }
  })()

  const reiterListe: TabDef<Reiter>[] = [
    { id: 'koepfe', labelKey: 'browser.entwickler.netz.koepfe' },
    ...(query.length || a.hatNutzlast ? [{ id: 'nutzlast' as const, labelKey: 'browser.entwickler.netz.nutzlast' }] : []),
    ...(ws
      ? [{ id: 'nachrichten' as const, labelKey: 'browser.entwickler.netz.nachrichten' }]
      : [
          { id: 'vorschau' as const, labelKey: 'browser.entwickler.netz.vorschau' },
          { id: 'antwort' as const, labelKey: 'browser.entwickler.netz.antwort' },
        ]),
    { id: 'cookies', labelKey: 'browser.entwickler.netz.cookies' },
    { id: 'zeit', labelKey: 'browser.entwickler.netz.zeit' },
  ]

  const kopieren = (wert: string) =>
    void navigator.clipboard.writeText(wert).then(
      () => toast.success(t('browser.entwickler.kopiert')),
      () => toast.error(t('browser.entwickler.kopierenFehler')),
    )

  const inhalt = (() => {
    switch (reiter) {
      case 'koepfe':
        return (
          <>
            <Liste
              titel={t('browser.entwickler.netz.allgemein')}
              eintraege={[
                [t('browser.entwickler.netz.adresse'), a.url],
                [t('browser.entwickler.netz.methode'), a.methode],
                [t('browser.entwickler.netz.status'), a.fehler ?? `${a.status ?? ''} ${a.statusText}`.trim()],
                ...(a.adresse ? [[t('browser.entwickler.netz.gegenstelle'), a.adresse] as [string, string]] : []),
                ...(a.protokoll ? [[t('browser.entwickler.netz.protokoll'), a.protokoll] as [string, string]] : []),
                ...(a.ausCache ? [[t('browser.entwickler.netz.cache'), t('browser.entwickler.netz.ausCache')] as [string, string]] : []),
              ]}
            />
            {a.ausloeser && (
              <section className="border-b border-outline-variant/50 px-3 py-1.5 text-label-sm">
                <h3 className="pb-1 font-semibold text-on-surface">{t('browser.entwickler.netz.ausloeser')}</h3>
                {a.ausloeser.url ? (
                  <button type="button" className="font-mono text-primary hover:underline" onClick={() => setZiel({ url: a.ausloeser!.url!, zeile: a.ausloeser!.lineNumber ?? 0 })}>
                    {ortText(a.ausloeser.url, a.ausloeser.lineNumber ?? 0)}
                  </button>
                ) : (
                  <span className="text-on-surface-variant">{a.ausloeser.type}</span>
                )}
              </section>
            )}
            <Liste titel={t('browser.entwickler.netz.antwortKoepfe')} eintraege={zeilen(antwortKoepfe(a))} />
            <Liste titel={t('browser.entwickler.netz.anfrageKoepfe')} eintraege={zeilen(anfrageKoepfe(a))} />
            {!a.roheAnfrageKoepfe && <p className="px-3 py-1.5 text-label-sm text-on-surface-variant">{t('browser.entwickler.netz.vorlaeufig')}</p>}
          </>
        )
      case 'nutzlast': {
        const geparst = nutzlast !== null ? json(nutzlast) : undefined
        const formular = nutzlast !== null && /x-www-form-urlencoded/i.test(kopf(anfrageKoepfe(a), 'content-type') ?? '')
        return (
          <>
            <Liste titel={t('browser.entwickler.netz.parameter')} eintraege={query} />
            {nutzlast !== null && (
              <section className="px-3 py-1.5">
                <h3 className="pb-1 text-label-sm font-semibold text-on-surface">{t('browser.entwickler.netz.koerper')}</h3>
                {geparst !== undefined ? (
                  <div className="font-mono text-label-sm">
                    <JsonWert wert={geparst} />
                  </div>
                ) : formular ? (
                  <Liste titel="" eintraege={[...new URLSearchParams(nutzlast).entries()]} />
                ) : (
                  <pre className="whitespace-pre-wrap break-all font-mono text-label-sm">{nutzlast}</pre>
                )}
              </section>
            )}
          </>
        )
      }
      case 'vorschau':
      case 'antwort': {
        if (koerper === null) return <p className="p-3 text-label-sm text-on-surface-variant">{t('browser.entwickler.laedt')}</p>
        if (koerper === 'fehlt') return <p className="p-3 text-label-sm text-on-surface-variant">{t('browser.entwickler.netz.keinKoerper')}</p>
        if (reiter === 'vorschau' && koerper.base64 && a.mime.startsWith('image/')) {
          return (
            <div className="flex justify-center p-3">
              <img src={`data:${a.mime};base64,${koerper.text}`} alt={a.url} className="max-h-80 max-w-full bg-[repeating-conic-gradient(#8884_0_25%,transparent_0_50%)] bg-[length:16px_16px]" />
            </div>
          )
        }
        const roh = text(koerper)
        const geparst = reiter === 'vorschau' ? json(roh) : undefined
        if (geparst !== undefined) {
          return (
            <div className="p-3 font-mono text-label-sm">
              <JsonWert wert={geparst} />
            </div>
          )
        }
        return (
          <div className="p-3">
            <pre className="whitespace-pre-wrap break-all font-mono text-label-sm">{roh.slice(0, TEXT_MAX)}</pre>
            {roh.length > TEXT_MAX && <p className="pt-2 text-label-sm text-on-surface-variant">{t('browser.entwickler.netz.gekuerzt', { zahl: roh.length })}</p>}
          </div>
        )
      }
      case 'cookies':
        return (
          <>
            <Liste titel={t('browser.entwickler.netz.cookiesAnfrage')} eintraege={cookiesDerAnfrage(a)} />
            <Liste titel={t('browser.entwickler.netz.cookiesAntwort')} eintraege={cookiesDerAntwort(a).map((c, i) => [String(i + 1), c])} />
            {cookiesDerAnfrage(a).length === 0 && cookiesDerAntwort(a).length === 0 && (
              <p className="p-3 text-label-sm text-on-surface-variant">{t('browser.entwickler.netz.keineCookies')}</p>
            )}
          </>
        )
      case 'zeit': {
        const p = phasen(a)
        const gesamt = a.ende !== null && a.start ? (a.ende - a.start) * 1000 : null
        return (
          <div className="p-3 text-label-sm">
            {p.length === 0 && <p className="text-on-surface-variant">{t('browser.entwickler.netz.keinZeitablauf')}</p>}
            {p.map((x) => (
              <div key={x.name} className="grid grid-cols-[7rem_1fr_4.5rem] items-center gap-2 py-0.5">
                <span>{t(`browser.entwickler.netz.phase.${x.name}`)}</span>
                <span className="h-2 rounded bg-primary/60" style={{ width: `${gesamt ? Math.max(2, (x.ms / gesamt) * 100) : 2}%` }} />
                <span className="text-right tabular-nums">{dauer(x.ms)}</span>
              </div>
            ))}
            {gesamt !== null && (
              <div className="mt-2 border-t border-outline-variant/50 pt-2 font-semibold">
                {t('browser.entwickler.netz.gesamt')}: {dauer(gesamt)} · {groesse(a.groesse)}
              </div>
            )}
          </div>
        )
      }
      case 'nachrichten':
        return (
          <ul className="font-mono text-label-sm">
            {a.rahmen.length === 0 && <li className="p-3 font-sans text-on-surface-variant">{t('browser.entwickler.netz.keineNachrichten')}</li>}
            {a.rahmen.map((r, i) => (
              <li key={i} className={`flex gap-2 border-b border-outline-variant/40 px-3 py-0.5 ${r.gesendet ? 'bg-primary/5' : ''}`}>
                {r.gesendet ? (
                  <ArrowUp className="mt-0.5 h-3 w-3 shrink-0 text-primary" aria-label={t('browser.entwickler.netz.gesendet')} />
                ) : (
                  <ArrowDown className="mt-0.5 h-3 w-3 shrink-0 text-status-success" aria-label={t('browser.entwickler.netz.empfangen')} />
                )}
                <span className="min-w-0 flex-1 break-all">{r.opcode === 1 ? r.daten : t('browser.entwickler.netz.binaer', { zahl: r.daten.length })}</span>
              </li>
            ))}
          </ul>
        )
    }
  })()

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-1 border-b border-outline-variant px-2 py-1">
        <Knopf text={t('browser.entwickler.netz.schliessen')} onClick={schliessen}>
          <X className="h-4 w-4" aria-hidden="true" />
        </Knopf>
        <span className="min-w-0 flex-1 truncate font-mono text-label-sm">{a.url}</span>
        <ActionMenu
          label={t('browser.entwickler.kopieren')}
          icon={<Copy className="h-4 w-4" aria-hidden="true" />}
          compact
          align="end"
          items={[
            { key: 'url', label: t('browser.entwickler.netz.urlKopieren'), onSelect: () => kopieren(a.url) },
            { key: 'curl', label: t('browser.entwickler.netz.curlKopieren'), onSelect: () => kopieren(alsCurl(a, nutzlast)) },
            { key: 'fetch', label: t('browser.entwickler.netz.fetchKopieren'), onSelect: () => kopieren(alsFetch(a, nutzlast)) },
            ...(koerper && koerper !== 'fehlt'
              ? [{ key: 'antwort', label: t('browser.entwickler.netz.antwortKopieren'), onSelect: () => kopieren(text(koerper)) }]
              : []),
          ]}
        />
      </div>
      <div className="shrink-0 border-b border-outline-variant px-2 py-1">
        <TabBar tabs={reiterListe} active={reiter} onChange={setReiter} embedded kompakt ariaLabel={t('browser.entwickler.netz.details')} />
      </div>
      <div className="min-h-0 flex-1 select-text overflow-y-auto">{inhalt}</div>
    </div>
  )
}
