import { useEffect, useState } from 'react'
import { Copy, QrCode } from 'lucide-react'
import QRCode from 'qrcode'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import firmenLogo from '@/desktop/assets/firmen-logo.png'
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
  const [generiertesQr, setGeneriertesQr] = useState<string | null>(qrDataUri || null)

  useEffect(() => {
    if (qrDataUri) {
      setGeneriertesQr(qrDataUri)
      return
    }
    // Lokale Client-seitige Level-H QR-Code-Generierung als robuster Fallback
    if (value) {
      QRCode.toDataURL(value.trim(), {
        errorCorrectionLevel: 'H', // 30% Fehlertoleranz fuer Logo-Einbettung und Sofort-Scan
        margin: 2,
        scale: 10,
        color: {
          dark: '#020617',
          light: '#ffffff',
        },
      })
        .then((url) => setGeneriertesQr(url))
        .catch(() => {})
    }
  }, [value, qrDataUri])

  return (
    <div className="msm-card space-y-5 border border-primary/40 bg-surface-container-high/90 p-6 shadow-2xl backdrop-blur-md">
      <div>
        <div className="flex items-center gap-2">
          <QrCode className="h-5 w-5 text-primary" aria-hidden="true" />
          <p className="text-base font-semibold text-on-surface">{t('hoster.secretOnce', { label })}</p>
        </div>
        <p className="mt-1 text-xs text-on-surface-variant">{hinweis || t('hoster.secretOnceHint')}</p>
      </div>

      {generiertesQr && (
        <div className="flex flex-col items-center justify-center gap-3 rounded-2xl bg-surface-container-lowest/80 p-5 border border-primary/25 shadow-[0_0_35px_rgba(56,189,248,0.12)]">
          <div className="relative inline-flex items-center justify-center rounded-2xl bg-white p-3.5 shadow-xl ring-4 ring-primary/20">
            <img
              src={generiertesQr}
              alt="QR-Code"
              className="h-48 w-48 sm:h-56 sm:w-56 object-contain rounded-lg"
              draggable={false}
            />
            {/* Zentriertes Marken-Emblem (wie Discord/Steam) */}
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-slate-950 p-1.5 shadow-2xl border-2 border-primary ring-2 ring-primary/40">
                <img
                  src={firmenLogo}
                  alt="MauntingStudios"
                  className="h-full w-full object-contain rounded-md"
                  draggable={false}
                />
              </div>
            </div>
          </div>
          <p className="text-center text-xs font-medium text-on-surface-variant max-w-sm">
            {t('mss.wizard.qrScanHint', 'Diesen QR-Code direkt mit der Smartphone- oder Desktop-Kamera scannen.')}
          </p>
        </div>
      )}

      <div>
        <span className="mb-1 block text-xs font-medium text-on-surface-variant">
          {t('ai.profile.devicesCodeLabel', 'Kopplungscode (manuelle Eingabe)')}
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
          {t('common.copy', 'Kopieren')}
        </Button>
        <Button type="button" onClick={onDismiss}>
          {t('hoster.secretUnderstood', 'Fertig')}
        </Button>
      </div>
    </div>
  )
}
