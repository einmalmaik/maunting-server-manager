import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { Mail, AlertTriangle } from 'lucide-react'

/**
 * Tab: Account-Info (Username, E-Mail, Verify-Status).
 * Read-only Anzeige, keine schreibenden Aktionen.
 */
export function AccountTab() {
  const { t } = useTranslation()
  const { user } = useAuthStore()

  return (
    <div className="msm-card p-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
          <Mail className="w-5 h-5 text-secondary" />
        </div>
        <h2 className="font-headline text-headline-sm text-primary">{t('auth.email')}</h2>
      </div>
      <div className="flex items-center gap-3">
        <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center text-lg font-bold text-primary border border-outline-variant">
          {user?.username.charAt(0).toUpperCase()}
        </div>
        <div>
          <p className="font-label-md text-sm text-on-surface font-medium">{user?.username}</p>
          <p className="font-body-md text-sm text-on-surface-variant">{user?.email}</p>
          {user?.email_verified === false && (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-status-error/10 text-status-error border border-status-error/30 mt-1">
              <AlertTriangle className="w-3 h-3" />
              {t('profile.notVerified', 'Nicht verifiziert')}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
