import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { useEffect } from 'react'
import { apiUrl } from '@/config/api'
import { oauthApi, type OAuthUserLink } from '@/api/oauth'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'
import { Link2, Unlink } from 'lucide-react'
import { useOAuthLinks } from './useOAuthLinks'

/**
 * Tab: Verknuepfte OAuth-Accounts.
 * Liest Links + verfuegbare Provider, bietet Link/Unlink, wertet den
 * OAuth-Callback-URL-Param `?linked=1` / `?error=...` aus.
 */
export function LinkedAccountsTab() {
  const { t, i18n } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const { oauthLinks, oauthAvailable, loading, reload } = useOAuthLinks()

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
    <div className="msm-card p-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="w-10 h-10 rounded-full bg-surface-container-highest flex items-center justify-center">
          <Link2 className="w-5 h-5 text-secondary" />
        </div>
        <div>
          <h2 className="font-headline text-headline-sm text-primary">{t('profile.linkedAccounts.title')}</h2>
          <p className="font-body-md text-sm text-on-surface-variant mt-1">
            {t('profile.linkedAccounts.subtitle')}
          </p>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-24">
          <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
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
                  <button
                    type="button"
                    onClick={() => handleUnlink(link)}
                    className="msm-btn-secondary px-3 py-1.5 text-xs inline-flex items-center gap-1.5"
                  >
                    <Unlink className="w-3.5 h-3.5" />
                    {t('profile.linkedAccounts.unlink')}
                  </button>
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
                  <a
                    key={p.slug}
                    href={apiUrl(`/oauth/${p.slug}/link/start`)}
                    className="msm-btn-secondary px-3 py-2 text-sm inline-flex items-center gap-2"
                  >
                    <Link2 className="w-3.5 h-3.5" />
                    {p.name}
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
