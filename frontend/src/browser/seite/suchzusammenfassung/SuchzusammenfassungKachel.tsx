/**
 * Neutrale, temporäre KI-Suchzusammenfassung (AI Overview) für SearXNG im Secure Browser.
 *
 * Eigenschaften:
 * - 0 Bytes Speicherung in der Datenbank (reiner ephemerer Zustand im Tab).
 * - Streng isoliert: kein Gedächtnis, keine Ethik-Engine.
 * - Minimaler Prompt-Overhead, schneller SSE-Stream.
 * - Klickbare Quellenverweise [1], [2], die zu den Treffern darunter scrollen.
 * - Nachfragen im selben Reiter möglich (Verlauf verbleibt rein im Speicher).
 * - Unterstützt konfigurierbare Denkstufen, beschränkt durch die Benutzerrolle.
 */
import { useEffect, useRef, useState } from 'react'
import { Brain, Check, ChevronDown, ChevronUp, Copy, Loader2, Send, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { apiStream } from '@/api/client'
import { Button, Dropdown, Input, Kurzinfo, type DropdownOption } from '@/Singra/UI'

import type { Treffer } from '../MsmSuche'

interface SuchNachricht {
  role: 'user' | 'assistant'
  content: string
}

interface SuchzusammenfassungKachelProps {
  begriff: string
  treffer: Treffer[]
  onNeueTreffer?: (neue: Treffer[]) => void
}

interface SuchKonfig {
  aktiviert: boolean
  modell: string | null
  denkstufen: string[]
  max_denkstufe: string | null
}

export function SuchzusammenfassungKachel({
  begriff,
  treffer,
  onNeueTreffer,
}: SuchzusammenfassungKachelProps) {
  const { t } = useTranslation()
  const [eingeklappt, setEingeklappt] = useState(false)
  const [deaktiviert, setDeaktiviert] = useState(false)
  const [laedt, setLaedt] = useState(false)
  const [modellName, setModellName] = useState<string | null>(null)
  const [hauptText, setHauptText] = useState('')
  const [gedankenText, setGedankenText] = useState('')
  const [verlauf, setVerlauf] = useState<SuchNachricht[]>([])
  const [nachfrage, setNachfrage] = useState('')
  const [denkstufe, setDenkstufe] = useState<string>('fast')
  const [kopiert, setKopiert] = useState(false)
  const [konfig, setKonfig] = useState<SuchKonfig | null>(null)

  const abbruchRef = useRef<AbortController | null>(null)
  const begonneneSucheRef = useRef<string | null>(null)



  const scrollToTreffer = (index: number) => {
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

  const starteStream = async (frage: string, dialogVerlauf: SuchNachricht[] = [], gewaehlteStufe: string = denkstufe) => {
    if (abbruchRef.current) {
      abbruchRef.current.abort()
    }
    const controller = new AbortController()
    abbruchRef.current = controller

    setLaedt(true)
    setGedankenText('')

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

  const absendenNachfrage = () => {
    const text = nachfrage.trim()
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
                  className="mx-0.5 inline-flex h-4 min-w-[1.25rem] cursor-pointer items-center justify-center rounded-full bg-primary/15 px-1 text-label-sm font-semibold text-primary transition-colors hover:bg-primary hover:text-on-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
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
          p: ({ children }) => <p className="mb-2 leading-relaxed last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="mb-2 list-disc pl-5 last:mb-0 space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="mb-2 list-decimal pl-5 last:mb-0 space-y-1">{children}</ol>,
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

  return (
    <section
      aria-label={t('browser.msmSuche.zusammenfassungTitel')}
      className="mb-4 rounded-2xl border border-outline-variant/50 bg-surface-container-low/90 p-4 text-on-surface shadow-sm backdrop-blur-md"
    >
      {/* Kopfbereich */}
      <div className="flex items-center justify-between gap-2 border-b border-outline-variant/30 pb-2.5">
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/15 text-primary">
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
          </div>
          <span className="text-title-sm font-semibold tracking-tight text-on-surface">
            {t('browser.msmSuche.zusammenfassungTitel')}
          </span>
          {modellName && (
            <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-label-sm text-on-surface-variant font-mono">
              {modellName}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {hauptText && (
            <Kurzinfo text={t(kopiert ? 'browser.msmSuche.zusammenfassungKopiert' : 'browser.msmSuche.zusammenfassungKopieren')}>
              <Button
                variant="ghost"
                size="sm"
                onClick={kopieren}
                aria-label={t(kopiert ? 'browser.msmSuche.zusammenfassungKopiert' : 'browser.msmSuche.zusammenfassungKopieren')}
                className="h-7 w-7 p-0 text-on-surface-variant hover:text-on-surface"
              >
                {kopiert ? <Check className="h-4 w-4 text-primary" /> : <Copy className="h-4 w-4" />}
              </Button>
            </Kurzinfo>
          )}
          <Kurzinfo text={t(eingeklappt ? 'browser.msmSuche.zusammenfassungAusklappen' : 'browser.msmSuche.zusammenfassungEinklappen')}>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEingeklappt((v) => !v)}
              aria-label={t(eingeklappt ? 'browser.msmSuche.zusammenfassungAusklappen' : 'browser.msmSuche.zusammenfassungEinklappen')}
              className="h-7 w-7 p-0 text-on-surface-variant hover:text-on-surface"
            >
              {eingeklappt ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
            </Button>
          </Kurzinfo>
        </div>
      </div>

      {/* Inhalt */}
      {!eingeklappt && (
        <div className="pt-3">
          {/* Ladezustand vor erstem Token */}
          {laedt && !hauptText && (
            <div className="flex items-center gap-2 text-body-sm text-on-surface-variant">
              <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden="true" />
              <span>{t('browser.msmSuche.zusammenfassungLaedt')}</span>
            </div>
          )}

          {/* Optionaler Denkprozess */}
          {gedankenText && (
            <details className="mb-3 rounded-xl border border-outline-variant/30 bg-surface-container/60 p-2.5 text-label-sm">
              <summary className="flex cursor-pointer select-none items-center gap-1.5 font-medium text-on-surface-variant hover:text-on-surface">
                <Brain className="h-3.5 w-3.5 text-primary" />
                <span>{t('browser.msmSuche.zusammenfassungDenkprozess')}</span>
              </summary>
              <div className="mt-2 whitespace-pre-wrap font-mono text-label-sm text-on-surface-variant/90 border-t border-outline-variant/20 pt-2">
                {gedankenText}
              </div>
            </details>
          )}

          {/* Hauptantwort */}
          {hauptText && (
            <div className="text-body-sm text-on-surface">
              {renderMarkdown(hauptText)}
            </div>
          )}

          {/* Dialogverlauf (Folgefragen) */}
          {verlauf.length > 0 && (
            <div className="mt-4 space-y-3 border-t border-outline-variant/30 pt-3">
              {verlauf.map((nachricht, idx) => (
                <div
                  key={idx}
                  className={`flex flex-col gap-1 text-body-sm ${
                    nachricht.role === 'user'
                      ? 'rounded-xl bg-surface-container-high/60 p-2.5 text-on-surface'
                      : 'text-on-surface'
                  }`}
                >
                  {nachricht.role === 'user' ? (
                    <div className="font-medium text-primary">{nachricht.content}</div>
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

          {/* Nachfrage-Eingabezeile */}
          {hauptText && (
            <div className="mt-4 border-t border-outline-variant/20 pt-3">
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
                    className="h-9 text-body-sm"
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
                    buttonClassName="h-9 min-w-0 px-2.5 text-label-sm whitespace-nowrap"
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

              <div className="mt-2 flex items-center justify-between text-label-sm text-on-surface-variant/70">
                <span>{t('browser.msmSuche.zusammenfassungDisclaimer')}</span>
                <span>{t('browser.msmSuche.zusammenfassungEphemerHinweis')}</span>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
