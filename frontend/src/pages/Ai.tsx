import { useEffect, useState } from 'react'
import { AudioLines, ChevronDown, MessageSquare, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiVoiceConfig } from '@/api/ai'
import { api } from '@/api/client'
import { AiAutonomyButton } from '@/components/ai/AiAutonomyButton'
import { AiChat } from '@/components/ai/AiChat'
import { AiSkillDirectory } from '@/components/ai/AiSkillDirectory'
import { SprachAnsicht } from '@/components/ai/voice/SprachAnsicht'
import { useHasPermission } from '@/hooks/useHasPermission'

/** Nur, was die Bereichsauswahl des Autonomie-Knopfs braucht. */
interface ServerOption {
  id: number
  name: string
}

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
 * Neben ihm steht im Sprachmodus der **Autonomie-Schalter**. Er hängt im
 * getippten Modus in der Kopfleiste des Chats und verschwand damit genau dann,
 * wenn man ihn am dringendsten braucht: im Sprachmodus, wo jede Rückfrage den
 * Menschen zwingt, mitten im Gespräch auf den Bildschirm zu sehen. Der Zustand
 * gilt ohnehin für beide Modi — es ist dieselbe Freigabe.
 *
 * Er steht hier deshalb **nur**, solange gesprochen wird, und nicht zusätzlich
 * zu dem im Chat. Zwei Instanzen nebeneinander wären zwei Knöpfe mit je eigenem
 * Zustand: wer den einen umlegt, sähe am anderen weiter den alten Stand und
 * schaltete mit dem nächsten Klick unbeabsichtigt zurück. Ein Schalter, der
 * seinen eigenen Zwilling nicht kennt, ist schlimmer als ein fehlender.
 *
 * Darunter eingeklappt das Skill-**Verzeichnis**: nur lesend, dafür vollständig.
 */
export function Ai() {
  const { t } = useTranslation()
  const canChat = useHasPermission('ai.chat.use')
  const canUseSkills = useHasPermission('ai.skills.use')
  const canSpeak = useHasPermission('ai.voice.use')
  const canUseAutonomy = useHasPermission('ai.autonomous.use')
  const [skillsOpen, setSkillsOpen] = useState(false)
  const [sprachkonfiguration, setSprachkonfiguration] = useState<AiVoiceConfig | null>(null)
  const [spricht, setSpricht] = useState(false)
  const [servers, setServers] = useState<ServerOption[]>([])

  // Zwei Bedingungen, und beide müssen stimmen: das Recht *und* ein
  // eingerichteter Sprachweg. Der besteht aus zwei Zugängen — Gehör und Stimme
  // —, aber das entscheidet das Backend: `available` ist erst wahr, wenn beide
  // stehen. Fehlt einer, gibt es keinen Knopf — nicht ausgegraut, sondern gar
  // nicht. Dieselbe Regel wie bei `web_search`.
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

  // Die Serverliste holt sich der Autonomie-Knopf nicht selbst — er bekommt
  // sie, wie im Chat. Und nur mit dem Recht: ohne es zöge jedes Öffnen der
  // Seite alle sichtbaren Server samt Bind-IP und Ports in den Browser, für
  // eine Auswahlliste, die gar nicht gezeichnet wird. Scheitert der Abruf,
  // bleibt die Liste leer — panelweite Freigabe geht dann immer noch.
  //
  // `spricht` steht mit in der Bedingung, weil der Knopf hier **nur** im
  // Sprachmodus steht: im getippten Modus zeichnet ihn `AiChat` selbst, und
  // dort holt er seine Liste auch selbst. Ohne diese Bedingung liefen im
  // Chatmodus zwei Abrufe derselben Liste für zwei Knöpfe nebeneinander.
  useEffect(() => {
    if (!canUseAutonomy || !spricht) return
    let lebt = true
    api<ServerOption[]>('/servers')
      .then((liste) => {
        if (lebt) setServers(liste)
      })
      .catch(() => undefined)
    return () => {
      lebt = false
    }
  }, [canUseAutonomy, spricht])

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
        <div className="flex shrink-0 items-center justify-end gap-3 pb-2">
          {canUseAutonomy && spricht && <AiAutonomyButton servers={servers} />}
          {sprachkonfiguration && (
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
          )}
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
