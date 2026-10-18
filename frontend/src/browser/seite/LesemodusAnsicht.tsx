/**
 * Puristische Leseansicht (Reader View):
 * Ablenkungsfreie, typografisch optimierte Darstellung von Webartikeln.
 * Besitzt data-msb-verdeckt="true", sodass WebView2-Fenster unter Windows
 * im Hintergrund zuverlässig maskiert bleiben.
 */
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  ArrowLeft,
  Bookmark,
  Copy,
  Sparkles,
  Type,
  Minus,
  Plus,
  Loader2,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Kurzinfo } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { saveNoteOffline } from '@/lib/offlineSync'
import { entwurfUebergeben } from '@/lib/aiEntwurf'

import { seitenHost } from '../services/geraetKonfig'
import { istGekoppelt, useSitzung } from '../services/sitzung'
import type { Tab } from '../services/tab'
import { useTabsStore } from '../services/tabsStore'
import {
  useLesemodusStore,
  type LinksModus,
  type Schriftgroesse,
} from '../services/lesemodusStore'
import { artikelExtrahieren, type ExtrahierterArtikel } from '../services/lesemodusService'

interface LesemodusAnsichtProps {
  tab: Tab
}

const SCHRIFTGROESSEN: Schriftgroesse[] = ['klein', 'mittel', 'gross', 'sehr_gross']
const LINKS_MODI: LinksModus[] = ['dezent', 'hervorgehoben', 'aus']

