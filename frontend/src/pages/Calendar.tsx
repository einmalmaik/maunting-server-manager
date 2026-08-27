import { useState, useEffect, useMemo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  BellRing,
  Calendar as CalendarIcon,
  ChevronLeft,
  ChevronRight,
  Clock,
  Download,
  Link,
  MapPin,
  Network,
  Plus,
  Server,
  Trash2,
  User,
  Users,
  X,
} from 'lucide-react'
import { api } from '@/api/client'
import { apiUrl } from '@/config/api'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { PageHeader } from '@/Singra/UI/PageHeader'
import { DateTimePicker } from '@/Singra/UI'
import { Button } from '@/components/ui/Button'
import { sendeGeraeteBenachrichtigung, pruefeUndFrageGeraeteBerechtigung } from '@/lib/benachrichtigung'

export type EventCategoryType = 'personal' | 'team' | 'server' | 'node'

export interface CalendarEventItem {
  id?: number
  event_id: string
  title: string
  start: string
  end: string
  description?: string
  location?: string
  all_day?: boolean
  color?: string
  calendar?: string
  event_type?: EventCategoryType | string
  team_id?: number | null
  team_name?: string | null
  server_id?: number | null
  server_name?: string | null
  creator_name?: string | null
  user_id?: number
  can_edit?: boolean
}

type ViewMode = 'month' | 'week' | 'day'

const COLOR_PALETTE = [
  { id: 'primary', label: 'Blau (Standard)', bg: 'bg-primary/20', text: 'text-primary', border: 'border-primary/40' },
  { id: 'emerald', label: 'Grün', bg: 'bg-emerald-500/20', text: 'text-emerald-400', border: 'border-emerald-500/40' },
  { id: 'amber', label: 'Gelb / Orange', bg: 'bg-amber-500/20', text: 'text-amber-400', border: 'border-amber-500/40' },
  { id: 'rose', label: 'Rot / Rose', bg: 'bg-rose-500/20', text: 'text-rose-400', border: 'border-rose-500/40' },
  { id: 'purple', label: 'Lila / Violett', bg: 'bg-purple-500/20', text: 'text-purple-400', border: 'border-purple-500/40' },
  { id: 'cyan', label: 'Cyan', bg: 'bg-cyan-500/20', text: 'text-cyan-400', border: 'border-cyan-500/40' },
]

function getColorClass(colorId?: string) {
  // Mapping von Backend-Farbnamen (blue -> primary, green -> emerald, etc.)
  let normalizedId = colorId
  if (colorId === 'blue') normalizedId = 'primary'
  if (colorId === 'green') normalizedId = 'emerald'
  const found = COLOR_PALETTE.find((c) => c.id === normalizedId)
  return found || COLOR_PALETTE[0]
}

function getDefaultColorForType(type: EventCategoryType): string {
  switch (type) {
    case 'team':
      return 'emerald'
    case 'server':
      return 'purple'
    case 'node':
      return 'amber'
    case 'personal':
    default:
      return 'primary'
  }
}

function pad(n: number) {
  return n < 10 ? `0${n}` : `${n}`
}

