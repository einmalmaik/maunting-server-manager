import { Brain } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Dropdown, type DropdownOption } from '@/Singra/UI'
import type { AiProviderAvailable } from '@/api/ai'
import type { Denkwahl } from '@/lib/aiChatPreferences'

export const AUS = 'aus'
export const AN_OHNE_STUFE = 'an'

export function wahlAusOption(wert: string): Denkwahl {
  if (wert === AUS) return { an: false, stufe: null }
  if (wert === AN_OHNE_STUFE) return { an: true, stufe: null }
  return { an: true, stufe: wert }
}

export function denkwahlFuer(jetzt: Denkwahl, provider: AiProviderAvailable): Denkwahl {
  if (!provider.reasoning) return { an: false, stufe: null }
  if (!jetzt.an && provider.can_disable) return { an: false, stufe: null }
  if (provider.efforts.length === 0) return { an: true, stufe: null }
  if (jetzt.stufe && provider.efforts.includes(jetzt.stufe)) return { an: true, stufe: jetzt.stufe }
  return { an: true, stufe: provider.default_effort ?? provider.efforts[0] }
}

export function ReasoningPicker({
  provider,
  wahl,
  onChange,
  disabled,
  compact = false,
}: {
  provider: AiProviderAvailable | null
  wahl: Denkwahl
  onChange: (wahl: Denkwahl) => void
  disabled?: boolean
  compact?: boolean
}) {
  const { t } = useTranslation()
  if (!provider?.reasoning) return null

  const optionen: DropdownOption[] = []
  if (provider.can_disable) optionen.push({ value: AUS, label: t('ai.reasoning.off') })
  if (provider.efforts.length === 0) {
    optionen.push({ value: AN_OHNE_STUFE, label: t('ai.chat.reasoning') })
  } else {
    for (const stufe of provider.efforts) {
      optionen.push({ value: stufe, label: t(`ai.reasoning.levels.${stufe}`, { defaultValue: stufe }) })
    }
  }

  return (
    <div className={`flex items-center gap-1.5 ${compact ? 'w-full' : ''}`}>
      {!compact && <Brain className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />}
      <div className={compact ? 'w-full' : 'min-w-[7rem] max-w-[11rem]'}>
        <Dropdown
          value={wahl.an ? (wahl.stufe ?? AN_OHNE_STUFE) : AUS}
          onChange={(gewaehlt) => onChange(wahlAusOption(gewaehlt))}
          options={optionen}
          disabled={disabled || optionen.length < 2}
          aria-label={t('ai.chat.reasoningLevel')}
        />
      </div>
    </div>
  )
}
