import type { Config } from 'tailwindcss'

/**
 * MSM Design System — Tailwind Configuration
 * Based on: Design idee für mssm / maunting_server_manager_msm/DESIGN.md
 *
 * Key Principles:
 * - Technical Calm: Deep hues for long monitoring sessions
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
        'surface': '#101417',
        'surface-dim': '#101417',
        'surface-bright': '#353a3d',
        'surface-container-lowest': '#0a0f12',
        'surface-container-low': '#181c1f',
        'surface-container': '#1c2023',
        'surface-container-high': '#262a2e',
        'surface-container-highest': '#313539',
        'surface-variant': '#313539',
        'surface-tint': '#acccd3',
        // Content colors
        'on-surface': '#dfe3e7',
        'on-surface-variant': '#c1c8c9',
        'on-background': '#dfe3e7',
        'background': '#101417',
        // Primary (Cyan/White)
        'primary': '#ffffff',
        'on-primary': '#15353a',
        'primary-container': '#c7e8ef',
        'on-primary-container': '#4b696f',
        'primary-fixed': '#c7e8ef',
        'primary-fixed-dim': '#acccd3',
        'on-primary-fixed': '#001f24',
        'on-primary-fixed-variant': '#2d4b51',
        'inverse-primary': '#456369',
        // Secondary (Singravault Blue)
        'secondary': '#0ea5e9',
        'on-secondary': '#ffffff',
        'secondary-container': '#0284c7',
        'on-secondary-container': '#f0f9ff',
        'secondary-fixed': '#38bdf8',
        'secondary-fixed-dim': '#0284c7',
        'on-secondary-fixed': '#0c4a6e',
        'on-secondary-fixed-variant': '#075985',
        'mint-accent': '#7dd3fc',
        // Tertiary (Ice Blue)
        'tertiary': '#ffffff',
        'on-tertiary': '#213145',
        'tertiary-container': '#d3e4fe',
        'on-tertiary-container': '#56657c',
        'tertiary-fixed': '#d3e4fe',
        'tertiary-fixed-dim': '#b7c8e1',
        'on-tertiary-fixed': '#0b1c30',
        'on-tertiary-fixed-variant': '#38485d',
        // Error / Status
        'error': '#ffb4ab',
        'on-error': '#690005',
        'error-container': '#93000a',
        'on-error-container': '#ffdad6',
        'status-success': 'hsl(158 64% 52%)',
        'status-warning': 'hsl(38 92% 50%)',
        'status-destructive': 'hsl(0 70% 55%)',
        // Infrastructure
        'outline': '#8b9293',
        'outline-variant': '#414849',
        'infrastructure-slate': '#475569',
        'cyan-glow': 'hsl(194 44% 68% / 0.18)',
        'deep-background': 'hsl(206 31% 4%)',
        // Inverse
        'inverse-surface': '#dfe3e7',
        'inverse-on-surface': '#2d3134',
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
        'accent-cta': '0 14px 30px hsl(194 44% 68% / 0.22)',
        'primary-glow': '0 0 20px rgba(255,255,255,0.15)',
        'primary-glow-hover': '0 0 30px rgba(255,255,255,0.25)',
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
        'headline-lg': ['32px', { lineHeight: '1.2', letterSpacing: '-0.02em', fontWeight: '700' }],
        'headline-lg-mobile': ['26px', { lineHeight: '1.2', fontWeight: '700' }],
        'headline-md': ['24px', { lineHeight: '1.3', fontWeight: '600' }],
        'body-lg': ['18px', { lineHeight: '1.6', fontWeight: '400' }],
        'body-md': ['16px', { lineHeight: '1.5', fontWeight: '400' }],
        'label-md': ['14px', { lineHeight: '1.4', letterSpacing: '0.05em', fontWeight: '500' }],
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
