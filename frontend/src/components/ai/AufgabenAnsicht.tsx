import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarClock, Pencil, Plus, Sparkles, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiTaskEntry, type AiTaskWrite } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button, DateTimePicker, Dropdown, type DropdownOption, Switch } from '@/Singra/UI'
import { useAuthStore } from '@/stores/authStore'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'
import { formatPanelTime, getAvailableTimezones } from '@/utils/timeFormat'

/**
 * Was das Formular hält — bewusst flach und vollständig, nicht die Teilangaben
 * der API. Beim Speichern wird daraus je Planart nur das Nötige gebaut; das
 * Backend prüft die Grenzen (`ai_task_service._anwenden`) und redigiert die
 * Texte. Hier gibt es keine zweite Fachprüfung, nur Bedienbarkeit.
 */
interface FormularDaten {
  title: string
  instruction: string
  plan_kind: 'daily' | 'interval' | 'once'
  time_of_day: string
  weekdays: number[]
  interval_hours: number
  once_at: string
  timezone: string
  email: boolean
  act: boolean
}

function leeresFormular(defaultTz?: string | null): FormularDaten {
  const browserTz = typeof Intl !== 'undefined' ? Intl.DateTimeFormat().resolvedOptions().timeZone : ''
  return {
    title: '',
    instruction: '',
    plan_kind: 'daily',
    time_of_day: '08:00',
    weekdays: [],
    interval_hours: 24,
    once_at: '',
    timezone: defaultTz || browserTz || 'UTC',
    email: false,
    act: false,
  }
}

function formularAus(aufgabe: AiTaskEntry): FormularDaten {
  return {
    title: aufgabe.title,
    instruction: aufgabe.instruction,
    plan_kind: aufgabe.plan_kind,
    time_of_day: aufgabe.time_of_day ?? '08:00',
    weekdays: aufgabe.weekdays
      ? aufgabe.weekdays.split(',').map((teil) => Number(teil)).filter(Boolean)
      : [],
    interval_hours: aufgabe.interval_hours ?? 24,
    // `datetime-local` kennt keine Zone; angezeigt und gespeichert wird die
    // Ortszeit der Aufgabe — das Backend legt bei zonenloser Angabe genau
    // diese Zone zugrunde (`_plan_uebernehmen`).
    once_at: aufgabe.once_at ? aufgabe.once_at.slice(0, 16) : '',
    timezone: aufgabe.timezone,
    email: aufgabe.channel !== 'chat',
    act: aufgabe.kind === 'act',
  }
}

/**
 * Aus dem Formular wird je Planart nur das übertragen, was sie braucht —
 * eine geerbte Uhrzeit an einem Intervallplan wäre genau die Angabe, nach der
 * sich nichts richtet (dasselbe Aufräumen macht auch das Backend).
 *
 * `channel` normalisiert bewusst auf `chat`/`both`: der Verlauf steht ohnehin
 * immer im Aufgabenfenster, die Frage ist nur, ob zusätzlich eine Mail geht.
 */
function alsPayload(daten: FormularDaten): AiTaskWrite {
  const payload: AiTaskWrite = {
    title: daten.title.trim(),
    instruction: daten.instruction.trim(),
    kind: daten.act ? 'act' : 'report',
    channel: daten.email ? 'both' : 'chat',
    timezone: daten.timezone.trim(),
    plan_kind: daten.plan_kind,
  }
  if (daten.plan_kind === 'daily') {
    payload.time_of_day = daten.time_of_day
    payload.weekdays = daten.weekdays
  } else if (daten.plan_kind === 'interval') {
    payload.interval_hours = daten.interval_hours
  } else {
    payload.once_at = daten.once_at
  }
  return payload
}

const WOCHENTAGE = [1, 2, 3, 4, 5, 6, 7] as const

const TIME_OPTIONS = Array.from({ length: 48 }, (_, i) => {
  const hour = String(Math.floor(i / 2)).padStart(2, '0')
  const minute = i % 2 === 0 ? '00' : '30'
  return `${hour}:${minute}`
})

const INTERVAL_OPTIONS = [1, 2, 3, 4, 6, 8, 12, 24, 48, 72, 168]

