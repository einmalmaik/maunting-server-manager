import { useCallback, useEffect, useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ShieldCheck } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from './Card'
import { Button } from './Button'

const STORAGE_KEY = 'msm.privacyNotice.dismissed'
const PRIVACY_VERSION = '1.0.0'

function hasAcknowledged(raw: string | null): boolean {
  if (!raw) return false
  try {
    const parsed = JSON.parse(raw) as { acknowledged?: unknown; version?: unknown }
    return parsed.acknowledged === true && parsed.version === PRIVACY_VERSION
  } catch {
    return false
  }
}

export function PrivacyAcknowledgementNotice() {
  const { t, i18n } = useTranslation()
  const titleId = useId()
  const [visible, setVisible] = useState(false)
  const isGerman = i18n.language.startsWith('de')

  useEffect(() => {
    try {
      setVisible(!hasAcknowledged(window.localStorage.getItem(STORAGE_KEY)))
    } catch {
      setVisible(true)
    }
  }, [])

  const dismiss = useCallback(() => {
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ acknowledged: true, version: PRIVACY_VERSION, at: new Date().toISOString() }),
      )
    } catch {
      // localStorage can be unavailable; hide for the current view.
    }
    setVisible(false)
  }, [])

  if (!visible) return null

  return (
    <aside aria-labelledby={titleId} className="fixed inset-x-0 bottom-0 z-50 px-4 pb-4">
      <Card className="mx-auto max-w-3xl shadow-panel">
        <CardHeader className="gap-2 pb-3">
          <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-on-surface-variant">
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{isGerman ? 'Datenschutz-Hinweis' : 'Privacy notice'}</span>
          </div>
          <CardTitle id={titleId} className="text-base font-semibold">
            {isGerman ? 'Wir respektieren deine Privatsphäre' : 'We respect your privacy'}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm leading-relaxed text-on-surface-variant">
            {isGerman
              ? 'MSM nutzt nur technisch notwendige Cookies und lokale Speicherung für Login, Sicherheit und Panel-Funktionen. Kein Tracking, keine Werbung.'
              : 'MSM only uses technically necessary cookies and local storage for login, security, and panel features. No tracking, no advertising.'}
          </p>
          <div className="flex shrink-0 flex-col gap-2 sm:flex-row sm:items-center">
            <Link
              to="/privacy"
              className="text-xs font-medium text-primary underline-offset-4 hover:underline"
            >
              {isGerman ? 'Datenschutzerklärung lesen' : 'Read privacy policy'}
            </Link>
            <Button variant="primary" size="sm" onClick={dismiss}>
              {isGerman ? 'Verstanden' : t('common.confirm', 'Understood')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </aside>
  )
}
