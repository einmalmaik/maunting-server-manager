import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { Check, KeyRound, ShieldAlert } from 'lucide-react'
import { api } from '@/api/client'
import { Logo } from '@/components/Logo'
import { Zahlenwahl } from '@/Singra/UI'
import { passkeyBestaetigen, type PasskeyBestaetigungsOptionen } from '@/services/passkeyService'

/**
 * Passkey-Bestätigung für die App, im Browser.
 *
 * Die App öffnet diese Seite, weil ein Passkey nur unter der Adresse des
 * Panels gilt. Ohne Anmeldung: den Menschen belegt der Passkey selbst. Die
 * Kennung steht im Fragment (`#…`) und geht so in kein Server-Log.
 *
 * Zuerst die Zahl aus der App wählen, dann der Passkey. Wer den Link von
 * jemand anderem bekam, kennt die Zahl nicht; eine falsche Wahl beendet den
 * Vorgang.
 */
interface Vorgang {
  zweck: string
  auswahl: number[]
  optionen: PasskeyBestaetigungsOptionen
}

type Stand = 'laden' | 'wahl' | 'sendet' | 'fertig' | 'fehler'

function zweckText(t: TFunction, zweck: string): string {
  switch (zweck) {
    case 'data_export': return t('auth.browserBestaetigung.purpose.data_export')
    case 'e2ee_reset': return t('auth.browserBestaetigung.purpose.e2ee_reset')
    case 'device_pairing': return t('auth.browserBestaetigung.purpose.device_pairing')
    case 'password_change': return t('auth.browserBestaetigung.purpose.password_change')
    case 'email_change': return t('auth.browserBestaetigung.purpose.email_change')
    case 'account_delete': return t('auth.browserBestaetigung.purpose.account_delete')
    case '2fa_disable': return t('auth.browserBestaetigung.purpose.2fa_disable')
    case 'oauth_link': return t('auth.browserBestaetigung.purpose.oauth_link')
    default: return zweck
  }
}

export function BrowserBestaetigung() {
  const { t } = useTranslation()
  const [kennung] = useState(() => window.location.hash.slice(1))
  const [stand, setStand] = useState<Stand>('laden')
  const [vorgang, setVorgang] = useState<Vorgang | null>(null)
  const [meldung, setMeldung] = useState('')

  useEffect(() => {
    if (!kennung) {
      setStand('fehler')
      setMeldung(t('auth.browserBestaetigung.expired'))
      return
    }
    api<Vorgang>('/auth/passkey/browser/optionen', { method: 'POST', body: JSON.stringify({ vorgang: kennung }) })
      .then((daten) => {
        setVorgang(daten)
        setStand('wahl')
      })
      .catch((err: unknown) => {
        setStand('fehler')
        setMeldung(err instanceof Error ? err.message : t('auth.browserBestaetigung.expired'))
      })
  }, [kennung, t])

  const waehlen = async (zahl: number) => {
    if (!vorgang) return
    setStand('sendet')
    setMeldung('')
    try {
      const passkey = await passkeyBestaetigen(vorgang.optionen)
      await api('/auth/passkey/browser/bestaetigen', {
        method: 'POST',
        body: JSON.stringify({ vorgang: kennung, zahl, passkey }),
      })
      setStand('fertig')
    } catch (err: unknown) {
      setStand('fehler')
      setMeldung(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="min-h-screen bg-background text-on-surface flex items-center justify-center p-margin-mobile md:p-margin-desktop relative overflow-hidden">
      <div className="absolute inset-0 msm-deep-grid opacity-50" />

      <div className="relative z-10 w-full max-w-md">
        <div className="flex items-center justify-center gap-3 mb-8">
          <Logo size="md" />
          <h1 className="font-headline text-body-lg font-extrabold text-primary leading-tight">MSM</h1>
        </div>

        <div className="msm-card p-8 text-center">
          <div className="w-12 h-12 rounded-full bg-surface-container-highest flex items-center justify-center mx-auto mb-4">
            <KeyRound className="w-6 h-6 text-secondary" aria-hidden="true" />
          </div>
          <h2 className="font-headline text-headline-md text-primary mb-1">{t('auth.browserBestaetigung.title')}</h2>

          {stand === 'laden' && (
            <p className="py-8 text-sm text-on-surface-variant" role="status">{t('common.loading')}</p>
          )}

          {(stand === 'wahl' || stand === 'sendet') && vorgang && (
            <div className="space-y-5">
              <p className="text-sm text-on-surface">
                {t('auth.browserBestaetigung.forAction', { aktion: zweckText(t, vorgang.zweck) })}
              </p>
              <p className="text-sm text-on-surface-variant">{t('auth.browserBestaetigung.pickNumber')}</p>
              <Zahlenwahl
                zahlen={vorgang.auswahl}
                onWaehlen={(zahl) => void waehlen(zahl)}
                label={t('auth.browserBestaetigung.pickNumber')}
                disabled={stand === 'sendet'}
              />
              <p className="text-xs text-on-surface-variant">{t('auth.browserBestaetigung.notYou')}</p>
            </div>
          )}

          {stand === 'fertig' && (
            <div className="py-6 space-y-4" role="status">
              <div className="w-16 h-16 rounded-full bg-status-success/10 border border-status-success/30 flex items-center justify-center mx-auto">
                <Check className="w-8 h-8 text-status-success" aria-hidden="true" />
              </div>
              <p className="text-base text-on-surface">{t('auth.browserBestaetigung.done')}</p>
            </div>
          )}

          {stand === 'fehler' && (
            <div className="py-6 space-y-4" role="alert">
              <div className="w-16 h-16 rounded-full bg-status-destructive/10 border border-status-destructive/30 flex items-center justify-center mx-auto">
                <ShieldAlert className="w-8 h-8 text-status-destructive" aria-hidden="true" />
              </div>
              <p className="text-base text-status-destructive">{meldung}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
