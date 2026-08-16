import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Hand, ShieldAlert, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiActionProposal, type AiRunInfo } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { AiVerlauf, mergeEntries } from './AiVerlauf'
import { useAiLauf } from './useAiLauf'

/**
 * Wie oft nachgesehen wird, ob im Hintergrund eine Reparatur angelaufen ist.
 *
 * Der Takt im Panel sieht jede Minute nach offenen Vorfällen; schneller als er
 * kann hier nichts entstehen. Zwanzig Sekunden sind das Mittelmaß dazwischen:
 * man sieht eine beginnende Reparatur, ohne dass die Seite dauernd fragt.
 *
 * Solange ein Strom hängt, wird gar nicht gefragt — dann kommt alles über SSE,
 * und eine Abfrage nebenher wäre nur Last.
 */
const NACHSEHEN_MS = 20_000

/**
 * Das Guardian-Fenster: ein Verlauf, den man liest.
 *
 * Hier schreiben die Läufe, die eine Guardian-Störung ausgelöst hat — und
 * sonst niemand. Es gibt kein Eingabefeld, und das ist der Zweck der Sache:
 * eine getippte Nachricht löst über `vorgaenger_abloesen` jeden offenen Lauf
 * ihrer Unterhaltung ab. In einem Fenster, in dem seit vier Uhr eine Reparatur
 * läuft, wäre ein Eingabefeld ein Knopf zum versehentlichen Abbrechen — genau
 * der Fehler, den die Trennung der Fenster beseitigt hat.
 *
 * Vorschlagskarten bleiben trotzdem bedienbar: eine Karte zu bestätigen ist
 * keine Nachricht, sie löst nichts ab, sondern weckt den geparkten Lauf dort,
 * wo er steht.
 */
