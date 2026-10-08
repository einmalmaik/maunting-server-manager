/**
 * Anwendung: was die Seite bei sich speichert. Lokaler und Sitzungsspeicher,
 * Cookies, IndexedDB, Cache-Speicher und der belegte Platz, jeweils für die
 * Herkunft des Tabs. Ändern und Löschen gilt sofort in der Seite.
 */
import { useCallback, useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, confirm, Dropdown, Input } from '@/Singra/UI'

import { CacheSpeicher, IndexedDb } from './Datenbanken'
import { AnwendungLeiste } from './Leiste'
import { groesse } from './netzText'
import { rufen } from './protokoll'

type Bereich = 'local' | 'session' | 'cookies' | 'indexeddb' | 'cache' | 'speicher'
const BEREICHE: Bereich[] = ['local', 'session', 'cookies', 'indexeddb', 'cache', 'speicher']

export function herkunft(url: string): string | null {
  try {
    const u = new URL(url)
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.origin : null
  } catch {
    return null
  }
}

/** Eine Tabelle Schlüssel → Wert, Wert per Doppelklick änderbar. */
function Tabelle({ zeilen, aendern, loeschen }: { zeilen: [string, string][]; aendern: (k: string, w: string) => void; loeschen: (k: string) => void }) {
  const { t } = useTranslation()
  const [bearbeitet, setBearbeitet] = useState<string | null>(null)
  const [text, setText] = useState('')
  if (zeilen.length === 0) return <p className="p-3 text-label-sm text-on-surface-variant">{t('browser.entwickler.anwendung.leer')}</p>
  return (
    <div role="table" className="font-mono text-label-sm">
      {zeilen.map(([k, w]) => (
        <div role="row" key={k} className="group grid grid-cols-[minmax(6rem,35%)_1fr_1.5rem] items-start gap-2 border-b border-outline-variant/40 px-3 py-0.5">
          <span role="cell" className="break-all text-secondary">{k}</span>
          <span role="cell" className="min-w-0 break-all" onDoubleClick={() => {
            setBearbeitet(k)
            setText(w)
          }}>
            {bearbeitet === k ? (
              <Input
                autoFocus
                aria-label={t('browser.entwickler.anwendung.wertVon', { name: k })}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onBlur={() => {
                  setBearbeitet(null)
                  if (text !== w) aendern(k, text)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') e.currentTarget.blur()
                  if (e.key === 'Escape') setBearbeitet(null)
                }}
                className="h-6 font-mono text-label-sm"
              />
            ) : (
              w
            )}
          </span>
          <button type="button" aria-label={t('browser.entwickler.anwendung.loeschen', { name: k })} onClick={() => loeschen(k)} className="text-on-surface-variant opacity-60 hover:text-status-destructive group-hover:opacity-100">
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      ))}
    </div>
  )
}

function Neu({ hinzufuegen }: { hinzufuegen: (k: string, w: string) => void }) {
  const { t } = useTranslation()
  const [k, setK] = useState('')
  const [w, setW] = useState('')
  return (
    <form
      className="flex items-center gap-1.5 border-t border-outline-variant p-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (!k.trim()) return
        hinzufuegen(k, w)
        setK('')
        setW('')
      }}
    >
      <Input aria-label={t('browser.entwickler.anwendung.schluessel')} placeholder={t('browser.entwickler.anwendung.schluessel')} value={k} onChange={(e) => setK(e.target.value)} className="h-7 font-mono text-label-sm" />
      <Input aria-label={t('browser.entwickler.anwendung.wert')} placeholder={t('browser.entwickler.anwendung.wert')} value={w} onChange={(e) => setW(e.target.value)} className="h-7 font-mono text-label-sm" />
      <Button size="sm" type="submit" disabled={!k.trim()}>
        {t('browser.entwickler.anwendung.hinzufuegen')}
      </Button>
    </form>
  )
}

function Webspeicher({ tab, origin, lokal }: { tab: string; origin: string; lokal: boolean }) {
  const [zeilen, setZeilen] = useState<[string, string][]>([])
  const id = { securityOrigin: origin, isLocalStorage: lokal }
  const laden = useCallback(() => {
    void rufen(tab, 'DOMStorage.enable')
      .then(() => rufen<{ entries: [string, string][] }>(tab, 'DOMStorage.getDOMStorageItems', { storageId: { securityOrigin: origin, isLocalStorage: lokal } }))
      .then((r) => setZeilen((r.entries ?? []).sort(([a], [b]) => a.localeCompare(b))), () => setZeilen([]))
  }, [tab, origin, lokal])
  useEffect(laden, [laden])
  const nach = (p: Promise<unknown>) => void p.catch(() => null).then(laden)
  return (
    <>
      <AnwendungLeiste laden={laden} leeren={() => nach(rufen(tab, 'DOMStorage.clear', { storageId: id }))} />
      <div className="min-h-0 flex-1 select-text overflow-y-auto">
        <Tabelle
          zeilen={zeilen}
          aendern={(key, value) => nach(rufen(tab, 'DOMStorage.setDOMStorageItem', { storageId: id, key, value }))}
          loeschen={(key) => nach(rufen(tab, 'DOMStorage.removeDOMStorageItem', { storageId: id, key }))}
        />
      </div>
      <Neu hinzufuegen={(key, value) => nach(rufen(tab, 'DOMStorage.setDOMStorageItem', { storageId: id, key, value }))} />
    </>
  )
}

interface Cookie {
  name: string
  value: string
  domain: string
  path: string
  expires: number
  size: number
  httpOnly: boolean
  secure: boolean
  session: boolean
  sameSite?: string
}

