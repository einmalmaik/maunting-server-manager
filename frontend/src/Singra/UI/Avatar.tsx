import { useState, useEffect } from 'react'
import { User as UserIcon } from 'lucide-react'
import { apiUrl, getIsAbsoluteApi } from '@/config/api'
import { apiStream } from '@/api/client'

export interface AvatarProps {
  src?: string | null
  name?: string | null
  size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl' | '2xl'
  status?: 'online' | 'offline' | 'idle' | 'dnd' | null
  className?: string
  alt?: string
  resolveUrl?: (url: string) => string
}

/**
 * Die Größe sitzt am äußeren Element, und der Kreis füllt es aus.
 *
 * Bis 09/2026 war es umgekehrt: `className` landete außen, `size` maß den Kreis
 * innen. Wer beides setzte — und das taten alle, die etwas Größeres als `xl`
 * brauchten —, bekam einen Kasten in der einen und einen Kreis in der anderen
 * Größe. Der Kreis saß dann oben links statt in der Mitte, und ein Ring, der
 * sich am äußeren Kasten ausrichtete, lag sichtbar daneben. Im Anruf fiel das
 * auf: der pulsierende Ring war 112 Pixel groß, das Bild darin 80.
 *
 * `2xl` gibt es, damit der Anruf keine eigenen Maße mehr erfinden muss.
 */
const sizeClasses = {
  xs: 'h-6 w-6 text-[10px]',
  sm: 'h-8 w-8 text-xs',
  md: 'h-10 w-10 text-sm',
  lg: 'h-12 w-12 text-base font-semibold',
  xl: 'h-20 w-20 text-2xl font-bold',
  '2xl': 'h-28 w-28 text-3xl font-bold',
}

const iconSizes = {
  xs: 'h-3 w-3',
  sm: 'h-4 w-4',
  md: 'h-5 w-5',
  lg: 'h-6 w-6',
  xl: 'h-10 w-10',
  '2xl': 'h-14 w-14',
}

const statusIndicatorSizes = {
  xs: 'h-1.5 w-1.5 -bottom-0.5 -right-0.5 border',
  sm: 'h-2 w-2 bottom-0 right-0 border-2',
  md: 'h-2.5 w-2.5 bottom-0 right-0 border-2',
  lg: 'h-3.5 w-3.5 bottom-0.5 right-0.5 border-2',
  xl: 'h-5 w-5 bottom-1 right-1 border-[3px]',
  '2xl': 'h-6 w-6 bottom-1.5 right-1.5 border-[3px]',
}

const statusColors = {
  online: 'bg-status-success',
  offline: 'bg-on-surface-variant/45',
  idle: 'bg-status-warning',
  dnd: 'bg-status-destructive',
}

const MAX_BLOB_CACHE = 200
const blobCache = new Map<string, string>()
const pendingBlobFetches = new Map<string, Promise<string | null>>()

function pruneBlobCache() {
  while (blobCache.size > MAX_BLOB_CACHE) {
    const firstKey = blobCache.keys().next().value
    if (firstKey) {
      const oldUrl = blobCache.get(firstKey)
      if (oldUrl) URL.revokeObjectURL(oldUrl)
      blobCache.delete(firstKey)
    } else {
      break
    }
  }
}

async function fetchImageBlobUrl(url: string): Promise<string | null> {
  if (blobCache.has(url)) return blobCache.get(url)!
  if (pendingBlobFetches.has(url)) return pendingBlobFetches.get(url)!

  const promise = (async () => {
    try {
      const res = await apiStream(url, { method: 'GET', headers: { Accept: 'image/*' } })
      if (!res.ok) return null
      const blob = await res.blob()
      if (!blob || blob.size === 0) return null
      const blobUrl = URL.createObjectURL(blob)
      pruneBlobCache()
      blobCache.set(url, blobUrl)
      return blobUrl
    } catch {
      return null
    } finally {
      pendingBlobFetches.delete(url)
    }
  })()

  pendingBlobFetches.set(url, promise)
  return promise
}

function checkIsDesktopContext(): boolean {
  if (getIsAbsoluteApi()) return true
  if (typeof window === 'undefined') return false
  return '__TAURI_INTERNALS__' in window || '__TAURI__' in window
}

export function Avatar({
  src,
  name,
  size = 'md',
  status,
  className = '',
  alt,
  resolveUrl,
}: AvatarProps) {
  const resolvedSrc = src ? (resolveUrl ? resolveUrl(src) : apiUrl(src)) : undefined
  const [blobSrc, setBlobSrc] = useState<string | null>(() => (resolvedSrc ? blobCache.get(resolvedSrc) ?? null : null))
  const [hasError, setHasError] = useState(false)

  // Reset error & update blobSrc when resolvedSrc changes (e.g. backend URL hydrated or avatar updated)
  useEffect(() => {
    setHasError(false)
    if (resolvedSrc && blobCache.has(resolvedSrc)) {
      setBlobSrc(blobCache.get(resolvedSrc)!)
    } else {
      setBlobSrc(null)
      // In Desktop Tauri / WebView2, standard <img> cannot pass Bearer headers cross-origin.
      // Eagerly fetch authenticated blob so avatars display without initial 401 delay.
      const isDesktopContext = checkIsDesktopContext()
      if (isDesktopContext && resolvedSrc && !resolvedSrc.startsWith('data:') && !resolvedSrc.startsWith('blob:')) {
        void fetchImageBlobUrl(resolvedSrc).then((fallbackUrl) => {
          if (fallbackUrl) {
            setBlobSrc(fallbackUrl)
          }
        })
      }
    }
  }, [resolvedSrc])

  const handleImageError = () => {
    if (!resolvedSrc) {
      setHasError(true)
      return
    }

    if (blobSrc) {
      setHasError(true)
      return
    }

    // In Desktop Tauri / WebView2 or cross-origin scenarios, standard <img> fails due to mixed-content or auth.
    // Fetching via apiStream as blob bypasses these restrictions.
    void fetchImageBlobUrl(resolvedSrc).then((fallbackUrl) => {
      if (fallbackUrl) {
        setBlobSrc(fallbackUrl)
      } else {
        setHasError(true)
      }
    })
  }

  const initials = name
    ? name.trim().slice(0, 2).toUpperCase()
    : ''

  const isDesktopContext = checkIsDesktopContext()
  const isDirectSafe = !resolvedSrc || resolvedSrc.startsWith('data:') || resolvedSrc.startsWith('blob:')
  const currentDisplaySrc = blobSrc || (isDesktopContext && !isDirectSafe ? null : resolvedSrc)
  const showImage = Boolean(currentDisplaySrc) && !hasError

  return (
    <div
      className={`relative inline-flex shrink-0 select-none rounded-full ${sizeClasses[size]} ${className}`}
    >
      <div className="h-full w-full rounded-full flex items-center justify-center overflow-hidden border border-outline-variant/40 bg-surface-container-high text-primary font-medium">
        {showImage ? (
          <img
            src={currentDisplaySrc!}
            alt={alt || name || 'Avatar'}
            onError={handleImageError}
            className="h-full w-full object-cover"
            loading="lazy"
          />
        ) : initials ? (
          <span>{initials}</span>
        ) : (
          <UserIcon className={`${iconSizes[size]} text-on-surface-variant`} aria-hidden="true" />
        )}
      </div>

      {status && (
        <span
          className={`absolute rounded-full border-background ${statusColors[status]} ${statusIndicatorSizes[size]}`}
          aria-label={status}
        />
      )}
    </div>
  )
}
