import { AtSign } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { BenutzernameFeld } from '@/components/BenutzernameFeld'
import { Logo } from '@/components/Logo'
import { benutzernameVorschlag } from '@/lib/benutzername'
import { useAuthStore } from '@/stores/authStore'

/**
 * Einmalige Namenswahl für Konten aus Social Login und Hoster-Shop.
 *
 * Solche Konten bekommen einen vorläufigen Namen (`username_gewaehlt=false`).
 * Bis 09/2026 war das die E-Mail ohne Sonderzeichen. `ProtectedRoute` und die
 * App zeigen diese Seite statt des Panels, bis ein Name gewählt ist. Danach
 * steht im Store `username_gewaehlt=true`, und das Panel erscheint an
 * derselben Adresse. Eine Umleitung braucht es dafür nicht.
 */
export function BenutzernameWaehlen() {
  const { t } = useTranslation()
  const vorlaeufig = useAuthStore((s) => s.user?.username)
  const email = useAuthStore((s) => s.user?.email)

  return (
    <div className="min-h-screen bg-background text-on-surface flex items-center justify-center p-margin-mobile md:p-margin-desktop relative overflow-hidden">
      <div className="absolute inset-0 msm-deep-grid opacity-50" />

      <div className="relative z-10 w-full max-w-md">
        <div className="flex items-center justify-center gap-3 mb-8">
          <Logo size="md" />
          <h1 className="font-headline text-body-lg font-extrabold text-primary leading-tight">MSM</h1>
        </div>

        <div className="msm-card p-8 space-y-5">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <AtSign className="h-5 w-5" aria-hidden="true" />
            </div>
            <h2 className="font-headline text-headline-sm text-on-surface">{t('benutzername.titel')}</h2>
          </div>
          <p className="text-sm text-on-surface-variant">{t('benutzername.hinweis')}</p>
          <BenutzernameFeld
            start={benutzernameVorschlag(vorlaeufig, email)}
            knopf={t('benutzername.weiter')}
            unveraendertErlaubt
            autoFocus
          />
        </div>
      </div>
    </div>
  )
}
