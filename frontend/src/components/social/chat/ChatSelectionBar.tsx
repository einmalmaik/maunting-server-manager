import { useTranslation } from 'react-i18next'
import { Copy, Forward, Star, Trash2 } from 'lucide-react'
import { Auswahlleiste, type AuswahlAktion } from '@/Singra/UI'

interface ChatSelectionBarProps {
  anzahl: number
  /** Löschen geht nur, wenn unter den gewählten eine eigene, noch nicht gelöschte ist. */
  loeschenMoeglich: boolean
  onBeenden: () => void
  onWeiterleiten: () => void
  onKopieren: () => void
  onMarkieren: () => void
  onLoeschen: () => void
}

/**
 * Die Auswahlleiste ersetzt die schwebenden Bedienelemente: Zähler und
 * Abbrechen oben, am Telefon die Aktionen unten in Daumenreichweite, ab `md`
 * oben neben dem Zähler (`Auswahlleiste`).
 */
export function ChatSelectionBar({
  anzahl,
  loeschenMoeglich,
  onBeenden,
  onWeiterleiten,
  onKopieren,
  onMarkieren,
  onLoeschen,
}: ChatSelectionBarProps) {
  const { t } = useTranslation()
  const anzahlLabel = t('messenger.selectedCount', { count: anzahl })
  const aktionen: AuswahlAktion[] = [
    { key: 'weiterleiten', label: t('messenger.forward'), icon: <Forward className="h-5 w-5 md:h-4 md:w-4" />, onSelect: onWeiterleiten },
    { key: 'kopieren', label: t('messenger.copyText'), kurz: t('common.copy'), icon: <Copy className="h-5 w-5 md:h-4 md:w-4" />, onSelect: onKopieren },
    { key: 'markieren', label: t('messenger.mark'), icon: <Star className="h-5 w-5 md:h-4 md:w-4" />, onSelect: onMarkieren },
    {
      key: 'loeschen',
      label: t('common.delete'),
      icon: <Trash2 className="h-5 w-5 md:h-4 md:w-4" />,
      destructive: true,
      disabled: !loeschenMoeglich,
      onSelect: onLoeschen,
    },
  ]

  return (
    <>
      <div className="absolute top-2.5 left-3 right-3 z-40 flex items-center rounded-full border border-outline-variant/30 bg-surface-container-high/95 px-2 py-1 shadow-sm backdrop-blur-md">
        <Auswahlleiste variante="kopf" anzahlLabel={anzahlLabel} aktionen={aktionen} abbrechenLabel={t('messenger.endSelection')} onAbbrechen={onBeenden} />
      </div>
      <Auswahlleiste
        variante="fuss"
        className="absolute inset-x-0 bottom-0 z-40"
        anzahlLabel={anzahlLabel}
        aktionen={aktionen}
        abbrechenLabel={t('messenger.endSelection')}
        onAbbrechen={onBeenden}
      />
    </>
  )
}
