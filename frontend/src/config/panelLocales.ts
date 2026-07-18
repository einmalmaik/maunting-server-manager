/** Panel UI languages (DE/EN only — no locale dropdown). */
export const panelLanguageCodes = ['de', 'en'] as const
export type PanelLanguageCode = (typeof panelLanguageCodes)[number]

export function normalizePanelLanguage(code: unknown): PanelLanguageCode {
  return typeof code === 'string' && code.toLowerCase().startsWith('de') ? 'de' : 'en'
}
