export type WallpaperPresetId = 'heimisch' | 'midnight' | 'cyber' | 'minimal' | 'custom'

export interface ChatWallpaperConfig {
  preset: WallpaperPresetId
  customDataUrl?: string
  dimLevel: number // 0 to 80 percent dimming for optimal text contrast
}

export const WALLPAPER_STORAGE_KEY = 'msm_chat_wallpaper_config'

export const DEFAULT_WALLPAPER_CONFIG: ChatWallpaperConfig = {
  preset: 'cyber',
  dimLevel: 25,
}

export function loadChatWallpaperConfig(): ChatWallpaperConfig {
  try {
    const raw = localStorage.getItem(WALLPAPER_STORAGE_KEY)
    if (!raw) return DEFAULT_WALLPAPER_CONFIG
    const parsed = JSON.parse(raw) as Partial<ChatWallpaperConfig>
    return {
      preset: parsed.preset || 'cyber',
      customDataUrl: parsed.customDataUrl,
      dimLevel: typeof parsed.dimLevel === 'number' ? parsed.dimLevel : 25,
    }
  } catch {
    return DEFAULT_WALLPAPER_CONFIG
  }
}

export function saveChatWallpaperConfig(config: ChatWallpaperConfig): void {
  try {
    localStorage.setItem(WALLPAPER_STORAGE_KEY, JSON.stringify(config))
  } catch {
    // Non-fatal if localStorage quota exceeded
  }
}

/**
 * MSM Heimisch: Eigens gestaltetes, nahtloses Maunting Studios & Singra
 * Vektor-Doodle-Muster (Server, Terminal >_, E2EE-Schilde, Nodes, CPU-Chips, Sterne).
 * Dezent, warm, heimisch und professionell bei ca. 5% Deckkraft.
 */
export const MSM_HEIMISCH_SVG_PATTERN = `
<svg width="240" height="240" viewBox="0 0 240 240" xmlns="http://www.w3.org/2000/svg" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
  <!-- Server Rack 1 -->
  <g transform="translate(18, 16) scale(0.9)" opacity="0.85">
    <rect x="0" y="0" width="38" height="26" rx="3" />
    <line x1="6" y1="9" x2="22" y2="9" />
    <line x1="6" y1="17" x2="18" y2="17" />
    <circle cx="31" cy="9" r="1.5" fill="currentColor" />
    <circle cx="31" cy="17" r="1.5" fill="currentColor" />
  </g>

  <!-- E2EE Shield Lock -->
  <g transform="translate(140, 18) scale(0.9)" opacity="0.85">
    <path d="M14 2L4 6v6c0 6.5 4.3 12.6 10 14 5.7-1.4 10-7.5 10-14V6l-10-4z" />
    <circle cx="14" cy="12" r="2" />
    <path d="M14 14v4" />
  </g>

  <!-- Terminal Window >_ -->
  <g transform="translate(75, 68) scale(0.85)" opacity="0.85">
    <rect x="0" y="0" width="36" height="24" rx="3" />
    <line x1="0" y1="7" x2="36" y2="7" />
    <path d="M6 13l4 3-4 3" />
    <line x1="14" y1="19" x2="22" y2="19" />
    <circle cx="5" cy="3.5" r="1" fill="currentColor" />
    <circle cx="9" cy="3.5" r="1" fill="currentColor" />
  </g>

  <!-- Cloud Node -->
  <g transform="translate(190, 72) scale(0.85)" opacity="0.8">
    <path d="M6 14a4 4 0 0 1 7.8-1.2A3.5 3.5 0 0 1 20 16a3.5 3.5 0 0 1-3.5 3.5H6A4 4 0 0 1 6 14z" />
    <circle cx="11" cy="23" r="1.2" fill="currentColor" />
    <line x1="11" y1="19.5" x2="11" y2="22" />
  </g>

  <!-- Database Cylinder -->
  <g transform="translate(15, 95) scale(0.85)" opacity="0.8">
    <ellipse cx="12" cy="5" rx="10" ry="3.5" />
    <path d="M2 5v12c0 2 4.5 3.5 10 3.5s10-1.5 10-3.5V5" />
    <path d="M2 11c0 2 4.5 3.5 10 3.5s10-1.5 10-3.5" />
  </g>

  <!-- Sparkles / Little Stars -->
  <path d="M135 100l1.5 3.5 3.5 1.5-3.5 1.5-1.5 3.5-1.5-3.5-3.5-1.5 3.5-1.5z" fill="currentColor" opacity="0.75" />
  <path d="M55 45l1 2 2 1-2 1-1 2-1-2-2-1 2-1z" fill="currentColor" opacity="0.7" />
  <path d="M225 35l1 2.5 2.5 1-2.5 1-1 2.5-1-2.5-2.5-1 2.5-1z" fill="currentColor" opacity="0.7" />

  <!-- Maunting / Singra Crest / Hex Shield -->
  <g transform="translate(145, 138) scale(0.9)" opacity="0.85">
    <polygon points="12,1 23,7 23,19 12,25 1,19 1,7" />
    <path d="M12 6v14M6 9.5l12 7M6 16.5l12-7" stroke-width="0.8" />
  </g>

  <!-- CPU Microchip -->
  <g transform="translate(30, 160) scale(0.85)" opacity="0.8">
    <rect x="4" y="4" width="20" height="20" rx="2" />
    <rect x="8" y="8" width="12" height="12" rx="1" />
    <line x1="9" y1="0" x2="9" y2="4" />
    <line x1="14" y1="0" x2="14" y2="4" />
    <line x1="19" y1="0" x2="19" y2="4" />
    <line x1="9" y1="24" x2="9" y2="28" />
    <line x1="14" y1="24" x2="14" y2="28" />
    <line x1="19" y1="24" x2="19" y2="28" />
    <line x1="0" y1="9" x2="4" y2="9" />
    <line x1="0" y1="14" x2="4" y2="14" />
    <line x1="0" y1="19" x2="4" y2="19" />
    <line x1="24" y1="9" x2="28" y2="9" />
    <line x1="24" y1="14" x2="28" y2="14" />
    <line x1="24" y1="19" x2="28" y2="19" />
  </g>

  <!-- Connection Nodes / Network Mesh -->
  <g transform="translate(95, 175) scale(0.85)" opacity="0.75">
    <circle cx="4" cy="18" r="2.5" />
    <circle cx="20" cy="6" r="2.5" />
    <circle cx="32" cy="22" r="2.5" />
    <line x1="6.5" y1="16.5" x2="17.5" y2="7.5" />
    <line x1="22" y1="8" x2="30" y2="20" />
    <line x1="6.5" y1="18.5" x2="29.5" y2="21.5" stroke-dasharray="2 2" />
  </g>

  <!-- Chat Bubble Icon -->
  <g transform="translate(195, 185) scale(0.85)" opacity="0.8">
    <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5z" />
  </g>

  <!-- Little Dots & Crosses -->
  <circle cx="108" cy="30" r="1" fill="currentColor" opacity="0.6" />
  <circle cx="80" cy="140" r="1" fill="currentColor" opacity="0.6" />
  <circle cx="215" cy="135" r="1" fill="currentColor" opacity="0.6" />
</svg>
`

export const HEIMISCH_PATTERN_DATA_URI = `data:image/svg+xml;utf8,${encodeURIComponent(MSM_HEIMISCH_SVG_PATTERN)}`
