import { useCallback, useEffect, useRef, useState } from 'react'
import { Bot, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiActionProposal, type AiRunInfo } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button } from '@/Singra/UI'
import { AI_ZUSTELLUNG_EVENT } from '@/lib/aiZustellung'
import { toast } from '@/stores/toastStore'
import { AiVerlauf, mergeEntries } from './AiVerlauf'
import { useAiLauf } from './useAiLauf'

/** Derselbe Takt wie im Guardian-Fenster, aus denselben Gründen. */
const NACHSEHEN_MS = 20_000

/**
 * Das Fenster eines Hintergrund-Auftrags: ein Verlauf, den man liest.
 *
 * Geladen wird über die **Kennung** und nie über die Art — Worker-Fenster
 * gibt es je Auftrag eines, `kind=worker` ist mehrdeutig. Es gibt kein
 * Eingabefeld und keinen Abbruch-Knopf: eine getippte Nachricht löste über
 * `vorgaenger_abloesen` den Auftrag ab (dieselbe Begründung wie im
 * Guardian-Fenster), und gesteuert wird im Gespräch — „Stopp den Auftrag"
 * geht an das Gehirn, das `worker_cancel` ruft
 * (docs/agentic-framework.md, §6).
 *
 * Vorschlagskarten bleiben bedienbar: eine Karte zu bestätigen ist keine
 * Nachricht, sie löst nichts ab, sondern weckt den geparkten Lauf dort, wo
 * er steht.
 */
