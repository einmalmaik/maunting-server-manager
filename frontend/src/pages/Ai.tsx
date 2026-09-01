import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AudioLines, CalendarClock, MessageSquare, ShieldAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiVoiceConfig } from '@/api/ai'
import { api } from '@/api/client'
import { AiAutonomyButton } from '@/components/ai/AiAutonomyButton'
import { AiChat } from '@/components/ai/AiChat'
import { AufgabenAnsicht } from '@/components/ai/AufgabenAnsicht'
import { GuardianAnsicht } from '@/components/ai/GuardianAnsicht'
import { WorkerAnsicht } from '@/components/ai/WorkerAnsicht'
import { SprachAnsicht } from '@/components/ai/voice/SprachAnsicht'
import { useHasPermission } from '@/hooks/useHasPermission'
import { aiChatPreferenceKeys, readAiProviderChoice } from '@/lib/aiChatPreferences'
import { browserWahlInsKontoUebernehmen } from '@/lib/aiProviderKonto'
import { useAuthStore } from '@/stores/authStore'

/** Nur, was die Bereichsauswahl des Autonomie-Knopfs braucht. */
interface ServerOption {
  id: number
  name: string
}

/**
 * Welche Ansicht die Seite gerade ausfüllt.
 *
 * `text` und `sprache` sind zwei Modi **derselben** Unterhaltung — getippt und
 * gesprochen. `guardian` ist etwas anderes: ein zweiter Verlauf, in den nur die
 * Läufe schreiben, die eine Störung ausgelöst hat. Er steht trotzdem in
 * derselben Reihe, weil er dasselbe Bild ausfüllt und man dazwischen wechselt.
 *
 * `worker` ist das Fenster **eines** Hintergrund-Auftrags und braucht darum
 * eine Kennung dazu (`?ansicht=worker&id=<uuid>`) — in der Adresse steht nur
 * die UUID, nie Titel oder Inhalt (keine sensiblen Daten in URLs). Einen
 * Umschalt-Knopf gibt es dafür nicht: es gibt beliebig viele Aufträge, ein
 * statischer Knopf wüsste nicht, wohin. Hin führen die Worker-Leiste im Chat
 * und die Glocke.
 *
 * `aufgaben` ist die Aufgabenliste: die stehenden Aufträge dieses Benutzers,
 * manuell verwaltbar mit denselben Dienstfunktionen, durch die auch die KI
 * geht. Sie steht in derselben Reihe wie das Guardian-Fenster und braucht
 * `ai.tasks.manage` — ohne das Recht gibt es weder Knopf noch Ansicht.
 */
type Ansicht = 'text' | 'sprache' | 'guardian' | 'worker' | 'aufgaben'

const ANSICHTEN: readonly Ansicht[] = ['text', 'sprache', 'guardian', 'worker', 'aufgaben']

function ansichtAusAbfrage(wert: string | null, id: string | null): Ansicht {
  const ansicht = (ANSICHTEN as readonly string[]).includes(wert ?? '')
    ? (wert as Ansicht)
    : 'text'
  // Ein Worker ohne Kennung ist keine Ansicht — dann eben der Chat, statt
  // eines leeren Fensters, das nicht sagen kann, wessen Verlauf es zeigt.
  return ansicht === 'worker' && !id ? 'text' : ansicht
}

/** Ein Knopf der Umschaltreihe. Aktiv heisst: diese Ansicht steht gerade. */
function Umschalter({ aktiv, onClick, icon, label, shortLabel }: {
  aktiv: boolean
  onClick: () => void
  icon: React.ReactNode
  label: string
  shortLabel?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={aktiv}
      aria-label={label}
      className={[
        'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium',
        'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 shrink-0',
        aktiv
          ? 'border-outline-variant/60 bg-surface-container-low/50 text-on-surface-variant hover:text-on-surface'
          : 'border-primary/30 bg-primary/10 text-primary hover:bg-primary/20',
      ].join(' ')}
    >
      {icon}
      <span>{shortLabel || label}</span>
    </button>
  )
}

