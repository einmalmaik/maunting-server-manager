import {
  forwardRef,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type HTMLAttributes,
} from 'react'
import { createPortal } from 'react-dom'
import { CalendarDays, ChevronLeft, ChevronRight, Clock } from 'lucide-react'
import { cx } from '@/utils/classNames'
import { NumberStepper } from './NumberStepper'

export interface DateTimePickerProps
  extends Omit<HTMLAttributes<HTMLDivElement>, 'value' | 'onChange'> {
  value: string
  onChange: (value: string) => void
  locale?: 'de' | 'en'
  placeholder?: string
  disabled?: boolean
  min?: string
  max?: string
  buttonClassName?: string
  'aria-label'?: string
  'data-testid'?: string
}

const pad = (value: number): string => String(value).padStart(2, '0')

const toDateTimeValue = (date: Date, hour: number, minute: number): string =>
  `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(hour)}:${pad(minute)}`

const parseDateTimeValue = (value: string): { date: Date; hour: number; minute: number } | null => {
  if (!value) return null
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(value)
  if (!match) return null
  const [, year, month, day, hourStr, minuteStr] = match
  const date = new Date(Number(year), Number(month) - 1, Number(day))
  if (Number.isNaN(date.getTime())) return null
  const hour = hourStr !== undefined ? Number(hourStr) : 12
  const minute = minuteStr !== undefined ? Number(minuteStr) : 0
  return { date, hour, minute }
}

const startOfMonth = (date: Date): Date => new Date(date.getFullYear(), date.getMonth(), 1)

const sameDay = (left: Date, right: Date): boolean =>
  left.getFullYear() === right.getFullYear() &&
  left.getMonth() === right.getMonth() &&
  left.getDate() === right.getDate()

const buildCalendarDays = (month: Date): Date[] => {
  const first = startOfMonth(month)
  const mondayOffset = (first.getDay() + 6) % 7
  const start = new Date(first)
  start.setDate(first.getDate() - mondayOffset)

  return Array.from({ length: 42 }, (_, index) => {
    const day = new Date(start)
    day.setDate(start.getDate() + index)
    return day
  })
}

const getMinMaxDate = (value?: string): Date | null => parseDateTimeValue(value ?? '')?.date ?? null