export function WorkerAnsicht({ conversationId }: { conversationId: string }) {
  const { t } = useTranslation()
  const [laedt, setLaedt] = useState(true)
  const [titel, setTitel] = useState<string | null>(null)
  const [lauf, setLauf] = useState<AiRunInfo | null>(null)
  const [verschwunden, setVerschwunden] = useState(false)
  const verlaufRef = useRef<HTMLDivElement | null>(null)
  const [amEnde, setAmEnde] = useState(true)

  const {
    entries, setEntries, streaming, laufendeWerkzeuge, merkeVorschlag, haengeAn,
  } = useAiLauf({
    providerId: null,
    canAttach: false,
    denken: { an: false, stufe: null },
    ladeKontext: async () => undefined,
    setAttachments: () => undefined,
  })

  const laden = useCallback(async () => {
    const [conversation, actions, aktiverLauf] = await Promise.all([
      aiApi.getWorkerConversation(conversationId),
      aiApi.listWorkerActions(conversationId),
      aiApi.getWorkerRun(conversationId).catch(() => null),
    ])
    setTitel(conversation.title)
    setEntries(mergeEntries(conversation.messages, actions))
    setLauf(aktiverLauf)
    return aktiverLauf
  }, [conversationId, setEntries])

  useEffect(() => {
    let lebt = true
    setLaedt(true)
    setVerschwunden(false)
    laden()
      .catch((error: unknown) => {
        if (!lebt) return
        // Ein 404 ist hier kein Störfall: der Auftrag ist fremd, aufgeräumt
        // oder die Kennung stammt aus einem alten Lesezeichen. Die Ansicht
        // sagt das ruhig, statt einen Fehler-Toast zu werfen.
        setVerschwunden(true)
        if (!(error instanceof SanitizedApiError)) {
          toast.error(t('ai.chat.errors.load'))
        }
      })
      .finally(() => {
        if (lebt) setLaedt(false)
      })
    return () => {
      lebt = false
    }
  }, [laden, t])

  // Anhängen bei lebendem Lauf, sonst gemächlich nachsehen — wortwörtlich
  // dasselbe Muster wie im Guardian-Fenster, samt `angehaengtRef` gegen die
  // Wiederanhänge-Schleife (Begründung dort).
  const angehaengtRef = useRef<string | null>(null)
  useEffect(() => {
    if (streaming || verschwunden) return
    if (lauf?.live && angehaengtRef.current !== lauf.id) {
      angehaengtRef.current = lauf.id
      void haengeAn(lauf.id)
      return
    }
    const timer = window.setInterval(() => {
      void laden().catch(() => undefined)
    }, NACHSEHEN_MS)
    return () => window.clearInterval(timer)
  }, [haengeAn, lauf, laden, streaming, verschwunden])

  // Meldet die Glocke eine Zustellung, kann auch dieser Auftrag gemeint sein
  // (er ist fertig geworden oder hat eine Frage gestellt) — nachladen statt
  // auf den nächsten Poll-Takt warten.
  useEffect(() => {
    if (verschwunden) return
    const nachladen = () => {
      if (!streaming) void laden().catch(() => undefined)
    }
    window.addEventListener(AI_ZUSTELLUNG_EVENT, nachladen)
    return () => window.removeEventListener(AI_ZUSTELLUNG_EVENT, nachladen)
  }, [laden, streaming, verschwunden])

  // Die 50-Pixel-Regel des Chats: wer fast unten steht, meint unten.
  useEffect(() => {
    if (!amEnde) return
    const bereich = verlaufRef.current
    if (bereich) bereich.scrollTop = bereich.scrollHeight
  }, [amEnde, entries, laufendeWerkzeuge])

  const aufVorschlag = useCallback((updated: AiActionProposal) => {
    merkeVorschlag(updated)
    // Der geparkte Auftrag wartet auf genau diese Entscheidung — ohne das
    // Anhängen bliebe es hier still, bis jemand die Seite neu lädt.
    const id = updated.run_id ?? lauf?.id
    if (id && updated.status !== 'proposed') {
      angehaengtRef.current = id
      void haengeAn(id)
    }
  }, [haengeAn, lauf?.id, merkeVorschlag])

  /** Die Status-Pille des Kopfes — vier Zustände, ruhig erzählt. */
  const statusPille = () => {
    const status = streaming ? 'running' : lauf?.status
    if (status === 'running') {
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary">
          {t('ai.worker.running')}
        </span>
      )
    }
    if (status === 'waiting_confirmation') {
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-status-warning/10 px-2.5 py-1 text-xs font-medium text-status-warning">
          {t('ai.worker.waitingApproval')}
        </span>
      )
    }
    if (status === 'waiting_user') {
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-status-warning/10 px-2.5 py-1 text-xs font-medium text-status-warning">
          {t('ai.worker.waitingUser')}
        </span>
      )
    }
    if (status === 'waiting_wake') {
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-container-high px-2.5 py-1 text-xs font-medium text-on-surface-variant">
          {t('ai.worker.waitingWake')}
        </span>
      )
    }
    // Kein offener Lauf: der Auftrag ist fertig — die Unterhaltung bleibt
    // lesbar, aus der Leiste ist er längst heraus.
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-container-high px-2.5 py-1 text-xs font-medium text-on-surface-variant">
        {t('ai.worker.finished')}
      </span>
    )
  }

  const leer = entries.length === 0

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-outline-variant/40 bg-surface-container-lowest">
      <header className="flex shrink-0 items-center gap-2 border-b border-outline-variant/40 px-4 py-3">
        <Bot className="h-4 w-4 shrink-0 text-secondary" aria-hidden="true" />
        <div className="min-w-0">
          <h2 className="truncate font-headline text-sm font-semibold text-on-surface">
            {titel ?? t('ai.worker.title')}
          </h2>
          <p className="truncate text-xs text-on-surface-variant">
            {t('ai.worker.subtitle')}
          </p>
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {!verschwunden && statusPille()}
          {!streaming && !verschwunden && (
            <Button
              type="button" variant="ghost" size="sm"
              onClick={() => void laden().catch(() => undefined)}
            >
              {t('common.refresh')}
            </Button>
          )}
        </div>
      </header>

      <div
        ref={verlaufRef}
        className="min-h-0 flex-1 overflow-y-auto"
        aria-live="polite"
        onScroll={(event) => {
          const { scrollTop, scrollHeight, clientHeight } = event.currentTarget
          setAmEnde(scrollHeight - scrollTop - clientHeight < 50)
        }}
      >
        <div className="mx-auto w-full max-w-3xl px-3 py-6 sm:px-4">
          {verschwunden && !laedt && (
            <div className="py-16 text-center">
              <Sparkles className="mx-auto h-10 w-10 text-primary/70" aria-hidden="true" />
              <h3 className="mt-4 font-headline text-lg font-semibold text-on-surface">
                {t('ai.worker.notFoundTitle')}
              </h3>
              <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-on-surface-variant">
                {t('ai.worker.notFoundDescription')}
              </p>
            </div>
          )}
          {!verschwunden && leer && !laedt && (
            <div className="py-16 text-center">
              <Sparkles className="mx-auto h-10 w-10 text-primary/70" aria-hidden="true" />
              <h3 className="mt-4 font-headline text-lg font-semibold text-on-surface">
                {t('ai.worker.emptyTitle')}
              </h3>
              <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-on-surface-variant">
                {t('ai.worker.emptyDescription')}
              </p>
            </div>
          )}
          {!verschwunden && (
            <AiVerlauf
              entries={entries}
              laufendeWerkzeuge={laufendeWerkzeuge}
              onProposalChange={aufVorschlag}
            />
          )}
        </div>
      </div>

      {/* Kein Eingabefeld und kein Abbruch-Knopf — der Hinweis steht dort, wo
          man das Feld suchte, und sagt auch, wie man stattdessen steuert. */}
      <p className="shrink-0 border-t border-outline-variant/40 px-4 py-3 text-xs text-on-surface-variant">
        {t('ai.worker.readOnly')}
      </p>
    </section>
  )
}