export function GuardianAnsicht() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [laedt, setLaedt] = useState(true)
  const [lauf, setLauf] = useState<AiRunInfo | null>(null)
  const [uebernimmt, setUebernimmt] = useState(false)
  const verlaufRef = useRef<HTMLDivElement | null>(null)
  const [amEnde, setAmEnde] = useState(true)

  const {
    entries, setEntries, streaming, laufendeWerkzeuge, merkeVorschlag, haengeAn,
  } = useAiLauf({
    providerId: null,
    canAttach: false,
    denken: { an: false, stufe: null },
    // Der Füllstandsring gehört zum Eingabefeld, und das gibt es hier nicht.
    ladeKontext: async () => undefined,
    setAttachments: () => undefined,
  })

  const laden = useCallback(async () => {
    const [conversation, actions, aktiverLauf] = await Promise.all([
      aiApi.getConversation('guardian'),
      aiApi.listActions('guardian'),
      aiApi.getActiveRun('guardian').catch(() => null),
    ])
    setEntries(mergeEntries(conversation.messages, actions))
    setLauf(aktiverLauf)
    return aktiverLauf
  }, [setEntries])

  useEffect(() => {
    let lebt = true
    laden()
      .catch((error: unknown) => {
        if (lebt) {
          toast.error(
            error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.load'),
          )
        }
      })
      .finally(() => {
        if (lebt) setLaedt(false)
      })
    return () => {
      lebt = false
    }
  }, [laden, t])

  /**
   * Nachsehen, ob im Hintergrund etwas angelaufen ist — und sich anhängen.
   *
   * `live` ist die ehrliche Auskunft, ob dieser Prozess dem Lauf beim Arbeiten
   * zusehen kann. Nach einem Neustart des Panels steht ein geparkter Lauf
   * weiterhin in der Datenbank, aber niemand hält ihn im Speicher; ein
   * Anhängeversuch endete dann in einem Ladebalken, der sich nie bewegt.
   *
   * `angehaengtRef` merkt sich, an welchem Lauf man schon hing. Ohne das
   * entstünde eine Schleife: der Strom endet, `streaming` fällt zurück, der
   * Zustand `lauf` trägt aber weiterhin `live` — und der Effekt hinge sich
   * sofort wieder an denselben, bereits fertigen Lauf. Der Chat hat die Frage
   * nicht, weil er den Lauf beim Öffnen **verbraucht** (`setLaufBeimOeffnen(null)`);
   * hier ist er dauerhafter Zustand, weil hier auch später noch einer anlaufen
   * kann, ohne dass jemand etwas tippt.
   */
  const angehaengtRef = useRef<string | null>(null)
  useEffect(() => {
    if (streaming) return
    if (lauf?.live && angehaengtRef.current !== lauf.id) {
      angehaengtRef.current = lauf.id
      void haengeAn(lauf.id)
      return
    }
    const timer = window.setInterval(() => {
      void laden().catch(() => undefined)
    }, NACHSEHEN_MS)
    return () => window.clearInterval(timer)
  }, [haengeAn, lauf, laden, streaming])

  // Dieselbe 50-Pixel-Regel wie im Chat und in der Serverkonsole: wer fast
  // unten steht, meint unten. Wer zurückgescrollt hat, um etwas zu lesen, soll
  // von der nächsten Zeile nicht wieder nach unten gerissen werden.
  useEffect(() => {
    if (!amEnde) return
    const bereich = verlaufRef.current
    if (bereich) bereich.scrollTop = bereich.scrollHeight
  }, [amEnde, entries, laufendeWerkzeuge])

  const aufVorschlag = useCallback((updated: AiActionProposal) => {
    merkeVorschlag(updated)
    // **Hier ging es sonst nicht weiter.** Der geparkte Lauf wartet auf genau
    // diese Entscheidung; ohne das Anhängen liefe er zwar weiter, aber im
    // Fenster bliebe es still, bis jemand die Seite neu lädt.
    const id = updated.run_id ?? lauf?.id
    if (id && updated.status !== 'proposed') {
      angehaengtRef.current = id
      void haengeAn(id)
    }
  }, [haengeAn, lauf?.id])

  /**
   * Übernehmen: der Auftrag endet, und man steht im Dauerchat.
   *
   * Beendet wird der **Auftrag**, nicht nur der Lauf — sonst startet der Takt
   * neunzig Sekunden später den nächsten, und die KI arbeitete weiter, während
   * der Mensch längst selbst Hand angelegt hat.
   *
   * Danach in den Chat: dort kann man tippen, hier nicht. Wer übernimmt, will
   * als Nächstes etwas sagen.
   */
  const uebernehmen = useCallback(async () => {
    setUebernimmt(true)
    try {
      const { aborted } = await aiApi.takeOverGuardian()
      // Kein „Übernommen", wenn es nichts zu übernehmen gab. Der Satz wäre
      // sonst eine Behauptung über einen Vorgang, den es nicht gab — und der
      // Knopf steht auch dann da, wenn gerade nichts läuft.
      if (aborted > 0) toast.success(t('ai.guardian.takeOverDone'))
      navigate('/ai')
    } catch (error: unknown) {
      toast.error(
        error instanceof SanitizedApiError ? error.message : t('ai.guardian.takeOverFailed'),
      )
      setUebernimmt(false)
    }
  }, [navigate, t])

  const leer = entries.length === 0

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-outline-variant/40 bg-surface-container-lowest">
      <header className="flex shrink-0 items-center gap-2 border-b border-outline-variant/40 px-4 py-3">
        <ShieldAlert className="h-4 w-4 shrink-0 text-secondary" aria-hidden="true" />
        <div className="min-w-0">
          <h2 className="truncate font-headline text-sm font-semibold text-on-surface">
            {t('ai.guardian.title')}
          </h2>
          <p className="truncate text-xs text-on-surface-variant">
            {t('ai.guardian.subtitle')}
          </p>
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {streaming ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary">
              {t('ai.guardian.running')}
            </span>
          ) : lauf?.status === 'waiting_confirmation' ? (
            /* Ein Reparaturlauf darf seit der E-Mail-Freigabe warten, statt
               aufzugeben. Ohne diesen Hinweis sähe das Fenster aus wie ein
               abgebrochener Lauf — und genau die Verwechslung war einer der
               vier Gründe, aus denen unbeaufsichtigte Läufe früher nie parken
               durften. */
            <span className="inline-flex items-center gap-1.5 rounded-full bg-status-warning/10 px-2.5 py-1 text-xs font-medium text-status-warning">
              {t('ai.guardian.waitingApproval')}
            </span>
          ) : (
            <Button
              type="button" variant="ghost" size="sm"
              onClick={() => void laden().catch(() => undefined)}
            >
              {t('common.refresh')}
            </Button>
          )}
          {/* Der ausdrückliche Abbruch — die einzige Stelle, an der man hier
              etwas auslöst. Ein Eingabefeld täte dasselbe versehentlich. */}
          <Button
            type="button" variant="ghost" size="sm"
            disabled={uebernimmt}
            title={t('ai.guardian.takeOverHint')}
            onClick={() => void uebernehmen()}
          >
            <Hand className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
            {t('ai.guardian.takeOver')}
          </Button>
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
          {leer && !laedt && (
            <div className="py-16 text-center">
              <Sparkles className="mx-auto h-10 w-10 text-primary/70" aria-hidden="true" />
              <h3 className="mt-4 font-headline text-lg font-semibold text-on-surface">
                {t('ai.guardian.emptyTitle')}
              </h3>
              <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-on-surface-variant">
                {t('ai.guardian.emptyDescription')}
              </p>
            </div>
          )}
          <AiVerlauf
            entries={entries}
            laufendeWerkzeuge={laufendeWerkzeuge}
            onProposalChange={aufVorschlag}
          />
        </div>
      </div>

      {/* Kein Eingabefeld — siehe der Kommentar über dieser Komponente. Der
          Hinweis steht dort, wo sonst das Feld wäre: sonst sucht man es. */}
      <p className="shrink-0 border-t border-outline-variant/40 px-4 py-3 text-xs text-on-surface-variant">
        {t('ai.guardian.readOnly')}
      </p>
    </section>
  )
}
