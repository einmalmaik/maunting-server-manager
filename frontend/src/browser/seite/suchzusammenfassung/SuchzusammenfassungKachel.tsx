/**
 * Neutrale, temporäre KI-Suchzusammenfassung (Singra AI Überblick) für Maunting Search.
 *
 * Eigenschaften:
 * - 0 Bytes Speicherung in der Datenbank (reiner ephemerer Zustand im Tab).
 * - Streng isoliert: kein Gedächtnis, keine Ethik-Engine.
 * - Minimaler Prompt-Overhead, schneller SSE-Stream.
 * - Klickbare Quellenverweise [1], [2], die die jeweilige Quellen-Karte hervorheben und zu den Treffern scrollen.
 * - Visuelles Top-Quellen-Dropdown mit 3 Vorschaukarten (dynamisches Favicon & Proxy-Thumbnail).
 * - Nachfragen im selben Reiter möglich (Verlauf verbleibt rein im Speicher).
 * - Unterstützt konfigurierbare Denkstufen, beschränkt durch die Benutzerrolle.
 */
import { useEffect, useRef, useState } from 'react'
import { Brain, Check, ChevronDown, ChevronUp, Copy, Layers, Loader2, MessageCircle, Send, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { apiStream } from '@/api/client'
import { Badge, Button, Dropdown, Input, Kurzinfo, type DropdownOption } from '@/Singra/UI'

import type { Treffer } from '../MsmSuche'
import { SeitenSymbol } from '../SeitenSymbol'
import { SuchBild } from '../SuchBild'

interface SuchNachricht {
  role: 'user' | 'assistant'
  content: string
}

interface SuchzusammenfassungKachelProps {
  begriff: string
  treffer: Treffer[]
  vorschlaege?: string[]
  onNeueTreffer?: (neue: Treffer[]) => void
  onOeffnen?: (url: string, neuerTab?: boolean) => void
}

interface SuchKonfig {
  aktiviert: boolean
  modell: string | null
  denkstufen: string[]
  max_denkstufe: string | null
}

function hostName(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, '')
  } catch {
    return url
  }
}

