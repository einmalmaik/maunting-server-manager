import { useState } from 'react'
import { ShieldAlert, CreditCard, Fingerprint, X } from 'lucide-react'

interface PaymentConfirmProps {
  isOpen: boolean
  onConfirm: () => void
  onCancel: () => void
  paymentType: 'credit_card' | 'iban'
  domain: string
}

export function PaymentConfirm({
  isOpen,
  onConfirm,
  onCancel,
  paymentType,
  domain,
}: PaymentConfirmProps) {
  const [pin, setPin] = useState('')

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-popover border border-border rounded-2xl shadow-2xl max-w-sm w-full p-5 text-xs text-foreground animate-scale-in">
        <div className="flex items-center justify-between pb-3 border-b border-border">
          <div className="flex items-center gap-2 text-status-warning font-semibold text-sm">
            <ShieldAlert className="w-5 h-5" />
            <span>Zahlungsdaten freigeben?</span>
          </div>
          <button
            onClick={onCancel}
            className="p-1 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="py-4 space-y-3">
          <p className="text-muted-foreground leading-relaxed">
            Die Webseite <span className="font-semibold text-foreground">{domain}</span> fordert{' '}
            {paymentType === 'credit_card' ? 'Kreditkarten-Informationen' : 'IBAN-Bankdaten'} an.
          </p>

          <div className="p-3 bg-muted/50 rounded-xl border border-border/60 flex items-center gap-3">
            <CreditCard className="w-6 h-6 text-primary" />
            <div>
              <div className="font-medium text-foreground">
                {paymentType === 'credit_card' ? 'Gespeicherte Visa / Mastercard' : 'Bankkonto (IBAN)'}
              </div>
              <div className="text-label-sm text-muted-foreground">
                Zero-Knowledge geschützt im DIS-Tresor
              </div>
            </div>
          </div>

          <div className="pt-2">
            <label className="text-label-sm font-semibold text-muted-foreground block mb-1">
              Bestätigung per Master-PIN oder Biometrie:
            </label>
            <input
              type="password"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              placeholder="Master-PIN eingeben..."
              className="w-full bg-background border border-border rounded-xl p-2.5 text-xs outline-none focus:border-primary"
              autoFocus
            />
          </div>
        </div>

        <div className="flex items-center gap-2 pt-2 border-t border-border">
          <button
            onClick={onConfirm}
            className="flex-1 flex items-center justify-center gap-1.5 py-2.5 rounded-xl bg-primary text-primary-foreground font-semibold hover:bg-primary/90 transition-colors shadow-sm"
          >
            <Fingerprint className="w-4 h-4" />
            <span>Jetzt einfügen</span>
          </button>
          <button
            onClick={onCancel}
            className="px-4 py-2.5 rounded-xl bg-muted hover:bg-muted/80 text-muted-foreground font-medium transition-colors"
          >
            Abbrechen
          </button>
        </div>
      </div>
    </div>
  )
}
