import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { Loader } from '@/components/ui/Loader'
import { resolveRouteAccessState } from '@/services/routeAccess'

/** Route-Level-Guard: renders children only for allowed routes.
 *
 * No dashboard fallback: loading, forbidden and error stay explicit so RBAC
 * failures are not hidden as navigation.
 */
export function RequirePermission({
  routeKey,
  children,
}: {
  routeKey: string
  children?: React.ReactNode
}) {
  const me = usePermissionsStore((s) => s.me)
  const isLoading = usePermissionsStore((s) => s.isLoading)
  const error = usePermissionsStore((s) => s.error)
  const refresh = usePermissionsStore((s) => s.refresh)
  const accessState = resolveRouteAccessState({ routeKey, me, isLoading, error })

  useEffect(() => {
    if (accessState === 'loading' && !isLoading) {
      void refresh()
    }
  }, [accessState, isLoading, refresh])

  if (accessState === 'allowed') return <>{children}</>

  if (accessState === 'loading') {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader label="Maunting Server Manager" />
      </div>
    )
  }

  return <RouteAccessMessage state={accessState} />
}

function RouteAccessMessage({ state }: { state: 'forbidden' | 'notFound' | 'error' }) {
  const { t } = useTranslation()
  const copy = {
    forbidden: {
      title: t('routes.forbiddenTitle', 'Kein Zugriff'),
      body: t('routes.forbiddenBody', 'Dir fehlt die Berechtigung für diese Seite.'),
    },
    notFound: {
      title: t('routes.notFoundTitle', 'Seite nicht gefunden'),
      body: t('routes.notFoundBody', 'Diese Route ist im Panel nicht registriert.'),
    },
    error: {
      title: t('routes.errorTitle', 'Berechtigungen konnten nicht geladen werden'),
      body: t(
        'routes.errorBody',
        'Bitte lade die Seite erneut. Wenn das Problem bleibt, prüfe die Verbindung zum Panel.',
      ),
    },
  }[state]

  return (
    <section className="msm-card max-w-2xl p-6" role={state === 'error' ? 'alert' : 'status'}>
      <h1 className="font-headline text-headline-sm text-primary">{copy.title}</h1>
      <p className="font-body-md text-body-md text-on-surface-variant mt-2">{copy.body}</p>
    </section>
  )
}
