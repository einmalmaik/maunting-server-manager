import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Bell, Bot, Mail, Smartphone } from 'lucide-react'
import { useAuthStore } from '@/stores/authStore'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { Switch } from '@/components/ui/Switch'

interface BenachrichtigungsGlockeProps {
  className?: string
  align?: 'left' | 'right' | 'sidebar'
  placement?: 'top' | 'bottom'
}

export function BenachrichtigungsGlocke({ className = '', align = 'sidebar', placement = 'top' }: BenachrichtigungsGlockeProps) {
  const { t } = useTranslation()
  const { user, updateUser } = useAuthStore()

  const [notificationsEnabled, setNotificationsEnabled] = useState<boolean>(user?.email_notifications ?? true)
  const [aiNotificationsEnabled, setAiNotificationsEnabled] = useState<boolean>(user?.ai_notifications ?? true)
  const [deviceNotificationsEnabled, setDeviceNotificationsEnabled] = useState<boolean>(user?.device_notifications ?? true)
  const [bellOpen, setBellOpen] = useState(false)
  const bellRef = useRef<HTMLDivElement>(null)

  const irgendwasAn = notificationsEnabled || aiNotificationsEnabled || deviceNotificationsEnabled

  useEffect(() => {
    if (user) {
      setNotificationsEnabled(user.email_notifications)
      setAiNotificationsEnabled(user.ai_notifications !== false)
      setDeviceNotificationsEnabled(user.device_notifications !== false)
    }
  }, [user?.email_notifications, user?.ai_notifications, user?.device_notifications])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (bellRef.current && !bellRef.current.contains(e.target as Node)) {
        setBellOpen(false)
      }
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setBellOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [])

  const schalte = async (feld: 'email' | 'ai' | 'device', naechster: boolean) => {
    if (!user) return
    const vorher =
      feld === 'email'
        ? notificationsEnabled
        : feld === 'ai'
        ? aiNotificationsEnabled
        : deviceNotificationsEnabled

    if (feld === 'email') setNotificationsEnabled(naechster)
    else if (feld === 'ai') setAiNotificationsEnabled(naechster)
    else setDeviceNotificationsEnabled(naechster)

    try {
      const param = feld === 'email' ? 'enabled' : feld === 'ai' ? 'ai' : 'device'
      await api(`/auth/me/notifications?${param}=${naechster}`, { method: 'PATCH' })
      updateUser(
        feld === 'email'
          ? { email_notifications: naechster }
          : feld === 'ai'
          ? { ai_notifications: naechster }
          : { device_notifications: naechster },
      )
    } catch {
      if (feld === 'email') setNotificationsEnabled(vorher)
      else if (feld === 'ai') setAiNotificationsEnabled(vorher)
      else setDeviceNotificationsEnabled(vorher)
      toast.error(t('notifications.updateFailed', 'Einstellung konnte nicht gespeichert werden.'))
    }
  }

  const alignClass =
    align === 'sidebar'
      ? 'right-0 lg:left-0 lg:right-auto'
      : align === 'left'
      ? 'left-0'
      : 'right-0'
  const placementClass = placement === 'top' ? 'bottom-full mb-2' : 'top-full mt-2'

  return (
    <div className={`relative ${className}`} ref={bellRef}>
      <button
        onClick={() => setBellOpen((offen) => !offen)}
        aria-expanded={bellOpen}
        aria-haspopup="menu"
        title={irgendwasAn ? t('notifications.activeLabel', 'Benachrichtigungen aktiv') : t('notifications.inactiveLabel', 'Benachrichtigungen stummgeschaltet')}
        aria-label={irgendwasAn ? t('notifications.activeLabel', 'Benachrichtigungen aktiv') : t('notifications.inactiveLabel', 'Benachrichtigungen stummgeschaltet')}
        className="p-2 rounded-full transition-colors active:scale-95 relative hover:bg-surface-variant/50 text-on-surface-variant hover:text-primary"
      >
        <div className="relative inline-flex">
          <Bell className="w-[18px] h-[18px]" />
          <span
            className={`absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full border-2 border-background ${
              irgendwasAn ? 'bg-status-success' : 'bg-status-destructive'
            }`}
            aria-hidden="true"
          />
        </div>
      </button>

      {bellOpen && (
        <div
          role="menu"
          className={`absolute ${alignClass} ${placementClass} w-72 sm:w-80 max-w-[calc(100vw-2rem)] bg-surface-container-high border border-outline-variant rounded-lg shadow-xl z-50 overflow-hidden`}
        >
          <div className="p-3 border-b border-outline-variant/30">
            <p className="font-label-md text-sm text-on-surface font-medium">
              {t('notifications.title', 'Benachrichtigungen')}
            </p>
          </div>
          <label className="flex items-start gap-3 px-3 py-2.5 hover:bg-surface-container-highest transition-colors cursor-pointer">
            <Mail className="mt-0.5 h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
            <span className="min-w-0 flex-1">
              <span className="block text-sm text-on-surface">
                {t('notifications.emailLabel', 'E-Mail-Benachrichtigungen')}
              </span>
              <span className="block text-xs text-on-surface-variant">
                {t('notifications.emailHint', 'Anmeldungen, Server-Ereignisse, Updates.')}
              </span>
            </span>
            <Switch
              checked={notificationsEnabled}
              onCheckedChange={(wert) => void schalte('email', wert)}
              aria-label={t('notifications.emailLabel', 'E-Mail-Benachrichtigungen')}
            />
          </label>
          <label className="flex items-start gap-3 px-3 py-2.5 hover:bg-surface-container-highest transition-colors cursor-pointer border-t border-outline-variant/20">
            <Bot className="mt-0.5 h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
            <span className="min-w-0 flex-1">
              <span className="block text-sm text-on-surface">
                {t('notifications.aiLabel', 'KI-Meldungen im Panel')}
              </span>
              <span className="block text-xs text-on-surface-variant">
                {t('notifications.aiHint', 'Hinweis, wenn ein Auftrag fertig ist oder wartet. Keine E-Mails.')}
              </span>
            </span>
            <Switch
              checked={aiNotificationsEnabled}
              onCheckedChange={(wert) => void schalte('ai', wert)}
              aria-label={t('notifications.aiLabel', 'KI-Meldungen im Panel')}
            />
          </label>
          <label className="flex items-start gap-3 px-3 py-2.5 hover:bg-surface-container-highest transition-colors cursor-pointer border-t border-outline-variant/20">
            <Smartphone className="mt-0.5 h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
            <span className="min-w-0 flex-1">
              <span className="block text-sm text-on-surface">
                {t('notifications.deviceLabel', 'Geräte-Benachrichtigungen')}
              </span>
              <span className="block text-xs text-on-surface-variant">
                {t('notifications.deviceHint', 'Pop-up-Meldungen auf Windows- und Android-Geräten bei Server-Vorfällen, Terminen und KI-Aufträgen.')}
              </span>
            </span>
            <Switch
              checked={deviceNotificationsEnabled}
              onCheckedChange={(wert) => void schalte('device', wert)}
              aria-label={t('notifications.deviceLabel', 'Geräte-Benachrichtigungen')}
            />
          </label>
        </div>
      )}
    </div>
  )
}
