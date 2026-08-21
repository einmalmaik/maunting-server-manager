import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate } from 'react-router-dom'
import { Bot, X } from 'lucide-react'

import {
  AI_RUHENDE_LAUFZUSTAENDE,
  aiApi,
  type AiConversationKind,
  type AiRunStatus,
} from '@/api/ai'
import { zustellungMelden } from '@/lib/aiZustellung'
import { useAuthStore } from '@/stores/authStore'
import { useHasPermission } from '@/hooks/useHasPermission'

/** Wie oft nachgesehen wird, solange ein Lauf arbeitet. */
const TAKT_MS = 8_000

/**
 * Wie oft nachgesehen wird, solange **nichts** laeuft.
 *
 * Hier stand vorher gar nichts: ruhte alles, hoerte der Takt auf. Das war
 * richtig, solange ein Lauf nur durch eine getippte Nachricht entstehen konnte —
 * wer tippt, ist im Chat, und dort meldet der Ereignisstrom selbst.
 *
 * Seit ein stehender Auftrag um acht Uhr **von selbst** anfaengt, stimmt die
 * Annahme nicht mehr. Eine Seite, die seit gestern Abend offen steht, haette
 * den Lauf nie bemerkt: kein Takt, kein Ereignis, keine Meldung. Sechzig
 * Sekunden sind derselbe Abstand, in dem der Server selbst nach faelligen
 * Auftraegen sieht — feiner waere eine Genauigkeit, die es dahinter gar nicht
 * gibt.
 */
const RUHETAKT_MS = 60_000

const TEXTE: Record<string, { key: string; fallback: string }> = {
  completed: { key: 'ai.notice.completed', fallback: 'Die KI ist mit deinem Auftrag fertig.' },
  waiting_confirmation: { key: 'ai.notice.waitingConfirmation', fallback: 'Die KI wartet auf deine Bestätigung.' },
  waiting_user: { key: 'ai.notice.waitingUser', fallback: 'Die KI hat eine Rückfrage an dich.' },
  failed: { key: 'ai.notice.failed', fallback: 'Der KI-Auftrag ist fehlgeschlagen.' },
}

/**
 * Dieselben Zustände, andere Sätze — weil es ein anderer Anlass ist.
 *
 * „Die KI ist mit deinem Auftrag fertig" wäre hier schlicht falsch: den
 * Auftrag hat niemand gegeben, eine Störung hat ihn ausgelöst. Und
 * „fehlgeschlagen" heißt bei einer Reparatur etwas anderes als bei einer
 * Frage — es heißt, dass ein Server womöglich noch steht.
 */
const GUARDIAN_TEXTE: Record<string, { key: string; fallback: string }> = {
  completed: { key: 'ai.notice.guardianCompleted', fallback: 'Die KI hat eine Guardian-Störung bearbeitet.' },
  waiting_confirmation: { key: 'ai.notice.guardianWaiting', fallback: 'Eine Guardian-Reparatur wartet auf deine Freigabe.' },
  waiting_user: { key: 'ai.notice.guardianWaiting', fallback: 'Eine Guardian-Reparatur wartet auf deine Freigabe.' },
  failed: { key: 'ai.notice.guardianFailed', fallback: 'Eine Guardian-Reparatur ist fehlgeschlagen.' },
}

/**
 * Ein dritter Anlass, ein dritter Satz: die Hintergrund-Aufträge.
 *
 * `completed` steht hier für „aus der Liste verschwunden" — ob der Auftrag
 * gelang oder scheiterte, weiß die Glocke nicht, und sie behauptet es auch
 * nicht: die ehrliche Auskunft liefert das Gehirn im Chat. Ein schlafender
 * Langläufer (`waiting_wake`) wird bewusst nie gemeldet — er hat angekündigt,
 * sich zu melden, und genau das tut er dann auch.
 */
const WORKER_TEXTE: Record<string, { key: string; fallback: string }> = {
  completed: { key: 'ai.notice.workerReported', fallback: 'Ein Worker hat berichtet.' },
  waiting_confirmation: { key: 'ai.notice.workerWaiting', fallback: 'Ein Worker wartet auf deine Freigabe.' },
  waiting_user: { key: 'ai.notice.workerQuestion', fallback: 'Ein Worker hat eine Rückfrage.' },
}

/**
 * Wohin die Meldung zeigt. Der Dauerchat ist die Seite ohne Zusatz.
 *
 * Für `worker` steht hier nur der Rückfall: ein Auftrag hat viele Fenster,
 * das echte Ziel (mit Kennung) trägt die Meldung selbst in `ziel`.
 */
const ZIEL: Record<AiConversationKind, string> = {
  primary: '/ai',
  guardian: '/ai?ansicht=guardian',
  worker: '/ai',
}

