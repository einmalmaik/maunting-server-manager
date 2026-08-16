import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api, SanitizedApiError } from '@/api/client'
import { Logo } from '@/components/Logo'
import { Bot, Check, X, ShieldAlert } from 'lucide-react'

/**
 * Die Seite hinter dem Link aus der Freigabemail.
 *
 * Warum es sie gibt: der autonome Modus repariert nachts eine Störung und
 * stößt auf einen Schritt, den er nie ohne Zustimmung tut — etwas Löschendes,
 * etwas Unumkehrbares. Bis vor Kurzem endete der Lauf genau dort mit dem Satz
 * „ohne Freigabe kann ich da nichts machen"; der Server blieb kaputt, und die
 * Mail sagte „konnte nicht behoben werden". Wer einen Monat im Urlaub ist,
 * schlägt keine Karte im Panel auf.
 *
 * Warum sie **ohne Anmeldung** auskommt: der Empfänger sitzt am Telefon. Das
 * Token im Pfad ist die ganze Berechtigung — kurzlebig, einmal verwendbar, an
 * genau einen Vorschlag gebunden. Alle Schranken des Panelwegs gelten
 * unverändert; die Mail ersetzt den Klick, nicht die Prüfung.
 *
 * Und warum das Laden nichts auslöst: Mailscanner und Vorschaudienste klicken
 * Links. Diese Seite **zeigt** nur; entschieden wird per POST von hier aus.
 */
interface Freigabe {
  tool_name: string
  server_name: string | null
  reason: string | null
  expected_effect: string | null
  preview: Record<string, unknown> | null
  expires_at: string
}

type Stand = 'laden' | 'offen' | 'sendet' | 'approved' | 'rejected' | 'fehler'