export const DateTimePicker = forwardRef<HTMLDivElement, DateTimePickerProps>(
  (
    {
      value,
      onChange,
      locale = 'de',
      placeholder,
      disabled = false,
      min,
      max,
      className = '',
      buttonClassName = '',
      'aria-label': ariaLabel,
      'data-testid': testId,
      ...props
    },
    ref,
  ) => {
    const parsed = parseDateTimeValue(value)
    const today = useMemo(() => new Date(), [])
    const [open, setOpen] = useState(false)
    const [visibleMonth, setVisibleMonth] = useState<Date>(() => startOfMonth(parsed?.date ?? today))
    const [menuStyle, setMenuStyle] = useState<CSSProperties | null>(null)
    const rootRef = useRef<HTMLDivElement | null>(null)
    const menuRef = useRef<HTMLDivElement | null>(null)
    const dialogId = useId()

    const selectedDate = parsed?.date ?? null
    const hour = parsed?.hour ?? 12
    const minute = parsed?.minute ?? 0
    const minDate = getMinMaxDate(min)
    const maxDate = getMinMaxDate(max)
    const monthDays = buildCalendarDays(visibleMonth)
    const weekdays =
      locale === 'de'
        ? ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
        : ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

    useEffect(() => {
      const next = parseDateTimeValue(value)
      if (next) setVisibleMonth(startOfMonth(next.date))
    }, [value])

    useEffect(() => {
      if (!open) return
      const onClick = (event: MouseEvent) => {
        const target = event.target as Node
        if (
          rootRef.current &&
          !rootRef.current.contains(target) &&
          menuRef.current &&
          !menuRef.current.contains(target)
        ) {
          setOpen(false)
        }
      }
      const onKey = (event: KeyboardEvent) => {
        if (event.key === 'Escape') setOpen(false)
      }
      document.addEventListener('mousedown', onClick)
      document.addEventListener('keydown', onKey)
      return () => {
        document.removeEventListener('mousedown', onClick)
        document.removeEventListener('keydown', onKey)
      }
    }, [open])

    useEffect(() => {
      if (!open) return
      const updatePosition = () => {
        const rect = rootRef.current?.getBoundingClientRect()
        if (!rect) return
        const RAND = 8
        const MENUE_HOEHE = 360
        const MENUE_BREITE = 320
        const platzUnten = window.innerHeight - rect.bottom - RAND * 2
        const platzOben = rect.top - RAND * 2
        const nachOben = platzUnten < MENUE_HOEHE && platzOben > platzUnten

        setMenuStyle({
          position: 'fixed',
          ...(nachOben
            ? { bottom: Math.max(RAND, window.innerHeight - rect.top + RAND) }
            : { top: rect.bottom + RAND }),
          left: Math.min(rect.left, window.innerWidth - MENUE_BREITE - RAND),
          width: Math.max(rect.width, MENUE_BREITE),
          zIndex: 100,
        })
      }
      updatePosition()
      window.addEventListener('resize', updatePosition)
      window.addEventListener('scroll', updatePosition, true)
      return () => {
        window.removeEventListener('resize', updatePosition)
        window.removeEventListener('scroll', updatePosition, true)
      }
    }, [open])

    const displayValue = parsed
      ? new Intl.DateTimeFormat(locale === 'de' ? 'de-DE' : 'en-US', {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
        }).format(
          new Date(
            parsed.date.getFullYear(),
            parsed.date.getMonth(),
            parsed.date.getDate(),
            hour,
            minute,
          ),
        )
      : (placeholder ?? (locale === 'de' ? 'Datum und Uhrzeit wählen' : 'Pick date and time'))

    const isOutOfRange = (date: Date): boolean => {
      const candidate = new Date(date.getFullYear(), date.getMonth(), date.getDate())
      if (
        minDate &&
        candidate < new Date(minDate.getFullYear(), minDate.getMonth(), minDate.getDate())
      )
        return true
      if (
        maxDate &&
        candidate > new Date(maxDate.getFullYear(), maxDate.getMonth(), maxDate.getDate())
      )
        return true
      return false
    }

    const selectDate = (date: Date) => {
      if (isOutOfRange(date)) return
      onChange(toDateTimeValue(date, hour, minute))
    }

    const updateTime = (nextHour: number, nextMinute: number) => {
      onChange(toDateTimeValue(selectedDate ?? today, nextHour, nextMinute))
    }

    return (
      <div ref={ref} className={cx('relative min-w-0 w-full', className)} {...props}>
        <div ref={rootRef}>
          <button
            type="button"
            disabled={disabled}
            aria-label={ariaLabel}
            aria-haspopup="dialog"
            aria-expanded={open}
            aria-controls={dialogId}
            data-testid={testId}
            onClick={() => !disabled && setOpen((current) => !current)}
            className={cx(
              'msm-input flex h-10 min-w-0 w-full items-center justify-between gap-2 px-3 text-left disabled:cursor-not-allowed disabled:opacity-50',
              open && 'border-primary ring-2 ring-primary/25',
              buttonClassName,
            )}
          >
            <span className="flex min-w-0 items-center gap-2 truncate">
              <CalendarDays className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
              <span className={cx('truncate text-sm', !parsed && 'text-on-surface-variant')}>
                {displayValue}
              </span>
            </span>
            <Clock className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
          </button>

          {open && menuStyle
            ? createPortal(
                <div
                  ref={menuRef}
                  id={dialogId}
                  role="dialog"
                  style={menuStyle}
                  data-msm-dropdown-menu=""
                  className="z-[100] rounded-xl border border-outline-variant bg-surface-container-high p-3 shadow-panel"
                  onPointerDown={(e) => e.nativeEvent.stopImmediatePropagation()}
                  onPointerUp={(e) => e.nativeEvent.stopImmediatePropagation()}
                  onMouseDown={(e) => e.nativeEvent.stopImmediatePropagation()}
                  onMouseUp={(e) => e.nativeEvent.stopImmediatePropagation()}
                  onClick={(e) => e.nativeEvent.stopImmediatePropagation()}
                >
                  <div className="flex items-center justify-between gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        setVisibleMonth(
                          (current) => new Date(current.getFullYear(), current.getMonth() - 1, 1),
                        )
                      }
                      className="grid h-8 w-8 place-items-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container-highest hover:text-on-surface"
                      aria-label={locale === 'de' ? 'Vorheriger Monat' : 'Previous month'}
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </button>
                    <span className="text-sm font-semibold text-on-surface">
                      {new Intl.DateTimeFormat(locale === 'de' ? 'de-DE' : 'en-US', {
                        month: 'long',
                        year: 'numeric',
                      }).format(visibleMonth)}
                    </span>
                    <button
                      type="button"
                      onClick={() =>
                        setVisibleMonth(
                          (current) => new Date(current.getFullYear(), current.getMonth() + 1, 1),
                        )
                      }
                      className="grid h-8 w-8 place-items-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container-highest hover:text-on-surface"
                      aria-label={locale === 'de' ? 'Nächster Monat' : 'Next month'}
                    >
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  </div>

                  <div className="mt-3 grid grid-cols-7 gap-1 text-center text-[10px] font-bold uppercase tracking-wider text-on-surface-variant">
                    {weekdays.map((day) => (
                      <span key={day}>{day}</span>
                    ))}
                  </div>
                  <div className="mt-1 grid grid-cols-7 gap-1">
                    {monthDays.map((day) => {
                      const muted = day.getMonth() !== visibleMonth.getMonth()
                      const active = selectedDate ? sameDay(day, selectedDate) : false
                      const disabledDay = isOutOfRange(day)
                      return (
                        <button
                          key={day.toISOString()}
                          type="button"
                          disabled={disabledDay}
                          onClick={() => selectDate(day)}
                          className={cx(
                            'grid h-8 place-items-center rounded-lg text-xs transition-colors',
                            muted ? 'text-on-surface-variant/30' : 'text-on-surface',
                            'hover:bg-surface-container-highest hover:text-on-surface',
                            active &&
                              'border border-primary/40 bg-primary/20 font-semibold text-primary shadow-sm',
                            disabledDay &&
                              'cursor-not-allowed opacity-30 hover:bg-transparent hover:text-on-surface-variant/30',
                          )}
                        >
                          {day.getDate()}
                        </button>
                      )
                    })}
                  </div>

                  <div className="mt-3 grid grid-cols-[1fr_auto_1fr] items-end gap-2 border-t border-outline-variant pt-3">
                    <div className="min-w-0 space-y-1.5">
                      <span className="block text-[10px] font-bold uppercase tracking-wider text-on-surface-variant">
                        {locale === 'de' ? 'Stunde' : 'Hour'}
                      </span>
                      <NumberStepper
                        size="sm"
                        value={hour}
                        min={0}
                        max={23}
                        onValueChange={(next) => updateTime(Number(next || 0), minute)}
                      />
                    </div>
                    <span className="pb-2 text-sm font-bold text-on-surface-variant">:</span>
                    <div className="min-w-0 space-y-1.5">
                      <span className="block text-[10px] font-bold uppercase tracking-wider text-on-surface-variant">
                        {locale === 'de' ? 'Minute' : 'Minute'}
                      </span>
                      <NumberStepper
                        size="sm"
                        value={minute}
                        min={0}
                        max={59}
                        step={5}
                        onValueChange={(next) => updateTime(hour, Number(next || 0))}
                      />
                    </div>
                  </div>
                </div>,
                document.body,
              )
            : null}
        </div>
      </div>
    )
  },
)

DateTimePicker.displayName = 'DateTimePicker'