function Cookies({ tab, url }: { tab: string; url: string }) {
  const { t } = useTranslation()
  const [cookies, setCookies] = useState<Cookie[]>([])
  const laden = useCallback(() => {
    void rufen<{ cookies: Cookie[] }>(tab, 'Network.getCookies', { urls: [url] }).then((r) => setCookies(r.cookies ?? []), () => setCookies([]))
  }, [tab, url])
  useEffect(laden, [laden])
  const weg = (c: Cookie) => rufen(tab, 'Network.deleteCookies', { name: c.name, domain: c.domain, path: c.path })
  const setzen = (c: Cookie, value: string) =>
    rufen(tab, 'Network.setCookie', {
      name: c.name,
      value,
      domain: c.domain,
      path: c.path,
      secure: c.secure,
      httpOnly: c.httpOnly,
      ...(c.sameSite ? { sameSite: c.sameSite } : {}),
      ...(c.session ? {} : { expires: c.expires }),
    })
  const nach = (p: Promise<unknown>) => void p.catch(() => null).then(laden)
  const finden = (name: string) => cookies.find((c) => c.name === name)!
  return (
    <>
      <AnwendungLeiste laden={laden} leeren={() => nach(Promise.all(cookies.map(weg)))} />
      <div className="min-h-0 flex-1 select-text overflow-y-auto">
        <Tabelle zeilen={cookies.map((c) => [c.name, c.value])} aendern={(n, w) => nach(setzen(finden(n), w))} loeschen={(n) => nach(weg(finden(n)))} />
        {cookies.length > 0 && (
          <dl className="border-t border-outline-variant/50 px-3 py-2 text-label-sm text-on-surface-variant">
            {cookies.map((c) => (
              <div key={`${c.name}-${c.domain}-${c.path}`} className="truncate">
                <span className="font-mono text-on-surface">{c.name}</span> · {c.domain}
                {c.path} · {c.session ? t('browser.entwickler.anwendung.sitzung') : new Date(c.expires * 1000).toLocaleString()} · {groesse(c.size)}
                {c.httpOnly && ' · HttpOnly'}
                {c.secure && ' · Secure'}
                {c.sameSite && ` · SameSite=${c.sameSite}`}
              </div>
            ))}
          </dl>
        )}
      </div>
    </>
  )
}

function Speicher({ tab, origin }: { tab: string; origin: string }) {
  const { t } = useTranslation()
  const [stand, setStand] = useState<{ usage: number; quota: number; usageBreakdown: { storageType: string; usage: number }[] } | null>(null)
  const laden = useCallback(() => {
    void rufen<NonNullable<typeof stand>>(tab, 'Storage.getUsageAndQuota', { origin }).then(setStand, () => setStand(null))
  }, [tab, origin])
  useEffect(laden, [laden])
  const loeschen = async () => {
    const ok = await confirm({ title: t('browser.entwickler.anwendung.allesLoeschen'), message: t('browser.entwickler.anwendung.allesLoeschenText', { origin }), danger: true })
    if (ok) void rufen(tab, 'Storage.clearDataForOrigin', { origin, storageTypes: 'all' }).catch(() => null).then(laden)
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-3 text-label-sm">
      {stand ? (
        <>
          <p className="text-on-surface">{t('browser.entwickler.anwendung.belegt', { belegt: groesse(stand.usage), frei: groesse(stand.quota) })}</p>
          <ul className="mt-2 text-on-surface-variant">
            {stand.usageBreakdown.filter((b) => b.usage > 0).map((b) => (
              <li key={b.storageType}>
                {t(`browser.entwickler.anwendung.art.${b.storageType}`, { defaultValue: b.storageType })}: {groesse(b.usage)}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="text-on-surface-variant">{t('browser.entwickler.nichtVerfuegbar')}</p>
      )}
      <Button className="mt-3" size="sm" variant="destructive" onClick={() => void loeschen()}>
        {t('browser.entwickler.anwendung.allesLoeschen')}
      </Button>
    </div>
  )
}

export function Anwendung({ tab, url }: { tab: string; url: string }) {
  const { t } = useTranslation()
  const [bereich, setBereich] = useState<Bereich>('local')
  const origin = herkunft(url)
  if (!origin) return <p className="p-3 text-label-sm text-on-surface-variant">{t('browser.entwickler.anwendung.ohneHerkunft')}</p>
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-outline-variant px-2 py-1">
        <div className="w-52">
          <Dropdown
            aria-label={t('browser.entwickler.anwendung.bereich')}
            value={bereich}
            onChange={(v) => setBereich(v as Bereich)}
            options={BEREICHE.map((b) => ({ value: b, label: t(`browser.entwickler.anwendung.bereiche.${b}`) }))}
            buttonClassName="h-7 text-label-sm"
          />
        </div>
        <span className="min-w-0 truncate font-mono text-label-sm text-on-surface-variant">{origin}</span>
      </div>
      {bereich === 'local' && <Webspeicher key={`l-${origin}`} tab={tab} origin={origin} lokal />}
      {bereich === 'session' && <Webspeicher key={`s-${origin}`} tab={tab} origin={origin} lokal={false} />}
      {bereich === 'cookies' && <Cookies key={url} tab={tab} url={url} />}
      {bereich === 'indexeddb' && <IndexedDb key={origin} tab={tab} origin={origin} />}
      {bereich === 'cache' && <CacheSpeicher key={origin} tab={tab} origin={origin} />}
      {bereich === 'speicher' && <Speicher key={origin} tab={tab} origin={origin} />}
    </div>
  )
}
