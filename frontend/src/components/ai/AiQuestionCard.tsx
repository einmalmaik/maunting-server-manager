import { HelpCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { AiQuestion } from '@/api/ai'

/**
 * Eine Rückfrage der KI mit anklickbaren Vorschlägen.
 *
 * Ein Klick sendet sofort — das ist der geringere Widerstand, und wer etwas
 * dranschreiben will, tippt ohnehin ins normale Eingabefeld. Deshalb steht der
 * Hinweis darauf unter den Knöpfen statt als vierte Option „Sonstiges": die
 * freie Antwort ist kein Sonderfall, sondern immer möglich.
 *
 * Beantwortete Fragen bleiben sichtbar, aber ohne Knöpfe. Sie zu entfernen
 * würde den Verlauf unlesbar machen — man sähe nur noch die Antwort und wüsste
 * nicht mehr, worauf sie sich bezieht.
 *
 * **Steht innerhalb der Antwortblase**, nicht daneben. Vorher war sie eine
 * eigenständige Karte im Verlauf, und darunter erschien die leere Nachricht der
 * KI mit „Keine Antwort erhalten" — die Frage wirkte wie ein Fremdkörper
 * zwischen zwei Nachrichten statt wie ein Teil der Antwort. Deshalb hier kein
 * eigener Rahmen und kein eigener Hintergrund mehr, nur ein Abstand nach oben.
 */
export function AiQuestionCard({
  question, answered, disabled, onAnswer,
}: {
  question: AiQuestion
  answered: boolean
  disabled: boolean
  onAnswer: (label: string) => void
}) {
  const { t } = useTranslation()

  return (
    <section className="mt-3" aria-label={t('ai.question.title')}>
      <div className="flex items-start gap-2">
        <HelpCircle className="mt-0.5 h-4 w-4 shrink-0 text-tertiary" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-6 text-on-surface">{question.question}</p>

          {answered ? (
            <p className="mt-2 text-xs text-on-surface-variant">{t('ai.question.answered')}</p>
          ) : (
            <>
              <div className="mt-3 flex flex-wrap gap-2">
                {question.options.map((option) => (
                  <button
                    key={option.label}
                    type="button"
                    disabled={disabled}
                    onClick={() => onAnswer(option.label)}
                    className="group max-w-full rounded-xl border border-outline-variant/50 bg-surface-container-low/60 px-3 py-2 text-left transition-colors hover:border-tertiary/50 hover:bg-tertiary/10 disabled:opacity-50"
                  >
                    <span className="block truncate text-sm font-medium text-on-surface">
                      {option.label}
                    </span>
                    {option.hint && (
                      <span className="mt-0.5 block text-xs leading-5 text-on-surface-variant">
                        {option.hint}
                      </span>
                    )}
                  </button>
                ))}
              </div>
              <p className="mt-2 text-xs text-on-surface-variant">{t('ai.question.freeText')}</p>
            </>
          )}
        </div>
      </div>
    </section>
  )
}
