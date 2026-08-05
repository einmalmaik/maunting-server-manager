import { useEffect, useRef, useState } from 'react'
import { Bot, MessageSquarePlus, Paperclip, Play, Send, Trash2, User, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, latestAiSkillVersions, streamAiMessage, type AiActionProposal, type AiAttachment, type AiConversation, type AiMessage, type AiProviderAvailable, type AiSkill } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button, Dropdown } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'
import { AiActionProposalCard } from './AiActionProposalCard'
import { useHasPermission } from '@/hooks/useHasPermission'

export function AiChat({ serverId }: { serverId?: number }) {
  const { t } = useTranslation()
  const canAttach = useHasPermission('ai.attachments.use')
  const canUseSkills = useHasPermission('ai.skills.use')
  const [providers, setProviders] = useState<AiProviderAvailable[]>([])
  const [providerId, setProviderId] = useState<number | null>(null)
  const [conversations, setConversations] = useState<AiConversation[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<AiMessage[]>([])
  const [proposals, setProposals] = useState<AiActionProposal[]>([])
  const [attachments, setAttachments] = useState<AiAttachment[]>([])
  const [skills, setSkills] = useState<AiSkill[]>([])
  const [skillId, setSkillId] = useState<string | null>(null)
  const [skillResults, setSkillResults] = useState<Array<Record<string, unknown>>>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(true)
  const [streaming, setStreaming] = useState(false)
  const [uploading, setUploading] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const endRef = useRef<HTMLDivElement | null>(null)
  const skipConversationLoadRef = useRef<string | null>(null)
  const mountedRef = useRef(true)
  const conversationIdRef = useRef<string | null>(null)

  useEffect(() => { conversationIdRef.current = conversationId }, [conversationId])

  useEffect(() => {
    // StrictMode fuehrt Setup/Cleanup in Entwicklung absichtlich doppelt aus.
    // Der Ref muss deshalb bei jedem Setup erneut aktiv gesetzt werden.
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      abortRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    let active = true
    Promise.all([aiApi.listProviders(), aiApi.listConversations(serverId), canUseSkills && serverId ? aiApi.listSkills() : Promise.resolve([])])
      .then(([providerRows, conversationRows, skillRows]) => {
        if (!active) return
        setProviders(providerRows)
        setProviderId(providerRows.find((item) => item.available)?.id ?? null)
        setConversations(conversationRows)
        setConversationId(conversationRows[0]?.id ?? null)
        const latestSkills = latestAiSkillVersions(skillRows)
        setSkills(latestSkills)
        setSkillId(latestSkills[0]?.id ?? null)
      })
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.load'))
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false; abortRef.current?.abort() }
  }, [canUseSkills, serverId, t])

  useEffect(() => {
    if (!conversationId) {
      setMessages([])
      setProposals([])
      setAttachments([])
      setSkillResults([])
      return
    }
    if (skipConversationLoadRef.current === conversationId) {
      skipConversationLoadRef.current = null
      return
    }
    let active = true
    setMessages([])
    setProposals([])
    setAttachments([])
    setSkillResults([])
    Promise.all([aiApi.getConversation(conversationId), aiApi.listActions(conversationId), canAttach ? aiApi.listAttachments(conversationId) : Promise.resolve([])])
      .then(([detail, actions, attachmentRows]) => { if (active) { setMessages(detail.messages); setProposals(actions); setAttachments(attachmentRows); setSkillResults([]) } })
      .catch((error: unknown) => {
        if (active) toast.error(error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.load'))
      })
    return () => { active = false }
  }, [canAttach, conversationId, t])

  useEffect(() => { endRef.current?.scrollIntoView({ block: 'nearest' }) }, [messages])

  const createConversation = async (): Promise<AiConversation | null> => {
    try {
      const created = await aiApi.createConversation(t('ai.chat.newTitle'), serverId)
      setConversations((current) => [created, ...current])
      skipConversationLoadRef.current = created.id
      setConversationId(created.id)
      setMessages([])
      setProposals([])
      setAttachments([])
      setSkillResults([])
      return created
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.create'))
      return null
    }
  }

  const removeConversation = async () => {
    if (!conversationId || streaming) return
    const accepted = await confirm({ message: t('ai.chat.deleteConfirm'), confirmText: t('common.delete'), danger: true })
    if (!accepted) return
    try {
      await aiApi.deleteConversation(conversationId)
      const remaining = conversations.filter((item) => item.id !== conversationId)
      setConversations(remaining)
      setConversationId(remaining[0]?.id ?? null)
      setMessages([])
      setProposals([])
      setAttachments([])
      setSkillResults([])
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.chat.errors.delete'))
    }
  }

  const uploadAttachment = async (file: File | undefined) => {
    if (!file || !conversationId || streaming || uploading) return
    const targetConversationId = conversationId
    setUploading(true)
    try {
      const created = await aiApi.uploadAttachment(targetConversationId, file)
      if (conversationIdRef.current === targetConversationId) {
        setAttachments((current) => [...current, created])
      }
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.attachments.error'))
    } finally {
      setUploading(false)
    }
  }

  const removeAttachment = async (attachment: AiAttachment) => {
    try {
      await aiApi.deleteAttachment(attachment.id)
      setAttachments((current) => current.filter((item) => item.id !== attachment.id))
    } catch { toast.error(t('ai.attachments.error')) }
  }

  const runSkill = async () => {
    if (!skillId || !conversationId || streaming) return
    setStreaming(true)
    try {
      const result = await aiApi.runSkill(skillId, conversationId)
      setSkillResults(result.read_results)
      setProposals(await aiApi.listActions(conversationId))
      toast.success(t('ai.skills.runCreated', { count: result.proposals.length }))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.skills.error'))
    } finally { setStreaming(false) }
  }

  const send = async (event: React.FormEvent) => {
    event.preventDefault()
    const content = input.trim()
    if (!content || !providerId || streaming) return
    const conversation = conversationId
      ? conversations.find((item) => item.id === conversationId) ?? null
      : await createConversation()
    if (!conversation) return

    const now = new Date().toISOString()
    const optimisticUser: AiMessage = { id: crypto.randomUUID(), role: 'user', content, status: 'complete', provider_id: null, model: null, created_at: now }
    const optimisticAssistant: AiMessage = { id: crypto.randomUUID(), role: 'assistant', content: '', status: 'streaming', provider_id: providerId, model: null, created_at: now }
    setMessages((current) => [...current, optimisticUser, optimisticAssistant])
    setInput('')
    setStreaming(true)
    const controller = new AbortController()
    abortRef.current = controller
    let streamFailed = false
    try {
      await streamAiMessage(conversation.id, {
        content,
        provider_id: providerId,
        request_id: crypto.randomUUID(),
      }, ({ event: eventName, data }) => {
        if (!mountedRef.current) return
        if (eventName === 'message') {
          setMessages((current) => current.map((item) => item.id === optimisticAssistant.id ? { ...item, id: data.message_id } : item))
        } else if (eventName === 'delta') {
          setMessages((current) => current.map((item) => item.role === 'assistant' && item.status === 'streaming' ? { ...item, content: item.content + data.content } : item))
        } else if (eventName === 'done') {
          setMessages((current) => current.map((item) => item.role === 'assistant' && item.status === 'streaming' ? { ...item, status: 'complete' } : item))
        } else if (eventName === 'proposal') {
          setProposals((current) => current.some((item) => item.id === data.id) ? current : [...current, data])
        } else {
          streamFailed = true
          setMessages((current) => current.map((item) => item.role === 'assistant' && item.status === 'streaming' ? { ...item, status: 'failed' } : item))
          toast.error(t(data.message_key, { defaultValue: t('ai.chat.errors.stream') }))
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
      if (streamFailed) setMessages((current) => current.map((item) => item.role === 'assistant' && item.status === 'streaming' ? { ...item, status: 'failed' } : item))
      void aiApi.listConversations(serverId).then(setConversations).catch(() => undefined)
    }
  }

  if (loading) return <div className="flex h-64 items-center justify-center"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>

  const availableProviders = providers.filter((provider) => provider.available)
  return (
    <section className="grid min-h-[36rem] overflow-hidden rounded-2xl border border-outline-variant/40 bg-surface-container-low/25 lg:grid-cols-[16rem_minmax(0,1fr)]" aria-label={t('ai.chat.title')}>
      <aside className="border-b border-outline-variant/40 bg-surface-container-low/45 p-3 lg:border-b-0 lg:border-r">
        <Button type="button" variant="secondary" className="w-full" disabled={streaming || uploading} onClick={() => void createConversation()}><MessageSquarePlus className="h-4 w-4" />{t('ai.chat.new')}</Button>
        <div className="mt-3 space-y-1">
          {conversations.map((conversation) => <button key={conversation.id} type="button" disabled={streaming || uploading} onClick={() => setConversationId(conversation.id)} className={`w-full truncate rounded-lg px-3 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${conversation.id === conversationId ? 'bg-primary/10 text-primary' : 'text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface'}`}>{conversation.title}</button>)}
          {conversations.length === 0 && <p className="px-2 py-4 text-xs text-on-surface-variant">{t('ai.chat.emptyConversations')}</p>}
        </div>
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="flex flex-wrap items-center gap-3 border-b border-outline-variant/40 p-3">
          <div className="min-w-[12rem] flex-1"><Dropdown value={providerId ? String(providerId) : null} onChange={(value) => setProviderId(Number(value))} options={availableProviders.map((provider) => ({ value: String(provider.id), label: provider.name, hint: provider.default_model }))} placeholder={t('ai.chat.selectProvider')} disabled={streaming || uploading} aria-label={t('ai.chat.selectProvider')} /></div>
          {canUseSkills && serverId && skills.length > 0 && <><div className="min-w-[12rem]"><Dropdown value={skillId} onChange={setSkillId} options={skills.map((skill) => ({ value: skill.id, label: skill.name, hint: `v${skill.version}` }))} placeholder={t('ai.skills.select')} disabled={streaming || uploading} aria-label={t('ai.skills.select')} /></div><Button type="button" variant="secondary" size="sm" disabled={!conversationId || !skillId || streaming || uploading} onClick={() => void runSkill()}><Play className="h-4 w-4" />{t('ai.skills.run')}</Button></>}
          {conversationId && <Button type="button" variant="ghost" size="sm" disabled={streaming || uploading} onClick={() => void removeConversation()} aria-label={t('ai.chat.delete')}><Trash2 className="h-4 w-4" /></Button>}
        </header>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4" aria-live="polite">
          {messages.length === 0 && <div className="mx-auto max-w-lg py-16 text-center"><Bot className="mx-auto h-9 w-9 text-primary/70" /><h2 className="mt-3 font-headline text-lg font-semibold text-on-surface">{t('ai.chat.emptyTitle')}</h2><p className="mt-2 text-sm text-on-surface-variant">{t('ai.chat.emptyDescription')}</p></div>}
          {messages.map((message) => <article key={message.id} className={`flex gap-3 ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}><div className={`max-w-[85%] rounded-xl border px-4 py-3 ${message.role === 'user' ? 'border-primary/25 bg-primary/10' : 'border-outline-variant/40 bg-surface-container'}`}><div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-on-surface-variant">{message.role === 'user' ? <User className="h-3.5 w-3.5" /> : <Bot className="h-3.5 w-3.5" />}{message.role === 'user' ? t('ai.chat.you') : t('ai.chat.assistant')}</div><p className="whitespace-pre-wrap break-words text-sm leading-6 text-on-surface">{message.content || (message.status === 'streaming' ? t('ai.chat.thinking') : t('ai.chat.noResponse'))}</p>{message.status === 'failed' && <p className="mt-2 text-xs text-status-error">{t('ai.chat.failed')}</p>}</div></article>)}
          {proposals.map((proposal) => <AiActionProposalCard key={proposal.id} proposal={proposal} onChange={(updated) => setProposals((current) => current.map((item) => item.id === updated.id ? updated : item))} />)}
          {skillResults.map((result, index) => <article key={`skill-result-${index}`} className="rounded-xl border border-outline-variant/40 bg-surface-container p-4"><p className="text-xs font-semibold text-on-surface-variant">{t('ai.skills.readResult')}{typeof result.tool_name === 'string' ? ` · ${t(`ai.skills.tools.${result.tool_name}`, { defaultValue: result.tool_name })}` : ''}</p><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs text-on-surface">{JSON.stringify(result.result ?? {}, null, 2)}</pre></article>)}
          <div ref={endRef} />
        </div>
        <form className="border-t border-outline-variant/40 p-3" onSubmit={send}>
          {attachments.length > 0 && <div className="mb-2 flex flex-wrap gap-2" aria-label={t('ai.attachments.list')}>{attachments.map((attachment) => <span key={attachment.id} className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-outline-variant/40 bg-surface-container-high px-2.5 py-1 text-xs text-on-surface-variant"><Paperclip className="h-3 w-3 shrink-0" aria-hidden="true" /><span className="truncate">{attachment.original_name}</span><button type="button" className="rounded-sm p-0.5 hover:bg-surface-container-highest focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" disabled={streaming || uploading} onClick={() => void removeAttachment(attachment)} aria-label={t('ai.attachments.remove')}><X className="h-3 w-3" /></button></span>)}</div>}
          <div className="flex items-end gap-2"><textarea className="msm-input min-h-11 flex-1 resize-y py-2.5" rows={2} maxLength={16000} value={input} onChange={(event) => setInput(event.target.value)} placeholder={t('ai.chat.placeholder')} disabled={streaming || uploading || availableProviders.length === 0} aria-label={t('ai.chat.message')} /><Button type="submit" className="h-11" disabled={streaming || uploading || !input.trim() || !providerId}><Send className="h-4 w-4" />{t('ai.chat.send')}</Button></div>
          {canAttach && <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1"><label className={`inline-flex items-center gap-2 text-xs text-primary ${!conversationId || streaming || uploading ? 'pointer-events-none opacity-50' : 'cursor-pointer'}`}><Paperclip className="h-3.5 w-3.5" aria-hidden="true" />{uploading ? t('ai.attachments.uploading') : t('ai.attachments.add')}<input type="file" className="sr-only" disabled={!conversationId || streaming || uploading} accept=".txt,.log,.cfg,.conf,.ini,.json,.properties,.toml,.yaml,.yml,.png,.jpg,.jpeg" aria-label={t('ai.attachments.add')} onChange={(event) => { void uploadAttachment(event.target.files?.[0]); event.target.value = '' }} /></label><span className="text-xs text-on-surface-variant">{t('ai.attachments.supported')}</span></div>}
          {availableProviders.length === 0 && <p className="mt-2 text-xs text-status-warning">{t('ai.chat.noProvider')}</p>}
          <p className="mt-2 text-xs text-on-surface-variant">{t('ai.chat.privacyHint')}</p>
        </form>
      </div>
    </section>
  )
}
