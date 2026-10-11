/**
 * Neutrale, temporäre KI-Suchzusammenfassung (AI Overview) für SearXNG im Secure Browser.
 *
 * Eigenschaften:
 * - 0 Bytes Speicherung in der Datenbank (reiner ephemerer Zustand im Tab).
 * - Streng isoliert: kein Gedächtnis, keine Ethik-Engine.
 * - Minimaler Prompt-Overhead, schneller SSE-Stream.
 * - Klickbare Quellenverweise [1], [2], die zu den Treffern darunter scrollen.
 * - Nachfragen im selben Reiter möglich (Verlauf verbleibt rein im Speicher).
 */
import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronUp, Loader2, Send, Sparkles, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { apiStream } from '@/api/client'
import { Button, Input } from '@/Singra/UI'

import type { Treffer } from '../MsmSuche'

interface SuchNachricht {
  role: 'user' | 'assistant'
  content: string
}

interface SuchzusammenfassungKachelProps {
  begriff: string
  treffer: Treffer[]
}

export function SuchzusammenfassungKachel({ begriff, treffer }: SuchzusammenfassungKachelProps) {
  const { t } = useTranslation()
  const [geschlossen, setGeschlossen] = useState(false)
  const [eingeklappt, setEingeklappt] = useState(false)
  const [deaktiviert, setDeaktiviert] = useState(false)
  const [laedt, setLaedt] = useState(false)
  const [modellName, setModellName] = useState<string | null>(null)
  const [hauptText, setHauptText] = useState('')
  const [verlauf, setVerlauf] = useState<SuchNachricht[]>([])
  const [nachfrage, setNachfrage] = useState('')

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

  const starteStream = async (frage: string, dialogVerlauf: SuchNachricht[] = []) => {
    if (abbruchRef.current) {
      abbruchRef.current.abort()
    }
    const controller = new AbortController()
    abbruchRef.current = controller

    setLaedt(true)

    const trefferPayload = treffer.slice(0, 10).map((item, idx) => ({
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
            } else if (data.type === 'content' && typeof data.text === 'string') {
              akkumulierterText += data.text
              if (dialogVerlauf.length === 0) {
                setHauptText(akkumulierterText)
              } else {
                // Bei Nachfragen den letzten Assistenten-Eintrag aktualisieren
                setVerlauf((prev) => {
                  const kopie = [...prev]
                  if (kopie.length > 0 && kopie[kopie.length - 1].role === 'assistant') {
                    kopie[kopie.length - 1] = { role: 'assistant', content: akkumulierterText }
                    return kopie
                  }
                  return [...kopie, { role: 'assistant', content: akkumulierterText }]
                })
              }
            } else if (data.type === 'done') {
              setLaedt(false)
            } else if (data.type === 'error') {
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
    setGeschlossen(false)
    setDeaktiviert(false)
    setHauptText('')
    setVerlauf([])
    setNachfrage('')

    void starteStream(begriff, [])

    return () => {
      if (abbruchRef.current) {
        abbruchRef.current.abort()
      }
    }
  }, [begriff, treffer])

  if (geschlossen || deaktiviert || (!laedt && !hauptText)) {
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
    void starteStream(text, neuerVerlauf.slice(0, -1))
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

  return (
    <section
      aria-label={t('browser.msmSuche.zusammenfassungTitel')}
      className="mb-3 rounded-2xl border border-outline-variant/50 bg-surface-container-low/80 p-4 text-on-surface shadow-sm backdrop-blur-md"
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
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setEingeklappt((v) => !v)}
            aria-label={t(eingeklappt ? 'browser.msmSuche.zusammenfassungAusklappen' : 'browser.msmSuche.zusammenfassungEinklappen')}
            className="h-7 w-7 p-0 text-on-surface-variant hover:text-on-surface"
          >
            {eingeklappt ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              if (abbruchRef.current) abbruchRef.current.abort()
              setGeschlossen(true)
            }}
            aria-label={t('browser.msmSuche.zusammenfassungSchliessen')}
            className="h-7 w-7 p-0 text-on-surface-variant hover:text-on-surface"
          >
            <X className="h-4 w-4" />
          </Button>
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
            <div className="mt-4 pt-2">
              <form
                onSubmit={(ev) => {
                  ev.preventDefault()
                  absendenNachfrage()
                }}
                className="flex items-center gap-2"
              >
                <Input
                  value={nachfrage}
                  onChange={(ev) => setNachfrage(ev.target.value)}
                  placeholder={t('browser.msmSuche.zusammenfassungNachfragePlaceholder')}
                  disabled={laedt}
                  className="h-9 text-body-sm"
                  aria-label={t('browser.msmSuche.zusammenfassungNachfragePlaceholder')}
                />
                <Button
                  type="submit"
                  variant="primary"
                  size="sm"
                  disabled={!nachfrage.trim() || laedt}
                  className="h-9 px-3"
                  aria-label={t('browser.msmSuche.zusammenfassungSenden')}
                >
                  <Send className="h-3.5 w-3.5" />
                </Button>
              </form>
              <p className="mt-1.5 text-label-sm text-on-surface-variant/80">
                {t('browser.msmSuche.zusammenfassungEphemerHinweis')}
              </p>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
