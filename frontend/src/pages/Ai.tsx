import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AudioLines, ChevronDown, MessageSquare, ShieldAlert, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiVoiceConfig } from '@/api/ai'
import { api } from '@/api/client'
import { AiAutonomyButton } from '@/components/ai/AiAutonomyButton'
import { AiChat } from '@/components/ai/AiChat'
import { AiSkillDirectory } from '@/components/ai/AiSkillDirectory'
import { GuardianAnsicht } from '@/components/ai/GuardianAnsicht'
import { WorkerAnsicht } from '@/components/ai/WorkerAnsicht'
import { SprachAnsicht } from '@/components/ai/voice/SprachAnsicht'
import { useHasPermission } from '@/hooks/useHasPermission'
import { aiChatPreferenceKeys, readAiProviderChoice } from '@/lib/aiChatPreferences'
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
 */
type Ansicht = 'text' | 'sprache' | 'guardian' | 'worker'

const ANSICHTEN: readonly Ansicht[] = ['text', 'sprache', 'guardian', 'worker']

function ansichtAusAbfrage(wert: string | null, id: string | null): Ansicht {
  const ansicht = (ANSICHTEN as readonly string[]).includes(wert ?? '')
    ? (wert as Ansicht)
    : 'text'
  // Ein Worker ohne Kennung ist keine Ansicht — dann eben der Chat, statt
  // eines leeren Fensters, das nicht sagen kann, wessen Verlauf es zeigt.
  return ansicht === 'worker' && !id ? 'text' : ansicht
}

/** Ein Knopf der Umschaltreihe. Aktiv heisst: diese Ansicht steht gerade. */
function Umschalter({ aktiv, onClick, icon, label }: {
  aktiv: boolean
  onClick: () => void
  icon: React.ReactNode
  label: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={aktiv}
      className={[
        'inline-flex items-center gap-2 rounded-lg border px-3.5 py-2 text-sm font-medium',
        'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60',
        aktiv
          ? 'border-outline-variant/60 bg-surface-container-low/50 text-on-surface-variant hover:text-on-surface'
          : 'border-primary/40 bg-primary/10 text-primary hover:bg-primary/15',
      ].join(' ')}
    >
      {icon}
      {label}
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
  const canUseSkills = useHasPermission('ai.skills.use')
  const canSpeak = useHasPermission('ai.voice.use')
  const canUseAutonomy = useHasPermission('ai.autonomous.use')
  const [skillsOpen, setSkillsOpen] = useState(false)
  const [sprachkonfiguration, setSprachkonfiguration] = useState<AiVoiceConfig | null>(null)
  const [servers, setServers] = useState<ServerOption[]>([])
  const [suchParameter, setzeSuchParameter] = useSearchParams()

  const providerId = user?.id ? readAiProviderChoice(aiChatPreferenceKeys(user.id).provider) : null

  const workerId = suchParameter.get('id')
  const gewuenscht = ansichtAusAbfrage(suchParameter.get('ansicht'), workerId)
  // Ohne eingerichteten Realtime-Zugang gibt es den Sprachmodus nicht — auch
  // dann nicht, wenn er in der Adresse steht. Dieselbe Regel wie beim Knopf.
  const ansicht: Ansicht =
    gewuenscht === 'sprache' && !sprachkonfiguration ? 'text' : gewuenscht

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
  // eingerichteter Sprachweg. Der besteht aus zwei Zugängen — Gehör und Stimme
  // —, aber das entscheidet das Backend: `available` ist erst wahr, wenn beide
  // stehen. Fehlt einer, gibt es keinen Knopf — nicht ausgegraut, sondern gar
  // nicht. Dieselbe Regel wie bei `web_search`.
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
          'Sprachmodus nicht verfuegbar: es braucht zwei Zugaenge — einen Chatzugang ' +
            'mit hinterlegtem Modell fuer Gesprochenes und einen ElevenLabs-Zugang mit ' +
            'Voice ID. Einzurichten unter Einstellungen → KI → Anbieter.',
          konfiguration,
        )
      })
      .catch((fehler: unknown) => {
        console.error(
          'Sprachmodus: /api/ai/voice/config nicht abrufbar. Steht die Migration noch ' +
            'aus (`alembic upgrade head`)? Die Spalten `transcription_model` und ' +
            '`default_voice` kamen am 16.08.2026 dazu.',
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
    // Volle Höhe abzüglich dessen, was der Rahmen schon verbraucht: 4rem Topbar
    // (Topbar.tsx, `h-16` auf allen Breakpoints) plus die Polsterung von `main`
    // (Shell.tsx, `p-margin-mobile md:p-margin-desktop` = 1rem bzw. 2.5rem, oben
    // und unten). Macht 6rem mobil und 9rem ab `md`. Wird die Polsterung in
    // Shell.tsx geändert, muss diese Rechnung mit.
    // `min-h-0` ist hier nicht kosmetisch: ohne das kann ein Flex-Kind nicht
    // kleiner werden als sein Inhalt, und der Verlauf würde die Seite statt
    // seines eigenen Bereichs scrollen.
    <div className="flex h-[calc(100dvh-6rem)] min-h-0 flex-col md:h-[calc(100dvh-9rem)]">
      <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 pb-2">
        {/* Der Autonomie-Schalter, solange gesprochen wird — die Begründung
            steht im Doc-Kommentar oben. Er steht **vor** der Umschaltreihe,
            weil er kein Wechsel ist, sondern eine Einstellung: zwischen den
            Modusknöpfen sähe er aus wie ein vierter Modus. */}
        {canUseAutonomy && ansicht === 'sprache' && <AiAutonomyButton servers={servers} />}
        {ansicht !== 'text' && (
          <Umschalter
            aktiv={false}
            onClick={() => setzeAnsicht('text')}
            icon={<MessageSquare className="h-4 w-4" aria-hidden="true" />}
            label={t('ai.voice.toTextMode')}
          />
        )}
        <Umschalter
          aktiv={ansicht === 'guardian'}
          onClick={() => setzeAnsicht(ansicht === 'guardian' ? 'text' : 'guardian')}
          icon={<ShieldAlert className="h-4 w-4" aria-hidden="true" />}
          label={t('ai.guardian.toGuardianMode')}
        />
        {sprachkonfiguration && ansicht !== 'sprache' && (
          <Umschalter
            aktiv={false}
            onClick={() => setzeAnsicht('sprache')}
            icon={<AudioLines className="h-4 w-4" aria-hidden="true" />}
            label={t('ai.voice.toVoiceMode')}
          />
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
      ) : ansicht === 'worker' && workerId ? (
        // `key`, damit ein Wechsel zwischen zwei Aufträgen die Ansicht neu
        // aufbaut, statt den Verlauf des einen in den anderen zu mischen.
        <WorkerAnsicht key={workerId} conversationId={workerId} />
      ) : (
        <AiChat />
      )}

      {canUseSkills && ansicht === 'text' && (
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
