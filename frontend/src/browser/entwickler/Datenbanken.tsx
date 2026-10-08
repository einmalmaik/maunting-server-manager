/** IndexedDB und Cache-Speicher der Anwendung: ansehen, blättern, löschen. */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, confirm, Dropdown } from '@/Singra/UI'

import { AnwendungLeiste } from './Leiste'
import { Wert } from './ObjektBaum'
import { rufen, type Objekt } from './protokoll'

const SEITE = 50

export function IndexedDb({ tab, origin }: { tab: string; origin: string }) {
  const { t } = useTranslation()
  const [namen, setNamen] = useState<string[]>([])
  const [db, setDb] = useState<string | null>(null)
  const [speicher, setSpeicher] = useState<{ name: string; keyPath: { string?: string } }[]>([])
  const [fach, setFach] = useState<string | null>(null)
  const [seite, setSeite] = useState(0)
  const [daten, setDaten] = useState<{ eintraege: { key: Objekt; value: Objekt }[]; mehr: boolean; fehler?: string } | null>(null)

  const namenLaden = useCallback(() => {
    void rufen(tab, 'IndexedDB.enable')
      .then(() => rufen<{ databaseNames: string[] }>(tab, 'IndexedDB.requestDatabaseNames', { securityOrigin: origin }))
      .then(
        (r) => {
          const liste = r.databaseNames ?? []
          setNamen(liste)
          // Die erste Datenbank steht gleich offen, eine verschwundene fällt weg.
          setDb((alt) => (alt && liste.includes(alt) ? alt : (liste[0] ?? null)))
        },
        () => setNamen([]),
      )
  }, [tab, origin])
  useEffect(namenLaden, [namenLaden])

  useEffect(() => {
    setFach(null)
    setSpeicher([])
    if (!db) return
    void rufen<{ databaseWithObjectStores: { objectStores: { name: string; keyPath: { string?: string } }[] } }>(tab, 'IndexedDB.requestDatabase', { securityOrigin: origin, databaseName: db }).then(
      (r) => {
        const liste = r.databaseWithObjectStores?.objectStores ?? []
        setSpeicher(liste)
        setFach(liste[0]?.name ?? null)
      },
      () => setSpeicher([]),
    )
  }, [tab, origin, db])

  const datenLaden = useCallback(() => {
    if (!db || !fach) return setDaten(null)
    void rufen<{ objectStoreDataEntries: { key: Objekt; value: Objekt }[]; hasMore: boolean }>(tab, 'IndexedDB.requestData', {
      securityOrigin: origin,
      databaseName: db,
      objectStoreName: fach,
      // Ohne `indexName` liest Chromium das Fach selbst; `''` suchte einen Index dieses Namens.
      skipCount: seite * SEITE,
      pageSize: SEITE,
    }).then(
      (r) => setDaten({ eintraege: r.objectStoreDataEntries ?? [], mehr: !!r.hasMore }),
      (e: unknown) => setDaten({ eintraege: [], mehr: false, fehler: String(e) }),
    )
  }, [tab, origin, db, fach, seite])
  useEffect(datenLaden, [datenLaden])

  const leeren = async () => {
    if (!db) return
    if (fach) {
      await rufen(tab, 'IndexedDB.clearObjectStore', { securityOrigin: origin, databaseName: db, objectStoreName: fach }).catch(() => null)
      return datenLaden()
    }
    const ok = await confirm({ message: t('browser.entwickler.anwendung.dbLoeschen', { name: db }), danger: true })
    if (!ok) return
    await rufen(tab, 'IndexedDB.deleteDatabase', { securityOrigin: origin, databaseName: db }).catch(() => null)
    setDb(null)
    namenLaden()
  }

  return (
    <>
      <AnwendungLeiste laden={() => {
          namenLaden()
          datenLaden()
        }} leeren={db ? () => void leeren() : undefined} />
      <div className="flex shrink-0 flex-wrap gap-2 border-b border-outline-variant/50 px-2 py-1">
        <div className="w-44">
          <Dropdown aria-label={t('browser.entwickler.anwendung.datenbank')} placeholder={namen.length ? t('browser.entwickler.anwendung.datenbank') : t('browser.entwickler.anwendung.keineDb')} value={db} onChange={setDb} options={namen.map((n) => ({ value: n, label: n }))} buttonClassName="h-7 text-label-sm" disabled={!namen.length} />
        </div>
        {db && (
          <div className="w-44">
            <Dropdown
              aria-label={t('browser.entwickler.anwendung.fach')}
              placeholder={t('browser.entwickler.anwendung.fach')}
              value={fach}
              onChange={(f) => {
                setFach(f)
                setSeite(0)
              }}
              options={speicher.map((s) => ({ value: s.name, label: s.name, hint: s.keyPath?.string }))}
              buttonClassName="h-7 text-label-sm"
            />
          </div>
        )}
      </div>
      <div className="min-h-0 flex-1 select-text overflow-y-auto font-mono text-label-sm">
        {daten?.eintraege.map((e, i) => (
          <div key={i} className="grid grid-cols-[minmax(5rem,30%)_1fr] gap-2 border-b border-outline-variant/40 px-3 py-0.5">
            <Wert tab={tab} o={e.key} />
            <Wert tab={tab} o={e.value} />
          </div>
        ))}
        {daten && daten.eintraege.length === 0 && (
          <p className="p-3 font-sans text-on-surface-variant">{daten.fehler ? t('browser.entwickler.anwendung.lesefehler', { grund: daten.fehler }) : t('browser.entwickler.anwendung.leer')}</p>
        )}
      </div>
      {daten && (seite > 0 || daten.mehr) && (
        <div className="flex shrink-0 items-center justify-end gap-2 border-t border-outline-variant px-2 py-1">
          <Button size="sm" variant="ghost" disabled={seite === 0} onClick={() => setSeite(seite - 1)}>
            {t('browser.entwickler.zurueck')}
          </Button>
          <span className="text-label-sm tabular-nums text-on-surface-variant">{seite + 1}</span>
          <Button size="sm" variant="ghost" disabled={!daten.mehr} onClick={() => setSeite(seite + 1)}>
            {t('browser.entwickler.weiter')}
          </Button>
        </div>
      )}
    </>
  )
}