export function LesemodusAnsicht({ tab }: LesemodusAnsichtProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const stand = useSitzung((s) => s.stand)
  const gekoppelt = istGekoppelt(stand)

  const lesemodusUmschalten = useTabsStore((s) => s.lesemodusUmschalten)

  const {
    schriftgroesse,
    schriftart,
    breite,
    linksModus,
    setSchriftgroesse,
    setSchriftart,
    setBreite,
    setLinksModus,
    artikelCache,
    artikelCachen,
  } = useLesemodusStore()

  const [typografieOffen, setTypografieOffen] = useState(false)
  const [laedt, setLaedt] = useState(false)
  const [fehler, setFehler] = useState<string | null>(null)
  const typografieKnopfRef = useRef<HTMLButtonElement | null>(null)
  const typografieMenueRef = useRef<HTMLDivElement | null>(null)
  const hauptRef = useRef<HTMLElement | null>(null)
  const [menueLage, setMenueLage] = useState<{ top: number; right: number }>({ top: 56, right: 16 })

  useEffect(() => {
    if (!typografieOffen) return

    if (typografieKnopfRef.current) {
      const rect = typografieKnopfRef.current.getBoundingClientRect()
      setMenueLage({
        top: rect.bottom + 8,
        right: Math.max(16, window.innerWidth - rect.right),
      })
    }

    const handleClickOutside = (e: MouseEvent | TouchEvent) => {
      const target = e.target as Node | null
      if (
        typografieMenueRef.current &&
        !typografieMenueRef.current.contains(target) &&
        typografieKnopfRef.current &&
        !typografieKnopfRef.current.contains(target)
      ) {
        setTypografieOffen(false)
      }
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setTypografieOffen(false)
        typografieKnopfRef.current?.focus()
      }
    }

    document.addEventListener('mousedown', handleClickOutside, true)
    document.addEventListener('keydown', handleKeyDown, true)

    return () => {
      document.removeEventListener('mousedown', handleClickOutside, true)
      document.removeEventListener('keydown', handleKeyDown, true)
    }
  }, [typografieOffen])

  const cacheKey = `${tab.id}:${tab.url}`
  const cachedArtikel = artikelCache[cacheKey]
  const [artikel, setArtikel] = useState<ExtrahierterArtikel | null>(cachedArtikel ?? null)

  useEffect(() => {
    if (cachedArtikel) {
      setArtikel(cachedArtikel)
      setFehler(null)
      return
    }

    let aktiv = true
    setLaedt(true)
    setFehler(null)

    void artikelExtrahieren(tab.id, tab.url)
      .then((extrahiert) => {
        if (!aktiv) return
        if (!extrahiert || (!extrahiert.reintext && !extrahiert.inhaltHtml)) {
          setFehler(null)
          setArtikel(null)
          return
        }
        artikelCachen(cacheKey, extrahiert)
        setArtikel(extrahiert)
      })
      .catch((err) => {
        if (!aktiv) return
        console.warn('Fehler bei der Lese-Extraktion:', err)
        setFehler(err instanceof Error ? err.message : String(err || t('browser.lesemodus.nichtVerfuegbar')))
        setArtikel(null)
      })
      .finally(() => {
        if (aktiv) setLaedt(false)
      })

    return () => {
      aktiv = false
    }
  }, [tab.id, tab.url, cacheKey, cachedArtikel, artikelCachen, t])

  const beenden = () => {
    void lesemodusUmschalten(tab.id)
  }

  // Notiz-Export (nur gekoppelt)
  const inNotizenSpeichern = async () => {
    if (!gekoppelt) {
      toast.error(t('browser.lesemodus.nurGekoppelt'))
      return
    }
    if (!artikel) return

    try {
      await saveNoteOffline({
        title: artikel.titel || t('browser.lesemodus.titel'),
        content: `${artikel.reintext}\n\n---\nQuelle: ${artikel.url}`,
        note_type: 'personal',
      })
      toast.success(t('browser.lesemodus.notizGespeichert'))
    } catch (err) {
      console.error('Fehler beim Speichern in Notizen:', err)
      toast.error(t('browser.lesemodus.nichtVerfuegbar'))
    }
  }

  // An Singra Haupt-Chat übergeben
  const mitKiZusammenfassen = () => {
    if (!artikel) return
    const text = `Fasse bitte folgenden Artikel verständlich und prägnant zusammen:\n\n${artikel.titel}\n${artikel.url}\n\n${artikel.reintext}`
    entwurfUebergeben(text)
    navigate('/ai')
  }

  // Artikeltext kopieren
  const textKopieren = async () => {
    if (!artikel) return
    try {
      await navigator.clipboard.writeText(`${artikel.titel}\n\n${artikel.reintext}`)
      toast.success(t('browser.lesemodus.textKopiert'))
    } catch {
      toast.error(t('browser.lesemodus.nichtVerfuegbar'))
    }
  }

  const groesseAendern = (richtung: 1 | -1) => {
    const idx = SCHRIFTGROESSEN.indexOf(schriftgroesse)
    const neuIdx = Math.max(0, Math.min(SCHRIFTGROESSEN.length - 1, idx + richtung))
    setSchriftgroesse(SCHRIFTGROESSEN[neuIdx])
  }

  // Typografie-Klassen basierend auf Einstellungen
  const schriftartKlasse =
    schriftart === 'serif' ? 'font-serif' : schriftart === 'mono' ? 'font-mono' : 'font-sans'

  const schriftgroesseKlasse =
    schriftgroesse === 'klein'
      ? 'text-base leading-relaxed'
      : schriftgroesse === 'gross'
        ? 'text-xl leading-[1.8]'
        : schriftgroesse === 'sehr_gross'
          ? 'text-2xl leading-[1.85]'
          : 'text-lg leading-[1.75]'

  const breiteKlasse =
    breite === 'schmal'
      ? 'max-w-2xl'
      : breite === 'breit'
        ? 'max-w-5xl'
        : 'max-w-3xl'

  const linksKlasse =
    linksModus === 'aus'
      ? '[&_a]:text-inherit [&_a]:no-underline [&_a]:pointer-events-none [&_a]:cursor-text'
      : linksModus === 'hervorgehoben'
        ? '[&_a]:text-primary [&_a]:underline [&_a]:cursor-pointer'
        : '[&_a]:text-inherit [&_a]:underline [&_a]:decoration-outline-variant/70 [&_a]:underline-offset-[3px] [&_a:hover]:text-primary [&_a:hover]:decoration-primary [&_a]:cursor-pointer [&_a]:transition-colors'

  const onInhaltKlick = (e: React.MouseEvent) => {
    const anker = (e.target as HTMLElement | null)?.closest('a')
    if (!anker) return

    e.preventDefault()

    if (linksModus === 'aus') return

    const href = anker.getAttribute('href')
    if (!href) return

    // 1. Fragment- / Hash-Link
    if (href.startsWith('#')) {
      const zielId = decodeURIComponent(href.slice(1))
      if (!zielId || zielId.toLowerCase() === 'top') {
        hauptRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
        return
      }
      const zielEl =
        document.getElementById(zielId) ||
        (typeof CSS !== 'undefined' && CSS.escape ? document.querySelector(`[name="${CSS.escape(zielId)}"]`) : null)
      if (zielEl && typeof zielEl.scrollIntoView === 'function') {
        zielEl.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
      return
    }

    // 2. Vollständige oder relative Web-URL
    try {
      const zielUrlObj = new URL(href, tab.url)
      const zielUrl = zielUrlObj.href

      try {
        const basisUrlObj = new URL(tab.url)
        if (
          zielUrlObj.origin === basisUrlObj.origin &&
          zielUrlObj.pathname === basisUrlObj.pathname &&
          zielUrlObj.hash
        ) {
          const zielId = decodeURIComponent(zielUrlObj.hash.slice(1))
          if (!zielId || zielId.toLowerCase() === 'top') {
            hauptRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
            return
          }
          const zielEl =
            document.getElementById(zielId) ||
            (typeof CSS !== 'undefined' && CSS.escape ? document.querySelector(`[name="${CSS.escape(zielId)}"]`) : null)
          if (zielEl && typeof zielEl.scrollIntoView === 'function') {
            zielEl.scrollIntoView({ behavior: 'smooth', block: 'start' })
            return
          }
        }
      } catch {
        // Fallback: neuen Tab öffnen
      }

      if (zielUrl.startsWith('http://') || zielUrl.startsWith('https://')) {
        const imHintergrund = e.ctrlKey || e.metaKey
        useTabsStore.getState().neuerTab(zielUrl, { elternId: tab.id, hintergrund: imHintergrund })
      }
    } catch (err) {
      console.warn('Ungültiger Link im Lesemodus:', err)
    }
  }

  const onInhaltAuxKlick = (e: React.MouseEvent) => {
    if (e.button !== 1) return
    const anker = (e.target as HTMLElement | null)?.closest('a')
    if (!anker) return

    e.preventDefault()

    if (linksModus === 'aus') return

    const href = anker.getAttribute('href')
    if (!href || href.startsWith('#')) return

    try {
      const zielUrl = new URL(href, tab.url).href
      if (zielUrl.startsWith('http://') || zielUrl.startsWith('https://')) {
        useTabsStore.getState().neuerTab(zielUrl, { elternId: tab.id, hintergrund: true })
      }
    } catch {
      // Ignorieren
    }
  }

  const host = seitenHost(tab.url) ?? tab.url

  return (
    <div
      data-msb-verdeckt="true"
      className="relative flex h-full w-full flex-col overflow-hidden bg-surface text-on-surface"
    >
      {/* Kopfleiste des Lesemodus */}
      <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center justify-between border-b border-outline-variant/60 bg-surface/95 px-4 backdrop-blur-md">
        {/* Links: Beenden & Host */}
        <div className="flex items-center gap-3">
          <Kurzinfo
            text={`${t('browser.lesemodus.beenden')} ${t('browser.lesemodus.kuerzelHinweis')}`}
            lage="unten"
            seite="anfang"
          >
            <button
              type="button"
              onClick={beenden}
              aria-label={t('browser.lesemodus.beenden')}
              className="flex h-8 w-8 items-center justify-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container-highest hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            </button>
          </Kurzinfo>

          <span className="rounded-full bg-surface-container px-2.5 py-0.5 text-label-sm font-medium text-on-surface-variant">
            {host}
          </span>
        </div>

        {/* Mitte: Lesezeit & Wortanzahl */}
        {artikel && (
          <div className="hidden items-center gap-2 text-label-sm text-on-surface-variant md:flex">
            <span>{t('browser.lesemodus.lesezeit', { minuten: artikel.lesedauerMinuten })}</span>
            <span>·</span>
            <span>{t('browser.lesemodus.woerter', { anzahl: artikel.wortanzahl })}</span>
          </div>
        )}

        {/* Rechts: Aktionen & Typografie-Wähler */}
        <div className="flex items-center gap-1.5 sm:gap-2">
          {/* Typografie-Menü Umschalter */}
            <Kurzinfo
              text={t('browser.lesemodus.schriftgroesse')}
              lage="unten"
              seite="ende"
              className="max-w-[min(18rem,calc(100vw-2rem))]"
            >
              <button
                ref={typografieKnopfRef}
                type="button"
                onClick={() => setTypografieOffen((o) => !o)}
                aria-expanded={typografieOffen}
                aria-label={t('browser.lesemodus.schriftgroesse')}
                className={`flex h-8 w-8 items-center justify-center rounded-lg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                  typografieOffen
                    ? 'bg-primary/15 text-primary'
                    : 'text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface'
                }`}
              >
                <Type className="h-4 w-4" aria-hidden="true" />
              </button>
            </Kurzinfo>

            {typografieOffen &&
              createPortal(
                <div
                  ref={typografieMenueRef}
                  role="dialog"
                  aria-label={t('browser.lesemodus.schriftgroesse')}
                  data-msb-verdeckt="true"
                  style={{
                    position: 'fixed',
                    top: menueLage.top,
                    right: menueLage.right,
                    zIndex: 70,
                  }}
                  className="w-72 rounded-xl border border-outline-variant bg-surface-container-high p-3.5 shadow-xl animate-fade-in"
                >
                  {/* Schriftgröße */}
                  <div className="mb-3.5">
                    <div className="mb-1.5 text-label-sm text-on-surface-variant">
                      {t('browser.lesemodus.schriftgroesse')}
                    </div>
                    <div className="flex items-center justify-between rounded-lg bg-surface-container p-1">
                      <button
                        type="button"
                        onClick={() => groesseAendern(-1)}
                        disabled={schriftgroesse === 'klein'}
                        className="flex h-7 w-8 items-center justify-center rounded text-on-surface disabled:opacity-40 hover:bg-surface-container-highest"
                        aria-label="Schrift verkleinern"
                      >
                        <Minus className="h-3.5 w-3.5" aria-hidden="true" />
                      </button>
                      <span className="text-body-sm font-medium capitalize">
                        {t(`browser.lesemodus.groesse${schriftgroesse === 'sehr_gross' ? 'SehrGross' : schriftgroesse.charAt(0).toUpperCase() + schriftgroesse.slice(1)}`)}
                      </span>
                      <button
                        type="button"
                        onClick={() => groesseAendern(1)}
                        disabled={schriftgroesse === 'sehr_gross'}
                        className="flex h-7 w-8 items-center justify-center rounded text-on-surface disabled:opacity-40 hover:bg-surface-container-highest"
                        aria-label="Schrift vergrößern"
                      >
                        <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                      </button>
                    </div>
                  </div>

                  {/* Schriftart */}
                  <div className="mb-3.5">
                    <div className="mb-1.5 text-label-sm text-on-surface-variant">
                      {t('browser.lesemodus.schriftart')}
                    </div>
                    <div className="grid grid-cols-3 gap-1 rounded-lg bg-surface-container p-1">
                      {(['sans', 'serif', 'mono'] as const).map((art) => (
                        <button
                          key={art}
                          type="button"
                          onClick={() => setSchriftart(art)}
                          className={`rounded py-1 text-center text-label-sm transition-colors ${
                            schriftart === art
                              ? 'bg-primary text-on-primary font-medium'
                              : 'text-on-surface hover:bg-surface-container-highest'
                          }`}
                        >
                          {t(`browser.lesemodus.art${art.charAt(0).toUpperCase() + art.slice(1)}`)}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Zeilenbreite */}
                  <div className="mb-3.5">
                    <div className="mb-1.5 text-label-sm text-on-surface-variant">
                      {t('browser.lesemodus.breite')}
                    </div>
                    <div className="grid grid-cols-3 gap-1 rounded-lg bg-surface-container p-1">
                      {(['schmal', 'standard', 'breit'] as const).map((b) => (
                        <button
                          key={b}
                          type="button"
                          onClick={() => setBreite(b)}
                          className={`rounded py-1 text-center text-label-sm transition-colors ${
                            breite === b
                              ? 'bg-primary text-on-primary font-medium'
                              : 'text-on-surface hover:bg-surface-container-highest'
                          }`}
                        >
                          {t(`browser.lesemodus.breite${b.charAt(0).toUpperCase() + b.slice(1)}`)}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Links im Text */}
                  <div>
                    <div className="mb-1.5 text-label-sm text-on-surface-variant">
                      {t('browser.lesemodus.links')}
                    </div>
                    <div className="grid grid-cols-3 gap-1 rounded-lg bg-surface-container p-1">
                      {LINKS_MODI.map((modus) => (
                        <button
                          key={modus}
                          type="button"
                          onClick={() => setLinksModus(modus)}
                          className={`rounded py-1 text-center text-label-sm transition-colors ${
                            linksModus === modus
                              ? 'bg-primary text-on-primary font-medium'
                              : 'text-on-surface hover:bg-surface-container-highest'
                          }`}
                        >
                          {modus === 'dezent'
                            ? t('browser.lesemodus.linksDezent')
                            : modus === 'hervorgehoben'
                              ? t('browser.lesemodus.linksHervorgehoben')
                              : t('browser.lesemodus.linksAus')}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>,
                document.body,
              )}

          {/* Mit KI zusammenfassen */}
          <Kurzinfo
            text={t('browser.lesemodus.zusammenfassen')}
            lage="unten"
            seite="ende"
            className="max-w-[min(18rem,calc(100vw-2rem))]"
          >
            <button
              type="button"
              onClick={mitKiZusammenfassen}
              aria-label={t('browser.lesemodus.zusammenfassen')}
              className="flex h-8 w-8 items-center justify-center rounded-lg text-primary transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              <Sparkles className="h-4 w-4" aria-hidden="true" />
            </button>
          </Kurzinfo>

          {/* In Notizen speichern */}
          <Kurzinfo
            text={gekoppelt ? t('browser.lesemodus.inNotizen') : t('browser.lesemodus.nurGekoppelt')}
            lage="unten"
            seite="ende"
            className="max-w-[min(18rem,calc(100vw-2rem))]"
          >
            <button
              type="button"
              onClick={() => void inNotizenSpeichern()}
              aria-label={t('browser.lesemodus.inNotizen')}
              disabled={!gekoppelt}
              className={`flex h-8 w-8 items-center justify-center rounded-lg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                gekoppelt
                  ? 'text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface'
                  : 'cursor-not-allowed opacity-40 text-on-surface-variant'
              }`}
            >
              <Bookmark className="h-4 w-4" aria-hidden="true" />
            </button>
          </Kurzinfo>

          {/* Text kopieren */}
          <Kurzinfo
            text={t('browser.lesemodus.kopieren')}
            lage="unten"
            seite="ende"
            className="max-w-[min(18rem,calc(100vw-2rem))]"
          >
            <button
              type="button"
              onClick={() => void textKopieren()}
              aria-label={t('browser.lesemodus.kopieren')}
              className="flex h-8 w-8 items-center justify-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container-highest hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            >
              <Copy className="h-4 w-4" aria-hidden="true" />
            </button>
          </Kurzinfo>
        </div>
      </header>

      {/* Haupt-Inhaltsbereich mit Lese-Spalte */}
      <main ref={hauptRef} className="flex-1 overflow-y-auto px-4 py-8 sm:px-8">
        <div className={`mx-auto ${breiteKlasse}`}>
          {laedt ? (
            <div className="flex h-64 flex-col items-center justify-center gap-3 text-on-surface-variant">
              <Loader2 className="h-6 w-6 animate-spin text-primary" aria-hidden="true" />
              <p className="text-body-md">{t('browser.lesemodus.laedt')}</p>
            </div>
          ) : fehler ? (
            <div role="alert" className="rounded-xl border border-status-destructive/30 bg-status-destructive/10 p-6 text-center">
              <p className="text-body-md font-medium text-status-destructive">
                {fehler}
              </p>
              <button
                type="button"
                onClick={beenden}
                className="mt-4 rounded-lg bg-primary px-4 py-2 text-label-md font-medium text-on-primary hover:bg-primary/90"
              >
                {t('browser.lesemodus.beenden')}
              </button>
            </div>
          ) : !artikel || (!artikel.reintext && !artikel.inhaltHtml) ? (
            <div className="rounded-xl border border-outline-variant bg-surface-container p-6 text-center">
              <p className="text-body-md text-on-surface-variant">
                {t('browser.lesemodus.nichtVerfuegbar')}
              </p>
              <button
                type="button"
                onClick={beenden}
                className="mt-4 rounded-lg bg-primary px-4 py-2 text-label-md font-medium text-on-primary hover:bg-primary/90"
              >
                {t('browser.lesemodus.beenden')}
              </button>
            </div>
          ) : (
            <article className={`space-y-6 ${schriftartKlasse}`}>
              {/* Artikeltitel mit ausgewogenem Umbruch */}
              {artikel.titel && (
                <h1
                  style={{ textWrap: 'balance', overflowWrap: 'break-word' }}
                  className="text-3xl font-bold tracking-tight text-on-surface sm:text-4xl"
                >
                  {artikel.titel}
                </h1>
              )}

              {/* Byline / Metadaten */}
              {(artikel.autor || artikel.datum) && (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-body-sm text-on-surface-variant border-b border-outline-variant/40 pb-4">
                  {artikel.autor && (
                    <span className="font-medium text-on-surface">{artikel.autor}</span>
                  )}
                  {artikel.autor && artikel.datum && <span>·</span>}
                  {artikel.datum && <span>{artikel.datum}</span>}
                </div>
              )}

              {/* Bereinigter HTML-Inhalt */}
              <div
                style={{ overflowWrap: 'break-word' }}
                onClick={onInhaltKlick}
                onAuxClick={onInhaltAuxKlick}
                className={`prose max-w-none text-on-surface ${schriftgroesseKlasse} ${linksKlasse} [&_p]:[text-wrap:pretty] [&_p]:mb-4 [&_h2]:text-2xl [&_h2]:font-bold [&_h2]:mt-8 [&_h2]:mb-3 [&_h2]:[text-wrap:balance] [&_h3]:text-xl [&_h3]:font-semibold [&_h3]:mt-6 [&_h3]:mb-2 [&_h3]:[text-wrap:balance] [&_blockquote]:border-l-4 [&_blockquote]:border-primary/40 [&_blockquote]:pl-4 [&_blockquote]:italic [&_blockquote]:my-4 [&_pre]:p-4 [&_pre]:rounded-lg [&_pre]:bg-surface-container-high [&_pre]:overflow-x-auto [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:bg-surface-container-high [&_code]:text-sm [&_ul]:list-disc [&_ul]:pl-6 [&_ul]:mb-4 [&_ol]:list-decimal [&_ol]:pl-6 [&_ol]:mb-4 [&_li]:mb-1 [&_img]:rounded-lg [&_img]:max-w-full [&_img]:mx-auto [&_img]:my-4`}
                dangerouslySetInnerHTML={{ __html: artikel.inhaltHtml }}
              />
            </article>
          )}
        </div>
      </main>
    </div>
  )
}
