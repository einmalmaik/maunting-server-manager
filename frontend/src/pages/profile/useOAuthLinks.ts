/**
 * Geteilte Typen und Hooks fuer die Profil-Tabs.
 *
 * Der {@link useOAuthLinks}-Hook kapselt das Laden der OAuth-User-Links und der
 * oeffentlich verfuegbaren Provider fuer `LinkedAccountsTab` (Verknuepfen/Loesen).
 * Ob ein Konto ein Passwort hat, steht an `user.has_password`, nicht an den Links.
 */
import { useCallback, useEffect, useState } from 'react'
import { oauthApi, type OAuthProviderPublic, type OAuthUserLink } from '@/api/oauth'
import { toast } from '@/stores/toastStore'

export interface OAuthLinksState {
  oauthLinks: OAuthUserLink[]
  oauthAvailable: OAuthProviderPublic[]
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
    loading,
    reload,
  }
}