interface CacheEintrag {
  requestURL: string
  requestMethod: string
  responseStatus: number
  responseType: string
  responseTime: number
}

export function CacheSpeicher({ tab, origin }: { tab: string; origin: string }) {
  const { t } = useTranslation()
  const [caches, setCaches] = useState<{ cacheId: string; cacheName: string }[]>([])
  const [cache, setCache] = useState<string | null>(null)
  const [eintraege, setEintraege] = useState<CacheEintrag[]>([])

  const laden = useCallback(() => {
    void rufen<{ caches: { cacheId: string; cacheName: string }[] }>(tab, 'CacheStorage.requestCacheNames', { securityOrigin: origin }).then((r) => setCaches(r.caches ?? []), () => setCaches([]))
  }, [tab, origin])
  useEffect(laden, [laden])
  const eintraegeLaden = useCallback(() => {
    if (!cache) return setEintraege([])
    void rufen<{ cacheDataEntries: CacheEintrag[] }>(tab, 'CacheStorage.requestEntries', { cacheId: cache, skipCount: 0, pageSize: 200 }).then(
      (r) => setEintraege(r.cacheDataEntries ?? []),
      () => setEintraege([]),
    )
  }, [tab, cache])
  useEffect(eintraegeLaden, [eintraegeLaden])

  const loeschen = async () => {
    if (!cache) return
    const name = caches.find((c) => c.cacheId === cache)?.cacheName ?? ''
    if (!(await confirm({ message: t('browser.entwickler.anwendung.cacheLoeschen', { name }), danger: true }))) return
    await rufen(tab, 'CacheStorage.deleteCache', { cacheId: cache }).catch(() => null)
    setCache(null)
    laden()
  }

  return (
    <>
      <AnwendungLeiste laden={() => {
          laden()
          eintraegeLaden()
        }} leeren={cache ? () => void loeschen() : undefined} />
      <div className="shrink-0 border-b border-outline-variant/50 px-2 py-1">
        <div className="w-56">
          <Dropdown aria-label={t('browser.entwickler.anwendung.cache')} placeholder={caches.length ? t('browser.entwickler.anwendung.cache') : t('browser.entwickler.anwendung.keinCache')} value={cache} onChange={setCache} options={caches.map((c) => ({ value: c.cacheId, label: c.cacheName }))} buttonClassName="h-7 text-label-sm" disabled={!caches.length} />
        </div>
      </div>
      <ul className="min-h-0 flex-1 select-text overflow-y-auto text-label-sm">
        {eintraege.map((e) => (
          <li key={e.requestURL} className="flex gap-2 border-b border-outline-variant/40 px-3 py-0.5">
            <span className="shrink-0 text-on-surface-variant">{e.requestMethod}</span>
            <span className="min-w-0 flex-1 truncate font-mono">{e.requestURL}</span>
            <span className="shrink-0 tabular-nums">{e.responseStatus}</span>
          </li>
        ))}
        {cache && eintraege.length === 0 && <li className="p-3 text-on-surface-variant">{t('browser.entwickler.anwendung.leer')}</li>}
      </ul>
    </>
  )
}
