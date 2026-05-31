import type { Config } from 'tailwindcss'

/**
 * MSM Design System — Tailwind Configuration
 * Based on: Design idee für mssm / maunting_server_manager_msm/DESIGN.md
 *
 * Key Principles:
 * - Technical Calm: deep neutral surfaces for long monitoring sessions
 * - Precision: Fine lines and monospaced accents
 * - Glassmorphism: Strategic elevation and focus
 * - Infrastructure-First: Dense layouts for logs/metrics
 */

const config: Config = {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // MSM Surface Elevation
        'surface': '#071013',
        'surface-dim': '#04090b',
        'surface-bright': '#253238',
        'surface-container-lowest': '#03080a',
        'surface-container-low': '#0b1518',
        'surface-container': '#101b1f',
        'surface-container-high': '#162328',
        'surface-container-highest': '#203038',
        'surface-variant': '#22343b',
        'surface-tint': '#9deeff',
        // Content colors
        'on-surface': '#e7f4f7',
        'on-surface-variant': '#a9bdc3',
        'on-background': '#e7f4f7',
        'background': '#071013',
        // Shadcn-compatible aliases used by existing central components
        'foreground': '#e7f4f7',
        'muted': '#162328',
        'muted-foreground': '#9db3b8',
        'border': '#284147',
        'input': '#284147',
        'ring': '#67e8f9',
        'card': '#101b1f',
        'card-foreground': '#e7f4f7',
        // Primary (Logo ice cyan)
        'primary': '#b9f6ff',
        'primary-foreground': '#031316',
        'on-primary': '#031316',
        'primary-container': '#0c3b45',
        'on-primary-container': '#d9fbff',
        'primary-fixed': '#b9f6ff',
        'primary-fixed-dim': '#67e8f9',
        'on-primary-fixed': '#031316',
        'on-primary-fixed-variant': '#0f3f47',
        'inverse-primary': '#0e7490',
        // Secondary (controlled teal/mint accent)
        'secondary': '#5eead4',
        'secondary-foreground': '#031316',
        'on-secondary': '#031316',
        'secondary-container': '#0f766e',
        'on-secondary-container': '#ecfeff',
        'secondary-fixed': '#5eead4',
        'secondary-fixed-dim': '#2dd4bf',
        'on-secondary-fixed': '#031316',
        'on-secondary-fixed-variant': '#134e4a',
        'mint-accent': '#86efac',
        // Tertiary (Ice Blue)
        'tertiary': '#d9f7ff',
        'on-tertiary': '#06222a',
        'tertiary-container': '#12323b',
        'on-tertiary-container': '#d9f7ff',
        'tertiary-fixed': '#d9f7ff',
        'tertiary-fixed-dim': '#9deeff',
        'on-tertiary-fixed': '#06222a',
        'on-tertiary-fixed-variant': '#164653',
        // Error / Status
        'error': '#ffb4ab',
        'on-error': '#690005',
        'error-container': '#93000a',
        'on-error-container': '#ffdad6',
        'status-success': 'hsl(158 64% 52%)',
        'status-warning': 'hsl(38 92% 50%)',
        'status-destructive': 'hsl(0 70% 55%)',
        'status-error': 'hsl(0 70% 55%)',
        'destructive': 'hsl(0 70% 55%)',
        'destructive-foreground': '#fff1f2',
        // Infrastructure
        'outline': '#5b737a',
        'outline-variant': '#284147',
        'infrastructure-slate': '#475569',
        'cyan-glow': 'hsl(190 92% 62% / 0.16)',
        'deep-background': 'hsl(206 31% 4%)',
        // Inverse
        'inverse-surface': '#e7f4f7',
        'inverse-on-surface': '#0b1518',
      },
      borderRadius: {
        'sm': '0.25rem',
        'DEFAULT': '0.25rem',
        'md': '0.5rem',
        'lg': '0.5rem',
        'xl': '0.75rem',
        '2xl': '1rem',
        '3xl': '1.25rem',
        'full': '9999px',
      },
      boxShadow: {
        'panel': '0 20px 48px hsl(0 0% 0% / 0.38)',
        'panel-strong': '0 24px 56px hsl(0 0% 0% / 0.45)',
        'accent-cta': '0 14px 30px hsl(190 92% 62% / 0.22)',
        'primary-glow': '0 0 20px hsl(190 92% 62% / 0.18)',
        'primary-glow-hover': '0 0 30px hsl(190 92% 62% / 0.28)',
      },
      fontFamily: {
        'headline': ['Manrope', 'system-ui', 'sans-serif'],
        'headline-md': ['Manrope', 'system-ui', 'sans-serif'],
        'headline-lg': ['Manrope', 'system-ui', 'sans-serif'],
        'headline-lg-mobile': ['Manrope', 'system-ui', 'sans-serif'],
        'body-md': ['Inter', 'system-ui', 'sans-serif'],
        'body-lg': ['Inter', 'system-ui', 'sans-serif'],
        'label-md': ['IBM Plex Sans', 'system-ui', 'sans-serif'],
        'mono-sm': ['JetBrains Mono', 'monospace'],
        'sans': ['Inter', 'system-ui', 'sans-serif'],
        'mono': ['JetBrains Mono', 'monospace'],
      },
      fontSize: {
        'headline-lg': ['32px', { lineHeight: '1.2', letterSpacing: '0', fontWeight: '700' }],
        'headline-lg-mobile': ['26px', { lineHeight: '1.2', fontWeight: '700' }],
        'headline-md': ['24px', { lineHeight: '1.3', fontWeight: '600' }],
        'body-lg': ['18px', { lineHeight: '1.6', fontWeight: '400' }],
        'body-md': ['16px', { lineHeight: '1.5', fontWeight: '400' }],
        'label-md': ['14px', { lineHeight: '1.4', letterSpacing: '0', fontWeight: '500' }],
        'mono-sm': ['13px', { lineHeight: '1.5', fontWeight: '400' }],
      },
      spacing: {
        'unit': '4px',
        'gutter': '1.25rem',
        'margin-mobile': '1rem',
        'margin-desktop': '2.5rem',
        'panel-padding': '1.5rem',
        'stack-compact': '0.5rem',
        'stack-default': '1rem',
      },
      backgroundImage: {
        'deep-grid': `linear-gradient(to right, rgba(65, 72, 73, 0.1) 1px, transparent 1px),
                       linear-gradient(to bottom, rgba(65, 72, 73, 0.1) 1px, transparent 1px)`,
      },
      backgroundSize: {
        'deep-grid': '40px 40px',
      },
    },
  },
  plugins: [],
}

export default config
