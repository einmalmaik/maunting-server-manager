import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Bot, Brain, BrainCircuit, Loader2, Paperclip, Play, Send, Sparkles, Trash2, User, Wrench, X, Zap } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  aiApi,
  latestAiSkillVersions,
  streamAiMessage,
  type AiActionProposal,
  type AiAttachment,
  type AiMessage,
  type AiProviderAvailable,
  type AiSkill,
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
  const canUseSkills = useHasPermission('ai.skills.use')
  const canUseAutonomy = useHasPermission('ai.autonomous.use')
  const canUseMemory = useHasPermission('ai.memory.use')

  const [providers, setProviders] = useState<AiProviderAvailable[]>([])
  const [providerId, setProviderId] = useState<number | null>(null)
  const [entries, setEntries] = useState<Entry[]>([])
  const [attachments, setAttachments] = useState<AiAttachment[]>([])
  const [skills, setSkills] = useState<AiSkill[]>([])
  const [skillId, setSkillId] = useState<string | null>(null)
  const [servers, setServers] = useState<ServerOption[]>([])
  const [skillServerId, setSkillServerId] = useState<string | null>(null)
  const [reasoning, setReasoning] = useState(false)
  // Ob der Einwilligungshinweis faellig ist. Die 24-Stunden-Regel entscheidet
  // das Backend — hier steht nur das Ergebnis.
  const [memoryNoticeDue, setMemoryNoticeDue] = useState(false)
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [streaming, setStreaming] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [dragging, setDragging] = useState(false)

  const abortRef = useRef<AbortController | null>(null)
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
      canUseSkills ? aiApi.listSkills() : Promise.resolve([] as AiSkill[]),
      canUseSkills
        ? api<ServerOption[]>('/servers').catch(() => [] as ServerOption[])
        : Promise.resolve([] as ServerOption[]),
      // Scheitert der Abruf, wird der Hinweis nicht gezeigt statt den ganzen
      // Chat scheitern zu lassen — er ist wichtig, aber nicht so wichtig.
      canUseMemory
        ? aiApi.getMemoryPreference().catch(() => null)
        : Promise.resolve(null),
    ])
      .then(([providerRows, conversation, actions, attachmentRows, skillRows, serverRows, memoryPreference]) => {
        if (!active) return
        setMemoryNoticeDue(Boolean(memoryPreference?.notice_due))
        setProviders(providerRows)
        setProviderId(providerRows.find((item) => item.available)?.id ?? null)
        // Vorschlaege werden chronologisch zwischen die Nachrichten einsortiert,
        // damit man sieht, auf welche Antwort sie sich beziehen. Vorher standen
        // sie gesammelt am Ende und wirkten losgeloest.
        setEntries(mergeEntries(conversation.messages, actions))
        setAttachments(attachmentRows)
        const latest = latestAiSkillVersions(skillRows)
        setSkills(latest)
        setSkillId(latest[0]?.id ?? null)
        setServers(serverRows.map((row) => ({ id: row.id, name: row.name })))
        setSkillServerId(serverRows[0] ? String(serverRows[0].id) : null)
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
  }, [canAttach, canUseSkills, canUseMemory, t])

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [entries])

  const availableProviders = useMemo(
    () => providers.filter((provider) => provider.available),
    [providers],
  )

  const uploadAttachment = useCallback(async (file: File | undefined) => {
    if (!file || streaming || uploading) return
    setUploading(true)
    try {
      const created = await aiApi.uploadAttachment(file)
      if (mountedRef.current) setAttachments((current) => [...current, created])
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

  const runSkill = async () => {
    if (!skillId || !skillServerId || streaming) return
    setStreaming(true)
    try {
      const result = await aiApi.runSkill(skillId, Number(skillServerId))
      setEntries((current) => [
        ...current,
        ...result.proposals.map((proposal) => ({
          kind: 'proposal' as const,
          id: proposal.id,
          proposal: proposal as unknown as AiActionProposal,
        })),
      ])
      toast.success(t('ai.skills.runCreated', { count: result.proposals.length }))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.skills.error'))
    } finally {
      setStreaming(false)
    }
  }

  const send = async (event: React.FormEvent) => {
    event.preventDefault()
    const content = input.trim()
    if (!content || !providerId || streaming) return

    const now = new Date().toISOString()
    const assistantId = crypto.randomUUID()
    const optimisticUser: AiMessage = {
      id: crypto.randomUUID(), role: 'user', content, reasoning: null,
      status: 'complete', provider_id: null, model: null, created_at: now,
    }
    const optimisticAssistant: AiMessage = {
      id: assistantId, role: 'assistant', content: '', reasoning: null,
      status: 'streaming', provider_id: providerId, model: null, created_at: now,
    }
    setEntries((current) => [
      ...current,
      { kind: 'message', id: optimisticUser.id, message: optimisticUser },
      { kind: 'message', id: assistantId, message: optimisticAssistant },
    ])
    setInput('')
    setStreaming(true)

    const controller = new AbortController()
    abortRef.current = controller
    let streamFailed = false

    /** Aendert genau die eine, gerade streamende Assistentennachricht. */
    const patchAssistant = (update: (message: AiMessage) => AiMessage) => {
      setEntries((current) => current.map((entry) => (
        entry.kind === 'message' && entry.id === assistantId
          ? { ...entry, message: update(entry.message) }
          : entry
      )))
    }

    try {
      await streamAiMessage({
        content,
        provider_id: providerId,
        request_id: crypto.randomUUID(),
        reasoning,
      }, ({ event: name, data }) => {
        if (!mountedRef.current) return
        if (name === 'message') {
          // Ab hier kennt der Server die Nachricht unter seiner eigenen ID.
          patchAssistant((message) => ({ ...message, id: data.message_id }))
          setEntries((current) => current.map((entry) => (
            entry.kind === 'message' && entry.id === assistantId
              ? { ...entry, id: data.message_id }
              : entry
          )))
        } else if (name === 'delta') {
          patchAssistantById(setEntries, undefined, assistantId, (message) => ({
            ...message, content: message.content + data.content,
          }))
        } else if (name === 'reasoning') {
          patchAssistantById(setEntries, undefined, assistantId, (message) => ({
            ...message, reasoning: (message.reasoning ?? '') + data.content,
          }))
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
        } else if (name === 'done') {
          patchAssistantById(setEntries, undefined, assistantId, (message) => ({
            ...message, status: 'complete',
          }))
        } else if (name === 'proposal' || name === 'action') {
          setEntries((current) => current.some(
            (entry) => entry.kind === 'proposal' && entry.id === data.id,
          ) ? current : [...current, { kind: 'proposal', id: data.id, proposal: data }])
        } else {
          streamFailed = true
          // Der stabile Code sagt konkret, was fehlt (falscher Key, falsches
          // Modell, falsche Basis-URL). Der allgemeine `message_key` bleibt
          // nur der Rueckfall fuer Codes ohne eigenen Text.
          toast.error(t(`ai.errors.codes.${data.code}`, {
            defaultValue: t(data.message_key, { defaultValue: t('ai.chat.errors.stream') }),
          }))
        }
      }, controller.signal)
    } catch (error: unknown) {
      if (!controller.signal.aborted) {
        streamFailed = true
        toast.error(error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.stream'))
      }
    } finally {
      abortRef.current = null
      if (!mountedRef.current) return
      setStreaming(false)
      if (streamFailed) {
        setEntries((current) => current.map((entry) => (
          entry.kind === 'message' && entry.message.status === 'streaming'
            ? { ...entry, message: { ...entry.message, status: 'failed' } }
            : entry
        )))
      }
    }
  }

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

        <label className="flex items-center gap-2 rounded-full border border-outline-variant/40 px-3 py-1.5 text-xs text-on-surface-variant">
          <Brain className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="hidden sm:inline">{t('ai.chat.reasoning')}</span>
          <Switch
            checked={reasoning}
            disabled={busy}
            onCheckedChange={setReasoning}
            aria-label={t('ai.chat.reasoning')}
          />
        </label>

        {canUseAutonomy && <AiAutonomyButton servers={servers} disabled={busy} />}

        <div className="ml-auto flex items-center gap-2">
          {canUseSkills && skills.length > 0 && (
            <>
              <div className="hidden min-w-[9rem] sm:block">
                <Dropdown
                  value={skillId}
                  onChange={setSkillId}
                  options={skills.map((skill) => ({ value: skill.id, label: skill.name, hint: `v${skill.version}` }))}
                  placeholder={t('ai.skills.select')}
                  disabled={busy}
                  aria-label={t('ai.skills.select')}
                />
              </div>
              <div className="hidden min-w-[9rem] md:block">
                <Dropdown
                  value={skillServerId}
                  onChange={setSkillServerId}
                  options={servers.map((server) => ({ value: String(server.id), label: server.name }))}
                  placeholder={t('ai.skills.selectServer')}
                  disabled={busy}
                  aria-label={t('ai.skills.selectServer')}
                />
              </div>
              <Button
                type="button" variant="secondary" size="sm"
                disabled={busy || !skillId || !skillServerId}
                onClick={() => void runSkill()}
              >
                <Play className="h-4 w-4" aria-hidden="true" />
                <span className="hidden sm:inline">{t('ai.skills.run')}</span>
              </Button>
            </>
          )}
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
            {entries.map((entry) => {
              if (entry.kind === 'tool') {
                const isMemory = entry.tool.tool_name === 'remember'
                return (
                  <p
                    key={entry.id}
                    className="flex items-center gap-2 text-xs text-on-surface-variant"
                  >
                    {isMemory
                      ? <BrainCircuit className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
                      : <Wrench className="h-3.5 w-3.5 shrink-0 text-secondary" aria-hidden="true" />}
                    {t(`ai.tools.${entry.tool.tool_name}`, { defaultValue: entry.tool.tool_name })}
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
                    onChange={(updated) => setEntries((current) => current.map((item) => (
                      item.kind === 'proposal' && item.id === updated.id
                        ? { ...item, proposal: updated }
                        : item
                    )))}
                  />
                )
              }

              const { message } = entry
              if (message.role === 'user') {
                return (
                  <article key={entry.id} className="flex justify-end gap-3">
                    <div className="max-w-[85%] rounded-2xl rounded-br-md border border-primary/25 bg-primary/10 px-4 py-2.5">
                      <p className="whitespace-pre-wrap break-words text-sm leading-6 text-on-surface">
                        {message.content}
                      </p>
                    </div>
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
                    ) : (
                      <p className="text-sm text-on-surface-variant">{t('ai.chat.noResponse')}</p>
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
          {attachments.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2" aria-label={t('ai.attachments.list')}>
              {attachments.map((attachment) => (
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

/** Aendert die streamende Nachricht, egal ob sie schon die Server-ID traegt. */
function patchAssistantById(
  setEntries: React.Dispatch<React.SetStateAction<Entry[]>>,
  serverId: string | undefined,
  optimisticId: string,
  update: (message: AiMessage) => AiMessage,
) {
  setEntries((current) => current.map((entry) => (
    entry.kind === 'message'
      && entry.message.role === 'assistant'
      && entry.message.status === 'streaming'
      && (entry.id === optimisticId || entry.id === serverId || serverId === undefined)
      ? { ...entry, message: update(entry.message) }
      : entry
  )))
}

/** Haengt einen Werkzeugeintrag vor die noch streamende Antwort. */
function insertBeforeStreaming(entries: Entry[], entry: Entry): Entry[] {
  const index = entries.findIndex(
    (item) => item.kind === 'message' && item.message.status === 'streaming' && item.message.role === 'assistant',
  )
  if (index < 0) return [...entries, entry]
  return [...entries.slice(0, index), entry, ...entries.slice(index)]
}
