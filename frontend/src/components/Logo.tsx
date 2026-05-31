import { LOGO_CONFIG } from '@/config/logo'
import { useState } from 'react'

interface LogoProps {
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const sizeClasses = {
  sm: 'w-6 h-6',
  md: 'w-10 h-10',
  lg: 'w-16 h-16',
} as const

/**
 * Central Logo component.
 *
 * Displays the configured MSM logo and falls back to "MSM" when the image
 * fails to load.
 *
 * Scaling is handled purely via CSS — no JS resize logic.
 */
export function Logo({ size = 'md', className = '' }: LogoProps) {
  const [failed, setFailed] = useState(false)
  const cls = sizeClasses[size]

  return (
    <div className={`${cls} relative flex-shrink-0 ${className}`}>
      {!failed ? (
        <img
          src={LOGO_CONFIG.src}
          alt={LOGO_CONFIG.alt}
          className={`${cls} block object-contain drop-shadow-[0_0_12px_hsl(190_92%_62%_/_0.28)]`}
          onError={() => setFailed(true)}
        />
      ) : (
        <div
          className={`${cls} flex items-center justify-center rounded-md border border-primary/30 bg-primary/10 text-[10px] font-bold text-primary`}
          aria-label={LOGO_CONFIG.alt}
          role="img"
        >
          MSM
        </div>
      )}
    </div>
  )
}
