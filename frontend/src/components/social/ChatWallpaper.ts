export type WallpaperPresetId = 'cyber' | 'midnight' | 'petrol' | 'minimal' | 'custom'

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
    const validPresets: WallpaperPresetId[] = ['cyber', 'midnight', 'petrol', 'minimal', 'custom']
    const preset = parsed.preset && validPresets.includes(parsed.preset as WallpaperPresetId)
      ? (parsed.preset as WallpaperPresetId)
      : 'cyber'
    return {
      preset,
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