function formatIsoForInput(dt: Date) {
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`
}

export function Calendar() {
  const { t, i18n } = useTranslation()
  const [currentDate, setCurrentDate] = useState(() => new Date())
  const [viewMode, setViewMode] = useState<ViewMode>('month')
  const [selectedCategory, setSelectedCategory] = useState<'all' | EventCategoryType>('all')
  const [events, setEvents] = useState<CalendarEventItem[]>([])
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [isFeedModalOpen, setIsFeedModalOpen] = useState(false)

  const [teamsList, setTeamsList] = useState<{ id: number; name: string }[]>([])
  const [serversList, setServersList] = useState<{ id: number; name: string }[]>([])

  // Formular-State
  const [formEventId, setFormEventId] = useState<string | null>(null)
  const [formTitle, setFormTitle] = useState('')
  const [formStart, setFormStart] = useState('')
  const [formEnd, setFormEnd] = useState('')
  const [formDescription, setFormDescription] = useState('')
  const [formLocation, setFormLocation] = useState('')
  const [formAllDay, setFormAllDay] = useState(false)
  const [formColor, setFormColor] = useState('primary')
  const [formEventType, setFormEventType] = useState<EventCategoryType>('personal')
  const [formTeamId, setFormTeamId] = useState<number | null>(null)
  const [formServerId, setFormServerId] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)

  const locale = i18n.language.startsWith('de') ? 'de-DE' : 'en-US'

  // Datumsbereich basierend auf viewMode berechnen
  const { rangeStart, rangeEnd } = useMemo(() => {
    const d = new Date(currentDate)
    if (viewMode === 'month') {
      const first = new Date(d.getFullYear(), d.getMonth(), 1)
      const last = new Date(d.getFullYear(), d.getMonth() + 1, 0, 23, 59, 59)
      // Montag als Wochenstart für Monatsansicht
      const startDay = (first.getDay() + 6) % 7
      first.setDate(first.getDate() - startDay)
      const endDay = (last.getDay() + 6) % 7
      last.setDate(last.getDate() + (6 - endDay))
      return { rangeStart: first.toISOString(), rangeEnd: last.toISOString() }
    } else if (viewMode === 'week') {
      const first = new Date(d)
      const day = (d.getDay() + 6) % 7
      first.setDate(d.getDate() - day)
      first.setHours(0, 0, 0, 0)
      const last = new Date(first)
      last.setDate(first.getDate() + 6)
      last.setHours(23, 59, 59, 999)
      return { rangeStart: first.toISOString(), rangeEnd: last.toISOString() }
    } else {
      const start = new Date(d)
      start.setHours(0, 0, 0, 0)
      const end = new Date(d)
      end.setHours(23, 59, 59, 999)
      return { rangeStart: start.toISOString(), rangeEnd: end.toISOString() }
    }
  }, [currentDate, viewMode])

  const fetchEvents = useCallback(() => {
    const catParam = selectedCategory !== 'all' ? `&event_type=${encodeURIComponent(selectedCategory)}` : ''
    api<CalendarEventItem[]>(
      `/calendar/events?start=${encodeURIComponent(rangeStart)}&end=${encodeURIComponent(rangeEnd)}${catParam}`
    )
      .then((data) => {
        setEvents(Array.isArray(data) ? data : [])
      })
      .catch((err) => {
        toast.error(err.message)
      })
  }, [rangeStart, rangeEnd, selectedCategory])

  useEffect(() => {
    // Lade Teams und Server für Zuordnungs-Dropdowns
    api<any[]>('/teams')
      .then((res) => {
        if (Array.isArray(res)) {
          setTeamsList(res.map((t) => ({ id: t.id, name: t.name })))
        }
      })
      .catch(() => {})

    api<any[]>('/servers')
      .then((res) => {
        if (Array.isArray(res)) {
          setServersList(res.map((s) => ({ id: s.id, name: s.name })))
        }
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetchEvents()

    // Automatische Aktualisierung in Echtzeit alle 10 Sekunden (still im Hintergrund)
    const interval = setInterval(() => {
      const catParam = selectedCategory !== 'all' ? `&event_type=${encodeURIComponent(selectedCategory)}` : ''
      api<CalendarEventItem[]>(
        `/calendar/events?start=${encodeURIComponent(rangeStart)}&end=${encodeURIComponent(rangeEnd)}${catParam}`
      )
        .then((data) => {
          if (Array.isArray(data)) setEvents(data)
        })
        .catch(() => {})
    }, 10_000)

    const handleFocusOrUpdate = () => {
      const catParam = selectedCategory !== 'all' ? `&event_type=${encodeURIComponent(selectedCategory)}` : ''
      api<CalendarEventItem[]>(
        `/calendar/events?start=${encodeURIComponent(rangeStart)}&end=${encodeURIComponent(rangeEnd)}${catParam}`
      )
        .then((data) => {
          if (Array.isArray(data)) setEvents(data)
        })
        .catch(() => {})
    }

    window.addEventListener('focus', handleFocusOrUpdate)
    window.addEventListener('msm:calendar-updated', handleFocusOrUpdate)

    return () => {
      clearInterval(interval)
      window.removeEventListener('focus', handleFocusOrUpdate)
      window.removeEventListener('msm:calendar-updated', handleFocusOrUpdate)
    }
  }, [rangeStart, rangeEnd, selectedCategory, fetchEvents])

  const [touchStart, setTouchStart] = useState<{ x: number; y: number } | null>(null)
  const [animationClass, setAnimationClass] = useState('')
  const [animKey, setAnimKey] = useState(0)
  const [testingPush, setTestingPush] = useState(false)

  const handleTestPush = async () => {
    setTestingPush(true)
    try {
      // Vorab Berechtigung prüfen & anfordern
      await pruefeUndFrageGeraeteBerechtigung()

      const res = await api<{
        status: string
        email_sent: boolean
        device_notifications_enabled: boolean
        title: string
        start: string
        time_hint: string
      }>('/calendar/test-reminder', { method: 'POST' })

      const sent = await sendeGeraeteBenachrichtigung({
        titel: `Terminerinnerung (${res.time_hint})`,
        text: `${res.title} am ${res.start}`,
        erzwingen: true,
      })

      if (res.email_sent) {
        toast.success(t('calendar.testReminderSentEmail', 'Test-Erinnerung per Push und E-Mail versendet!'))
      } else if (sent) {
        toast.success(t('calendar.testReminderSent', 'Test-Erinnerung per Push ausgelöst!'))
      } else {
        toast.error(
          'Test-Erinnerung generiert. Falls kein Pop-up erscheint, bitte Benachrichtigungen für diese App in den Smartphone-Einstellungen erlauben.'
        )
      }
    } catch {
      toast.error(t('calendar.testReminderError', 'Fehler beim Senden der Test-Erinnerung'))
    } finally {
      setTestingPush(false)
    }
  }

  const handleTouchStart = (e: React.TouchEvent) => {
    if (e.touches.length === 1) {
      setTouchStart({ x: e.touches[0].clientX, y: e.touches[0].clientY })
    }
  }

  const handleTouchEnd = (e: React.TouchEvent) => {
    if (!touchStart || e.changedTouches.length === 0) return
    const deltaX = e.changedTouches[0].clientX - touchStart.x
    const deltaY = e.changedTouches[0].clientY - touchStart.y
    setTouchStart(null)

    // Nur bei dominanter horizontaler Wischbewegung (mind. 50px und 1.5x steiler als vertikal)
    if (Math.abs(deltaX) >= 50 && Math.abs(deltaX) > Math.abs(deltaY) * 1.5) {
      if (deltaX > 0) {
        handlePrev()
      } else {
        handleNext()
      }
    }
  }

  const handlePrev = () => {
    setAnimationClass('animate-calendar-slide-right')
    setAnimKey((k) => k + 1)
    const d = new Date(currentDate)
    if (viewMode === 'month') {
      d.setMonth(d.getMonth() - 1)
    } else if (viewMode === 'week') {
      d.setDate(d.getDate() - 7)
    } else {
      d.setDate(d.getDate() - 1)
    }
    setCurrentDate(d)
  }

  const handleNext = () => {
    setAnimationClass('animate-calendar-slide-left')
    setAnimKey((k) => k + 1)
    const d = new Date(currentDate)
    if (viewMode === 'month') {
      d.setMonth(d.getMonth() + 1)
    } else if (viewMode === 'week') {
      d.setDate(d.getDate() + 7)
    } else {
      d.setDate(d.getDate() + 1)
    }
    setCurrentDate(d)
  }

  const handleToday = () => {
    setAnimationClass('animate-fade-in')
    setAnimKey((k) => k + 1)
    setCurrentDate(new Date())
  }

  const openCreateModal = (prefilledDate?: Date) => {
    const start = prefilledDate ? new Date(prefilledDate) : new Date()
    if (prefilledDate) {
      if (start.getHours() === 0 && start.getMinutes() === 0) {
        start.setHours(9, 0, 0, 0)
      }
    } else {
      start.setMinutes(0, 0, 0)
      start.setHours(start.getHours() + 1)
    }
    const end = new Date(start)
    end.setHours(start.getHours() + 1)

    const targetType: EventCategoryType = selectedCategory !== 'all' ? selectedCategory : 'personal'

    setFormEventId(null)
    setFormTitle('')
    setFormStart(formatIsoForInput(start))
    setFormEnd(formatIsoForInput(end))
    setFormDescription('')
    setFormLocation('')
    setFormAllDay(false)
    setFormEventType(targetType)
    setFormTeamId(null)
    setFormServerId(null)
    setFormColor(getDefaultColorForType(targetType))
    setIsModalOpen(true)
  }

  const openEditModal = (ev: CalendarEventItem) => {
    const rawType = (ev.event_type as EventCategoryType) || 'personal'
    const evType: EventCategoryType = ['personal', 'team', 'server', 'node'].includes(rawType)
      ? rawType
      : 'personal'

    setFormEventId(ev.event_id)
    setFormTitle(ev.title)
    setFormStart(formatIsoForInput(new Date(ev.start)))
    setFormEnd(formatIsoForInput(new Date(ev.end)))
    setFormDescription(ev.description || '')
    setFormLocation(ev.location || '')
    setFormAllDay(Boolean(ev.all_day))
    setFormEventType(evType)
    setFormTeamId(ev.team_id || null)
    setFormServerId(ev.server_id || null)
    setFormColor(ev.color ? (ev.color === 'blue' ? 'primary' : ev.color === 'green' ? 'emerald' : ev.color) : getDefaultColorForType(evType))
    setIsModalOpen(true)
  }

  const handleSaveEvent = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!formTitle.trim()) {
      toast.error('Bitte gib einen Termintitel an')
      return
    }

    setSaving(true)
    try {
      if (formEventId) {
        await api(`/calendar/events/${encodeURIComponent(formEventId)}`, {
          method: 'PUT',
          body: JSON.stringify({
            title: formTitle.trim(),
            start_time: formStart,
            end_time: formEnd,
            description: formDescription.trim() || null,
            location: formLocation.trim() || null,
            all_day: formAllDay,
            color: formColor,
            event_type: formEventType,
            team_id: formEventType === 'team' ? formTeamId : null,
            server_id: formEventType === 'server' ? formServerId : null,
          }),
        })
        toast.success('Termin aktualisiert')
      } else {
        await api('/calendar/events', {
          method: 'POST',
          body: JSON.stringify({
            title: formTitle.trim(),
            start_time: formStart,
            end_time: formEnd,
            description: formDescription.trim() || null,
            location: formLocation.trim() || null,
            all_day: formAllDay,
            color: formColor,
            event_type: formEventType,
            team_id: formEventType === 'team' ? formTeamId : null,
            server_id: formEventType === 'server' ? formServerId : null,
          }),
        })
        toast.success('Termin erfolgreich erstellt')
      }
      setIsModalOpen(false)
      fetchEvents()
      window.dispatchEvent(new Event('msm:calendar-updated'))
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteEvent = async () => {
    if (!formEventId) return
    const ok = await confirm({
      title: 'Termin löschen',
      message: 'Möchtest du diesen Termin wirklich unwiderruflich aus deinem Kalender löschen?',
      confirmText: 'Löschen',
      cancelText: 'Abbrechen',
      danger: true,
    })
    if (!ok) return

    setSaving(true)
    try {
      await api(`/calendar/events/${encodeURIComponent(formEventId)}`, {
        method: 'DELETE',
      })
      toast.success('Termin gelöscht')
      setIsModalOpen(false)
      fetchEvents()
      window.dispatchEvent(new Event('msm:calendar-updated'))
    } catch (err: any) {
      toast.error(err.message)
    } finally {
      setSaving(false)
    }
  }

  // Kalender-Monatsraster berechnen
  const monthDays = useMemo(() => {
    if (viewMode !== 'month') return []
    const d = new Date(currentDate)
    const year = d.getFullYear()
    const month = d.getMonth()

    const firstDayOfMonth = new Date(year, month, 1)
    const startDayIndex = (firstDayOfMonth.getDay() + 6) % 7 // Mo = 0

    const days: Array<{ date: Date; isCurrentMonth: boolean; isToday: boolean }> = []

    // Vorheriger Monat
    for (let i = startDayIndex - 1; i >= 0; i--) {
      const prevDate = new Date(year, month, -i)
      days.push({ date: prevDate, isCurrentMonth: false, isToday: false })
    }

    // Aktueller Monat
    const lastDayOfMonth = new Date(year, month + 1, 0)
    const totalDaysInMonth = lastDayOfMonth.getDate()
    const today = new Date()

    for (let i = 1; i <= totalDaysInMonth; i++) {
      const current = new Date(year, month, i)
      const isToday =
        current.getDate() === today.getDate() &&
        current.getMonth() === today.getMonth() &&
        current.getFullYear() === today.getFullYear()
      days.push({ date: current, isCurrentMonth: true, isToday })
    }

    // Nächster Monat bis Raster voll ist (z. B. 35 oder 42 Tage)
    const remaining = (7 - (days.length % 7)) % 7
    for (let i = 1; i <= remaining; i++) {
      const nextDate = new Date(year, month + 1, i)
      days.push({ date: nextDate, isCurrentMonth: false, isToday: false })
    }

    return days
  }, [currentDate, viewMode])

  // Events für einen bestimmten Tag filtern
  const getEventsForDay = (day: Date) => {
    const dayStart = new Date(day)
    dayStart.setHours(0, 0, 0, 0)
    const dayEnd = new Date(day)
    dayEnd.setHours(23, 59, 59, 999)

    return events.filter((ev) => {
      const evStart = new Date(ev.start)
      const evEnd = new Date(ev.end)
      return evStart <= dayEnd && evEnd >= dayStart
    })
  }

  // Wochen-Tage
  const weekDays = useMemo(() => {
    if (viewMode !== 'week') return []
    const d = new Date(currentDate)
    const day = (d.getDay() + 6) % 7
    const first = new Date(d)
    first.setDate(d.getDate() - day)

    const today = new Date()
    return Array.from({ length: 7 }, (_, i) => {
      const dayDate = new Date(first)
      dayDate.setDate(first.getDate() + i)
      const isToday =
        dayDate.getDate() === today.getDate() &&
        dayDate.getMonth() === today.getMonth() &&
        dayDate.getFullYear() === today.getFullYear()
      return { date: dayDate, isToday }
    })
  }, [currentDate, viewMode])

  // Header Title
  const headerTitle = useMemo(() => {
    if (viewMode === 'month') {
      return currentDate.toLocaleDateString(locale, { month: 'long', year: 'numeric' })
    } else if (viewMode === 'week') {
      return `${t('calendar.week', 'Woche')} (${currentDate.toLocaleDateString(locale, { month: 'short', year: 'numeric' })})`
    } else {
      return currentDate.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
    }
  }, [currentDate, viewMode, locale, t])

  const [feedUrl, setFeedUrl] = useState('')
  const [loadingFeedUrl, setLoadingFeedUrl] = useState(false)

  const openFeedModal = () => {
    setIsFeedModalOpen(true)
    setLoadingFeedUrl(true)
    api<{ feed_url: string; token: string }>('/calendar/feed-url')
      .then((res) => {
        const resolved = apiUrl(res.feed_url)
        const fullUrl = resolved.startsWith('http')
          ? resolved
          : `${window.location.origin}${resolved}`
        setFeedUrl(fullUrl)
      })
      .catch(() => {
        const fallback = apiUrl('/calendar/feed.ics')
        const fullUrl = fallback.startsWith('http')
          ? fallback
          : `${window.location.origin}${fallback}`
        setFeedUrl(fullUrl)
      })
      .finally(() => {
        setLoadingFeedUrl(false)
      })
  }

  const renderCategoryBadge = (ev: CalendarEventItem, isCompact = false) => {
    if (ev.event_type === 'team') {
      return (
        <span
          title={ev.team_name ? `Team: ${ev.team_name}` : 'Team-Termin'}
          className={`inline-flex items-center gap-1 rounded font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 ${
            isCompact ? 'text-[9px] px-1 py-0.5' : 'text-[10px] px-1.5 py-0.5'
          }`}
        >
          <Users className={isCompact ? 'w-2.5 h-2.5' : 'w-3 h-3'} />
          <span className="truncate max-w-[85px]">{ev.team_name || 'Team'}</span>
        </span>
      )
    }
    if (ev.event_type === 'server') {
      return (
        <span
          title={ev.server_name ? `Server: ${ev.server_name}` : 'Server-Wartung'}
          className={`inline-flex items-center gap-1 rounded font-semibold bg-purple-500/20 text-purple-400 border border-purple-500/30 ${
            isCompact ? 'text-[9px] px-1 py-0.5' : 'text-[10px] px-1.5 py-0.5'
          }`}
        >
          <Server className={isCompact ? 'w-2.5 h-2.5' : 'w-3 h-3'} />
          <span className="truncate max-w-[85px]">{ev.server_name || 'Server'}</span>
        </span>
      )
    }
    if (ev.event_type === 'node') {
      return (
        <span
          title="Node / Infrastruktur"
          className={`inline-flex items-center gap-1 rounded font-semibold bg-amber-500/20 text-amber-400 border border-amber-500/30 ${
            isCompact ? 'text-[9px] px-1 py-0.5' : 'text-[10px] px-1.5 py-0.5'
          }`}
        >
          <Network className={isCompact ? 'w-2.5 h-2.5' : 'w-3 h-3'} />
          <span>Node</span>
        </span>
      )
    }
    return null
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('calendar.title', 'Kalender')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={handleTestPush}
              disabled={testingPush}
              className="gap-1.5"
            >
              <BellRing className={`w-4 h-4 ${testingPush ? 'animate-spin' : ''}`} />
              {t('calendar.testPush', 'Push testen')}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={openFeedModal}
              className="gap-1.5"
            >
              <Download className="w-4 h-4" />
              {t('calendar.subscribe', 'Abonnieren')}
            </Button>
          </div>
        }
      />

      {/* Kategorie Filter Leiste */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1 scrollbar-thin">
        <button
          type="button"
          onClick={() => setSelectedCategory('all')}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border transition-all ${
            selectedCategory === 'all'
              ? 'bg-primary text-on-primary border-primary shadow-sm'
              : 'bg-surface-container/60 text-on-surface-variant border-outline-variant/40 hover:bg-surface-container hover:text-on-surface'
          }`}
        >
          <CalendarIcon className="w-3.5 h-3.5" />
          <span>{t('calendar.filterAll', 'Alle')}</span>
          <span className="text-[10px] opacity-80 font-mono">({events.length})</span>
        </button>
        <button
          type="button"
          onClick={() => setSelectedCategory('personal')}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border transition-all ${
            selectedCategory === 'personal'
              ? 'bg-primary/20 text-primary border-primary/60 shadow-sm ring-1 ring-primary/40'
              : 'bg-surface-container/60 text-on-surface-variant border-outline-variant/40 hover:bg-surface-container hover:text-on-surface'
          }`}
        >
          <User className="w-3.5 h-3.5 text-primary" />
          <span>{t('calendar.filterPersonal', 'Persönlich')}</span>
          <span className="text-[10px] opacity-80 font-mono">
            ({events.filter((e) => !e.event_type || e.event_type === 'personal').length})
          </span>
        </button>
        <button
          type="button"
          onClick={() => setSelectedCategory('team')}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border transition-all ${
            selectedCategory === 'team'
              ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/60 shadow-sm ring-1 ring-emerald-500/40'
              : 'bg-surface-container/60 text-on-surface-variant border-outline-variant/40 hover:bg-surface-container hover:text-on-surface'
          }`}
        >
          <Users className="w-3.5 h-3.5 text-emerald-400" />
          <span>{t('calendar.filterTeam', 'Team')}</span>
          <span className="text-[10px] opacity-80 font-mono">
            ({events.filter((e) => e.event_type === 'team').length})
          </span>
        </button>
        <button
          type="button"
          onClick={() => setSelectedCategory('server')}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border transition-all ${
            selectedCategory === 'server'
              ? 'bg-purple-500/20 text-purple-400 border-purple-500/60 shadow-sm ring-1 ring-purple-500/40'
              : 'bg-surface-container/60 text-on-surface-variant border-outline-variant/40 hover:bg-surface-container hover:text-on-surface'
          }`}
        >
          <Server className="w-3.5 h-3.5 text-purple-400" />
          <span>{t('calendar.filterServer', 'Server-Wartung')}</span>
          <span className="text-[10px] opacity-80 font-mono">
            ({events.filter((e) => e.event_type === 'server').length})
          </span>
        </button>
        <button
          type="button"
          onClick={() => setSelectedCategory('node')}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border transition-all ${
            selectedCategory === 'node'
              ? 'bg-amber-500/20 text-amber-400 border-amber-500/60 shadow-sm ring-1 ring-amber-500/40'
              : 'bg-surface-container/60 text-on-surface-variant border-outline-variant/40 hover:bg-surface-container hover:text-on-surface'
          }`}
        >
          <Network className="w-3.5 h-3.5 text-amber-400" />
          <span>{t('calendar.filterNode', 'Node')}</span>
          <span className="text-[10px] opacity-80 font-mono">
            ({events.filter((e) => e.event_type === 'node').length})
          </span>
        </button>
      </div>

      {/* Kalender Steuerleiste */}
      <div className="msm-card p-4 flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={handlePrev} aria-label="Zurück">
            <ChevronLeft className="w-4 h-4" />
          </Button>
          <Button variant="secondary" size="sm" onClick={handleToday}>
            {t('calendar.today', 'Heute')}
          </Button>
          <Button variant="secondary" size="sm" onClick={handleNext} aria-label="Vor">
            <ChevronRight className="w-4 h-4" />
          </Button>
          <span className="font-headline text-lg font-bold text-on-surface ml-3">
            {headerTitle}
          </span>
        </div>

        {/* View Switcher */}
        <div className="flex items-center bg-surface-container rounded-lg p-1 border border-outline-variant/40">
          <button
            type="button"
            onClick={() => setViewMode('month')}
            className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors ${
              viewMode === 'month'
                ? 'bg-primary text-on-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            {t('calendar.viewMonth', 'Monat')}
          </button>
          <button
            type="button"
            onClick={() => setViewMode('week')}
            className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors ${
              viewMode === 'week'
                ? 'bg-primary text-on-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            {t('calendar.viewWeek', 'Woche')}
          </button>
          <button
            type="button"
            onClick={() => setViewMode('day')}
            className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors ${
              viewMode === 'day'
                ? 'bg-primary text-on-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            {t('calendar.viewDay', 'Tag')}
          </button>
        </div>
      </div>

      {/* Kalender-Inhaltsbereich mit horizontaler Wischgesten-Steuerung */}
      <div
        key={animKey}
        className={`touch-pan-y ${animationClass}`}
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
      >
        {/* Hauptansicht: MONAT */}
        {viewMode === 'month' && (
          <div className="msm-card p-0 overflow-hidden">
          {/* Wochentag-Kopfzeile */}
          <div className="grid grid-cols-7 border-b border-outline-variant/40 bg-surface-container/50 text-center font-label-md text-xs font-semibold uppercase tracking-wider text-on-surface-variant py-2.5">
            <div>Mo</div>
            <div>Di</div>
            <div>Mi</div>
            <div>Do</div>
            <div>Fr</div>
            <div>Sa</div>
            <div>So</div>
          </div>

          {/* Tages-Zellen */}
          <div className="grid grid-cols-7 auto-rows-fr divide-x divide-y divide-outline-variant/20 bg-surface">
            {monthDays.map(({ date, isCurrentMonth, isToday }, idx) => {
              const dayEvents = getEventsForDay(date)
              return (
                <div
                  key={idx}
                  onClick={() => openCreateModal(date)}
                  className={`min-h-[110px] p-1.5 flex flex-col justify-between transition-colors hover:bg-surface-container/40 cursor-pointer ${
                    !isCurrentMonth ? 'opacity-35 bg-surface-container/10' : ''
                  } ${isToday ? 'bg-primary/5 ring-1 ring-inset ring-primary/40' : ''}`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span
                      className={`inline-flex items-center justify-center text-xs font-semibold rounded-full w-6 h-6 ${
                        isToday
                          ? 'bg-primary text-on-primary'
                          : isCurrentMonth
                          ? 'text-on-surface'
                          : 'text-on-surface-variant'
                      }`}
                    >
                      {date.getDate()}
                    </span>
                    {dayEvents.length > 0 && (
                      <span className="text-[10px] text-on-surface-variant px-1 font-mono">
                        {dayEvents.length}
                      </span>
                    )}
                  </div>

                  {/* Event Chips */}
                  <div className="space-y-1 overflow-hidden">
                    {dayEvents.slice(0, 3).map((ev) => {
                      const colorStyle = getColorClass(ev.color)
                      const timeStr = new Date(ev.start).toLocaleTimeString(locale, {
                        hour: '2-digit',
                        minute: '2-digit',
                      })
                      return (
                        <div
                          key={ev.event_id}
                          onClick={(e) => {
                            e.stopPropagation()
                            openEditModal(ev)
                          }}
                          className={`text-[11px] leading-tight px-1.5 py-0.5 rounded border truncate flex items-center gap-1 ${colorStyle.bg} ${colorStyle.text} ${colorStyle.border} hover:brightness-110`}
                        >
                          {ev.event_type === 'team' && <Users className="w-2.5 h-2.5 shrink-0 opacity-90 text-emerald-400" />}
                          {ev.event_type === 'server' && <Server className="w-2.5 h-2.5 shrink-0 opacity-90 text-purple-400" />}
                          {ev.event_type === 'node' && <Network className="w-2.5 h-2.5 shrink-0 opacity-90 text-amber-400" />}
                          <span className="font-semibold shrink-0">{timeStr}</span>
                          <span className="truncate">{ev.title}</span>
                        </div>
                      )
                    })}
                    {dayEvents.length > 3 && (
                      <div className="text-[10px] text-primary/80 font-medium px-1">
                        +{dayEvents.length - 3} weitere
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Hauptansicht: WOCHE */}
      {viewMode === 'week' && (
        <div className="msm-card p-0 overflow-hidden">
          {/* Desktop & Tablet: 7-Spalten Kalender-Raster */}
          <div className="hidden md:block">
            <div className="grid grid-cols-7 border-b border-outline-variant/40 bg-surface-container/50 text-center py-2.5">
              {weekDays.map(({ date, isToday }, idx) => (
                <div key={idx} className="flex flex-col items-center">
                  <span className="text-[11px] font-label-md uppercase text-on-surface-variant">
                    {date.toLocaleDateString(locale, { weekday: 'short' })}
                  </span>
                  <span
                    className={`mt-0.5 inline-flex items-center justify-center text-xs font-semibold rounded-full w-6 h-6 ${
                      isToday ? 'bg-primary text-on-primary' : 'text-on-surface'
                    }`}
                  >
                    {date.getDate()}
                  </span>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-7 divide-x divide-outline-variant/20 min-h-[500px] bg-surface">
              {weekDays.map(({ date, isToday }, idx) => {
                const dayEvents = getEventsForDay(date)
                return (
                  <div
                    key={idx}
                    onClick={() => openCreateModal(date)}
                    className={`p-2 space-y-2 hover:bg-surface-container/30 cursor-pointer ${
                      isToday ? 'bg-primary/5' : ''
                    }`}
                  >
                    {dayEvents.length === 0 ? (
                      <div className="h-full flex items-center justify-center text-xs text-on-surface-variant/40 italic">
                        Keine Termine
                      </div>
                    ) : (
                      dayEvents.map((ev) => {
                        const colorStyle = getColorClass(ev.color)
                        const startStr = new Date(ev.start).toLocaleTimeString(locale, {
                          hour: '2-digit',
                          minute: '2-digit',
                        })
                        const endStr = new Date(ev.end).toLocaleTimeString(locale, {
                          hour: '2-digit',
                          minute: '2-digit',
                        })
                        return (
                          <div
                            key={ev.event_id}
                            onClick={(e) => {
                              e.stopPropagation()
                              openEditModal(ev)
                            }}
                            className={`p-2 rounded-lg border text-xs ${colorStyle.bg} ${colorStyle.text} ${colorStyle.border} hover:brightness-110`}
                          >
                            <div className="flex items-start justify-between gap-1">
                              <div className="font-semibold text-sm truncate flex-1">{ev.title}</div>
                              {renderCategoryBadge(ev, true)}
                            </div>
                            <div className="flex items-center gap-1 text-[10px] opacity-80 mt-1">
                              <Clock className="w-3 h-3" />
                              <span>{startStr} – {endStr}</span>
                            </div>
                            {ev.location && (
                              <div className="flex items-center gap-1 text-[10px] opacity-80 mt-0.5 truncate">
                                <MapPin className="w-3 h-3" />
                                <span className="truncate">{ev.location}</span>
                              </div>
                            )}
                          </div>
                        )
                      })
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          {/* Smartphone & Mobilansicht: Vertikale Tages-Kartenliste der Woche */}
          <div className="block md:hidden divide-y divide-outline-variant/20 bg-surface">
            {weekDays.map(({ date, isToday }, idx) => {
              const dayEvents = getEventsForDay(date)
              const weekdayStr = date.toLocaleDateString(locale, { weekday: 'long' })
              const dateStr = date.toLocaleDateString(locale, { day: 'numeric', month: 'short' })
              return (
                <div
                  key={idx}
                  className={`p-3 space-y-2 transition-colors ${
                    isToday ? 'bg-primary/5' : ''
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span
                        className={`inline-flex items-center justify-center text-xs font-bold rounded-full w-6 h-6 ${
                          isToday ? 'bg-primary text-on-primary' : 'bg-surface-container text-on-surface'
                        }`}
                      >
                        {date.getDate()}
                      </span>
                      <span className="text-sm font-semibold text-on-surface">
                        {weekdayStr}, {dateStr}
                      </span>
                      {isToday && (
                        <span className="text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded bg-primary/15 text-primary">
                          Heute
                        </span>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => openCreateModal(date)}
                      className="p-1 rounded-md text-on-surface-variant hover:text-primary hover:bg-surface-container transition-colors"
                      title="Termin hinzufügen"
                    >
                      <Plus className="w-4 h-4" />
                    </button>
                  </div>

                  {dayEvents.length === 0 ? (
                    <div
                      onClick={() => openCreateModal(date)}
                      className="py-2 px-3 rounded-lg border border-dashed border-outline-variant/40 text-xs text-on-surface-variant/50 hover:bg-surface-container/20 cursor-pointer text-center"
                    >
                      Keine Termine — Tippen zum Erstellen
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {dayEvents.map((ev) => {
                        const colorStyle = getColorClass(ev.color)
                        const startStr = new Date(ev.start).toLocaleTimeString(locale, {
                          hour: '2-digit',
                          minute: '2-digit',
                        })
                        const endStr = new Date(ev.end).toLocaleTimeString(locale, {
                          hour: '2-digit',
                          minute: '2-digit',
                        })
                        return (
                          <div
                            key={ev.event_id}
                            onClick={() => openEditModal(ev)}
                            className={`p-2.5 rounded-lg border text-xs cursor-pointer flex items-center justify-between gap-2 ${colorStyle.bg} ${colorStyle.text} ${colorStyle.border} hover:brightness-110`}
                          >
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center justify-between gap-2">
                                <div className="font-semibold text-sm truncate">{ev.title}</div>
                                {renderCategoryBadge(ev, true)}
                              </div>
                              <div className="flex items-center gap-3 text-[11px] opacity-80 mt-1">
                                <span className="flex items-center gap-1">
                                  <Clock className="w-3.5 h-3.5" />
                                  {startStr} – {endStr}
                                </span>
                                {ev.location && (
                                  <span className="flex items-center gap-1 truncate">
                                    <MapPin className="w-3.5 h-3.5" />
                                    <span className="truncate">{ev.location}</span>
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Hauptansicht: TAG */}
      {viewMode === 'day' && (
        <div className="msm-card p-6 max-w-2xl mx-auto space-y-4">
          <div className="flex items-center justify-between border-b border-outline-variant/40 pb-4">
            <div>
              <h3 className="font-headline text-lg font-bold text-on-surface">
                {currentDate.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
              </h3>
              <p className="text-xs text-on-surface-variant">
                {getEventsForDay(currentDate).length} Termine für diesen Tag eingetragen
              </p>
            </div>
            <Button size="sm" onClick={() => openCreateModal(currentDate)} className="gap-1">
              <Plus className="w-4 h-4" />
              Neuer Termin
            </Button>
          </div>

          <div className="space-y-3">
            {getEventsForDay(currentDate).length === 0 ? (
              <div className="py-12 text-center text-on-surface-variant">
                <CalendarIcon className="w-10 h-10 mx-auto opacity-30 mb-2" />
                <p className="text-sm">Keine Termine für diesen Tag vorhanden.</p>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => openCreateModal(currentDate)}
                  className="mt-4 gap-1.5"
                >
                  <Plus className="w-4 h-4" />
                  Termin erstellen
                </Button>
              </div>
            ) : (
              getEventsForDay(currentDate).map((ev) => {
                const colorStyle = getColorClass(ev.color)
                const startStr = new Date(ev.start).toLocaleTimeString(locale, {
                  hour: '2-digit',
                  minute: '2-digit',
                })
                const endStr = new Date(ev.end).toLocaleTimeString(locale, {
                  hour: '2-digit',
                  minute: '2-digit',
                })
                return (
                  <div
                    key={ev.event_id}
                    onClick={() => openEditModal(ev)}
                    className={`p-4 rounded-xl border cursor-pointer transition-all hover:scale-[1.01] ${colorStyle.bg} ${colorStyle.text} ${colorStyle.border}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <h4 className="font-headline font-bold text-base">{ev.title}</h4>
                        {renderCategoryBadge(ev)}
                      </div>
                      <span className="text-xs font-mono font-semibold px-2 py-0.5 rounded bg-surface/50">
                        {startStr} – {endStr}
                      </span>
                    </div>
                    {ev.location && (
                      <div className="flex items-center gap-1.5 text-xs opacity-90 mt-2">
                        <MapPin className="w-3.5 h-3.5" />
                        <span>{ev.location}</span>
                      </div>
                    )}
                    {ev.description && (
                      <p className="text-xs mt-2 text-on-surface/80 whitespace-pre-wrap">
                        {ev.description}
                      </p>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </div>
      )}
      </div>

      {/* MODAL: Termin anlegen / bearbeiten */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-sm animate-fade-in">
          <div className="msm-card w-full max-w-lg p-6 shadow-2xl space-y-5 animate-scale-in max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between border-b border-outline-variant/30 pb-3">
              <h3 className="font-headline text-lg font-bold text-on-surface">
                {formEventId ? t('calendar.editEvent', 'Termin bearbeiten') : t('calendar.createEvent', 'Neuer Termin')}
              </h3>
              <button
                type="button"
                onClick={() => setIsModalOpen(false)}
                className="text-on-surface-variant hover:text-on-surface p-1 rounded-md"
                aria-label="Schließen"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <form onSubmit={handleSaveEvent} className="space-y-4">
              <div>
                <label className="block text-xs font-label-md font-semibold text-on-surface-variant uppercase mb-1">
                  {t('calendar.category', 'Kategorie')} *
                </label>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setFormEventType('personal')
                      setFormColor('primary')
                    }}
                    className={`flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border text-xs font-semibold transition-all ${
                      formEventType === 'personal'
                        ? 'bg-primary/20 text-primary border-primary ring-1 ring-primary'
                        : 'bg-surface-container-low text-on-surface-variant border-outline-variant/40 hover:bg-surface-container'
                    }`}
                  >
                    <User className="w-3.5 h-3.5 text-primary" />
                    Persönlich
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setFormEventType('team')
                      setFormColor('emerald')
                    }}
                    className={`flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border text-xs font-semibold transition-all ${
                      formEventType === 'team'
                        ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500 ring-1 ring-emerald-500'
                        : 'bg-surface-container-low text-on-surface-variant border-outline-variant/40 hover:bg-surface-container'
                    }`}
                  >
                    <Users className="w-3.5 h-3.5 text-emerald-400" />
                    Team
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setFormEventType('server')
                      setFormColor('purple')
                    }}
                    className={`flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border text-xs font-semibold transition-all ${
                      formEventType === 'server'
                        ? 'bg-purple-500/20 text-purple-400 border-purple-500 ring-1 ring-purple-500'
                        : 'bg-surface-container-low text-on-surface-variant border-outline-variant/40 hover:bg-surface-container'
                    }`}
                  >
                    <Server className="w-3.5 h-3.5 text-purple-400" />
                    Server
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setFormEventType('node')
                      setFormColor('amber')
                    }}
                    className={`flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border text-xs font-semibold transition-all ${
                      formEventType === 'node'
                        ? 'bg-amber-500/20 text-amber-400 border-amber-500 ring-1 ring-amber-500'
                        : 'bg-surface-container-low text-on-surface-variant border-outline-variant/40 hover:bg-surface-container'
                    }`}
                  >
                    <Network className="w-3.5 h-3.5 text-amber-400" />
                    Node
                  </button>
                </div>
              </div>

              {formEventType === 'team' && (
                <div>
                  <label htmlFor="cal-form-team" className="block text-xs font-label-md font-semibold text-on-surface-variant uppercase mb-1">
                    {t('calendar.teamSelect', 'Team zuordnen')}
                  </label>
                  <select
                    id="cal-form-team"
                    value={formTeamId || ''}
                    onChange={(e) => setFormTeamId(e.target.value ? Number(e.target.value) : null)}
                    className="msm-input w-full text-xs"
                  >
                    <option value="">-- {t('calendar.selectTeamOptional', 'Team wählen (optional)')} --</option>
                    {teamsList.map((tm) => (
                      <option key={tm.id} value={tm.id}>
                        {tm.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {formEventType === 'server' && (
                <div>
                  <label htmlFor="cal-form-server" className="block text-xs font-label-md font-semibold text-on-surface-variant uppercase mb-1">
                    {t('calendar.serverSelect', 'Server zuordnen')}
                  </label>
                  <select
                    id="cal-form-server"
                    value={formServerId || ''}
                    onChange={(e) => setFormServerId(e.target.value ? Number(e.target.value) : null)}
                    className="msm-input w-full text-xs"
                  >
                    <option value="">-- {t('calendar.selectServerOptional', 'Server wählen (optional)')} --</option>
                    {serversList.map((srv) => (
                      <option key={srv.id} value={srv.id}>
                        {srv.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div>
                <label htmlFor="cal-form-title" className="block text-xs font-label-md font-semibold text-on-surface-variant uppercase mb-1">
                  {t('calendar.eventTitle', 'Titel / Anlass')} *
                </label>
                <input
                  id="cal-form-title"
                  type="text"
                  required
                  value={formTitle}
                  onChange={(e) => setFormTitle(e.target.value)}
                  placeholder={t('calendar.eventTitlePlaceholder', 'z. B. Team-Meeting, Wartung Server 1')}
                  className="msm-input w-full"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-label-md font-semibold text-on-surface-variant uppercase mb-1">
                    {t('calendar.start', 'Beginn')} *
                  </label>
                  <DateTimePicker
                    value={formStart}
                    onChange={(val) => setFormStart(val)}
                    locale={i18n.language.startsWith('de') ? 'de' : 'en'}
                    placeholder={t('calendar.selectStart', 'Beginn wählen')}
                    aria-label={t('calendar.start', 'Beginn')}
                    className="w-full"
                  />
                </div>
                <div>
                  <label className="block text-xs font-label-md font-semibold text-on-surface-variant uppercase mb-1">
                    {t('calendar.end', 'Ende')} *
                  </label>
                  <DateTimePicker
                    value={formEnd}
                    onChange={(val) => setFormEnd(val)}
                    locale={i18n.language.startsWith('de') ? 'de' : 'en'}
                    placeholder={t('calendar.selectEnd', 'Ende wählen')}
                    aria-label={t('calendar.end', 'Ende')}
                    className="w-full"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="cal-form-location" className="block text-xs font-label-md font-semibold text-on-surface-variant uppercase mb-1">
                  {t('calendar.location', 'Ort / Meeting-Link')}
                </label>
                <input
                  id="cal-form-location"
                  type="text"
                  value={formLocation}
                  onChange={(e) => setFormLocation(e.target.value)}
                  placeholder={t('calendar.locationPlaceholder', 'z. B. Konferenzraum A oder Teams / Zoom')}
                  className="msm-input w-full"
                />
              </div>

              <div>
                <label htmlFor="cal-form-description" className="block text-xs font-label-md font-semibold text-on-surface-variant uppercase mb-1">
                  {t('calendar.descriptionLabel', 'Beschreibung / Notizen')}
                </label>
                <textarea
                  id="cal-form-description"
                  rows={3}
                  value={formDescription}
                  onChange={(e) => setFormDescription(e.target.value)}
                  placeholder={t('calendar.descriptionPlaceholder', 'Agenda, Vorbereitungspunkte oder Details...')}
                  className="msm-input w-full resize-none text-xs"
                />
              </div>

              {/* Farbwahl */}
              <div>
                <label className="block text-xs font-label-md font-semibold text-on-surface-variant uppercase mb-2">
                  {t('calendar.color', 'Farbkennzeichnung')}
                </label>
                <div className="flex flex-wrap items-center gap-2">
                  {COLOR_PALETTE.map((c) => (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => setFormColor(c.id)}
                      className={`px-2.5 py-1 rounded-md text-xs font-semibold border transition-all ${c.bg} ${c.text} ${
                        formColor === c.id ? `ring-2 ring-primary ${c.border}` : 'opacity-70 hover:opacity-100'
                      }`}
                    >
                      {c.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex items-center justify-between border-t border-outline-variant/30 pt-4 mt-4">
                {formEventId ? (
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={handleDeleteEvent}
                    disabled={saving}
                    className="text-error border-error/40 hover:bg-error/10 gap-1.5"
                  >
                    <Trash2 className="w-4 h-4" />
                    {t('common.delete', 'Löschen')}
                  </Button>
                ) : <div />}

                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => setIsModalOpen(false)}
                    disabled={saving}
                  >
                    {t('common.cancel', 'Abbrechen')}
                  </Button>
                  <Button type="submit" disabled={saving}>
                    {saving ? t('calendar.saving', 'Speichern...') : t('common.save', 'Speichern')}
                  </Button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: Kalender-Abonnement & Feed */}
      {isFeedModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-sm animate-fade-in">
          <div className="msm-card w-full max-w-lg p-6 shadow-2xl space-y-5 animate-scale-in">
            <div className="flex items-center justify-between border-b border-outline-variant/30 pb-3">
              <h3 className="font-headline text-lg font-bold text-on-surface">
                {t('calendar.feedModalTitle', 'Kalender abonnieren & exportieren')}
              </h3>
              <button
                type="button"
                onClick={() => setIsFeedModalOpen(false)}
                className="text-on-surface-variant hover:text-on-surface p-1 rounded-md"
                aria-label={t('common.close', 'Schließen')}
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <p className="text-xs leading-relaxed text-on-surface-variant">
              {t(
                'calendar.feedModalDescription',
                'Du kannst deinen MSM-Kalender in jeder gängigen Kalender-App (Windows Kalender, Microsoft Outlook, Thunderbird, Apple Calendar, Google Calendar) synchronisieren oder als .ics-Datei herunterladen.'
              )}
            </p>

            <div className="space-y-2">
              <label htmlFor="cal-feed-url-input" className="block text-xs font-label-md font-semibold text-on-surface-variant uppercase">
                {t('calendar.feedUrlLabel', 'iCal / Webcal Feed-URL')}
              </label>
              <div className="flex items-center gap-2">
                <input
                  id="cal-feed-url-input"
                  type="text"
                  readOnly
                  value={loadingFeedUrl ? t('calendar.loadingFeedUrl', 'Lade Feed-URL...') : feedUrl}
                  className="msm-input flex-1 font-mono text-xs select-all"
                />
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={loadingFeedUrl || !feedUrl}
                  onClick={() => {
                    navigator.clipboard.writeText(feedUrl)
                    toast.success(t('calendar.feedUrlCopied', 'URL in die Zwischenablage kopiert'))
                  }}
                  className="gap-1"
                >
                  <Link className="w-4 h-4" />
                  {t('common.copy', 'Kopieren')}
                </Button>
              </div>
            </div>

            <div className="border-t border-outline-variant/30 pt-4 flex justify-between items-center">
              <a
                href={feedUrl}
                download="msm-calendar.ics"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-surface-container border border-outline-variant/60 text-xs font-semibold text-on-surface hover:bg-surface-container-high"
              >
                <Download className="w-4 h-4" />
                {t('calendar.downloadIcs', '.ics-Datei herunterladen')}
              </a>
              <Button onClick={() => setIsFeedModalOpen(false)}>
                {t('common.close', 'Schließen')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
