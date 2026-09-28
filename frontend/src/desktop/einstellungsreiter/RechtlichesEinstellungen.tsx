import { ExternalLink, ShieldCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { usePublicLegalSettings } from '@/hooks/usePublicLegalSettings'
import { Badge, Button } from '@/Singra/UI'
import { oeffneBrowser } from '../tauri'

export function RechtlichesEinstellungen() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const legal = usePublicLegalSettings()

  async function impressumOeffnen(url: string) {
    try {
      await oeffneBrowser(url)
    } catch {
      window.open(url, '_blank', 'noopener,noreferrer')
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Slogan & Philosophie */}
      <section className="msm-card bg-surface-container-low/40 p-5">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-primary/10 p-2.5 text-primary">
            <ShieldCheck className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h2 className="font-headline text-base font-semibold text-on-surface">
              {t('mss.einstellungen.rechtliches.slogan')}
            </h2>
            <p className="mt-1 text-xs text-on-surface-variant">
              {t('mss.einstellungen.rechtliches.beschreibung')}
            </p>
          </div>
        </div>
      </section>

      {/* Datenschutzerklärung */}
      <section className="msm-card flex flex-col gap-4 p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium text-on-surface">
              {t('mss.einstellungen.rechtliches.datenschutzTitel')}
            </h3>
            <Badge variant="default">
              {t('mss.einstellungen.rechtliches.datenschutzVersion', { version: 'v2.7' })}
            </Badge>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => navigate('/privacy')}
            className="shrink-0"
          >
            {t('mss.einstellungen.rechtliches.datenschutzOeffnen')}
          </Button>
        </div>
      </section>

      {/* Impressum */}
      <section className="msm-card flex flex-col gap-4 p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-medium text-on-surface">
                {t('mss.einstellungen.rechtliches.impressumTitel')}
              </h3>
              <Badge
                variant={legal.imprint_enabled && legal.imprint_url ? 'success' : 'default'}
              >
                {legal.imprint_enabled && legal.imprint_url
                  ? t('mss.einstellungen.rechtliches.impressumAktiv')
                  : t('mss.einstellungen.rechtliches.impressumInaktiv')}
              </Badge>
            </div>
            {legal.imprint_enabled && legal.imprint_url && (
              <p className="mt-2 text-xs font-mono text-primary truncate max-w-md">
                {legal.imprint_url}
              </p>
            )}
          </div>
          {legal.imprint_enabled && legal.imprint_url && (
            <Button
              variant="primary"
              size="sm"
              onClick={() => void impressumOeffnen(legal.imprint_url)}
              className="shrink-0"
            >
              <ExternalLink className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              {t('mss.einstellungen.rechtliches.impressumOeffnen')}
            </Button>
          )}
        </div>
      </section>
    </div>
  )
}
