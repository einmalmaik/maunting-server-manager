import { Lock, Check, X } from 'lucide-react'

export interface SavePromptData {
  domain: string
  url: string
  username: string
  password: string
}

interface PasswordPromptProps {
  data: SavePromptData | null
  onSave: () => void
  onDismiss: () => void
}

export function PasswordPrompt({ data, onSave, onDismiss }: PasswordPromptProps) {
  if (!data) return null

  return (
    <div className="absolute top-14 right-6 z-50 w-80 bg-popover/95 backdrop-blur-xl border border-primary/30 rounded-2xl shadow-2xl p-4 text-xs text-foreground animate-scale-in">
      <div className="flex items-center gap-2 mb-2 pb-2 border-b border-border">
        <div className="p-1.5 rounded-lg bg-primary/10 text-primary">
          <Lock className="w-4 h-4" />
        </div>
        <div>
          <h4 className="font-semibold text-xs leading-tight">Passwort speichern?</h4>
          <p className="text-label-sm text-muted-foreground truncate">{data.domain}</p>
        </div>
      </div>

      <p className="text-muted-foreground text-label-sm mb-3 leading-relaxed">
        Sollen die Zugangsdaten für diesen Account verschlüsselt in deinem DIS-Tresor abgelegt werden?
      </p>

      <div className="bg-muted/50 rounded-xl p-2.5 space-y-1 mb-3 text-label-sm">
        <div className="flex justify-between">
          <span className="text-muted-foreground">Benutzer:</span>
          <span className="font-mono text-foreground">{data.username || 'Unbekannt'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Passwort:</span>
          <span className="font-mono text-foreground">••••••••••••</span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={onSave}
          className="flex-1 flex items-center justify-center gap-1.5 py-2 rounded-xl bg-primary text-primary-foreground font-semibold hover:bg-primary/90 transition-colors shadow-sm"
        >
          <Check className="w-3.5 h-3.5" />
          <span>Speichern</span>
        </button>
        <button
          onClick={onDismiss}
          className="p-2 rounded-xl bg-muted hover:bg-muted/80 text-muted-foreground transition-colors"
          aria-label="Verwerfen"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}
