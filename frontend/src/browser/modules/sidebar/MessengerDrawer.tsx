import { useState } from 'react'
import {
  MessageSquare,
  Send,
  User,
  Users,
  ArrowLeft,
} from 'lucide-react'
import { useBrowserStore } from '../../services/browserStore'

interface DemoChat {
  id: string
  name: string
  isGroup: boolean
  lastMessage: string
  time: string
  unread: number
  online: boolean
}

export function MessengerDrawer() {
  const { isCoupled } = useBrowserStore()
  const [activeChatId, setActiveChatId] = useState<string | null>(null)
  const [chatMessage, setChatMessage] = useState('')

  const demoChats: DemoChat[] = [
    {
      id: 'c-1',
      name: 'Entwickler-Team',
      isGroup: true,
      lastMessage: 'Browser-Prototyp läuft stabil!',
      time: '19:15',
      unread: 2,
      online: true,
    },
    {
      id: 'c-2',
      name: 'Server Admin',
      isGroup: false,
      lastMessage: 'Backup-Node ist synchronisiert.',
      time: '18:42',
      unread: 0,
      online: true,
    },
    {
      id: 'c-3',
      name: 'Support & Alerts',
      isGroup: false,
      lastMessage: 'Alle Guardian-Container aktiv.',
      time: 'Gestern',
      unread: 0,
      online: false,
    },
  ]

  const activeChat = demoChats.find((c) => c.id === activeChatId)

  if (!isCoupled) {
    return (
      <div className="p-6 text-center flex flex-col items-center justify-center h-full select-none">
        <div className="p-3 rounded-2xl bg-muted text-muted-foreground mb-3">
          <MessageSquare className="w-8 h-8" />
        </div>
        <h3 className="text-sm font-semibold mb-1">Messenger nicht gekoppelt</h3>
        <p className="text-xs text-muted-foreground mb-4 max-w-xs">
          Kopple den Browser mit deinem MSM-Server, um direkt während des Surfens mit deinem Team
          und Kontakten zu chatten.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full text-foreground select-none">
      {/* Header */}
      <div className="p-3 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2">
          {activeChatId && (
            <button
              onClick={() => setActiveChatId(null)}
              className="p-1 hover:bg-muted rounded text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
          )}
          <div className="flex items-center gap-2">
            <MessageSquare className="w-4 h-4 text-primary" />
            <span className="text-xs font-semibold">
              {activeChat ? activeChat.name : 'MSM Messenger'}
            </span>
          </div>
        </div>
      </div>

      {/* Ansicht: Chatliste oder Aktiver Chat */}
      {!activeChatId ? (
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {demoChats.map((chat) => (
            <div
              key={chat.id}
              onClick={() => setActiveChatId(chat.id)}
              className="p-2.5 rounded-xl hover:bg-muted/60 cursor-pointer transition-colors flex items-center gap-3"
            >
              <div className="relative shrink-0">
                <div className="w-9 h-9 rounded-full bg-primary/10 text-primary flex items-center justify-center font-semibold text-xs">
                  {chat.isGroup ? <Users className="w-4 h-4" /> : <User className="w-4 h-4" />}
                </div>
                {chat.online && (
                  <span className="absolute bottom-0 right-0 w-2.5 h-2.5 bg-status-success border-2 border-background rounded-full" />
                )}
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex justify-between items-baseline mb-0.5">
                  <span className="text-xs font-semibold truncate text-foreground">
                    {chat.name}
                  </span>
                  <span className="text-label-sm text-muted-foreground">{chat.time}</span>
                </div>
                <p className="text-label-sm text-muted-foreground truncate">
                  {chat.lastMessage}
                </p>
              </div>

              {chat.unread > 0 && (
                <span className="w-4 h-4 rounded-full bg-primary text-primary-foreground text-label-sm font-bold flex items-center justify-center shrink-0">
                  {chat.unread}
                </span>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="flex-1 flex flex-col h-full min-h-0">
          <div className="flex-1 overflow-y-auto p-3 space-y-2.5 text-xs select-text">
            <div className="flex justify-start">
              <div className="p-2.5 rounded-2xl bg-card border border-border text-foreground rounded-bl-none max-w-[85%]">
                {activeChat?.lastMessage}
              </div>
            </div>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault()
              setChatMessage('')
            }}
            className="p-3 border-t border-border bg-card"
          >
            <div className="relative flex items-center">
              <input
                type="text"
                value={chatMessage}
                onChange={(e) => setChatMessage(e.target.value)}
                placeholder="Nachricht schreiben..."
                className="w-full bg-muted/60 border border-border rounded-xl pl-3 pr-10 py-2 text-xs outline-none focus:border-primary select-text"
              />
              <button
                type="submit"
                disabled={!chatMessage.trim()}
                className="absolute right-1.5 p-1.5 rounded-lg bg-primary text-primary-foreground disabled:opacity-30"
              >
                <Send className="w-3.5 h-3.5" />
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