/**
 * Die KI-Seite ist der Chat — nicht eine Seite *mit* einem Chat.
 *
 * Seit dem Sprachmodus sind es zwei Modi derselben Unterhaltung: getippt und
 * gesprochen. Umgeschaltet wird oben rechts, und es ist wirklich ein Wechsel und
 * kein Nebeneinander — der Chat verschwindet, die Kugel übernimmt. Ein
 * Sprachmodus neben einem Eingabefeld wäre beides halb.
 *
 * Dazu kommt das **Guardian-Fenster**: der Verlauf der Reparaturen, die im
 * Hintergrund laufen. Es ist bewusst dieselbe Umschaltreihe und keine eigene
 * Seite — es ist derselbe Assistent, nur ein anderer Anlass, und wer eine
 * Störung gemeldet bekommt, soll einen Klick weit weg sein. Geschrieben wird
 * dort nicht; warum, steht in `GuardianAnsicht`.
 *
 * Die Wahl steht in der Adresse (`?ansicht=guardian`). Das ist der Weg, über
 * den der Guardian-Reiter eines Servers und die Glocke hierher zeigen — ein
 * Zustand allein in der Komponente wäre von dort nicht erreichbar. Dieselbe
 * Bauart wie die Reiter der Serverseite.
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
  const user = useAuthStore((state) => state.user)
  const canChat = useHasPermission('ai.chat.use')
  const canSpeak = useHasPermission('ai.voice.use')
  const canUseAutonomy = useHasPermission('ai.autonomous.use')
  const canManageTasks = useHasPermission('ai.tasks.manage')
  const [sprachkonfiguration, setSprachkonfiguration] = useState<AiVoiceConfig | null>(null)
  const [servers, setServers] = useState<ServerOption[]>([])
  const [suchParameter, setzeSuchParameter] = useSearchParams()

  // Die Modellwahl fuer den Sprachmodus: zuerst die am Konto gespeicherte
  // (dieselbe, die Overlay und Desktop-App sehen), sonst die alte Browserwahl.
  const providerId =
    user?.ai_provider_id
    ?? (user?.id ? readAiProviderChoice(aiChatPreferenceKeys(user.id).provider) : null)

  // Einmalige Uebernahme Browser → Konto, auch von hier: wer nur den
  // Sprachmodus benutzt und den Chat nie oeffnet, bekaeme sie sonst nicht —
  // und sein Overlay spraeche weiter mit dem erstbesten Zugang.
  useEffect(() => {
    void browserWahlInsKontoUebernehmen()
  }, [])

  const workerId = suchParameter.get('id')
  const gewuenscht = ansichtAusAbfrage(suchParameter.get('ansicht'), workerId)
  // Ohne eingerichteten Sprachzugang gibt es den Sprachmodus nicht — auch
  // dann nicht, wenn er in der Adresse steht. Dieselbe Regel wie beim Knopf.
  // Und ohne `ai.tasks.manage` keine Aufgabenliste: das Backend wiese den
  // Abruf ohnehin mit 403 ab, aber eine Ansicht, die nur einen Fehler zeigen
  // kann, ist keine.
  const ansicht: Ansicht =
    (gewuenscht === 'sprache' && !sprachkonfiguration)
      || (gewuenscht === 'aufgaben' && !canManageTasks)
      ? 'text'
      : gewuenscht

  const setzeAnsicht = (neu: Ansicht) => {
    const naechste = new URLSearchParams(suchParameter)
    if (neu === 'text') naechste.delete('ansicht')
    else naechste.set('ansicht', neu)
    // Die Worker-Kennung gehört zur Worker-Ansicht — wer wegwechselt, nimmt
    // sie nicht mit, sonst zeigte ein späteres `?ansicht=worker` einen
    // Auftrag, den niemand gemeint hat.
    if (neu !== 'worker') naechste.delete('id')
    // `replace`, damit der Zurück-Knopf des Browsers aus der KI-Seite
    // herausführt und nicht durch drei Ansichten davon.
    setzeSuchParameter(naechste, { replace: true })
  }

  // Zwei Bedingungen, und beide müssen stimmen: das Recht *und* ein
  // eingerichteter Sprachweg. Das Backend entscheidet, ob OpenAI Realtime oder
  // der Legacy-Weg aus Gehör und Stimme betriebsbereit ist.
  useEffect(() => {
    if (!canSpeak) return
    let lebt = true
    aiApi
      .getVoiceConfig(providerId ?? undefined)
      .then((konfiguration) => {
        if (!lebt) return
        if (konfiguration.available) {
          setSprachkonfiguration(konfiguration)
          return
        }
        console.info(
          'Sprachmodus nicht verfuegbar: OpenAI Realtime oder der Legacy-Weg ' +
            'aus Transkriptionsmodell und ElevenLabs ist nicht vollständig eingerichtet.',
          konfiguration,
        )
      })
      .catch((fehler: unknown) => {
        console.error(
          'Sprachmodus: /api/ai/voice/config ist nicht abrufbar. Prüfe Backend, ' +
            'Datenbankmigration und Provider-Konfiguration.',
          fehler,
        )
      })
    return () => {
      lebt = false
    }
  }, [canSpeak, providerId])

  // Die Serverliste holt sich der Autonomie-Knopf nicht selbst — er bekommt
  // sie, wie im Chat. Und nur mit dem Recht: ohne es zöge jedes Öffnen der
  // Seite alle sichtbaren Server samt Bind-IP und Ports in den Browser, für
  // eine Auswahlliste, die gar nicht gezeichnet wird. Scheitert der Abruf,
  // bleibt die Liste leer — panelweite Freigabe geht dann immer noch.
  //
  // Die Ansicht steht mit in der Bedingung, weil der Knopf hier **nur** im
  // Sprachmodus steht: im getippten Modus zeichnet ihn `AiChat` selbst, und
  // dort holt er seine Liste auch selbst. Ohne diese Bedingung liefen im
  // Chatmodus zwei Abrufe derselben Liste für zwei Knöpfe nebeneinander. Im
  // Guardian-Fenster gibt es ihn gar nicht — dort wird nicht gehandelt.
  useEffect(() => {
    if (!canUseAutonomy || ansicht !== 'sprache') return
    let lebt = true
    api<ServerOption[]>('/servers')
      .then((liste) => {
        if (lebt) setServers(liste)
      })
      .catch(() => undefined)
    return () => {
      lebt = false
    }
  }, [ansicht, canUseAutonomy])

  if (!canChat) {
    return (
      <div className="msm-page">
        <div className="msm-card p-6 text-sm text-on-surface-variant">{t('ai.chat.noPermission')}</div>
      </div>
    )
  }

  return (
    <div className="flex h-[calc(100dvh-5.5rem)] max-h-[calc(100dvh-5.5rem)] min-h-0 w-full flex-1 flex-col overflow-hidden">
      <div className="flex shrink-0 items-center justify-between sm:justify-end gap-1.5 sm:gap-2 pb-1.5 sm:pb-2">
        {ansicht !== 'text' ? (
          <div className="flex items-center justify-between w-full sm:w-auto gap-2">
            <Umschalter
              aktiv={false}
              onClick={() => setzeAnsicht('text')}
              icon={<MessageSquare className="h-4 w-4" aria-hidden="true" />}
              label={t('ai.voice.toTextMode')}
            />
            {canUseAutonomy && ansicht === 'sprache' && <AiAutonomyButton servers={servers} />}
          </div>
        ) : (
          <div className="flex items-center justify-between sm:justify-end w-full gap-1.5 sm:gap-2">
            <div className="flex items-center gap-1 sm:gap-1.5">
              {canManageTasks && (
                <Umschalter
                  aktiv={false}
                  onClick={() => setzeAnsicht('aufgaben')}
                  icon={<CalendarClock className="h-3.5 w-3.5 sm:h-4 sm:w-4 shrink-0" aria-hidden="true" />}
                  label={t('ai.tasks.toTasks')}
                  shortLabel="Aufgaben"
                />
              )}
              <Umschalter
                aktiv={false}
                onClick={() => setzeAnsicht('guardian')}
                icon={<ShieldAlert className="h-3.5 w-3.5 sm:h-4 sm:w-4 shrink-0" aria-hidden="true" />}
                label={t('ai.guardian.toGuardianMode')}
                shortLabel="Guardian"
              />
            </div>

            {sprachkonfiguration && (
              <Umschalter
                aktiv={false}
                onClick={() => setzeAnsicht('sprache')}
                icon={<AudioLines className="h-3.5 w-3.5 sm:h-4 sm:w-4 shrink-0" aria-hidden="true" />}
                label={t('ai.voice.toVoiceMode')}
                shortLabel="Realtime"
              />
            )}
          </div>
        )}
      </div>

      {ansicht === 'sprache' && sprachkonfiguration ? (
        <SprachAnsicht
          konfiguration={sprachkonfiguration}
          aufChat={() => setzeAnsicht('text')}
          providerId={providerId}
        />
      ) : ansicht === 'guardian' ? (
        <GuardianAnsicht />
      ) : ansicht === 'aufgaben' ? (
        <AufgabenAnsicht />
      ) : ansicht === 'worker' && workerId ? (
        // `key`, damit ein Wechsel zwischen zwei Aufträgen die Ansicht neu
        // aufbaut, statt den Verlauf des einen in den anderen zu mischen.
        <WorkerAnsicht key={workerId} conversationId={workerId} />
      ) : (
        <AiChat />
      )}
    </div>
  )
}
