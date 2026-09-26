import { Copy, QrCode } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, MauntingQrCard } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

/**
 * Zeigt ein frisch erzeugtes Geheimnis genau einmal an.
 *
 * Der Wert lebt ausschliesslich im Zustand der aufrufenden Komponente — nie in
 * `localStorage`, nie in einer Liste, die neu befuellt wird. Nach einem
 * Neuladen ist er weg, und genau das heisst „genau einmal“: MSM speichert nur
 * einen Hash, ein verlorener Schluessel ist nur ueber eine Rotation zu
 * ersetzen.
 */
export function SecretOnce({
  label,
  value,
  qrDataUri,
  hinweis,
  onDismiss,
}: {
  label: string
  value: string
  qrDataUri?: string | null
  hinweis?: string
  onDismiss: () => void
}) {
  const { t } = useTranslation()

  return (
    <div className="msm-card space-y-5 border border-primary/40 bg-surface-container-high/90 p-6 shadow-2xl backdrop-blur-md">
      <div>
        <div className="flex items-center gap-2">
          <QrCode className="h-5 w-5 text-primary" aria-hidden="true" />
          <p className="text-base font-semibold text-on-surface">{t('hoster.secretOnce', { label })}</p>
        </div>
        <p className="mt-1 text-xs text-on-surface-variant">{hinweis || t('hoster.secretOnceHint')}</p>
      </div>

      <MauntingQrCard
        value={value}
        qrDataUri={qrDataUri}
        hint={t('mss.wizard.qrScanHint')}
      />

      <div>
        <span className="mb-1 block text-xs font-medium text-on-surface-variant">
          {t('ai.profile.devicesCodeLabel')}
        </span>
        <code className="block break-all rounded-xl bg-surface-container-lowest border border-outline-variant/30 p-3.5 text-center font-mono text-base tracking-widest font-bold text-primary select-all shadow-inner">
          {value}
        </code>
      </div>

      <div className="flex flex-wrap justify-end gap-2 pt-1">
        <Button
          type="button"
          variant="secondary"
          onClick={() => {
            void navigator.clipboard?.writeText(value)
            toast.success(t('hoster.copied'))
          }}
        >
          <Copy className="h-4 w-4" aria-hidden="true" />
          {t('common.copy')}
        </Button>
        <Button type="button" onClick={onDismiss}>
          {t('hoster.secretUnderstood')}
        </Button>
      </div>
    </div>
  )
}
