/**
 * Single Source of Truth for logo assets.
 *
 * The app uses `frontend/public/logo.png` as the canonical logo.
 * Email templates embed their own optimized base64 version server-side.
 */
export const LOGO_CONFIG = {
  /** Relative path used by the React frontend (served from `public/`) */
  src: '/logo.png',
  /** Absolute URL used by email templates and external services */
  emailUrl: 'https://panel.mauntingstudios.de/logo.png',
  alt: 'MauntingStudios',
} as const
