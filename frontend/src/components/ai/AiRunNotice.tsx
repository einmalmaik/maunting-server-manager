import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate } from 'react-router-dom'
import { Bot, X } from 'lucide-react'

import { AI_RUHENDE_LAUFZUSTAENDE, aiApi, type AiRunStatus } from '@/api/ai'
import { useAuthStore } from '@/stores/authStore'
import { useHasPermission } from '@/hooks/useHasPermission'

/** Wie oft nachgesehen wird, solange ein Lauf arbeitet. */
const TAKT_MS = 8_000

const TEXTE: Record<string, { key: string; fallback: string }> = {
  completed: { key: 'ai.notice.completed', fallback: 'Die KI ist mit deinem Auftrag fertig.' },
  waiting_confirmation: { key: 'ai.notice.waitingConfirmation', fallback: 'Die KI wartet auf deine Bestätigung.' },
  waiting_user: { key: 'ai.notice.waitingUser', fallback: 'Die KI hat eine Rückfrage an dich.' },
  failed: { key: 'ai.notice.failed', fallback: 'Der KI-Auftrag ist fehlgeschlagen.' },
}

/**
 * Meldet unten rechts, wenn ein KI-Auftrag fertig ist oder wartet.
 *
 * Der Gegenwert zur Hintergrundausfuehrung: seit die KI weiterarbeitet, waehrend
 * man woanders ist, muss sie auch Bescheid sagen koennen. Ohne das waere der
 * Fortschritt unsichtbar — man muesste den Chat offen lassen, also genau das
 * tun, was nicht mehr noetig sein sollte.
 *
 * Bewusst kein Dauerpoller: nachgesehen wird nur, solange tatsaechlich etwas
 * laeuft. Ist nichts los, kostet die Komponente einen einzigen Aufruf beim
 * Anmelden und danach nichts mehr.
 */
export function AiRunNotice() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const ort = useLocation()
  const darfChatten = useHasPermission('ai.chat.use')
  const user = useAuthStore((state) => state.user)
  const meldungenAn = user?.ai_notifications !== false

  const [meldung, setMeldung] = useState<{ status: AiRunStatus } | null>(null)
  // Der zuletzt gesehene Zustand. Gemeldet wird der **Uebergang**, nicht der
  // Zustand: sonst kaeme bei jedem Takt dieselbe Meldung erneut.
  const letzterRef = useRef<{ id: string; status: AiRunStatus } | null>(null)
  const imChat = ort.pathname.startsWith('/ai')

  const nachsehen = useCallback(async () => {
    let lauf: Awaited<ReturnType<typeof aiApi.getActiveRun>> = null
    try {
      lauf = await aiApi.getActiveRun()
    } catch {
      // Eine gescheiterte Nachfrage ist kein Grund, etwas anzuzeigen.
      return false
    }
    const vorher = letzterRef.current
    if (!lauf) {
      // Der Lauf ist aus der Liste der offenen verschwunden — also fertig.
      if (vorher && vorher.status === 'running' && !imChat) {
        setMeldung({ status: 'completed' })
      }
      letzterRef.current = null
      return false
    }
    const gewechselt = !vorher || vorher.id !== lauf.id || vorher.status !== lauf.status
    if (gewechselt && vorher?.id === lauf.id && AI_RUHENDE_LAUFZUSTAENDE.includes(lauf.status) && !imChat) {
      setMeldung({ status: lauf.status })
    }
    letzterRef.current = { id: lauf.id, status: lauf.status }
    return lauf.status === 'running'
  }, [imChat])

  useEffect(() => {
    if (!darfChatten || !meldungenAn) return
    let aktiv = true
    let timer: ReturnType<typeof setTimeout> | undefined

    const takt = async () => {
      if (!aktiv) return
      const laeuftNoch = await nachsehen()
      if (!aktiv) return
      // Weiter nachsehen, solange etwas arbeitet. Ruht alles, hoert der Takt
      // auf — bis die Chatseite beim naechsten Start wieder etwas anstoesst.
      if (laeuftNoch) timer = setTimeout(takt, TAKT_MS)
    }
    void takt()
    return () => {
      aktiv = false
      if (timer) clearTimeout(timer)
    }
  }, [darfChatten, meldungenAn, nachsehen, ort.pathname])

  // Wer im Chat steht, sieht es ohnehin.
  if (!meldung || imChat || !darfChatten || !meldungenAn) return null
  const text = TEXTE[meldung.status] ?? TEXTE.completed

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
          onClick={() => { setMeldung(null); navigate('/ai') }}
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
