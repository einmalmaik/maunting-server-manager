import { useEffect, useState } from 'react'
import { AudioLines, ChevronDown, MessageSquare, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiVoiceConfig } from '@/api/ai'
import { AiChat } from '@/components/ai/AiChat'
import { AiSkillDirectory } from '@/components/ai/AiSkillDirectory'
import { SprachAnsicht } from '@/components/ai/voice/SprachAnsicht'
import { useHasPermission } from '@/hooks/useHasPermission'

/**
 * Die KI-Seite ist der Chat — nicht eine Seite *mit* einem Chat.
 *
 * Seit dem Sprachmodus sind es zwei Modi derselben Unterhaltung: getippt und
 * gesprochen. Umgeschaltet wird oben rechts, und es ist wirklich ein Wechsel und
 * kein Nebeneinander — der Chat verschwindet, die Kugel übernimmt. Ein
 * Sprachmodus neben einem Eingabefeld wäre beides halb.
 *
 * Der Umschalter sitzt **auf dieser Seite** und nicht in der Topbar, obwohl er
 * dort optisch hingehörte. Die Topbar gehört allen Seiten; ein Knopf darin, der
 * nur unter `/ai` etwas bedeutet, wäre eine Abhängigkeit vom Rahmen zur Seite.
 *
 * Darunter eingeklappt das Skill-**Verzeichnis**: nur lesend, dafür vollständig.
 */
export function Ai() {
  const { t } = useTranslation()
  const canChat = useHasPermission('ai.chat.use')
  const canUseSkills = useHasPermission('ai.skills.use')
  const canSpeak = useHasPermission('ai.voice.use')
  const [skillsOpen, setSkillsOpen] = useState(false)
  const [sprachkonfiguration, setSprachkonfiguration] = useState<AiVoiceConfig | null>(null)
  const [spricht, setSpricht] = useState(false)

  // Zwei Bedingungen, und beide müssen stimmen: das Recht *und* ein
  // eingerichteter Realtime-Zugang. Ohne Zugang gibt es keinen Knopf — nicht
  // ausgegraut, sondern gar nicht. Dieselbe Regel wie bei `web_search`.
  useEffect(() => {
    if (!canSpeak) return
    let lebt = true
    aiApi
      .getVoiceConfig()
      .then((konfiguration) => {
        if (lebt && konfiguration.available) setSprachkonfiguration(konfiguration)
      })
      .catch(() => undefined)
    return () => {
      lebt = false
    }
  }, [canSpeak])

  if (!canChat) {
    return (
      <div className="msm-page">
        <div className="msm-card p-6 text-sm text-on-surface-variant">{t('ai.chat.noPermission')}</div>
      </div>
    )
  }

  return (
    // Volle Höhe abzüglich dessen, was der Rahmen schon verbraucht: 4rem Topbar
    // (Topbar.tsx, `h-16` auf allen Breakpoints) plus die Polsterung von `main`
    // (Shell.tsx, `p-margin-mobile md:p-margin-desktop` = 1rem bzw. 2.5rem, oben
    // und unten). Macht 6rem mobil und 9rem ab `md`. Wird die Polsterung in
    // Shell.tsx geändert, muss diese Rechnung mit.
    // `min-h-0` ist hier nicht kosmetisch: ohne das kann ein Flex-Kind nicht
    // kleiner werden als sein Inhalt, und der Verlauf würde die Seite statt
    // seines eigenen Bereichs scrollen.
    <div className="flex h-[calc(100dvh-6rem)] min-h-0 flex-col md:h-[calc(100dvh-9rem)]">
      {sprachkonfiguration && (
        <div className="flex shrink-0 justify-end pb-2">
          <button
            type="button"
            onClick={() => setSpricht((an) => !an)}
            aria-pressed={spricht}
            className={[
              'inline-flex items-center gap-2 rounded-lg border px-3.5 py-2 text-sm font-medium',
              'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60',
              spricht
                ? 'border-outline-variant/60 bg-surface-container-low/50 text-on-surface-variant hover:text-on-surface'
                : 'border-primary/40 bg-primary/10 text-primary hover:bg-primary/15',
            ].join(' ')}
          >
            {spricht ? (
              <MessageSquare className="h-4 w-4" aria-hidden="true" />
            ) : (
              <AudioLines className="h-4 w-4" aria-hidden="true" />
            )}
            {t(spricht ? 'ai.voice.toTextMode' : 'ai.voice.toVoiceMode')}
          </button>
        </div>
      )}

      {spricht && sprachkonfiguration ? (
        <SprachAnsicht
          konfiguration={sprachkonfiguration}
          aufChat={() => setSpricht(false)}
        />
      ) : (
        <AiChat />
      )}

      {canUseSkills && !spricht && (
        <div className="shrink-0 border-t border-outline-variant/40">
          <button
            type="button"
            onClick={() => setSkillsOpen((current) => !current)}
            aria-expanded={skillsOpen}
            className="flex w-full items-center gap-2 px-4 py-2 text-xs font-medium text-on-surface-variant transition-colors hover:text-on-surface"
          >
            <Sparkles className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {t('ai.skills.directoryTitle')}
            <ChevronDown
              className={`ml-auto h-3.5 w-3.5 transition-transform ${skillsOpen ? 'rotate-180' : ''}`}
              aria-hidden="true"
            />
          </button>
          {skillsOpen && (
            <div className="max-h-[60vh] overflow-y-auto border-t border-outline-variant/40 p-4">
              <AiSkillDirectory />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
