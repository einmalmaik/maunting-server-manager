import { createContext, useContext } from 'react'

export const PrivacyNoticeVisibilityContext = createContext(false)

export function usePrivacyNoticeVisible(): boolean {
  return useContext(PrivacyNoticeVisibilityContext)
}
