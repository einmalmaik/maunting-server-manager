/**
 * Single Source of Truth for logo assets.
 *
 * To replace the placeholder with a real logo:
 * 1. Put your logo file (SVG or PNG) into `frontend/public/logo.svg`.
 * 2. Update `emailUrl` below to the absolute public URL.
 *
 * The Logo component will automatically display the image when it loads
 * and gracefully fall back to the text placeholder otherwise.
 */
export const LOGO_CONFIG = {
  /** Relative path used by the React frontend (served from `public/`) */
  src: '/logo.png',
  /** Absolute URL used by email templates and external services */
  emailUrl: 'https://panel.mauntingstudios.de/logo.svg',
  alt: 'MauntingStudios',
} as const
