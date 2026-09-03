import { useState, useEffect } from 'react'
import { User as UserIcon } from 'lucide-react'
import { apiUrl } from '@/config/api'

export interface AvatarProps {
  src?: string | null
  name?: string | null
  size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl'
  status?: 'online' | 'offline' | 'idle' | 'dnd' | null
  className?: string
  alt?: string
  resolveUrl?: (url: string) => string
}

const sizeClasses = {
  xs: 'h-6 w-6 text-[10px]',
  sm: 'h-8 w-8 text-xs',
  md: 'h-10 w-10 text-sm',
  lg: 'h-12 w-12 text-base font-semibold',
  xl: 'h-20 w-20 text-2xl font-bold',
}

const iconSizes = {
  xs: 'h-3 w-3',
  sm: 'h-4 w-4',
  md: 'h-5 w-5',
  lg: 'h-6 w-6',
  xl: 'h-10 w-10',
}

const statusIndicatorSizes = {
  xs: 'h-1.5 w-1.5 -bottom-0.5 -right-0.5 border',
  sm: 'h-2 w-2 bottom-0 right-0 border-2',
  md: 'h-2.5 w-2.5 bottom-0 right-0 border-2',
  lg: 'h-3.5 w-3.5 bottom-0.5 right-0.5 border-2',
  xl: 'h-5 w-5 bottom-1 right-1 border-[3px]',
}

const statusColors = {
  online: 'bg-status-success',
  offline: 'bg-on-surface-variant/45',
  idle: 'bg-status-warning',
  dnd: 'bg-status-danger',
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
  const [hasError, setHasError] = useState(false)

  // Reset error when src changes
  useEffect(() => {
    setHasError(false)
  }, [src])

  const initials = name
    ? name.trim().slice(0, 2).toUpperCase()
    : ''

  const resolvedSrc = src ? (resolveUrl ? resolveUrl(src) : apiUrl(src)) : undefined
  const showImage = Boolean(resolvedSrc) && !hasError

  return (
    <div className={`relative inline-flex shrink-0 select-none ${className}`}>
      <div
        className={`rounded-full flex items-center justify-center overflow-hidden border border-outline-variant/40 bg-surface-container-high text-primary font-medium ${sizeClasses[size]}`}
      >
        {showImage ? (
          <img
            src={resolvedSrc!}
            alt={alt || name || 'Avatar'}
            onError={() => setHasError(true)}
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
