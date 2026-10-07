import { useState } from 'react'
import {
  Lock,
  Search,
  Copy,
  Eye,
  EyeOff,
  ShieldCheck,
  Check,
} from 'lucide-react'
import { useBrowserStore } from '../../services/browserStore'

interface DemoEntry {
  id: string
  service: string
  username: string
  password: string
  url: string
}

export function VaultDrawer() {
  const { isCoupled } = useBrowserStore()
  const [searchTerm, setSearchTerm] = useState('')
  const [revealedIds, setRevealedIds] = useState<Record<string, boolean>>({})
  const [copiedField, setCopiedField] = useState<string | null>(null)

  const demoEntries: DemoEntry[] = [
    {
      id: 'v-1',
      service: 'GitHub',
      username: 'developer@mauntingstudios.com',
      password: 'mypassword123!Secure',
      url: 'https://github.com',
    },
    {
      id: 'v-2',
      service: 'Google Account',
      username: 'admin@mauntingstudios.com',
      password: 'GoogleSecurePass99#',
      url: 'https://google.com',
    },
    {
      id: 'v-3',
      service: 'YouTube',
      username: 'creator@mauntingstudios.com',
      password: 'YTSuperSecret2026!',
      url: 'https://youtube.com',
    },
  ]

  const filtered = demoEntries.filter(
    (e) =>
      e.service.toLowerCase().includes(searchTerm.toLowerCase()) ||
      e.username.toLowerCase().includes(searchTerm.toLowerCase()) ||
      e.url.toLowerCase().includes(searchTerm.toLowerCase())
  )

  const copyToClipboard = (text: string, fieldId: string) => {
    navigator.clipboard.writeText(text)
    setCopiedField(fieldId)
    setTimeout(() => setCopiedField(null), 1500)
  }

  const toggleReveal = (id: string) => {
    setRevealedIds((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  if (!isCoupled) {
    return (
      <div className="p-6 text-center flex flex-col items-center justify-center h-full">
        <div className="p-3 rounded-2xl bg-muted text-muted-foreground mb-3">
          <Lock className="w-8 h-8" />
        </div>
        <h3 className="text-sm font-semibold mb-1">Passwortmanager nicht gekoppelt</h3>
        <p className="text-xs text-muted-foreground mb-4 max-w-xs">
          Kopple den Browser in den Einstellungen mit deinem MSM-Server, um deinen DIS-Tresor in
          Echtzeit zu synchronisieren.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full text-foreground select-none">
      {/* Header */}
      <div className="p-3 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Lock className="w-4 h-4 text-status-success" />
          <span className="text-xs font-semibold">DIS Passwortmanager</span>
        </div>
        <div className="flex items-center gap-1 text-label-sm text-status-success bg-muted px-2 py-0.5 rounded-full">
          <ShieldCheck className="w-3 h-3" />
          <span>Synchronisiert</span>
        </div>
      </div>

      {/* Suche */}
      <div className="p-3 border-b border-border">
        <div className="relative flex items-center">
          <Search className="w-3.5 h-3.5 absolute left-2.5 text-muted-foreground" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Zugangsdaten durchsuchen..."
            className="w-full bg-muted/60 border border-border rounded-xl pl-8 pr-3 py-1.5 text-xs outline-none focus:border-primary"
          />
        </div>
      </div>

      {/* Liste */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2.5">
        {filtered.map((entry) => {
          const isRevealed = !!revealedIds[entry.id]

          return (
            <div
              key={entry.id}
              className="p-3 rounded-xl bg-card border border-border hover:border-border/80 transition-all shadow-sm space-y-2"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-foreground truncate">
                  {entry.service}
                </span>
                <span className="text-label-sm text-muted-foreground truncate max-w-[120px]">
                  {entry.url}
                </span>
              </div>

              {/* Benutzername Zeile */}
              <div className="flex items-center justify-between bg-muted/40 px-2 py-1 rounded-lg text-xs">
                <span className="truncate text-muted-foreground select-text font-mono text-label-sm">
                  {entry.username}
                </span>
                <button
                  onClick={() => copyToClipboard(entry.username, `user-${entry.id}`)}
                  className="p-1 hover:bg-muted rounded text-muted-foreground hover:text-foreground"
                  title="Benutzername kopieren"
                >
                  {copiedField === `user-${entry.id}` ? (
                    <Check className="w-3 h-3 text-status-success" />
                  ) : (
                    <Copy className="w-3 h-3" />
                  )}
                </button>
              </div>

              {/* Passwort Zeile */}
              <div className="flex items-center justify-between bg-muted/40 px-2 py-1 rounded-lg text-xs">
                <span className="truncate text-muted-foreground font-mono text-label-sm">
                  {isRevealed ? entry.password : '••••••••••••'}
                </span>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => toggleReveal(entry.id)}
                    className="p-1 hover:bg-muted rounded text-muted-foreground hover:text-foreground"
                    title={isRevealed ? 'Passwort verbergen' : 'Passwort anzeigen'}
                  >
                    {isRevealed ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
                  </button>
                  <button
                    onClick={() => copyToClipboard(entry.password, `pw-${entry.id}`)}
                    className="p-1 hover:bg-muted rounded text-muted-foreground hover:text-foreground"
                    title="Passwort kopieren"
                  >
                    {copiedField === `pw-${entry.id}` ? (
                      <Check className="w-3 h-3 text-status-success" />
                    ) : (
                      <Copy className="w-3 h-3" />
                    )}
                  </button>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Footer Info */}
      <div className="p-3 border-t border-border bg-muted/30 text-label-sm text-muted-foreground text-center">
        <span>Zero-Knowledge AES-256-GCM • Master Key liegt nie im RAM</span>
      </div>
    </div>
  )
}
