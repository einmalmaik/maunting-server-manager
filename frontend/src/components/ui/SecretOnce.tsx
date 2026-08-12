import { Copy } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

/**
 * Zeigt ein frisch erzeugtes Geheimnis genau einmal an.
 *
 * Der Wert lebt ausschliesslich im Zustand der aufrufenden Komponente — nie in
 * `localStorage`, nie in einer Liste, die neu befuellt wird. Nach einem
 * Neuladen ist er weg, und genau das heisst „genau einmal“: MSM speichert nur
 * einen Hash, ein verlorener Schluessel ist nur ueber eine Rotation zu
 * ersetzen.
 *
 * Stand hier lange als lokale, nicht exportierte Funktion in `HosterTab.tsx`.
 * Mit den KI-Werkzeugen gibt es einen **zweiten** Ort, an dem ein Schluessel
 * entsteht — die Vorschlagskarte im Chat, nach dem Anlegen einer Integration.
 * Eine Abschrift haette bedeutet, dass eine Korrektur an der Warnung nur eine
 * der beiden Stellen erreicht.
 */
export function SecretOnce({
  label,
  value,
  onDismiss,
}: {
  label: string
  value: string
  onDismiss: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className="msm-card space-y-3 border border-warning/40 p-6">
      <p className="text-sm font-semibold text-on-surface">{t('hoster.secretOnce', { label })}</p>
      <p className="text-xs text-on-surface-variant">{t('hoster.secretOnceHint')}</p>
      <code className="block break-all rounded-lg bg-surface-container-low/60 p-3 text-sm">{value}</code>
      <div className="flex flex-wrap justify-end gap-2">
        <Button
          type="button"
          variant="secondary"
          onClick={() => {
            void navigator.clipboard?.writeText(value)
            toast.success(t('hoster.copied'))
          }}
        >
          <Copy className="h-4 w-4" aria-hidden="true" />{t('common.copy', 'Kopieren')}
        </Button>
        <Button type="button" onClick={onDismiss}>{t('hoster.secretUnderstood')}</Button>
      </div>
    </div>
  )
}
