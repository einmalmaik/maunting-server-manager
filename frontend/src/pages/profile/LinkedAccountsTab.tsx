import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { oauthApi, type OAuthProviderPublic, type OAuthUserLink } from '@/api/oauth'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'
import { Fingerprint, Link2, Unlink } from 'lucide-react'
import { useOAuthLinks } from './useOAuthLinks'
import { ConnectedMailboxesSection } from './ConnectedMailboxesSection'
import { ConnectedCalendarsSection } from './ConnectedCalendarsSection'
import { Button, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, Input } from '@/Singra/UI'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { Spinner } from '@/components/ui/Spinner'
import { useAuthStore } from '@/stores/authStore'
import { passkeyNachweis } from '@/services/passkeyService'
/**
 * Tab: Verknuepfte Accounts & Dienste.
 * Enthält:
 * 1. OAuth / SSO Login-Provider (Google, Discord, GitHub ...)
 * 2. E-Mail-Postfächer (IMAP / SMTP / Google) für den KI-Assistenten
 * 3. Kalender (CalDAV) für den KI-Assistenten
 */
export function LinkedAccountsTab() {
  const { t, i18n } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const { oauthLinks, oauthAvailable, loading, reload } = useOAuthLinks()
  const [linkZiel, setLinkZiel] = useState<OAuthProviderPublic | null>(null)

  // URL-Param-Auswertung fuer OAuth-Linking-Callback
  useEffect(() => {
    const linked = searchParams.get('linked')
    const linkError = searchParams.get('error')
    if (linked === '1') {
      toast.success(t('profile.linkedAccounts.linkSuccess'))
      setSearchParams({}, { replace: true })
      void reload()
    } else if (linkError) {
      const key = `profile.linkedAccounts.linkError${linkError
        .split('_')
        .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
        .join('')}`
      const translated = t(key, '')
      toast.error(translated || t('profile.linkedAccounts.linkErrorUnknown'))
      setSearchParams({}, { replace: true })
    }
  }, [searchParams, setSearchParams, t, reload])

  const handleUnlink = async (link: OAuthUserLink) => {
    const ok = await confirm({
      message: t('profile.linkedAccounts.unlinkConfirm', { provider: link.provider_name }),
      danger: true,
      confirmText: t('profile.linkedAccounts.unlink'),
    })
    if (!ok) return
    try {
      await oauthApi.unlinkProvider(link.provider_id)
      toast.success(t('profile.linkedAccounts.unlinkSuccess'))
      await reload()
    } catch (err: any) {
      toast.error(err.message)
    }
  }

  const formatDate = (iso: string | null): string => {
    if (!iso) return t('profile.linkedAccounts.neverUsed')
    try {
      return new Intl.DateTimeFormat(i18n.language, {
        dateStyle: 'medium',
        timeStyle: 'short',
      }).format(new Date(iso))
    } catch {
      return iso
    }
  }

  const linkedSlugs = new Set(oauthLinks.map((l) => l.provider_slug))
  const unlinkedProviders = oauthAvailable.filter((p) => !linkedSlugs.has(p.slug))

  return (
    <div className="space-y-6">
      {/* OAuth Login Accounts */}
      <div className="msm-card p-6">
        <div className="flex items-center gap-2 mb-6">
          <Link2 className="h-5 w-5 text-secondary" aria-hidden="true" />
          <div>
            <h2 className="font-headline text-title-lg font-semibold text-on-surface">{t('profile.linkedAccounts.title')}</h2>
            <p className="font-body-md text-sm text-on-surface-variant mt-1">
              {t('profile.linkedAccounts.subtitle')}
            </p>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center h-24">
            <Spinner size="md" className="text-primary" />
          </div>
        ) : (
          <div className="space-y-4">
            {oauthLinks.length === 0 ? (
              <p className="font-body-md text-sm text-on-surface-variant">
                {t('profile.linkedAccounts.empty')}
              </p>
            ) : (
              <ul className="divide-y divide-outline-variant/30">
                {oauthLinks.map((link) => (
                  <li key={link.id} className="py-3 first:pt-0 last:pb-0 flex items-center gap-4">
                    <div className="flex-1 min-w-0">
                      <p className="font-label-md text-sm text-on-surface font-medium">{link.provider_name}</p>
                      <p className="font-body-md text-xs text-on-surface-variant mt-0.5">
                        {t('profile.linkedAccounts.linkedSince', { date: formatDate(link.created_at) })}
                        {link.last_used_at && (
                          <span className="ml-2">
                            · {t('profile.linkedAccounts.lastUsed', { date: formatDate(link.last_used_at) })}
                          </span>
                        )}
                      </p>
                    </div>
                    <Button variant="secondary" size="sm"
                      type="button"
                      onClick={() => handleUnlink(link)}
                      className="inline-flex items-center gap-1.5"
                    >
                      <Unlink className="w-3.5 h-3.5" />
                      {t('profile.linkedAccounts.unlink')}
                    </Button>
                  </li>
                ))}
              </ul>
            )}

            {unlinkedProviders.length > 0 && (
              <div className="pt-4 border-t border-outline-variant/30">
                <p className="font-label-md text-xs text-on-surface-variant uppercase tracking-wider mb-3">
                  {t('profile.linkedAccounts.connect')}
                </p>
                <div className="flex flex-wrap gap-2">
                  {unlinkedProviders.map((p) => (
                    <Button
                      key={p.slug}
                      variant="secondary"
                      type="button"
                      onClick={() => setLinkZiel(p)}
                      className="inline-flex items-center gap-1.5"
                    >
                      <Link2 className="w-3.5 h-3.5" />
                      {p.name}
                    </Button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {linkZiel && (
        <VerknuepfenDialog key={linkZiel.slug} provider={linkZiel} onClose={() => setLinkZiel(null)} />
      )}

      {/* Connected Mailboxes for AI */}
      <ConnectedMailboxesSection />

      {/* Connected Calendars for AI */}
      <ConnectedCalendarsSection />
    </div>
  )
}

/**
 * Nachweis vor dem Verknuepfen: bei 2FA der eingerichtete Faktor, sonst das
 * Passwort. Eine Verknuepfung ist ein Zugang ohne Ablauf, ein angemeldetes
 * Token allein reicht dafuer nicht. Konten ohne beides legen zuerst ein
 * Passwort fest.
 */
function VerknuepfenDialog({ provider, onClose }: { provider: OAuthProviderPublic; onClose: () => void }) {
  const { t } = useTranslation()
  const user = useAuthStore((s) => s.user)
  const methode = user?.two_factor_enabled ? (user.two_factor_method ?? 'totp') : null
  const ohneNachweis = !methode && user?.has_password === false
  const [passwort, setPasswort] = useState('')
  const [code, setCode] = useState('')
  const [laeuft, setLaeuft] = useState(false)
  const [fehler, setFehler] = useState('')

  const starten = async (e: React.FormEvent) => {
    e.preventDefault()
    setFehler('')
    setLaeuft(true)
    try {
      // Der Passkey zuerst: der Browser verlangt die Abfrage nah am Klick.
      const passkey = methode === 'passkey' ? await passkeyNachweis('oauth_link') : null
      const { url } = await oauthApi.startLink(provider.slug, {
        password: methode ? '' : passwort,
        otp_code: methode === 'totp' ? code : '',
        passkey,
      })
      window.location.assign(url)
    } catch (err) {
      setFehler(err instanceof Error ? err.message : String(err))
      setLaeuft(false)
    }
  }

  return (
    <Dialog open onOpenChange={(offen) => { if (!offen) onClose() }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('profile.linkedAccounts.linkTitle', { provider: provider.name })}</DialogTitle>
          <DialogDescription>{t('profile.linkedAccounts.linkProofHint')}</DialogDescription>
        </DialogHeader>
        {ohneNachweis ? (
          <>
            <p className="p-6 text-sm text-on-surface-variant">{t('profile.linkedAccounts.linkNeedsPassword')}</p>
            <DialogFooter>
              <Button variant="secondary" type="button" onClick={onClose}>
                {t('common.close')}
              </Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={starten}>
            <div className="p-6 space-y-4">
              {methode === 'totp' ? (
                <Input
                  id="verknuepfen-otp"
                  label={t('auth.otpCode')}
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  placeholder="000000"
                  required
                  disabled={laeuft}
                />
              ) : methode === 'passkey' ? (
                <p className="text-sm text-on-surface-variant">{t('profile.linkedAccounts.linkPasskeyHint')}</p>
              ) : (
                <PasswordInput
                  id="verknuepfen-passwort"
                  label={t('profile.currentPassword')}
                  value={passwort}
                  onChange={(e) => setPasswort(e.target.value)}
                  autoComplete="current-password"
                  required
                  disabled={laeuft}
                />
              )}
              {fehler && <div className="msm-alert-error text-sm" role="alert">{fehler}</div>}
            </div>
            <DialogFooter>
              <Button variant="secondary" type="button" onClick={onClose} disabled={laeuft}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={laeuft} className="inline-flex items-center gap-2">
                {laeuft ? <Spinner /> : methode === 'passkey' ? <Fingerprint className="w-4 h-4" /> : <Link2 className="w-4 h-4" />}
                {t('profile.linkedAccounts.linkContinue')}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
