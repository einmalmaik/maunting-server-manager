import { useCallback, useEffect, useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ShieldCheck } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from './Card'
import { Button } from './Button'

const STORAGE_KEY = 'msm.privacyNotice.dismissed'
const PRIVACY_VERSION = '2.0.0'

function hasAcknowledged(raw: string | null): boolean {
  if (!raw) return false
  try {
    const parsed = JSON.parse(raw) as { acknowledged?: unknown; version?: unknown }
    return parsed.acknowledged === true && parsed.version === PRIVACY_VERSION
  } catch {
    return false
  }
}

interface PrivacyAcknowledgementNoticeProps {
  onVisibilityChange?: (visible: boolean) => void
}

export function PrivacyAcknowledgementNotice({ onVisibilityChange }: PrivacyAcknowledgementNoticeProps) {
  const { t } = useTranslation()
  const titleId = useId()
  const descriptionId = useId()
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    let nextVisible = true
    try {
      nextVisible = !hasAcknowledged(window.localStorage.getItem(STORAGE_KEY))
    } catch {
      nextVisible = true
    }
    setVisible(nextVisible)
    onVisibilityChange?.(nextVisible)
  }, [onVisibilityChange])

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
    onVisibilityChange?.(false)
  }, [onVisibilityChange])

  if (!visible) return null

  return (
    <aside
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      className="fixed inset-x-0 bottom-0 z-40 px-4 pb-4"
    >
      <Card className="mx-auto max-w-3xl shadow-panel">
        <CardHeader className="gap-2 pb-3">
          <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-on-surface-variant">
            <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{t('privacyNotice.eyebrow')}</span>
          </div>
          <CardTitle id={titleId} className="text-base font-semibold">
            {t('privacyNotice.title')}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <p id={descriptionId} className="text-sm leading-relaxed text-on-surface-variant">
            {t('privacyNotice.description')}
          </p>
          <div className="flex shrink-0 flex-col gap-2 sm:flex-row sm:items-center">
            <Link
              to="/privacy"
              className="text-xs font-medium text-primary underline-offset-4 hover:underline"
            >
              {t('privacyNotice.readPolicy')}
            </Link>
            <Button variant="primary" size="sm" onClick={dismiss}>
              {t('privacyNotice.confirm')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </aside>
  )
}
