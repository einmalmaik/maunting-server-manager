import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Bot, Brain, BrainCircuit, Check, Loader2, Paperclip, Pencil, Send, Sparkles, Trash2, User, Wrench, X, Zap } from 'lucide-react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'

import {
  aiApi,
  attachAiRun,
  streamAiMessage,
  type AiActionProposal,
  type AiAttachment,
  type AiMessage,
  type AiProviderAvailable,
  type AiRunInfo,
  type AiRunStatus,
  type AiStreamEvent,
  type AiToolUse,
} from '@/api/ai'
import { api, SanitizedApiError } from '@/api/client'
import { Button, Dropdown, Switch } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'
import { AiActionProposalCard } from './AiActionProposalCard'
import { AiAutonomyButton } from './AiAutonomyButton'
import { AiMarkdown } from './AiMarkdown'
import { AiMemoryNotice } from './AiMemoryNotice'
import { AiQuestionCard } from './AiQuestionCard'
import { AiReasoningBlock } from './AiReasoningBlock'
import { useHasPermission } from '@/hooks/useHasPermission'

/** Ein Eintrag im sichtbaren Verlauf — chronologisch, nicht nach Typ sortiert. */
type Entry =
  | { kind: 'message'; id: string; message: AiMessage }
  | { kind: 'tool'; id: string; tool: AiToolUse }
  // Marke fuer das Falten des aelteren Verlaufs. Ohne sichtbaren Hinweis
  // wuerde die KI spaeter Dinge "vergessen", ohne dass jemand weiss warum.
  | { kind: 'compacted'; id: string }
  | { kind: 'proposal'; id: string; proposal: AiActionProposal }

interface ServerOption {
  id: number
  name: string
}

const ATTACHMENT_ACCEPT = '.txt,.log,.cfg,.conf,.ini,.json,.properties,.toml,.yaml,.yml,.png,.jpg,.jpeg'

/** Zustaende, in denen der Lauf nichts mehr von selbst tut. */
const RUHT: readonly AiRunStatus[] = [
  'completed', 'failed', 'cancelled', 'waiting_confirmation', 'waiting_user',
]

/**
 * Der KI-Assistent: **eine** Unterhaltung, die die Seite ausfuellt.
 *
 * Bewusst wie ein Messenger und nicht wie ein Verwaltungsformular. Es gibt
 * keinen Weg, einen zweiten Chat anzulegen — der Assistent ist ein
 * Gespraechspartner, keine Ablage. Alles Weitere (Provider, Denkschritte,
 * autonomer Modus, Skills) haengt als Schalter am Chat statt in eigenen
 * Kaesten daneben.
 */
