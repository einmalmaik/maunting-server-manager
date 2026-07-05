import { api } from './client'

export interface PublicLegalSettings {
  imprint_enabled: boolean
  imprint_url: string
}

export async function getPublicLegalSettings(): Promise<PublicLegalSettings> {
  return api<PublicLegalSettings>('/system/legal')
}
