import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/Button'

interface Props {
  /** Die angezeigte Seite, bei 1 beginnend. */
  page: number
  /** Wieviele Seiten es gibt. Bei höchstens einer zeigt die Leiste nichts. */
  pageCount: number
  /** Links daneben: worüber geblättert wird, etwa „5.000 Einträge". */
  label?: string
  /** Sperrt beide Knöpfe, solange die Liste gerade lädt oder schreibt. */
  disabled?: boolean
  onChange: (page: number) => void
}

/**
 * Eine Zeile zum Blättern: zurück, „Seite 3 von 25", weiter.
 *
 * Gebaut für Listen, die der Server in Stücken liefert, weil das Ganze zu teuer
 * wäre. Genau dann darf die Leiste nicht nur navigieren, sondern muss auch
 * sagen, wie viel es insgesamt ist — sonst ist eine Seite von einem stillen
 * Deckel nicht zu unterscheiden. Dafür ist `label` da, und deshalb steht es
 * links und nicht als Kleingedrucktes irgendwo.
 *
 * Bewusst ohne Seitenzahlen zum Anspringen und ohne „pro Seite"-Auswahl. Zwei
 * Knöpfe und eine Standortangabe sind das, was gebraucht wird; alles Weitere
 * wäre eine zweite Bedienoberfläche für dasselbe.
 */
export function Pagination({ page, pageCount, label, disabled = false, onChange }: Props) {
  const { t } = useTranslation()

  // Eine Seite ist keine Seitenfolge. Die Leiste verschwindet dann ganz, statt
  // zwei tote Knöpfe stehen zu lassen.
  if (pageCount <= 1) return null

  return (
    <nav
      className="flex flex-wrap items-center justify-between gap-3 border-t border-outline-variant/40 pt-4 text-sm text-on-surface-variant"
      aria-label={t('common.pagination.label')}
    >
      <span>{label}</span>
      <div className="flex items-center gap-2">
        <Button
          type="button" variant="secondary" size="sm"
          disabled={disabled || page <= 1}
          onClick={() => onChange(page - 1)}
        >
          <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          {t('common.back')}
        </Button>
        {/* `aria-live`, weil sich beim Blättern sonst nichts ansagt: der Fokus
            bleibt auf dem Knopf, und die Liste darüber wechselt lautlos. */}
        <span className="px-1 font-medium text-on-surface" aria-live="polite">
          {t('common.pagination.page', { page, pages: pageCount })}
        </span>
        <Button
          type="button" variant="secondary" size="sm"
          disabled={disabled || page >= pageCount}
          onClick={() => onChange(page + 1)}
        >
          {t('common.next')}
          <ChevronRight className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </nav>
  )
}
