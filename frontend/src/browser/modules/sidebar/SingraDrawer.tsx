import { useState } from 'react'
import {
  Sparkles,
  Send,
  Mic,
  Radio,
  FileText,
  Loader2,
  Bot,
  User,
} from 'lucide-react'
import { useBrowserStore } from '../../services/browserStore'

interface ChatMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

export function SingraDrawer() {
  const { isCoupled, tabs, activeTabId } = useBrowserStore()
  const activeTab = tabs.find((t) => t.id === activeTabId)

  const [messages, setMessages] = useState<ChatMsg[]>([
    {
      id: 'm-1',
      role: 'assistant',
      content:
        'Hallo! Ich bin Singra, dein KI-Begleiter. Ich bin mit deinem MSM-Server verbunden und stehe dir beim Surfen zur Seite. Aus Datenschutzgründen lese ich deine geöffneten Webseiten nicht automatisch mit.',
      timestamp: Date.now() - 60000,
    },
  ])
  const [inputPrompt, setInputPrompt] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [isVoiceActive, setIsVoiceActive] = useState(false)

  const handleSend = (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!inputPrompt.trim() || isLoading) return

    const userMsg: ChatMsg = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: inputPrompt,
      timestamp: Date.now(),
    }

    setMessages((prev) => [...prev, userMsg])
    setInputPrompt('')
    setIsLoading(true)

    setTimeout(() => {
      const botMsg: ChatMsg = {
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: `Ich habe deine Anfrage erhalten. Dies ist die synchronisierte Singra-Instanz deines MSM-Accounts.`,
        timestamp: Date.now(),
      }
      setMessages((prev) => [...prev, botMsg])
      setIsLoading(false)
    }, 1000)
  }

  const handleSummarizeCurrentPage = () => {
    if (!activeTab?.url || activeTab.url.startsWith('about:')) return
    setInputPrompt(`Fasse die aktuelle Seite (${activeTab.title}) kurz und prägnant zusammen.`)
  }

  if (!isCoupled) {
    return (
      <div className="p-6 text-center flex flex-col items-center justify-center h-full">
        <div className="p-3 rounded-2xl bg-muted text-muted-foreground mb-3">
          <Sparkles className="w-8 h-8" />
        </div>
        <h3 className="text-sm font-semibold mb-1">Singra KI nicht gekoppelt</h3>
        <p className="text-xs text-muted-foreground mb-4 max-w-xs">
          Kopple den Browser mit deinem MSM-Server, um deinen persönlichen KI-Chatverlauf und
          Sprachassistenten freizuschalten.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full text-foreground select-none">
      {/* Header */}
      <div className="p-3 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-lg bg-primary/10 text-primary">
            <Sparkles className="w-4 h-4" />
          </div>
          <div>
            <div className="text-xs font-semibold leading-tight">Singra KI</div>
            <div className="text-label-sm text-muted-foreground">MSM-Synchronisiert</div>
          </div>
        </div>

        {/* Real-Time Voice Button */}
        <button
          onClick={() => setIsVoiceActive(!isVoiceActive)}
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition-all ${
            isVoiceActive
              ? 'bg-primary text-primary-foreground shadow-sm animate-pulse'
              : 'bg-muted text-muted-foreground hover:text-foreground'
          }`}
          aria-label={isVoiceActive ? 'Voice-Modus aktiv' : 'Voice-Modus im Hintergrund starten'}
        >
          {isVoiceActive ? <Radio className="w-3.5 h-3.5" /> : <Mic className="w-3.5 h-3.5" />}
          <span>{isVoiceActive ? 'Live Audio' : 'Voice'}</span>
        </button>
      </div>

      {/* Datenschutz-Hinweis & Seiten-Aktion */}
      {activeTab?.url && !activeTab.url.startsWith('about:') && (
        <div className="p-2.5 border-b border-border bg-muted/30 flex items-center justify-between text-xs">
          <span className="text-muted-foreground truncate max-w-[160px]">
            {activeTab.title}
          </span>
          <button
            onClick={handleSummarizeCurrentPage}
            className="flex items-center gap-1 text-primary hover:underline font-medium shrink-0"
          >
            <FileText className="w-3 h-3" />
            <span>Seite übergeben</span>
          </button>
        </div>
      )}

      {/* Chat Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3 text-xs select-text">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex gap-2.5 ${
              msg.role === 'user' ? 'justify-end' : 'justify-start'
            }`}
          >
            {msg.role === 'assistant' && (
              <div className="w-6 h-6 rounded-full bg-primary/10 text-primary flex items-center justify-center shrink-0 mt-0.5">
                <Bot className="w-3.5 h-3.5" />
              </div>
            )}
            <div
              className={`p-3 rounded-2xl max-w-[85%] leading-relaxed ${
                msg.role === 'user'
                  ? 'bg-primary text-primary-foreground rounded-br-none'
                  : 'bg-card border border-border text-foreground rounded-bl-none shadow-sm'
              }`}
            >
              {msg.content}
            </div>
            {msg.role === 'user' && (
              <div className="w-6 h-6 rounded-full bg-muted text-muted-foreground flex items-center justify-center shrink-0 mt-0.5">
                <User className="w-3.5 h-3.5" />
              </div>
            )}
          </div>
        ))}

        {isLoading && (
          <div className="flex items-center gap-2 text-muted-foreground text-xs py-2">
            <Loader2 className="w-4 h-4 animate-spin text-primary" />
            <span>Singra überlegt...</span>
          </div>
        )}
      </div>

      {/* Input Form */}
      <form onSubmit={handleSend} className="p-3 border-t border-border bg-card">
        <div className="relative flex items-center">
          <input
            type="text"
            value={inputPrompt}
            onChange={(e) => setInputPrompt(e.target.value)}
            placeholder="Frag Singra etwas..."
            className="w-full bg-muted/60 border border-border rounded-xl pl-3 pr-10 py-2 text-xs outline-none focus:border-primary select-text"
          />
          <button
            type="submit"
            disabled={!inputPrompt.trim() || isLoading}
            className="absolute right-1.5 p-1.5 rounded-lg bg-primary text-primary-foreground disabled:opacity-30 transition-opacity"
          >
            <Send className="w-3.5 h-3.5" />
          </button>
        </div>
      </form>
    </div>
  )
}
