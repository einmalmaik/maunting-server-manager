/**
 * Speicher der Tresor-Cloud für ein Konto: eigener Wert oder die Vorgabe aus
 * den Panel-Einstellungen. Der Server kennt nur Zahlen, keine Dateien.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { Button, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/Singra/UI'
import { NumberStepper } from '@/components/ui/NumberStepper'
import { formatBytes } from '@/components/server/fileHelpers'

export interface KontoSpeicher {
  user_id: number
  belegt: number
  quote: number
  eigene_quote: number | null
}

const GIB = 1024 ** 3
/** Wie `vault_blob_service.MAX_QUOTE` im Backend (1 PiB). */
const MAX_QUOTE_GB = 1024 ** 2

interface Props {
  konto: KontoSpeicher
  name: string
  standard: number
  onClose: () => void
  onSaved: (konto: KontoSpeicher) => void
}

export function TresorQuoteDialog({ konto, name, standard, onClose, onSaved }: Props) {
  const { t } = useTranslation()
  const [gb, setGb] = useState(String(Math.round(konto.quote / GIB)))
  const [speichert, setSpeichert] = useState(false)

  const setzen = async (quoteBytes: number | null) => {
    setSpeichert(true)
    try {
      const antwort = await api<{ quote: number; eigene_quote: number | null }>(`/admin/users/${konto.user_id}/tresor-quote`, {
        method: 'PUT',
        body: JSON.stringify({ quote_bytes: quoteBytes }),
      })
      onSaved({ ...konto, ...antwort })
      toast.success(t('users.tresor.gespeichert'))
      onClose()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSpeichert(false)
    }
  }

  return (
    <Dialog open onOpenChange={(offen) => !offen && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('users.tresor.titel', { name })}</DialogTitle>
          <DialogDescription>
            {t('users.tresor.belegt', { belegt: formatBytes(konto.belegt), quote: formatBytes(konto.quote) })}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2 p-6">
          <label htmlFor="tresor-quote-konto" className="block text-sm text-on-surface">
            {t('users.tresor.quote')}
          </label>
          <NumberStepper id="tresor-quote-konto" value={gb} onValueChange={setGb} min={0} max={MAX_QUOTE_GB} disabled={speichert} />
          <p className="text-xs text-on-surface-variant">
            {konto.eigene_quote === null
              ? t('users.tresor.nutztVorgabe', { standard: formatBytes(standard) })
              : t('users.tresor.eigenerWert', { standard: formatBytes(standard) })}
          </p>
        </div>
        <DialogFooter>
          {konto.eigene_quote !== null && (
            <Button type="button" variant="ghost" disabled={speichert} onClick={() => void setzen(null)}>
              {t('users.tresor.aufVorgabe')}
            </Button>
          )}
          <Button type="button" disabled={speichert || gb === ''} onClick={() => void setzen(Number(gb) * GIB)}>
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
