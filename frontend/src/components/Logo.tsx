import { LOGO_CONFIG } from '@/config/logo'

interface LogoProps {
  size?: 'sm' | 'md' | 'lg'
}

const sizeClasses = {
  sm: 'w-6 h-6',
  md: 'w-10 h-10',
  lg: 'w-16 h-16',
} as const

/**
 * Central Logo component.
 *
 * Displays the configured logo image and falls back to the text
 * placeholder "M" when the image fails to load (e.g. file not present).
 *
 * Scaling is handled purely via CSS — no JS resize logic.
 */
export function Logo({ size = 'md' }: LogoProps) {
  const cls = sizeClasses[size]

  return (
    <div className={`${cls} relative flex-shrink-0`}>
      <img
        src={LOGO_CONFIG.src}
        alt={LOGO_CONFIG.alt}
        className={`${cls} object-contain block rounded-md`}
        onError={(e) => {
          e.currentTarget.style.display = 'none'
          const fallback = e.currentTarget.nextElementSibling as HTMLElement | null
          if (fallback) fallback.style.display = 'flex'
        }}
      />
      <div
        className={`${cls} absolute inset-0 rounded-md bg-primary items-center justify-center text-on-primary font-headline text-headline-md font-extrabold hidden`}
        aria-hidden="true"
      >
        M
      </div>
    </div>
  )
}