export function AiFreigabe() {
  const { t } = useTranslation()
  const { token } = useParams<{ token: string }>()
  const [stand, setStand] = useState<Stand>('laden')
  const [freigabe, setFreigabe] = useState<Freigabe | null>(null)
  const [meldung, setMeldung] = useState('')

  const laden = useCallback(async () => {
    if (!token) {
      setStand('fehler')
      setMeldung(t('ai.approval.invalid'))
      return
    }
    try {
      const daten = await api<Freigabe>(`/ai/approvals/${encodeURIComponent(token)}`)
      setFreigabe(daten)
      setStand('offen')
    } catch (error: unknown) {
      setStand('fehler')
      // Backend und Panel geben denselben einen Satz für unbekannt,
      // abgelaufen und verbraucht. Drei verschiedene Meldungen sagten einem
      // Fremden, welche Token es gibt.
      setMeldung(t('ai.approval.invalid'))
      void error
    }
  }, [t, token])

  useEffect(() => {
    void laden()
  }, [laden])

  const entscheiden = async (entscheidung: 'approved' | 'rejected') => {
    if (!token) return
    setStand('sendet')
    setMeldung('')
    try {
      await api(`/ai/approvals/${encodeURIComponent(token)}/decide`, {
        method: 'POST',
        body: JSON.stringify({ decision: entscheidung }),
      })
      setStand(entscheidung)
    } catch (error: unknown) {
      setStand('fehler')
      setMeldung(
        error instanceof SanitizedApiError ? error.message : t('ai.approval.failed'),
      )
    }
  }

  const werkzeug = freigabe
    ? t(`ai.actions.tools.${freigabe.tool_name}`, { defaultValue: freigabe.tool_name })
    : ''

  return (
    <div className="min-h-screen bg-background text-on-surface flex items-center justify-center p-margin-mobile md:p-margin-desktop relative overflow-hidden">
      <div className="absolute inset-0 msm-deep-grid opacity-50" />

      <div className="relative z-10 w-full max-w-lg">
        {/* Derselbe Kopf wie auf den Anmeldeseiten — diese Seite steht wie
            sie außerhalb der angemeldeten Oberfläche und muss von sich aus
            erkennbar sein, wenn jemand sie aus einer E-Mail heraus öffnet. */}
        <div className="flex items-center justify-center gap-3 mb-8">
          <Logo size="md" />
          <h1 className="font-headline text-body-lg font-extrabold text-primary leading-tight">
            MSM
          </h1>
        </div>

        <div className="msm-card p-8">
          <div className="text-center mb-6">
            <div className="w-12 h-12 rounded-full bg-surface-container-highest flex items-center justify-center mx-auto mb-4">
              <Bot className="w-6 h-6 text-secondary" />
            </div>
            <h2 className="font-headline text-headline-md text-primary mb-1">
              {t('ai.approval.title')}
            </h2>
          </div>

          {stand === 'laden' && (
            <p className="py-8 text-center text-sm text-on-surface-variant" role="status">
              {t('common.loading')}
            </p>
          )}

          {stand === 'fehler' && (
            <div className="text-center py-6 space-y-4">
              <div className="w-16 h-16 rounded-full bg-status-error/10 border border-status-error/30 flex items-center justify-center mx-auto">
                <ShieldAlert className="w-8 h-8 text-status-error" />
              </div>
              <p className="font-body-md text-base text-status-error">{meldung}</p>
            </div>
          )}

          {stand === 'approved' && (
            <div className="text-center py-6 space-y-4">
              <div className="w-16 h-16 rounded-full bg-status-success/10 border border-status-success/30 flex items-center justify-center mx-auto">
                <Check className="w-8 h-8 text-status-success" />
              </div>
              <p className="font-body-md text-base text-on-surface">
                {t('ai.approval.approvedDone')}
              </p>
            </div>
          )}

          {stand === 'rejected' && (
            <div className="text-center py-6 space-y-4">
              <div className="w-16 h-16 rounded-full bg-surface-container-highest flex items-center justify-center mx-auto">
                <X className="w-8 h-8 text-on-surface-variant" />
              </div>
              <p className="font-body-md text-base text-on-surface">
                {t('ai.approval.rejectedDone')}
              </p>
            </div>
          )}

          {(stand === 'offen' || stand === 'sendet') && freigabe && (
            <div className="space-y-5">
              <dl className="space-y-3">
                <div>
                  <dt className="text-xs text-on-surface-variant font-medium">
                    {t('ai.approval.action')}
                  </dt>
                  <dd className="text-sm text-on-surface font-semibold">{werkzeug}</dd>
                </div>
                {freigabe.server_name && (
                  <div>
                    <dt className="text-xs text-on-surface-variant font-medium">
                      {t('ai.approval.server')}
                    </dt>
                    <dd className="text-sm text-on-surface font-semibold">
                      {freigabe.server_name}
                    </dd>
                  </div>
                )}
                {freigabe.reason && (
                  <div>
                    <dt className="text-xs text-on-surface-variant font-medium">
                      {t('ai.approval.reason')}
                    </dt>
                    <dd className="text-sm text-on-surface-variant">{freigabe.reason}</dd>
                  </div>
                )}
                {freigabe.expected_effect && (
                  <div>
                    <dt className="text-xs text-on-surface-variant font-medium">
                      {t('ai.approval.effect')}
                    </dt>
                    <dd className="text-sm text-on-surface-variant">
                      {freigabe.expected_effect}
                    </dd>
                  </div>
                )}
              </dl>

              <p className="text-xs text-on-surface-variant">
                {t('ai.approval.expiresAt', {
                  date: new Date(freigabe.expires_at).toLocaleString(),
                })}
              </p>

              <div className="flex flex-col sm:flex-row gap-3">
                <button
                  type="button"
                  disabled={stand === 'sendet'}
                  onClick={() => void entscheiden('approved')}
                  className="msm-btn-primary flex-1 py-3 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {t('ai.approval.approve')}
                </button>
                <button
                  type="button"
                  disabled={stand === 'sendet'}
                  onClick={() => void entscheiden('rejected')}
                  className="msm-btn-secondary flex-1 py-3 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {t('ai.approval.reject')}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