/**
 * Meldet unten rechts, wenn ein KI-Auftrag fertig ist oder wartet.
 *
 * Der Gegenwert zur Hintergrundausfuehrung: seit die KI weiterarbeitet, waehrend
 * man woanders ist, muss sie auch Bescheid sagen koennen. Ohne das waere der
 * Fortschritt unsichtbar — man muesste den Chat offen lassen, also genau das
 * tun, was nicht mehr noetig sein sollte.
 *
 * Der schnelle Takt gilt nur, solange etwas läuft **und** man nicht im Chat
 * steht. Sonst der Ruhetakt: im Chat meldet der Ereignisstrom selbst, und eine
 * Meldung, die dort ohnehin unterdrückt wird, ist keine Abfrage alle acht
 * Sekunden wert.
 */
export function AiRunNotice() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const ort = useLocation()
  const darfChatten = useHasPermission('ai.chat.use')
  const user = useAuthStore((state) => state.user)
  const meldungenAn = user?.ai_notifications !== false

  const [meldung, setMeldung] = useState<
    { status: AiRunStatus; kind: AiConversationKind; ziel?: string } | null
  >(null)
  // Der zuletzt gesehene Zustand — **je Fenster**. Gemeldet wird der
  // *Uebergang*, nicht der Zustand: sonst kaeme bei jedem Takt dieselbe
  // Meldung erneut.
  //
  // Je Fenster und nicht als eine Zeile, weil es zwei Laeufe gleichzeitig geben
  // kann: der Mensch fragt etwas, waehrend im Hintergrund ein Server repariert
  // wird. Eine gemeinsame Zeile saehe den Wechsel zwischen beiden als
  // Zustandswechsel eines Laufs und meldete abwechselnd Unsinn.
  const letzterRef = useRef<
    Partial<Record<AiConversationKind, { id: string; status: AiRunStatus }>>
  >({})
  // Dasselbe fuer die Hintergrund-Auftraege, nur je **Fenster-Kennung**: es
  // gibt beliebig viele gleichzeitig, und jeder wechselt fuer sich.
  const workerRef = useRef<Record<string, AiRunStatus>>({})

  // **Unterdrueckt wird nur das Fenster, in das man gerade sieht.** Vorher
  // genuegte der Pfad `/ai`: wer im Chat stand, bekam auch von einer beendeten
  // Reparatur nichts mit, obwohl er sie gar nicht sehen konnte.
  const suchParameter = new URLSearchParams(ort.search)
  const ansichtParam = ort.pathname.startsWith('/ai') ? suchParameter.get('ansicht') : null
  const offenesFenster: AiConversationKind | null = ort.pathname.startsWith('/ai')
    ? (ansichtParam === 'guardian'
        ? 'guardian'
        : ansichtParam === 'worker'
          ? 'worker'
          : 'primary')
    : null
  // Bei `worker` reicht die Art nicht: man sieht **einen** Auftrag, nicht alle.
  const offeneWorkerId = offenesFenster === 'worker' ? suchParameter.get('id') : null

  const nachsehen = useCallback(async () => {
    const fenster: AiConversationKind[] = ['primary', 'guardian']
    let laeuftNoch = false
    for (const art of fenster) {
      let lauf: Awaited<ReturnType<typeof aiApi.getActiveRun>> = null
      try {
        lauf = await aiApi.getActiveRun(art)
      } catch {
        // Eine gescheiterte Nachfrage ist kein Grund, etwas anzuzeigen.
        continue
      }
      const vorher = letzterRef.current[art]
      const stumm = offenesFenster === art
      if (!lauf) {
        // Der Lauf ist aus der Liste der offenen verschwunden — also fertig.
        if (vorher && vorher.status === 'running' && !stumm) {
          setMeldung({ status: 'completed', kind: art })
        }
        // Ein beendeter Dauerchat-Lauf heisst auch: dort steht jetzt etwas
        // Neues — zum Beispiel die Lieferung der Meldestelle, die niemand
        // getippt hat. Der offene Chat laedt daraufhin nach; deshalb feuert
        // das Signal auch (gerade!) dann, wenn die Meldung selbst stumm bleibt.
        if (art === 'primary' && vorher) zustellungMelden()
        delete letzterRef.current[art]
        continue
      }
      const gewechselt = !vorher || vorher.id !== lauf.id || vorher.status !== lauf.status
      // **Jeder** beobachtete Wechsel des Dauerchat-Laufs ist eine Zustellung:
      // auch das erste Auftauchen (ein zweiter Tab oder die Desktop-App hat
      // etwas geschickt) und das Parken auf eine Rueckfrage. Vorher feuerte
      // nur das Verschwinden — ein fremder Lauf, den der Takt nie zu Gesicht
      // bekam, blieb im offenen Chat unsichtbar bis zum harten Neuladen.
      if (art === 'primary' && gewechselt) zustellungMelden()
      if (
        gewechselt
        && vorher?.id === lauf.id
        && AI_RUHENDE_LAUFZUSTAENDE.includes(lauf.status)
        && !stumm
      ) {
        setMeldung({ status: lauf.status, kind: art })
      }
      letzterRef.current[art] = { id: lauf.id, status: lauf.status }
      if (lauf.status === 'running') laeuftNoch = true
    }

    // Die Hintergrund-Auftraege: Uebergaenge je Fenster-Kennung. Die Liste
    // traegt nur Lebende — Verschwinden heisst beendet, und der Bericht ist
    // als Meldung unterwegs (docs/agentic-framework.md, §4).
    let workerLebt = false
    try {
      const workers = await aiApi.listWorkers()
      workerLebt = workers.length > 0
      const jetzt = new Set(workers.map((w) => w.conversation_id))
      let zustellung = false
      for (const kennung of Object.keys(workerRef.current)) {
        if (jetzt.has(kennung)) continue
        delete workerRef.current[kennung]
        zustellung = true
        if (offenesFenster !== 'primary') {
          setMeldung({ status: 'completed', kind: 'worker', ziel: '/ai' })
        }
      }
      for (const worker of workers) {
        const vorher = workerRef.current[worker.conversation_id]
        if (vorher && vorher !== worker.status) {
          if (worker.status === 'waiting_confirmation') {
            // Die Karte haengt im Fenster des Auftrags — dorthin zeigen,
            // ausser man sieht genau dieses Fenster ohnehin an.
            if (!(offenesFenster === 'worker' && offeneWorkerId === worker.conversation_id)) {
              setMeldung({
                status: worker.status,
                kind: 'worker',
                ziel: `/ai?ansicht=worker&id=${encodeURIComponent(worker.conversation_id)}`,
              })
            }
          } else if (worker.status === 'waiting_user') {
            // Die Frage stellt das Gehirn im Dauerchat — dorthin zeigen und
            // den offenen Chat nachladen lassen.
            zustellung = true
            if (offenesFenster !== 'primary') {
              setMeldung({ status: worker.status, kind: 'worker', ziel: '/ai' })
            }
          }
        }
        workerRef.current[worker.conversation_id] = worker.status
      }
      if (zustellung) zustellungMelden()
    } catch {
      // Auch hier: eine gescheiterte Nachfrage ist keine Meldung wert.
    }
    // Schnell weiter, solange etwas arbeitet und man nicht im Chat steht
    // (dort meldet der Ereignisstrom selbst) — **oder** ein Auftrag lebt:
    // dessen Zustellung soll den offenen Chat nicht erst nach dem Ruhetakt
    // erreichen, denn der Chat pollt selbst nicht.
    return (laeuftNoch && offenesFenster === null) || workerLebt
  }, [offeneWorkerId, offenesFenster])

  useEffect(() => {
    if (!darfChatten || !meldungenAn) return
    let aktiv = true
    let timer: ReturnType<typeof setTimeout> | undefined

    const takt = async () => {
      if (!aktiv) return
      const schnell = await nachsehen()
      if (!aktiv) return
      // Schnell nachsehen, solange `nachsehen` das verlangt; sonst langsam
      // weiter, statt aufzuhören. Der langsame Takt ist die einzige Art, wie
      // eine offene Seite von einem Lauf erfährt, den niemand ausgelöst hat.
      timer = setTimeout(takt, schnell ? TAKT_MS : RUHETAKT_MS)
    }
    void takt()
    return () => {
      aktiv = false
      if (timer) clearTimeout(timer)
    }
  }, [darfChatten, meldungenAn, nachsehen])

  // Wer in **dieses** Fenster sieht, sieht es ohnehin. Wer im Chat steht,
  // sieht eine laufende Reparatur dagegen nicht — und soll sie gemeldet
  // bekommen. Worker-Meldungen entscheiden das feiner schon bei der
  // Erzeugung (welcher Auftrag, welches Ziel) — die Art allein sagt hier
  // nichts: man sieht **einen** Auftrag, nicht alle.
  if (
    !meldung
    || (meldung.kind === offenesFenster && meldung.kind !== 'worker')
    || !darfChatten
    || !meldungenAn
  ) return null
  const tabelle = meldung.kind === 'guardian'
    ? GUARDIAN_TEXTE
    : meldung.kind === 'worker'
      ? WORKER_TEXTE
      : TEXTE
  const text = tabelle[meldung.status] ?? tabelle.completed

  return (
    <div
      role="status"
      className="fixed bottom-4 right-4 z-[9998] flex max-w-[min(calc(100vw-2rem),24rem)] items-start gap-3 rounded-xl border border-outline-variant bg-surface-container-high p-3 shadow-panel"
    >
      <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-primary/10 text-primary">
        <Bot className="h-4 w-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm text-on-surface">{t(text.key, text.fallback)}</p>
        <button
          type="button"
          className="mt-1 text-xs font-medium text-primary hover:underline"
          onClick={() => { setMeldung(null); navigate(meldung.ziel ?? ZIEL[meldung.kind]) }}
        >
          {t('ai.notice.open', 'Zum Assistenten')}
        </button>
      </div>
      <button
        type="button"
        className="rounded-md p-1 text-on-surface-variant hover:bg-surface-container-highest"
        onClick={() => setMeldung(null)}
        aria-label={t('common.close', 'Schließen')}
      >
        <X className="h-4 w-4" aria-hidden="true" />
      </button>
    </div>
  )
}