/**
 * Die Aufgabenliste: stehende Aufträge sehen, anlegen, ändern, pausieren,
 * löschen — dieselben Dienstfunktionen, durch die auch die KI geht. Alles,
 * was die KI kann, kann der Benutzer hier auch; deaktiviert die KI (oder eine
 * manuelle Zeitplan-Änderung am Server) eine Aufgabe, steht das hier sichtbar.
 *
 * Bewusst dieselbe Bauform wie das Guardian-Fenster: eine kleine Seite im
 * Stil des Chats, erreichbar über die Umschaltreihe der KI-Seite. Die Läufe
 * selbst passieren im Hintergrund; ihr Verlauf hängt am Aufgabenfenster
 * (`conversation_id` → `?ansicht=worker&id=…`) und nie im Dauerchat.
 */
export function AufgabenAnsicht() {
  const { t, i18n } = useTranslation()
  const { user } = useAuthStore()
  const [aufgaben, setAufgaben] = useState<AiTaskEntry[]>([])
  const [laedt, setLaedt] = useState(true)
  const [formular, setFormular] = useState<FormularDaten | null>(null)
  const [bearbeitet, setBearbeitet] = useState<string | null>(null)
  const [speichert, setSpeichert] = useState(false)

  const zeitOptionen = useMemo(() => {
    const timeVal = formular?.time_of_day || '08:00'
    const werte = [...new Set([...TIME_OPTIONS, timeVal])].sort()
    return werte.map((option) => ({
      value: option,
      label: option,
    }))
  }, [formular?.time_of_day])

  const intervallOptionen = useMemo(() => {
    const custom = formular?.interval_hours ?? 24
    const werte = [...new Set([...INTERVAL_OPTIONS, custom])].sort((a, b) => a - b)
    return werte.map((stunden) => ({
      value: String(stunden),
      label: t('ai.tasks.planInterval', { count: stunden }),
    }))
  }, [formular?.interval_hours, t])

  const zeitzonenOptionen: DropdownOption[] = useMemo(() => {
    const zones = getAvailableTimezones()
    const current = formular?.timezone ? [formular.timezone] : []
    const userTz = user?.time_zone ? [user.time_zone] : []
    const all = [...new Set([...userTz, ...current, ...zones])].sort()
    return all.map((tz) => ({ value: tz, label: tz }))
  }, [user?.time_zone, formular?.timezone])

  const laden = useCallback(async () => {
    const liste = await aiApi.listTasks()
    setAufgaben(liste)
  }, [])

  useEffect(() => {
    let lebt = true
    laden()
      .catch((fehler: unknown) => {
        if (lebt) {
          toast.error(
            fehler instanceof SanitizedApiError ? fehler.message : t('ai.tasks.errors.load'),
          )
        }
      })
      .finally(() => {
        if (lebt) setLaedt(false)
      })
    return () => {
      lebt = false
    }
  }, [laden, t])

  const planAnzeige = (aufgabe: AiTaskEntry): string => {
    if (aufgabe.plan_kind === 'interval') {
      return t('ai.tasks.planInterval', { count: aufgabe.interval_hours ?? 0 })
    }
    if (aufgabe.plan_kind === 'once') {
      const wann = aufgabe.once_at
        ? new Date(aufgabe.once_at).toLocaleString(i18n.language)
        : '—'
      return t('ai.tasks.planOnce', { when: wann })
    }
    const zeit = aufgabe.time_of_day ? formatPanelTime(aufgabe.time_of_day, '24h') : '—'
    if (aufgabe.weekdays) {
      const tage = aufgabe.weekdays
        .split(',')
        .filter(Boolean)
        .map((tag) => t(`ai.tasks.weekday.${tag}`))
        .join(', ')
      return t('ai.tasks.planWeekdays', { days: tage, time: zeit, zone: aufgabe.timezone })
    }
    return t('ai.tasks.planDaily', { time: zeit, zone: aufgabe.timezone })
  }

  const speichern = async () => {
    if (!formular) return
    setSpeichert(true)
    try {
      if (bearbeitet) {
        await aiApi.updateTask(bearbeitet, alsPayload(formular))
        toast.success(t('ai.tasks.saved'))
      } else {
        await aiApi.createTask(alsPayload(formular))
        toast.success(t('ai.tasks.created'))
      }
      setFormular(null)
      setBearbeitet(null)
      await laden()
    } catch (fehler: unknown) {
      toast.error(
        fehler instanceof SanitizedApiError ? fehler.message : t('ai.tasks.errors.save'),
      )
    } finally {
      setSpeichert(false)
    }
  }

  const umschalten = async (aufgabe: AiTaskEntry, aktiv: boolean) => {
    try {
      const neu = await aiApi.updateTask(aufgabe.task_id, { enabled: aktiv })
      setAufgaben((liste) => liste.map((zeile) => (zeile.task_id === neu.task_id ? neu : zeile)))
      toast.success(aktiv ? t('ai.tasks.resumed') : t('ai.tasks.pausedToast'))
    } catch (fehler: unknown) {
      toast.error(
        fehler instanceof SanitizedApiError ? fehler.message : t('ai.tasks.errors.save'),
      )
    }
  }

  const loeschen = async (aufgabe: AiTaskEntry) => {
    const sicher = await confirm({
      message: t('ai.tasks.deleteConfirm'),
      confirmText: t('common.delete'),
      danger: true,
    })
    if (!sicher) return
    try {
      await aiApi.deleteTask(aufgabe.task_id)
      setAufgaben((liste) => liste.filter((zeile) => zeile.task_id !== aufgabe.task_id))
      toast.success(t('ai.tasks.deleted', { title: aufgabe.title }))
    } catch (fehler: unknown) {
      toast.error(
        fehler instanceof SanitizedApiError ? fehler.message : t('ai.tasks.errors.delete'),
      )
    }
  }

  const setze = <K extends keyof FormularDaten>(feld: K, wert: FormularDaten[K]) => {
    setFormular((alt) => (alt ? { ...alt, [feld]: wert } : alt))
  }

  const leer = aufgaben.length === 0 && !laedt

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-outline-variant/40 bg-surface-container-lowest">
      <header className="flex shrink-0 items-center gap-2 border-b border-outline-variant/40 px-4 py-3">
        <CalendarClock className="h-4 w-4 shrink-0 text-secondary" aria-hidden="true" />
        <div className="min-w-0">
          <h2 className="truncate font-headline text-sm font-semibold text-on-surface">
            {t('ai.tasks.title')}
          </h2>
          <p className="truncate text-xs text-on-surface-variant">{t('ai.tasks.subtitle')}</p>
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <Button
            type="button" variant="ghost" size="sm"
            onClick={() => void laden().catch(() => undefined)}
          >
            {t('common.refresh')}
          </Button>
          {!formular && (
            <Button
              type="button" variant="primary" size="sm"
              onClick={() => {
                setBearbeitet(null)
                setFormular(leeresFormular(user?.time_zone))
              }}
            >
              <Plus className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              {t('ai.tasks.new')}
            </Button>
          )}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-3 px-3 py-6 sm:px-4">
          {formular && (
            <form
              className="space-y-4 rounded-xl border border-outline-variant/60 bg-surface-container-low/50 p-4"
              onSubmit={(event) => {
                event.preventDefault()
                void speichern()
              }}
            >
              <h3 className="font-headline text-sm font-semibold text-on-surface">
                {bearbeitet ? t('ai.tasks.editTitle') : t('ai.tasks.new')}
              </h3>
              <div>
                <label
                  htmlFor="aufgabe-name"
                  className="mb-1.5 block font-label-md text-label-md uppercase tracking-wider text-on-surface-variant"
                >
                  {t('ai.tasks.name')}
                </label>
                <input
                  id="aufgabe-name"
                  className="msm-input"
                  maxLength={120}
                  value={formular.title}
                  onChange={(event) => setze('title', event.target.value)}
                />
              </div>
              <div>
                <label
                  htmlFor="aufgabe-auftrag"
                  className="mb-1.5 block font-label-md text-label-md uppercase tracking-wider text-on-surface-variant"
                >
                  {t('ai.tasks.prompt')}
                </label>
                <textarea
                  id="aufgabe-auftrag"
                  className="msm-input min-h-24"
                  maxLength={2000}
                  value={formular.instruction}
                  onChange={(event) => setze('instruction', event.target.value)}
                />
                <p className="mt-1 text-xs text-on-surface-variant">{t('ai.tasks.promptHint')}</p>
              </div>

              <fieldset className="space-y-3 border-0 p-0">
                <legend className="mb-1.5 block font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                  {t('ai.tasks.schedule')}
                </legend>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                  {(['daily', 'interval', 'once'] as const).map((art) => (
                    <button
                      key={art}
                      type="button"
                      onClick={() => setze('plan_kind', art)}
                      className={`rounded-md border px-3 py-2 text-left text-sm transition-colors ${
                        formular.plan_kind === art
                          ? 'border-secondary bg-secondary/10 text-primary'
                          : 'border-outline bg-surface-container-high text-on-surface-variant'
                      }`}
                    >
                      {t(`ai.tasks.planKind${art === 'daily' ? 'Daily' : art === 'interval' ? 'Interval' : 'Once'}`)}
                    </button>
                  ))}
                </div>

                {formular.plan_kind === 'daily' && (
                  <div className="space-y-3">
                    <div>
                      <label
                        htmlFor="aufgabe-uhrzeit"
                        className="mb-1.5 block text-xs text-on-surface-variant"
                      >
                        {t('ai.tasks.timeOfDay')}
                      </label>
                      <Dropdown
                        id="aufgabe-uhrzeit"
                        value={formular.time_of_day}
                        onChange={(wert) => setze('time_of_day', wert)}
                        options={zeitOptionen}
                        className="max-w-40"
                        aria-label={t('ai.tasks.timeOfDay')}
                      />
                    </div>
                    <div>
                      <span className="mb-1.5 block text-xs text-on-surface-variant">
                        {t('ai.tasks.weekdays', 'Wochentage')}
                      </span>
                      <div className="flex flex-wrap gap-1.5">
                        {WOCHENTAGE.map((tag) => {
                          const aktiv = formular.weekdays.includes(tag)
                          return (
                            <button
                              key={tag}
                              type="button"
                              aria-pressed={aktiv}
                              onClick={() =>
                                setze(
                                  'weekdays',
                                  aktiv
                                    ? formular.weekdays.filter((wert) => wert !== tag)
                                    : [...formular.weekdays, tag],
                                )
                              }
                              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                                aktiv
                                  ? 'border-primary/40 bg-primary/10 text-primary'
                                  : 'border-outline bg-surface-container-high text-on-surface-variant'
                              }`}
                            >
                              {t(`ai.tasks.weekday.${tag}`)}
                            </button>
                          )
                        })}
                      </div>
                      <p className="mt-1 text-xs text-on-surface-variant">
                        {t('ai.tasks.weekdaysHint')}
                      </p>
                    </div>
                  </div>
                )}

                {formular.plan_kind === 'interval' && (
                  <div>
                    <label
                      htmlFor="aufgabe-intervall"
                      className="mb-1.5 block text-xs text-on-surface-variant"
                    >
                      {t('ai.tasks.intervalHours')}
                    </label>
                    <Dropdown
                      id="aufgabe-intervall"
                      value={String(formular.interval_hours)}
                      onChange={(wert) => setze('interval_hours', Number(wert) || 1)}
                      options={intervallOptionen}
                      className="max-w-60"
                      aria-label={t('ai.tasks.intervalHours')}
                    />
                  </div>
                )}

                {formular.plan_kind === 'once' && (
                  <div>
                    <label
                      htmlFor="aufgabe-zeitpunkt"
                      className="mb-1.5 block text-xs text-on-surface-variant"
                    >
                      {t('ai.tasks.onceAt')}
                    </label>
                    <DateTimePicker
                      id="aufgabe-zeitpunkt"
                      value={formular.once_at}
                      onChange={(wert) => setze('once_at', wert)}
                      locale={i18n.language.startsWith('de') ? 'de' : 'en'}
                      className="max-w-72"
                      aria-label={t('ai.tasks.onceAt')}
                    />
                  </div>
                )}

                <div>
                  <label
                    htmlFor="aufgabe-zeitzone"
                    className="mb-1.5 block text-xs text-on-surface-variant"
                  >
                    {t('ai.tasks.timezone')}
                  </label>
                  <Dropdown
                    id="aufgabe-zeitzone"
                    value={formular.timezone}
                    onChange={(wert) => setze('timezone', wert)}
                    options={zeitzonenOptionen}
                    searchable={true}
                    searchPlaceholder={t('ai.tasks.timezoneSearch', 'Zeitzone suchen …')}
                    className="max-w-64"
                    aria-label={t('ai.tasks.timezone')}
                  />
                  <p className="mt-1 text-xs text-on-surface-variant">
                    {t('ai.tasks.timezoneHint')}
                  </p>
                </div>
              </fieldset>

              <div className="space-y-2">
                <label className="flex items-center gap-3">
                  <Switch
                    checked={formular.email}
                    onCheckedChange={(wert) => setze('email', wert)}
                    aria-label={t('ai.tasks.email')}
                  />
                  <span className="text-sm text-on-surface">{t('ai.tasks.email')}</span>
                </label>
                <label className="flex items-center gap-3">
                  <Switch
                    checked={formular.act}
                    onCheckedChange={(wert) => setze('act', wert)}
                    aria-label={t('ai.tasks.act')}
                  />
                  <span className="text-sm text-on-surface">{t('ai.tasks.act')}</span>
                </label>
                <p className="text-xs text-on-surface-variant">{t('ai.tasks.actHint')}</p>
              </div>

              <div className="flex items-center justify-end gap-2">
                <Button
                  type="button" variant="ghost" size="sm"
                  onClick={() => {
                    setFormular(null)
                    setBearbeitet(null)
                  }}
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  type="submit" variant="primary" size="sm"
                  disabled={speichert || !formular.title.trim() || !formular.instruction.trim()}
                >
                  {speichert
                    ? t('common.loading')
                    : bearbeitet
                      ? t('common.save')
                      : t('ai.tasks.create')}
                </Button>
              </div>
            </form>
          )}

          {leer && !formular && (
            <div className="py-16 text-center">
              <Sparkles className="mx-auto h-10 w-10 text-primary/70" aria-hidden="true" />
              <h3 className="mt-4 font-headline text-lg font-semibold text-on-surface">
                {t('ai.tasks.emptyTitle')}
              </h3>
              <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-on-surface-variant">
                {t('ai.tasks.emptyDescription')}
              </p>
            </div>
          )}

          {aufgaben.map((aufgabe) => (
            <article
              key={aufgabe.task_id}
              className="rounded-xl border border-outline-variant/60 bg-surface-container-low/50 p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="truncate font-headline text-sm font-semibold text-on-surface">
                      {aufgabe.title}
                    </h3>
                    {!aufgabe.enabled && (
                      <span className="inline-flex items-center rounded-full border border-outline-variant/60 bg-surface-container-high px-2 py-0.5 text-xs text-on-surface-variant">
                        {t('ai.tasks.paused')}
                      </span>
                    )}
                    {aufgabe.kind === 'act' && (
                      <span className="inline-flex items-center rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                        {t('ai.tasks.actBadge')}
                      </span>
                    )}
                    {aufgabe.channel !== 'chat' && (
                      <span className="inline-flex items-center rounded-full border border-outline-variant/60 bg-surface-container-high px-2 py-0.5 text-xs text-on-surface-variant">
                        {t('ai.tasks.emailBadge')}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-on-surface-variant">{planAnzeige(aufgabe)}</p>
                  {aufgabe.enabled && aufgabe.next_run && (
                    <p className="mt-0.5 text-xs text-on-surface-variant">
                      {t('ai.tasks.nextRun', {
                        when: new Date(aufgabe.next_run).toLocaleString(i18n.language),
                      })}
                    </p>
                  )}
                </div>
                <Switch
                  checked={aufgabe.enabled}
                  onCheckedChange={(wert) => void umschalten(aufgabe, wert)}
                  aria-label={t('ai.tasks.enabledAria')}
                />
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button
                  type="button" variant="ghost" size="sm"
                  onClick={() => {
                    setBearbeitet(aufgabe.task_id)
                    setFormular(formularAus(aufgabe))
                  }}
                >
                  <Pencil className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                  {t('common.edit')}
                </Button>
                <Button
                  type="button" variant="ghost" size="sm"
                  onClick={() => void loeschen(aufgabe)}
                >
                  <Trash2 className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                  {t('common.delete')}
                </Button>
                {aufgabe.conversation_id && (
                  // Nur die UUID in der Adresse — nie Titel oder Auftragstext
                  // (keine sensiblen Daten in URLs, wie bei der Worker-Ansicht).
                  <Link
                    to={`/ai?ansicht=worker&id=${encodeURIComponent(aufgabe.conversation_id)}`}
                    className="ml-auto text-xs font-medium text-primary hover:underline"
                  >
                    {t('ai.tasks.openWindow')}
                  </Link>
                )}
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  )
}
