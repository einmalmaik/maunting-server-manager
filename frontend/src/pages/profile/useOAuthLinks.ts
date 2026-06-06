/**
 * Geteilte Typen und Hooks fuer die Profil-Tabs.
 *
 * Der {@link useOAuthLinks}-Hook kapselt das Laden der OAuth-User-Links und der
 * oeffentlich verfuegbaren Provider, weil sowohl `LinkedAccountsTab` (Verknuepfen/Loesen)
 * als auch `DangerZoneTab` (social-only Loeschen) darauf zugreifen.
 */
import { useCallback, useEffect, useState } from 'react'
import { oauthApi, type OAuthProviderPublic, type OAuthUserLink } from '@/api/oauth'
import { toast } from '@/stores/toastStore'

export interface OAuthLinksState {
  oauthLinks: OAuthUserLink[]
  oauthAvailable: OAuthProviderPublic[]
  isSocialOnly: boolean
  loading: boolean
  reload: () => Promise<void>
}

export function useOAuthLinks(): OAuthLinksState {
  const [oauthLinks, setOauthLinks] = useState<OAuthUserLink[]>([])
  const [oauthAvailable, setOauthAvailable] = useState<OAuthProviderPublic[]>([])
  const [loading, setLoading] = useState(true)

  const reload = useCallback(async () => {
    try {
      const [links, publicProviders] = await Promise.all([
        oauthApi.listMyLinks(),
        oauthApi.listPublicProviders(),
      ])
      setOauthLinks(links)
      setOauthAvailable(publicProviders)
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  return {
    oauthLinks,
    oauthAvailable,
    isSocialOnly: oauthLinks.length > 0,
    loading,
    reload,
  }
}