export function SuchzusammenfassungKachel({
  begriff,
  treffer,
  vorschlaege = [],
  onNeueTreffer,
  onOeffnen,
}: SuchzusammenfassungKachelProps) {
  const { t } = useTranslation()
  const [eingeklappt, setEingeklappt] = useState(false)
  // Quellen sind eingeklappt, bis jemand sie sehen will: erst die Zusammenfassung
  // lesen, dann die Quellen. Genau andersherum wäre es überladen.
  const [quellenOffen, setQuellenOffen] = useState(false)
  const [hervorgehoben, setHervorgehoben] = useState<number | null>(null)
  const [deaktiviert, setDeaktiviert] = useState(false)
  const [laedt, setLaedt] = useState(false)
  const [modellName, setModellName] = useState<string | null>(null)
  const [hauptText, setHauptText] = useState('')
  const [gedankenText, setGedankenText] = useState('')
  const [recherche, setRecherche] = useState<string[]>([])
  const [verlauf, setVerlauf] = useState<SuchNachricht[]>([])
  const [nachfrage, setNachfrage] = useState('')
  const [denkstufe, setDenkstufe] = useState<string>('fast')
  const [kopiert, setKopiert] = useState(false)
  const [konfig, setKonfig] = useState<SuchKonfig | null>(null)

  const abbruchRef = useRef<AbortController | null>(null)
  const begonneneSucheRef = useRef<string | null>(null)

  const topQuellen = treffer.slice(0, 3)

  const scrollToTreffer = (index: number) => {
    setHervorgehoben(index)
    setTimeout(() => setHervorgehoben((aktuell) => (aktuell === index ? null : aktuell)), 1800)

    const el = document.getElementById(`treffer-${index}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('ring-2', 'ring-primary')
      setTimeout(() => el.classList.remove('ring-2', 'ring-primary'), 2000)
    }
  }

  const kopieren = async () => {
    if (!hauptText) return
    try {
      await navigator.clipboard.writeText(hauptText)
      setKopiert(true)
      setTimeout(() => setKopiert(false), 2000)
    } catch {
      // Ignorieren falls Clipboard blockiert ist
    }
  }

  const imChatWeiterfuehren = async () => {
    if (!hauptText) return
    const text = `Frage: ${begriff}\n\nSingra-Überblick:\n${hauptText}\n\nTop-Quellen:\n${topQuellen.map((q, i) => `[${i + 1}] ${q.titel} – ${q.url}`).join('\n')}`
    try {
      await navigator.clipboard.writeText(text)
      setKopiert(true)
      setTimeout(() => setKopiert(false), 2000)
    } catch {
      // Ignorieren falls Clipboard blockiert ist
    }
    window.dispatchEvent(new CustomEvent('msm:singra-weiter', { detail: { frage: begriff, zusammenfassung: hauptText } }))
  }

  const starteStream = async (frage: string, dialogVerlauf: SuchNachricht[] = [], gewaehlteStufe: string = denkstufe) => {
    if (abbruchRef.current) {
      abbruchRef.current.abort()
    }
    const controller = new AbortController()
    abbruchRef.current = controller

    setLaedt(true)
    setGedankenText('')
    setRecherche([])

    const trefferPayload = treffer.slice(0, 15).map((item, idx) => ({
      index: idx + 1,
      titel: item.titel,
      url: item.url,
      inhalt: item.inhalt,
    }))

    const verlaufPayload = dialogVerlauf.map((e) => ({
      role: e.role,
      content: e.content,
    }))

    try {
      const res = await apiStream('/browser/suche/zusammenfassung', {
        method: 'POST',
        signal: controller.signal,
        body: JSON.stringify({
          q: frage,
          treffer: trefferPayload,
          verlauf: verlaufPayload,
          denkstufe: gewaehlteStufe === 'fast' ? undefined : gewaehlteStufe,
        }),
      })

      if (!res.ok) {
        setDeaktiviert(true)
        setLaedt(false)
        return
      }

      const reader = res.body?.getReader()
      if (!reader) {
        setLaedt(false)
        return
      }

      const decoder = new TextDecoder()
      let buffer = ''
      let akkumulierterText = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          const getrimmt = line.trim()
          if (!getrimmt.startsWith('data:')) continue

          const jsonStr = getrimmt.slice(5).trim()
          if (!jsonStr) continue

          try {
            const data = JSON.parse(jsonStr)
            if (data.type === 'disabled') {
              setDeaktiviert(true)
              setLaedt(false)
              return
            }
            if (data.type === 'start') {
              if (data.model) setModellName(data.model)
              if (Array.isArray(data.denkstufen)) {
                setKonfig({
                  aktiviert: true,
                  modell: data.model ?? null,
                  denkstufen: data.denkstufen,
                  max_denkstufe: data.max_denkstufe ?? null,
                })
              }
            } else if (data.type === 'thought' && typeof data.text === 'string') {
              setGedankenText((prev) => prev + data.text)
            } else if (data.type === 'recherche' && typeof data.anfrage === 'string') {
              setRecherche((prev) => (prev.includes(data.anfrage) ? prev : [...prev, data.anfrage]))
            } else if (data.type === 'neue_treffer' && Array.isArray(data.treffer)) {
              onNeueTreffer?.(data.treffer as Treffer[])
            } else if (data.type === 'content' && typeof data.text === 'string') {
              akkumulierterText += data.text
              if (dialogVerlauf.length === 0) {
                setHauptText(akkumulierterText)
              } else {
                setVerlauf((prev) => {
                  const kopie = [...prev]
                  if (kopie.length > 0 && kopie[kopie.length - 1].role === 'assistant') {
                    kopie[kopie.length - 1] = { role: 'assistant', content: akkumulierterText }
                    return kopie
                  }
                  return [...kopie, { role: 'assistant', content: akkumulierterText }]
                })
              }
            } else if (data.type === 'done' || data.type === 'error') {
              setLaedt(false)
            }
          } catch {
            // Ignoriere unvollständige JSON-Chunks
          }
        }
      }
    } catch (err: unknown) {
      if ((err as { name?: string })?.name !== 'AbortError') {
        setLaedt(false)
      }
    } finally {
      setLaedt(false)
    }
  }

  // Initialer Start beim Laden der Suchbegriffe
  useEffect(() => {
    if (!begriff || treffer.length === 0) return
    if (begonneneSucheRef.current === begriff) return

    begonneneSucheRef.current = begriff
    setDeaktiviert(false)
    setHauptText('')
    setGedankenText('')
    setVerlauf([])
    setNachfrage('')

    void starteStream(begriff, [])

    return () => {
      if (abbruchRef.current) {
        abbruchRef.current.abort()
      }
    }
  }, [begriff, treffer])

  if (deaktiviert || (!laedt && !hauptText)) {
    return null
  }

  const stelleFolgefrage = (frageText: string) => {
    const text = frageText.trim()
    if (!text || laedt) return

    const neuerVerlauf: SuchNachricht[] = [
      ...verlauf,
      { role: 'user', content: text },
      { role: 'assistant', content: '' },
    ]

    setVerlauf(neuerVerlauf)
    setNachfrage('')
    void starteStream(text, neuerVerlauf.slice(0, -1), denkstufe)
  }

  const absendenNachfrage = () => {
    stelleFolgefrage(nachfrage)
  }

  const renderMarkdown = (text: string) => {
    // Wandelt [1], [2] in Markdown-Links [#treffer-1], [#treffer-2] um
    const aufbereitet = text.replace(/\[(\d+)\](?!\()/g, '[$1](#treffer-$1)')

    return (
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith('#treffer-')) {
              const nr = parseInt(href.replace('#treffer-', ''), 10)
              return (
                <button
                  type="button"
                  onClick={(ev) => {
                    ev.preventDefault()
                    scrollToTreffer(nr)
                  }}
                  className="mx-0.5 inline-flex h-5 min-w-[1.25rem] cursor-pointer items-center justify-center rounded-md border border-primary/35 bg-primary/15 px-1.5 text-label-sm font-semibold text-primary transition-colors hover:bg-primary hover:text-on-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
                  aria-label={t('browser.msmSuche.quelle', { nr })}
                >
                  {children}
                </button>
              )
            }
            return (
              <a href={href} target="_blank" rel="noreferrer noopener" className="text-primary underline underline-offset-2 hover:text-primary/80">
                {children}
              </a>
            )
          },
          p: ({ children }) => <p className="mb-2.5 leading-relaxed last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="mb-2.5 list-disc pl-5 last:mb-0 space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="mb-2.5 list-decimal pl-5 last:mb-0 space-y-1">{children}</ol>,
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          strong: ({ children }) => <strong className="font-semibold text-on-surface">{children}</strong>,
        }}
      >
        {aufbereitet}
      </ReactMarkdown>
    )
  }

  const stufenOptionen = konfig?.denkstufen ?? []
  const stufenDropdownOptionen: DropdownOption[] = [
    { value: 'fast', label: t('browser.msmSuche.zusammenfassungDenkstufeFast') },
    ...stufenOptionen.map((st) => ({
      value: st,
      label: t(`browser.msmSuche.zusammenfassungDenkstufe${st.charAt(0).toUpperCase() + st.slice(1)}`, { defaultValue: st }),
    })),
  ]

  const schnellChips = vorschlaege.slice(0, 3)

  return (
    <section
      aria-label={t('browser.msmSuche.zusammenfassungTitel')}
      className="msm-card relative overflow-hidden bg-gradient-to-b from-surface-container/95 to-surface-container-low/98 p-4 sm:p-5 text-on-surface shadow-panel ring-1 ring-primary/25 transition-all"
    >
      {/* Kopfbereich mit Singra AI Branding & deutlich sichtbarer Action-Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-outline-variant/35 pb-3.5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-primary/35 bg-primary/15 text-primary shadow-sm">
            <Sparkles className="h-4 w-4" aria-hidden="true" />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-title-sm font-semibold tracking-tight text-on-surface">
              {t('browser.msmSuche.zusammenfassungTitel')}
            </h2>
            <Badge variant="info">
              {t('browser.msmSuche.zusammenfassungSynthese')}
            </Badge>
            {modellName && (
              <span className="rounded-full bg-surface-container-high px-2 py-0.5 font-mono text-label-sm text-on-surface-variant">
                {modellName}
              </span>
            )}
          </div>
        </div>

        {/* Gut sichtbare DNA-Action-Buttons oben rechts */}
        <div className="flex items-center gap-1.5">
          {hauptText && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={kopieren}
              aria-label={t(kopiert ? 'browser.msmSuche.zusammenfassungKopiert' : 'browser.msmSuche.zusammenfassungKopieren')}
              className="h-8 px-2.5 text-label-sm"
            >
              {kopiert ? <Check className="h-3.5 w-3.5 text-primary" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5 text-primary" aria-hidden="true" />}
              <span>{t(kopiert ? 'browser.msmSuche.zusammenfassungKopiert' : 'browser.msmSuche.zusammenfassungKopierenKurz')}</span>
            </Button>
          )}

          {hauptText && topQuellen.length > 0 && !eingeklappt && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setQuellenOffen((v) => !v)}
              aria-expanded={quellenOffen}
              aria-label={t('browser.msmSuche.quellenUmschalten', { count: topQuellen.length })}
              className="h-8 px-2.5 text-label-sm"
            >
              <Layers className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
              <span>{t('browser.msmSuche.quellenAnzahl', { count: topQuellen.length })}</span>
            </Button>
          )}

          <Kurzinfo text={t(eingeklappt ? 'browser.msmSuche.zusammenfassungAusklappen' : 'browser.msmSuche.zusammenfassungEinklappen')}>
            <Button
              type="button"
              variant="secondary"
              size="icon"
              onClick={() => setEingeklappt((v) => !v)}
              aria-label={t(eingeklappt ? 'browser.msmSuche.zusammenfassungAusklappen' : 'browser.msmSuche.zusammenfassungEinklappen')}
              className="h-8 w-8 text-on-surface-variant hover:text-on-surface"
            >
              {eingeklappt ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
            </Button>
          </Kurzinfo>
        </div>
      </div>

      {/* Einklappbarer Inhalt der Singra AI Box */}
      {!eingeklappt && (
        <div className="space-y-4 pt-3.5">
          {/* Ladezustand vor erstem Token */}
          {laedt && !hauptText && (
            <div className="flex items-center gap-2 text-body-sm text-on-surface-variant">
              <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden="true" />
              <span>{t('browser.msmSuche.zusammenfassungLaedt')}</span>
            </div>
          )}

          {/* Optionaler Denkprozess */}
          {gedankenText && (
            <details className="rounded-xl border border-outline-variant/30 bg-surface-container/60 p-2.5 text-label-sm">
              <summary className="flex cursor-pointer select-none items-center gap-1.5 font-medium text-on-surface-variant hover:text-on-surface">
                <Brain className="h-3.5 w-3.5 text-primary" />
                <span>{t('browser.msmSuche.zusammenfassungDenkprozess')}</span>
              </summary>
              <div className="mt-2 whitespace-pre-wrap border-t border-outline-variant/20 pt-2 font-mono text-label-sm text-on-surface-variant/90">
                {gedankenText}
              </div>
            </details>
          )}

          {/* Nachrecherche: welche Suchanfragen die KI von sich aus gestellt hat */}
          {recherche.length > 0 && (
            <div className="flex items-center gap-2 text-body-sm text-on-surface-variant">
              {laedt && <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" aria-hidden="true" />}
              <span>
                {t('browser.msmSuche.zusammenfassungRecherche')}: {recherche.join(' · ')}
              </span>
            </div>
          )}

          {/* Hauptantwort */}
          {hauptText && (
            <div className="text-body-sm text-on-surface">
              {renderMarkdown(hauptText)}
            </div>
          )}

          {/* Quellen-Block: eingeklappt nur die drei Seiten-Symbole, aufgeklappt
              die Vorschaukarten mit Foto der jeweiligen Seite. */}
          {hauptText && topQuellen.length > 0 && (
            <div className="border-t border-outline-variant/30 pt-3">
              <div className="mb-2.5 flex items-center justify-between gap-2">
                <button
                  type="button"
                  onClick={() => setQuellenOffen((v) => !v)}
                  aria-expanded={quellenOffen}
                  className="flex items-center gap-2 text-label-sm font-medium text-on-surface-variant transition-colors hover:text-on-surface"
                >
                  <span>{t('browser.msmSuche.topQuellenTitel')}</span>
                  <ChevronDown
                    className={`h-3.5 w-3.5 text-primary transition-transform ${quellenOffen ? 'rotate-0' : '-rotate-90'}`}
                    aria-hidden="true"
                  />
                </button>
              </div>

              {!quellenOffen ? (
                // Eingeklappt: genau die drei Symbole der Quellen, nichts sonst.
                <div className="flex items-center gap-3">
                  {topQuellen.map((src, idx) => {
                    const nr = idx + 1
                    const domain = hostName(src.url)
                    return (
                      <button
                        key={src.url}
                        id={`source-card-${nr}`}
                        type="button"
                        onClick={(ev) => (onOeffnen ? onOeffnen(src.url, ev.ctrlKey || ev.metaKey) : scrollToTreffer(nr))}
                        onMouseEnter={() => setHervorgehoben(nr)}
                        onMouseLeave={() => setHervorgehoben(null)}
                        className={`flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full border transition-all ${
                          hervorgehoben === nr
                            ? 'border-primary shadow-primary-glow'
                            : 'border-outline-variant/50 hover:border-primary/60'
                        }`}
                        aria-label={domain}
                      >
                        <SeitenSymbol url={src.url} host={domain} />
                      </button>
                    )
                  })}
                </div>
              ) : (
                <div className="flex gap-2.5 overflow-x-auto pb-1 sm:grid sm:grid-cols-3 sm:overflow-visible msm-ohne-rollbalken">
                  {topQuellen.map((src, idx) => {
                    const nr = idx + 1
                    const domain = hostName(src.url)
                    const aktiv = hervorgehoben === nr
                    return (
                      <button
                        key={src.url}
                        id={`source-card-${nr}`}
                        type="button"
                        onClick={(ev) => {
                          if (onOeffnen) {
                            onOeffnen(src.url, ev.ctrlKey || ev.metaKey)
                          } else {
                            scrollToTreffer(nr)
                          }
                        }}
                        onMouseEnter={() => setHervorgehoben(nr)}
                        onMouseLeave={() => setHervorgehoben(null)}
                        className={`group flex min-w-[220px] sm:min-w-0 flex-col justify-between overflow-hidden rounded-xl border bg-surface-container transition-all text-left ${
                          aktiv
                            ? 'border-primary ring-2 ring-primary/40 shadow-primary-glow'
                            : 'border-outline-variant/40 hover:border-primary/50 hover:bg-surface-container-high/60'
                        }`}
                      >
                        {/* Vorschau: das Bild der Seite über den eigenen Proxy.
                            Ohne Bild bleibt die DNA-Fläche, nie ein weißer Block. */}
                        <div className="relative flex h-24 w-full flex-col justify-between overflow-hidden border-b border-outline-variant/30 bg-gradient-to-br from-surface-container-high via-surface-container to-primary-container/30 p-2.5">
                          {src.bild && (
                            <SuchBild
                              url={src.bild}
                              alt=""
                              className="pointer-events-none absolute inset-0 h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                            />
                          )}
                          <div className="relative z-10 flex items-center justify-between gap-2">
                            <span className="flex h-6 w-6 shrink-0 items-center justify-center overflow-hidden rounded-full border border-white/20 bg-surface/85 shadow-sm backdrop-blur-sm">
                              <SeitenSymbol url={src.url} host={domain} />
                            </span>
                            <span className="truncate rounded bg-surface/80 px-1.5 py-0.5 text-label-sm font-semibold text-primary backdrop-blur-sm">
                              {nr}
                            </span>
                          </div>
                          <span className="relative z-10 truncate text-label-sm font-medium text-on-surface drop-shadow">{domain}</span>
                        </div>

                        {/* Card Body */}
                        <div className="flex flex-1 flex-col justify-between p-2.5">
                          <p className="line-clamp-2 text-label-sm font-medium leading-snug text-on-surface transition-colors group-hover:text-primary">
                            {src.titel || domain}
                          </p>
                        </div>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {/* Dialogverlauf (Folgefragen) */}
          {verlauf.length > 0 && (
            <div className="space-y-2.5 border-t border-outline-variant/30 pt-3">
              {verlauf.map((nachricht, idx) => (
                <div
                  key={idx}
                  className={`flex flex-col gap-1 rounded-xl p-3 text-body-sm ${
                    nachricht.role === 'user'
                      ? 'border border-outline-variant/35 bg-surface-container-high/50 text-on-surface'
                      : 'border border-outline-variant/25 bg-surface-container/60 text-on-surface'
                  }`}
                >
                  {nachricht.role === 'user' ? (
                    <div className="flex items-center justify-between gap-2 text-label-sm font-medium text-primary">
                      <span>↳ {nachricht.content}</span>
                    </div>
                  ) : nachricht.content ? (
                    renderMarkdown(nachricht.content)
                  ) : (
                    <div className="flex items-center gap-1.5 text-body-sm text-on-surface-variant">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                      <span>{t('browser.msmSuche.zusammenfassungLaedt')}</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Schnellwahl-Chips für Folgefragen */}
          {hauptText && schnellChips.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              {schnellChips.map((chip) => (
                <button
                  key={chip}
                  type="button"
                  disabled={laedt}
                  onClick={() => stelleFolgefrage(chip)}
                  className="flex items-center gap-1 rounded-lg border border-outline-variant/40 bg-surface-container px-2.5 py-1 text-label-sm text-on-surface-variant transition-colors hover:border-primary/40 hover:bg-surface-container-high hover:text-primary disabled:opacity-50"
                >
                  <span className="text-primary">↳</span>
                  <span className="truncate max-w-[260px]">{chip}</span>
                </button>
              ))}
            </div>
          )}

          {/* Nachfrage-Eingabezeile */}
          {hauptText && (
            <div className="border-t border-outline-variant/25 pt-3">
              <form
                onSubmit={(ev) => {
                  ev.preventDefault()
                  absendenNachfrage()
                }}
                className="flex items-center gap-2"
              >
                <div className="min-w-0 flex-1">
                  <Input
                    value={nachfrage}
                    onChange={(ev) => setNachfrage(ev.target.value)}
                    placeholder={t('browser.msmSuche.zusammenfassungNachfragePlaceholder')}
                    disabled={laedt}
                    className="h-9"
                    aria-label={t('browser.msmSuche.zusammenfassungNachfragePlaceholder')}
                  />
                </div>

                {/* Denkstufe-Auswahl, falls der Betreiber und die Rolle Stufen zulässt */}
                {stufenOptionen.length > 0 && (
                  <Dropdown
                    value={denkstufe}
                    onChange={(st) => setDenkstufe(st)}
                    options={stufenDropdownOptionen}
                    disabled={laedt}
                    className="w-auto shrink-0 max-w-[140px]"
                    buttonClassName="h-9 px-2.5 text-label-sm whitespace-nowrap"
                    aria-label={t('browser.msmSuche.zusammenfassungDenkstufe')}
                  />
                )}

                <Button
                  type="submit"
                  variant="primary"
                  size="sm"
                  disabled={!nachfrage.trim() || laedt}
                  className="h-9 px-3 shrink-0"
                  aria-label={t('browser.msmSuche.zusammenfassungSenden')}
                >
                  <Send className="h-3.5 w-3.5" />
                </Button>
              </form>

              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={imChatWeiterfuehren}
                  disabled={!hauptText}
                  className="h-8 px-2.5 text-label-sm"
                  aria-label={t('browser.msmSuche.imChatWeiterfuehren')}
                >
                  <MessageCircle className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
                  <span>{t(kopiert ? 'browser.msmSuche.imChatWeiterKopiert' : 'browser.msmSuche.imChatWeiterfuehren')}</span>
                </Button>
              </div>
              <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 text-label-sm text-on-surface-variant/80">
                <span>{t('browser.msmSuche.zusammenfassungDisclaimer')}</span>
                <span className="flex items-center gap-1.5">
                  <span className="h-1.5 w-1.5 rounded-full bg-primary" aria-hidden="true" />
                  <span>{t('browser.msmSuche.zusammenfassungEphemerHinweis')}</span>
                </span>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