export function AiChat() {
  const { t } = useTranslation()
  const canAttach = useHasPermission('ai.attachments.use')
  const canUseAutonomy = useHasPermission('ai.autonomous.use')
  const canUseMemory = useHasPermission('ai.memory.use')

  const [providers, setProviders] = useState<AiProviderAvailable[]>([])
  const [providerId, setProviderId] = useState<number | null>(null)
  const [entries, setEntries] = useState<Entry[]>([])
  const [attachments, setAttachments] = useState<AiAttachment[]>([])
  const [servers, setServers] = useState<ServerOption[]>([])
  // Nicht mehr an/aus, sondern eine Tiefe. `null` heisst „nicht nachdenken";
  // ein Wort ist die gewaehlte Stufe. Welche Stufen es gibt, sagt der Provider
  // aus dem Katalog — sie sind je Modell verschieden und bereits auf die Rolle
  // dieses Benutzers geklemmt.
  const [effort, setEffort] = useState<string | null>(null)
  // Ob der Einwilligungshinweis faellig ist. Die 24-Stunden-Regel entscheidet
  // das Backend — hier steht nur das Ergebnis.
  const [memoryNoticeDue, setMemoryNoticeDue] = useState(false)
  const [input, setInput] = useState('')
  // Welche eigene Nachricht gerade umformuliert wird, und womit.
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editDraft, setEditDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [streaming, setStreaming] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)
  // Der Lauf, der gerade noch etwas vorhat. Er ueberlebt diese Komponente —
  // wir merken ihn uns nur, um uns wieder anhaengen zu koennen.
  const [runId, setRunId] = useState<string | null>(null)
  // Was beim Oeffnen schon lief. Wird in einem eigenen Effekt angehaengt, weil
  // das Anhaengen erst gehen kann, wenn die Verarbeitung steht.
  const [laufBeimOeffnen, setLaufBeimOeffnen] = useState<AiRunInfo | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  // `streaming` ist Zustand und damit fuer die Ereignisschleife zu spaet: zwei
  // Anhaengeversuche kurz hintereinander saehen beide noch `false`.
  const streamingRef = useRef(false)
  const endRef = useRef<HTMLDivElement | null>(null)
  const mountedRef = useRef(true)
  const dragDepthRef = useRef(0)

  useEffect(() => {
    // StrictMode fuehrt Setup/Cleanup in Entwicklung absichtlich doppelt aus.
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      abortRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    let active = true
    Promise.all([
      aiApi.listProviders(),
      aiApi.getConversation(),
      aiApi.listActions(),
      canAttach ? aiApi.listAttachments() : Promise.resolve([] as AiAttachment[]),
      api<ServerOption[]>('/servers').catch(() => [] as ServerOption[]),
      // Scheitert der Abruf, wird der Hinweis nicht gezeigt statt den ganzen
      // Chat scheitern zu lassen — er ist wichtig, aber nicht so wichtig.
      canUseMemory
        ? aiApi.getMemoryPreference().catch(() => null)
        : Promise.resolve(null),
      // Laeuft da noch etwas von vorhin? Scheitert die Frage, wird eben nicht
      // angehaengt — der Verlauf steht ohnehin.
      aiApi.getActiveRun().catch(() => null),
    ])
      .then(([providerRows, conversation, actions, attachmentRows, serverRows, memoryPreference, aktiverLauf]) => {
        if (!active) return
        setLaufBeimOeffnen(aktiverLauf)
        setMemoryNoticeDue(Boolean(memoryPreference?.notice_due))
        setProviders(providerRows)
        setProviderId(providerRows.find((item) => item.available)?.id ?? null)
        // Vorschlaege werden chronologisch zwischen die Nachrichten einsortiert,
        // damit man sieht, auf welche Antwort sie sich beziehen. Vorher standen
        // sie gesammelt am Ende und wirkten losgeloest.
        setEntries(mergeEntries(conversation.messages, actions))
        setAttachments(attachmentRows)
        setServers(serverRows.map((row) => ({ id: row.id, name: row.name })))
      })
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.load'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [canAttach, canUseMemory, t])

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [entries])

  const availableProviders = useMemo(
    () => providers.filter((provider) => provider.available),
    [providers],
  )

  const aktiverProvider = useMemo(
    () => availableProviders.find((provider) => provider.id === providerId) ?? null,
    [availableProviders, providerId],
  )

  /**
   * Beim Providerwechsel die Denkstufe auf etwas Gueltiges bringen.
   *
   * Jedes Modell kennt andere Stufen — „xhigh" beim einen gibt es beim
   * naechsten nicht. Bliebe die alte Wahl stehen, senkte der Server sie
   * stillschweigend, und die Oberflaeche zeigte etwas anderes an, als
   * tatsaechlich gilt. Bei einem Modell, das Nachdenken nicht abschalten kann,
   * ist ausserdem `null` keine gueltige Wahl.
   */
  useEffect(() => {
    if (!aktiverProvider) return
    setEffort((current) => {
      if (!aktiverProvider.reasoning) return null
      if (current !== null && aktiverProvider.efforts.includes(current)) return current
      if (current === null && aktiverProvider.can_disable) return null
      return aktiverProvider.default_effort ?? aktiverProvider.efforts[0] ?? null
    })
  }, [aktiverProvider])

  /** Hochgeladen, aber noch nicht abgeschickt — die Chips über dem Eingabefeld. */
  const offeneAnhaenge = useMemo(
    () => attachments.filter((item) => item.message_id === null),
    [attachments],
  )

  /** Anhänge nach der Nachricht, mit der sie gesendet wurden. */
  const anhaengeJeNachricht = useMemo(() => {
    const karte = new Map<string, AiAttachment[]>()
    for (const item of attachments) {
      if (!item.message_id) continue
      const liste = karte.get(item.message_id)
      if (liste) liste.push(item)
      else karte.set(item.message_id, [item])
    }
    return karte
  }, [attachments])

  const uploadAttachment = useCallback(async (file: File | undefined) => {
    if (!file || streaming || uploading) return
    setUploading(true)
    try {
      const created = await aiApi.uploadAttachment(file)
      // Nach Kennung einsetzen statt anhaengen: das Nachladen nach dem Absenden
      // (siehe `message`-Ereignis) kann eine Antwort ueberholen, die noch
      // unterwegs war. Beim blossen Anhaengen stuende die Datei dann zweimal in
      // der Liste — React beschwert sich ueber den doppelten Key, und der
      // Benutzer sieht einen Anhang, den er nur einmal hochgeladen hat.
      if (mountedRef.current) {
        setAttachments((current) => [
          ...current.filter((item) => item.id !== created.id),
          created,
        ])
      }
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.attachments.error'))
    } finally {
      if (mountedRef.current) setUploading(false)
    }
  }, [streaming, t, uploading])

  const removeAttachment = async (attachment: AiAttachment) => {
    try {
      await aiApi.deleteAttachment(attachment.id)
      setAttachments((current) => current.filter((item) => item.id !== attachment.id))
    } catch {
      toast.error(t('ai.attachments.error'))
    }
  }

  const clearHistory = async () => {
    if (streaming) return
    const accepted = await confirm({
      title: t('ai.chat.clearTitle'),
      message: t('ai.chat.clearConfirm'),
      confirmText: t('ai.chat.clear'),
      danger: true,
    })
    if (!accepted) return
    try {
      await aiApi.clearHistory()
      setEntries([])
      setAttachments([])
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.delete'))
    }
  }

  const send = async (event: React.FormEvent) => {
    event.preventDefault()
    const content = input.trim()
    if (!content || !providerId || streaming) return
    setInput('')
    await sendContent(content)
  }

  /**
   * Nimmt eine bereits gesendete eigene Nachricht zurück und stellt sie neu.
   *
   * Zwei Schritte, weil sie zwei verschiedene Dinge sind: der Schnitt räumt
   * den Verlauf ab dieser Nachricht auf — sie selbst eingeschlossen —, und
   * erst danach geht der neue Text den gewohnten Weg. Die KI sieht von der
   * alten Fassung nichts mehr; sie steht weder im Verlauf noch im Kontext.
   */
  const submitEdit = async (messageId: string) => {
    const content = editDraft.trim()
    if (!content || !providerId || streaming) return
    try {
      await aiApi.editMessage(messageId, content)
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.edit'))
      return
    }
    // Erst wenn der Schnitt durch ist: sonst stünde die alte Fassung noch da,
    // während der Server sie schon nicht mehr kennt.
    setEntries((current) => {
      const index = current.findIndex((item) => item.kind === 'message' && item.id === messageId)
      return index === -1 ? current : current.slice(0, index)
    })
    setEditingId(null)
    setEditDraft('')
    await sendContent(content)
  }

  /** Aendert genau eine Nachricht im Verlauf. */
  const aendere = useCallback((id: string, update: (message: AiMessage) => AiMessage) => {
    setEntries((current) => current.map((entry) => (
      entry.kind === 'message' && entry.id === id
        ? { ...entry, message: update(entry.message) }
        : entry
    )))
  }, [])

  const merkeVorschlag = useCallback((proposal: AiActionProposal) => {
    setEntries((current) => (
      current.some((entry) => entry.kind === 'proposal' && entry.id === proposal.id)
        ? current.map((entry) => (
            entry.kind === 'proposal' && entry.id === proposal.id
              ? { ...entry, proposal }
              : entry
          ))
        : [...current, { kind: 'proposal', id: proposal.id, proposal }]
    ))
  }, [])

  /**
   * Baut den Ereignisverarbeiter eines Laufs.
   *
   * Bewusst **einer** fuer beide Wege — frisch gesendet und nachtraeglich
   * angehaengt. Zwei Verarbeiter waeren zwei Wahrheiten darueber, wie ein Lauf
   * aussieht, und genau daran bricht so etwas spaeter.
   *
   * `optimistischeId` ist die Blase, die beim Senden schon steht, bevor der
   * Server seine eigene ID vergeben hat. Beim Anhaengen gibt es sie nicht.
   */
  const machVerarbeiter = useCallback((
    optimistischeId: string | null,
    optimistischeBenutzerId: string | null = null,
  ) => {
    let aktuell: string | null = optimistischeId
    let offeneOptimistische = optimistischeId !== null
    let offeneBenutzerblase = optimistischeBenutzerId
    let gescheitert = false

    const verarbeite = ({ event: name, data }: AiStreamEvent) => {
      if (!mountedRef.current) return
      if (name === 'snapshot') {
        setRunId(data.run_id)
        // Der Abzug **ersetzt** den Stand, er ergaenzt ihn nicht: er ist die
        // vollstaendige Antwort bis hierher. Alles anzuhaengen wuerde den Text
        // verdoppeln, wenn man sich waehrend des Schreibens wieder anhaengt.
        if (data.message_id) {
          const laeuft = !RUHT.includes(data.status)
          const id = data.message_id
          setEntries((current) => {
            const vorhanden = current.some(
              (entry) => entry.kind === 'message' && entry.id === id,
            )
            const gesetzt = (message: AiMessage): AiMessage => ({
              ...message,
              content: data.content,
              reasoning: data.reasoning || null,
              question: data.question,
              status: laeuft ? 'streaming' : 'complete',
            })
            if (vorhanden) {
              return current.map((entry) => (
                entry.kind === 'message' && entry.id === id
                  ? { ...entry, message: gesetzt(entry.message) }
                  : entry
              ))
            }
            return [...current, {
              kind: 'message',
              id,
              message: gesetzt({
                id, role: 'assistant', content: '', reasoning: null, question: null,
                status: 'streaming', provider_id: providerId, model: null,
                created_at: new Date().toISOString(),
              }),
            }]
          })
          aktuell = id
          offeneOptimistische = false
        }
        // Werkzeugspuren ueberleben kein Neuladen — der Abzug bringt sie zurueck.
        data.tools.forEach((tool, index) => {
          const id = `run-${data.run_id}-tool-${index}`
          setEntries((current) => (
            current.some((entry) => entry.kind === 'tool' && entry.id === id)
              ? current
              : insertBeforeStreaming(current, { kind: 'tool', id, tool })
          ))
        })
        data.proposals.forEach(merkeVorschlag)
        return
      }
      if (name === 'run') {
        setRunId(RUHT.includes(data.status) ? null : data.run_id)
        if (RUHT.includes(data.status)) setStreaming(false)
        return
      }
      if (name === 'segment') {
        // Eine Fortsetzung schreibt eine **neue** Nachricht. Die naechste
        // `message` legt sie an; hier wird nur die alte losgelassen.
        aktuell = null
        return
      }
      if (name === 'message') {
        // Die Benutzerblase steht optimistisch mit einer erfundenen ID da.
        // Sie hier zu berichtigen ist keine Kosmetik: die Anhaenge dieser Frage
        // sind serverseitig an die **echte** Nachricht gebunden und fanden ihre
        // Blase sonst nie.
        if (offeneBenutzerblase && data.user_message_id) {
          const echteId = data.user_message_id
          const alteId = offeneBenutzerblase
          setEntries((current) => current.map((entry) => (
            entry.kind === 'message' && entry.id === alteId
              ? { ...entry, id: echteId, message: { ...entry.message, id: echteId } }
              : entry
          )))
          offeneBenutzerblase = null
          // Jetzt tragen die Anhaenge eine Nachricht — nachladen, damit sie aus
          // der Chipleiste in ihre Blase wandern.
          if (canAttach) {
            void aiApi.listAttachments()
              .then((rows) => { if (mountedRef.current) setAttachments(rows) })
              .catch(() => undefined)
          }
        }
        if (offeneOptimistische && optimistischeId) {
          // Ab hier kennt der Server die Nachricht unter seiner eigenen ID.
          const neueId = data.message_id
          setEntries((current) => current.map((entry) => (
            entry.kind === 'message' && entry.id === optimistischeId
              ? { ...entry, id: neueId, message: { ...entry.message, id: neueId } }
              : entry
          )))
          offeneOptimistische = false
        } else {
          const id = data.message_id
          setEntries((current) => (
            current.some((entry) => entry.kind === 'message' && entry.id === id)
              ? current
              : [...current, {
                  kind: 'message',
                  id,
                  message: {
                    id, role: 'assistant', content: '', reasoning: null, question: null,
                    status: 'streaming', provider_id: providerId, model: null,
                    created_at: new Date().toISOString(),
                  },
                }]
          ))
        }
        aktuell = data.message_id
        return
      }
      if (!aktuell && (name === 'delta' || name === 'reasoning' || name === 'question' || name === 'done')) {
        return
      }
      if (name === 'delta') {
        aendere(aktuell!, (message) => ({ ...message, content: message.content + data.content }))
      } else if (name === 'reasoning') {
        aendere(aktuell!, (message) => ({
          ...message, reasoning: (message.reasoning ?? '') + data.content,
        }))
      } else if (name === 'question') {
        // Die Frage gehoert an die Antwort, nicht neben sie. Als eigener
        // Eintrag stand sie frueher VOR der noch leeren Assistentenblase,
        // unter der dann "Keine Antwort erhalten" erschien.
        aendere(aktuell!, (message) => ({ ...message, question: data }))
      } else if (name === 'done') {
        aendere(aktuell!, (message) => ({ ...message, status: 'complete' }))
      } else if (name === 'tool') {
        setEntries((current) => insertBeforeStreaming(current, {
          kind: 'tool', id: `${data.tool_name}-${current.length}`, tool: data,
        }))
      } else if (name === 'compacted') {
        // Die Marke gehoert an den Anfang: sie beschreibt, was *vorher* war.
        setEntries((current) => [
          { kind: 'compacted', id: `compacted-${data.conversation_id}` },
          ...current.filter((entry) => entry.kind !== 'compacted'),
        ])
      } else if (name === 'proposal' || name === 'action') {
        merkeVorschlag(data)
      } else if (name === 'error') {
        gescheitert = true
        // Der stabile Code sagt konkret, was fehlt (falscher Key, falsches
        // Modell, falsche Basis-URL). Der allgemeine `message_key` bleibt
        // nur der Rueckfall fuer Codes ohne eigenen Text.
        toast.error(t(`ai.errors.codes.${data.code}`, {
          defaultValue: t(data.message_key, { defaultValue: t('ai.chat.errors.stream') }),
        }))
      }
    }
    return { verarbeite, istGescheitert: () => gescheitert }
  }, [aendere, canAttach, merkeVorschlag, providerId, t])

  /**
   * Verfolgt einen Lauf, bis er ruht — oder bis der Benutzer weggeht.
   *
   * Geht er weg, bricht **nur die Anzeige** ab. Der Lauf arbeitet auf dem
   * Server weiter; genau das war vorher nicht so.
   */
  const verfolge = useCallback(async (
    beginne: (verarbeite: (event: AiStreamEvent) => void, signal: AbortSignal) => Promise<void>,
    optimistischeId: string | null,
    optimistischeBenutzerId: string | null = null,
  ) => {
    const controller = new AbortController()
    abortRef.current = controller
    const { verarbeite, istGescheitert } = machVerarbeiter(optimistischeId, optimistischeBenutzerId)
    let abgebrochen = false
    try {
      await beginne(verarbeite, controller.signal)
    } catch (error: unknown) {
      if (controller.signal.aborted) {
        abgebrochen = true
      } else {
        toast.error(error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.stream'))
        setEntries((current) => current.map((entry) => (
          entry.kind === 'message' && entry.message.status === 'streaming'
            ? { ...entry, message: { ...entry.message, status: 'failed' } }
            : entry
        )))
      }
    } finally {
      abortRef.current = null
      if (mountedRef.current && !abgebrochen) {
        setStreaming(false)
        if (istGescheitert()) {
          setEntries((current) => current.map((entry) => (
            entry.kind === 'message' && entry.message.status === 'streaming'
              ? { ...entry, message: { ...entry.message, status: 'failed' } }
              : entry
          )))
        }
      }
    }
  }, [machVerarbeiter, t])

  /** Haengt sich an einen Lauf, der schon arbeitet. */
  const haengeAn = useCallback(async (id: string) => {
    if (streamingRef.current) return
    setStreaming(true)
    streamingRef.current = true
    try {
      await verfolge(
        (verarbeite, signal) => attachAiRun(id, verarbeite, signal),
        null,
      )
    } finally {
      streamingRef.current = false
    }
  }, [verfolge])

  const sendContent = async (content: string) => {
    if (!content || !providerId || streaming) return

    const now = new Date().toISOString()
    const assistantId = crypto.randomUUID()
    const optimisticUser: AiMessage = {
      id: crypto.randomUUID(), role: 'user', content, reasoning: null, question: null,
      status: 'complete', provider_id: null, model: null, created_at: now,
    }
    const optimisticAssistant: AiMessage = {
      id: assistantId, role: 'assistant', content: '', reasoning: null, question: null,
      status: 'streaming', provider_id: providerId, model: null, created_at: now,
    }
    setEntries((current) => [
      ...current,
      { kind: 'message', id: optimisticUser.id, message: optimisticUser },
      { kind: 'message', id: assistantId, message: optimisticAssistant },
    ])
    setStreaming(true)
    streamingRef.current = true
    try {
      await verfolge(
        (verarbeite, signal) => streamAiMessage({
          content,
          provider_id: providerId,
          request_id: crypto.randomUUID(),
          reasoning: effort !== null,
          reasoning_effort: effort,
        }, verarbeite, signal),
        assistantId,
        optimisticUser.id,
      )
    } finally {
      streamingRef.current = false
    }
  }

  /**
   * Beim Oeffnen an einen laufenden Lauf anhaengen.
   *
   * Das ist die andere Haelfte von "der Lauf haengt an nichts": er arbeitet
   * weiter, waehrend man woanders ist — und wenn man zurueckkommt, sieht man
   * ihn wieder. Ohne das stuende hier eine abgebrochene Antwort.
   */
  useEffect(() => {
    if (!laufBeimOeffnen) return
    setLaufBeimOeffnen(null)
    if (!laufBeimOeffnen.live) return
    setRunId(laufBeimOeffnen.id)
    void haengeAn(laufBeimOeffnen.id)
  }, [haengeAn, laufBeimOeffnen])

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center" aria-label={t('common.loading')}>
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    )
  }

  const busy = streaming || uploading
  const empty = entries.length === 0

  return (
    <section
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      aria-label={t('ai.chat.title')}
      onDragEnter={(event) => {
        if (!canAttach || busy) return
        event.preventDefault()
        dragDepthRef.current += 1
        setDragging(true)
      }}
      onDragOver={(event) => { if (canAttach && !busy) event.preventDefault() }}
      onDragLeave={() => {
        // Zaehler statt Boolean: das Verlassen eines Kindelements feuert
        // ebenfalls `dragleave` und wuerde die Flaeche sonst flackern lassen.
        dragDepthRef.current = Math.max(0, dragDepthRef.current - 1)
        if (dragDepthRef.current === 0) setDragging(false)
      }}
      onDrop={(event) => {
        if (!canAttach || busy) return
        event.preventDefault()
        dragDepthRef.current = 0
        setDragging(false)
        void uploadAttachment(event.dataTransfer.files?.[0])
      }}
    >
      {/* ── Kopfzeile: Provider, Denkschritte, Autonomie, Skills ───────── */}
      <header className="flex flex-wrap items-center gap-2 border-b border-outline-variant/40 px-3 py-2 sm:px-4">
        <div className="min-w-[10rem] max-w-[16rem] flex-1">
          <Dropdown
            value={providerId ? String(providerId) : null}
            onChange={(value) => setProviderId(Number(value))}
            options={availableProviders.map((provider) => ({
              value: String(provider.id),
              label: provider.name,
              hint: provider.default_model,
            }))}
            placeholder={t('ai.chat.selectProvider')}
            disabled={busy}
            aria-label={t('ai.chat.selectProvider')}
          />
        </div>

        <ReasoningPicker
          provider={aktiverProvider}
          value={effort}
          onChange={setEffort}
          disabled={busy}
        />

        {canUseAutonomy && <AiAutonomyButton servers={servers} disabled={busy} />}

        <div className="ml-auto flex items-center gap-2">
          <Button
            type="button" variant="ghost" size="sm"
            disabled={busy || empty}
            onClick={() => void clearHistory()}
            aria-label={t('ai.chat.clear')}
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </header>

      {/* ── Verlauf ───────────────────────────────────────────────────── */}
      <div className="relative min-h-0 flex-1 overflow-y-auto" aria-live="polite">
        {dragging && (
          <div className="pointer-events-none absolute inset-3 z-10 flex items-center justify-center rounded-2xl border-2 border-dashed border-primary/60 bg-primary/5">
            <span className="flex items-center gap-2 text-sm font-medium text-primary">
              <Paperclip className="h-4 w-4" aria-hidden="true" />
              {t('ai.attachments.drop')}
            </span>
          </div>
        )}

        <div className="mx-auto w-full max-w-3xl px-3 py-6 sm:px-4">
          {empty && (
            <div className="py-16 text-center">
              <Sparkles className="mx-auto h-10 w-10 text-primary/70" aria-hidden="true" />
              <h2 className="mt-4 font-headline text-xl font-semibold text-on-surface">
                {t('ai.chat.emptyTitle')}
              </h2>
              <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-on-surface-variant">
                {t('ai.chat.emptyDescription')}
              </p>
              <ul className="mx-auto mt-6 grid max-w-lg gap-2 text-left">
                {['logs', 'network', 'mods'].map((key) => (
                  <li key={key}>
                    <button
                      type="button"
                      className="w-full rounded-xl border border-outline-variant/40 bg-surface-container-low/40 px-4 py-3 text-sm text-on-surface-variant transition-colors hover:border-primary/40 hover:text-on-surface"
                      onClick={() => setInput(t(`ai.chat.examples.${key}`))}
                    >
                      {t(`ai.chat.examples.${key}`)}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="space-y-4">
            {entries.map((entry, index) => {
              if (entry.kind === 'tool') {
                const isMemory = entry.tool.tool_name === 'remember'
                const skillKey = entry.tool.skill_key
                // Ein Skill bekommt seinen Namen in den Verlauf, nicht den
                // Werkzeugnamen: "Skill *Valheim braucht 6 GB* gelernt" sagt
                // etwas, "learn_skill" sagt nichts.
                const skillLabel = skillKey
                  ? t(
                      entry.tool.skill_learned
                        ? (entry.tool.skill_status === 'pending'
                            ? 'ai.skills.learnedPending'
                            : 'ai.skills.learned')
                        : 'ai.skills.used',
                      { name: entry.tool.skill_name || skillKey },
                    )
                  : null
                return (
                  <p
                    key={entry.id}
                    className="flex items-center gap-2 text-xs text-on-surface-variant"
                  >
                    {skillKey
                      ? <Sparkles className="h-3.5 w-3.5 shrink-0 text-tertiary" aria-hidden="true" />
                      : isMemory
                        ? <BrainCircuit className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
                        : <Wrench className="h-3.5 w-3.5 shrink-0 text-secondary" aria-hidden="true" />}
                    {skillLabel
                      ?? t(`ai.tools.${entry.tool.tool_name}`, { defaultValue: entry.tool.tool_name })}
                  </p>
                )
              }
              if (entry.kind === 'compacted') {
                return (
                  <div key={entry.id} className="flex items-center gap-3 py-2">
                    <span className="h-px flex-1 bg-outline-variant/40" />
                    <span className="text-xs text-on-surface-variant">{t('ai.chat.compacted')}</span>
                    <span className="h-px flex-1 bg-outline-variant/40" />
                  </div>
                )
              }
              if (entry.kind === 'proposal') {
                return (
                  <AiActionProposalCard
                    key={entry.id}
                    proposal={entry.proposal}
                    onChange={(updated) => {
                      merkeVorschlag(updated)
                      // **Hier ging es frueher nicht weiter.** Die Aktion lief,
                      // und der Chat blieb stumm — man musste eine neue
                      // Nachricht schreiben, damit die KI ueberhaupt erfuhr,
                      // wie ihr eigener Vorschlag ausgegangen ist.
                      const lauf = updated.run_id ?? runId
                      if (lauf && updated.status !== 'proposed') {
                        void haengeAn(lauf)
                      }
                    }}
                  />
                )
              }

              const { message } = entry
              if (message.role === 'user') {
                const isEditing = editingId === message.id
                return (
                  <article key={entry.id} className="group flex justify-end gap-3">
                    {isEditing ? (
                      <div className="w-full max-w-[85%] space-y-2">
                        <textarea
                          className="msm-input min-h-[4.5rem] w-full text-sm"
                          value={editDraft}
                          maxLength={16_000}
                          autoFocus
                          disabled={busy}
                          onChange={(event) => setEditDraft(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === 'Escape') { setEditingId(null); setEditDraft('') }
                            if (event.key === 'Enter' && !event.shiftKey) {
                              event.preventDefault()
                              void submitEdit(message.id)
                            }
                          }}
                          aria-label={t('ai.chat.edit')}
                        />
                        {/* Der Hinweis gehoert hierher, nicht in eine Rueckfrage
                            danach: wer bearbeitet, soll vorher wissen, dass der
                            Verlauf ab hier verschwindet. */}
                        <p className="text-xs text-on-surface-variant">{t('ai.chat.editHint')}</p>
                        <div className="flex justify-end gap-2">
                          <Button
                            type="button" size="sm" variant="secondary" disabled={busy}
                            onClick={() => { setEditingId(null); setEditDraft('') }}
                          >
                            <X className="h-4 w-4" aria-hidden="true" />
                            {t('common.cancel')}
                          </Button>
                          <Button
                            type="button" size="sm"
                            disabled={busy || !editDraft.trim()}
                            onClick={() => void submitEdit(message.id)}
                          >
                            <Check className="h-4 w-4" aria-hidden="true" />
                            {t('ai.chat.editSend')}
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => { setEditingId(message.id); setEditDraft(message.content) }}
                          className="mt-1 self-start rounded-lg p-1.5 text-on-surface-variant opacity-0 transition-opacity hover:text-on-surface focus-visible:opacity-100 group-hover:opacity-100 disabled:opacity-0"
                          aria-label={t('ai.chat.edit')}
                        >
                          <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                        </button>
                        <div className="max-w-[85%] rounded-2xl rounded-br-md border border-primary/25 bg-primary/10 px-4 py-2.5">
                          <p className="whitespace-pre-wrap break-words text-sm leading-6 text-on-surface">
                            {message.content}
                          </p>
                          {/* Die Anhaenge stehen **in** der Nachricht, mit der
                              sie gesendet wurden. Vorher hingen sie nur an der
                              Unterhaltung: nach einem Neuladen war nicht mehr
                              erkennbar, zu welcher Frage sie gehoerten. */}
                          <AnhangListe
                            anhaenge={anhaengeJeNachricht.get(message.id) ?? []}
                            t={t}
                          />
                        </div>
                      </>
                    )}
                    <span className="mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-surface-container-high text-on-surface-variant">
                      <User className="h-3.5 w-3.5" aria-hidden="true" />
                    </span>
                  </article>
                )
              }

              const isStreaming = message.status === 'streaming'
              return (
                <article key={entry.id} className="flex gap-3">
                  <span className="mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-primary/10 text-primary">
                    <Bot className="h-3.5 w-3.5" aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    {message.reasoning && (
                      <AiReasoningBlock content={message.reasoning} streaming={isStreaming} />
                    )}
                    {message.content ? (
                      <AiMarkdown content={message.content} />
                    ) : isStreaming ? (
                      <p className="flex items-center gap-2 text-sm text-on-surface-variant">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                        {t('ai.chat.thinking')}
                      </p>
                    ) : message.question ? null : (
                      // Eine Rueckfrage *ist* die Antwort. Frueher stand hier
                      // "Keine Antwort erhalten" unter jeder gestellten Frage,
                      // weil die Frage in einer eigenen Karte lag und der
                      // Nachrichtentext leer blieb.
                      <p className="text-sm text-on-surface-variant">{t('ai.chat.noResponse')}</p>
                    )}
                    {/* Die Rueckfrage gehoert in dieselbe Blase wie der Text
                        davor — sie ist Teil dieser Antwort und keine neue
                        Nachricht. Beantwortet ist sie, sobald irgendein
                        spaeterer Eintrag existiert: dann hat der Benutzer
                        geschrieben, ob per Knopf oder frei getippt. Damit
                        ueberlebt der Zustand auch ein Neuladen der Seite, ohne
                        dass er irgendwo gespeichert werden muesste. */}
                    {message.question && (
                      <AiQuestionCard
                        question={message.question}
                        answered={index < entries.length - 1}
                        disabled={busy}
                        onAnswer={(label) => void sendContent(label)}
                      />
                    )}
                    {message.status === 'failed' && (
                      <p className="mt-2 text-xs text-status-error">{t('ai.chat.failed')}</p>
                    )}
                  </div>
                </article>
              )
            })}
          </div>
          <div ref={endRef} />
        </div>
      </div>

      {/* ── Eingabe ───────────────────────────────────────────────────── */}
      <form className="border-t border-outline-variant/40 px-3 py-3 sm:px-4" onSubmit={send}>
        {/* Der Hinweis steht ueber dem Eingabefeld, nicht in einer
            Einstellungsseite: er soll dort auftauchen, wo die Entscheidung
            Folgen hat — bevor jemand etwas Persoenliches tippt. */}
        {memoryNoticeDue && (
          <AiMemoryNotice onAnswered={() => setMemoryNoticeDue(false)} />
        )}
        <div className="mx-auto w-full max-w-3xl">
          {/* Nur Ungesendetes: alles Uebrige steht in seiner Nachricht. Vorher
              blieb jeder Anhang hier stehen und ging bei jeder Folgefrage
              erneut an den Anbieter. */}
          {offeneAnhaenge.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2" aria-label={t('ai.attachments.list')}>
              {offeneAnhaenge.map((attachment) => (
                <span
                  key={attachment.id}
                  className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-outline-variant/40 bg-surface-container-high px-2.5 py-1 text-xs text-on-surface-variant"
                >
                  <Paperclip className="h-3 w-3 shrink-0" aria-hidden="true" />
                  <span className="truncate">{attachment.original_name}</span>
                  <button
                    type="button"
                    className="rounded-sm p-0.5 hover:bg-surface-container-highest focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    disabled={busy}
                    onClick={() => void removeAttachment(attachment)}
                    aria-label={t('ai.attachments.remove')}
                  >
                    <X className="h-3 w-3" aria-hidden="true" />
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className="flex items-end gap-2 rounded-2xl border border-outline-variant/50 bg-surface-container-low/50 p-2 focus-within:border-primary/50">
            {canAttach && (
              <label
                className={`grid h-9 w-9 shrink-0 place-items-center rounded-full text-on-surface-variant transition-colors ${
                  busy ? 'pointer-events-none opacity-50' : 'cursor-pointer hover:bg-surface-container-high hover:text-on-surface'
                }`}
                title={t('ai.attachments.add')}
              >
                {uploading
                  ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  : <Paperclip className="h-4 w-4" aria-hidden="true" />}
                <input
                  type="file" className="sr-only" disabled={busy} accept={ATTACHMENT_ACCEPT}
                  aria-label={t('ai.attachments.add')}
                  onChange={(event) => {
                    void uploadAttachment(event.target.files?.[0])
                    event.target.value = ''
                  }}
                />
              </label>
            )}
            <textarea
              className="max-h-40 min-h-9 flex-1 resize-none border-0 bg-transparent py-1.5 text-sm leading-6 text-on-surface placeholder:text-on-surface-variant focus:outline-none"
              rows={1}
              maxLength={16000}
              value={input}
              onChange={(event) => {
                setInput(event.target.value)
                // Waechst mit dem Text, wie man es von einem Chat kennt.
                event.target.style.height = 'auto'
                event.target.style.height = `${Math.min(event.target.scrollHeight, 160)}px`
              }}
              onKeyDown={(event) => {
                // Enter sendet, Shift+Enter macht einen Umbruch. Bei aktiver
                // IME-Komposition darf Enter nichts ausloesen, sonst schickt
                // eine Zeichenauswahl die halbe Nachricht ab.
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  void send(event as unknown as React.FormEvent)
                }
              }}
              placeholder={t('ai.chat.placeholder')}
              disabled={busy || availableProviders.length === 0}
              aria-label={t('ai.chat.message')}
            />
            <Button
              type="submit"
              size="sm"
              className="h-9 w-9 shrink-0 rounded-full p-0"
              disabled={busy || !input.trim() || !providerId}
              aria-label={t('ai.chat.send')}
            >
              {streaming
                ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                : <Send className="h-4 w-4" aria-hidden="true" />}
            </Button>
          </div>

          {availableProviders.length === 0 && (
            <p className="mt-2 flex items-center gap-1.5 text-xs text-status-warning">
              <Zap className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {t('ai.chat.noProvider')}
            </p>
          )}
          <p className="mt-2 text-center text-xs text-on-surface-variant">{t('ai.chat.privacyHint')}</p>
        </div>
      </form>
    </section>
  )
}

/**
 * Sortiert Vorschlaege chronologisch zwischen die Nachrichten.
 *
 * Beide Listen sind bereits nach `created_at` sortiert; hier werden sie nur
 * zusammengefuehrt. Ein Vorschlag steht damit dort, wo er entstanden ist.
 */
/**
 * Die Anhänge einer Nachricht, unter ihrem Text.
 *
 * Zeigt auch, wenn beim Aufnehmen etwas unkenntlich gemacht wurde. Vorher wurde
 * eine Datei mit einem Tokenmuster komplett abgewiesen — bei echten Serverlogs
 * passiert das ständig. Jetzt wird redigiert, und der Hinweis hier ist der
 * Grund, warum niemand sich über ein `[REDACTED]` im eigenen Log wundern muss.
 */
/**
 * Die Wahl der Denktiefe — je Modell verschieden, deshalb keine feste Liste.
 *
 * Gemessen am 2026-08-11 ueber alle 402 Modelle im OpenRouter-Katalog gibt es
 * genau vier Faelle, und jeder sieht hier anders aus:
 *
 * 1. **Modell denkt nicht** (130) → gar keine Anzeige. Ein Regler, der nichts
 *    bewirkt, ist schlimmer als keiner.
 * 2. **Stufen** (127, in 20 verschiedenen Zusammenstellungen) → eine Auswahl
 *    aus genau diesen Stufen. Deshalb kommt die Liste aus dem Katalog und nicht
 *    aus einer Konstante: „xhigh" gibt es bei einem Modell und beim naechsten
 *    nicht.
 * 3. **Nur an/aus** (145) → ein Schalter, wie bisher. Das ist die Mehrheit der
 *    denkenden Modelle.
 * 4. **Nicht abschaltbar** (82) → „aus" fehlt in der Auswahl. Der Anbieter
 *    denkt ohnehin und rechnet es ab; ein Aus-Knopf waere gelogen.
 *
 * Die Stufen sind bereits auf die Rolle des Benutzers geklemmt — das erledigt
 * der Server in `ai_reasoning.waehlbare_stufen`. Hier wird nichts entschieden,
 * nur angezeigt.
 */
function ReasoningPicker({ provider, value, onChange, disabled }: {
  provider: AiProviderAvailable | null
  value: string | null
  onChange: (value: string | null) => void
  disabled: boolean
}) {
  const { t } = useTranslation()
  if (!provider?.reasoning) return null

  const rahmen = 'flex items-center gap-2 rounded-full border border-outline-variant/40 px-3 py-1.5 text-xs text-on-surface-variant'

  // Kein Stufenwissen: derselbe Schalter wie bisher — fuer 145 der 272
  // denkenden Modelle ist das die einzige Wahl, die es ueberhaupt gibt.
  if (provider.efforts.length === 0) {
    return (
      <label className={rahmen}>
        <Brain className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="hidden sm:inline">{t('ai.chat.reasoning')}</span>
        <Switch
          checked={value !== null}
          disabled={disabled || !provider.can_disable}
          onCheckedChange={(an) => onChange(an ? (provider.default_effort ?? '') : null)}
          aria-label={t('ai.chat.reasoning')}
        />
      </label>
    )
  }

  return (
    <label className={rahmen}>
      <Brain className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span className="sr-only">{t('ai.chat.reasoningLevel')}</span>
      <select
        className="bg-transparent text-xs text-on-surface outline-none"
        value={value ?? ''}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value === '' ? null : event.target.value)}
        aria-label={t('ai.chat.reasoningLevel')}
      >
        {provider.can_disable && <option value="">{t('ai.reasoning.off')}</option>}
        {provider.efforts.map((stufe) => (
          <option key={stufe} value={stufe}>
            {t(`ai.reasoning.levels.${stufe}`, { defaultValue: stufe })}
          </option>
        ))}
      </select>
    </label>
  )
}

function AnhangListe({ anhaenge, t }: { anhaenge: AiAttachment[]; t: TFunction }) {
  if (anhaenge.length === 0) return null
  return (
    <ul className="mt-2 space-y-1 border-t border-primary/20 pt-2">
      {anhaenge.map((anhang) => (
        <li key={anhang.id} className="flex items-center gap-1.5 text-xs text-on-surface-variant">
          <Paperclip className="h-3 w-3 shrink-0" aria-hidden="true" />
          <span className="truncate">{anhang.original_name}</span>
          {anhang.redacted_spans ? (
            <span className="shrink-0 rounded-full border border-outline-variant/50 px-1.5 py-0.5 text-[10px]">
              {t('ai.attachments.redacted', { count: anhang.redacted_spans })}
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  )
}

function mergeEntries(messages: AiMessage[], proposals: AiActionProposal[]): Entry[] {
  const merged: Entry[] = [
    ...messages.map((message) => ({ kind: 'message' as const, id: message.id, message })),
    ...proposals.map((proposal) => ({ kind: 'proposal' as const, id: proposal.id, proposal })),
  ]
  return merged.sort((a, b) => {
    const left = entryTimestamp(a)
    const right = entryTimestamp(b)
    return left.localeCompare(right)
  })
}

/** Zeitstempel eines Eintrags; typlose Marken sortieren an den Anfang. */
function entryTimestamp(entry: Entry): string {
  if (entry.kind === 'message') return entry.message.created_at
  if (entry.kind === 'proposal') return entry.proposal.created_at
  return ''
}

/** Haengt einen Werkzeugeintrag vor die noch streamende Antwort. */
function insertBeforeStreaming(entries: Entry[], entry: Entry): Entry[] {
  const index = entries.findIndex(
    (item) => item.kind === 'message' && item.message.status === 'streaming' && item.message.role === 'assistant',
  )
  if (index < 0) return [...entries, entry]
  return [...entries.slice(0, index), entry, ...entries.slice(index)]
}
