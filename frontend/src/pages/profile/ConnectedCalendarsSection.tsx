import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Calendar, Plus, Trash2, ShieldCheck, RefreshCw, Star, Info, X } from 'lucide-react'
import { userIntegrationsApi, type CalendarItem, type CalendarCreateInput } from '@/api/userIntegrations'
import { Checkbox } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'

export function ConnectedCalendarsSection() {
  const { t } = useTranslation()
  const [calendars, setCalendars] = useState<CalendarItem[]>([])
  const [loading, setLoading] = useState(true)
  const [showAddModal, setShowAddModal] = useState(false)
  const [testingId, setTestingId] = useState<number | null>(null)

  // Form State
  const [name, setName] = useState('')
  const providerType = 'caldav'
  const [isDefault, setIsDefault] = useState(false)
  const [caldavUrl, setCaldavUrl] = useState('')
  const [caldavUsername, setCaldavUsername] = useState('')
  const [password, setPassword] = useState('')
  const [saving, setSaving] = useState(false)

  const loadCalendars = async () => {
    try {
      setLoading(true)
      const list = await userIntegrationsApi.getCalendars()
      setCalendars(list)
    } catch (err: any) {
      toast.error(err.message || 'Fehler beim Laden der Kalender')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadCalendars()
  }, [])

  const handleTest = async (id: number) => {
    setTestingId(id)
    try {
      const res = await userIntegrationsApi.testCalendar(id)
      if (res.ok) {
        toast.success(t('profile.calendars.testSuccess', 'Verbindungstest erfolgreich!'))
      } else {
        toast.error(t('profile.calendars.testFailed', { details: res.details, defaultValue: `Fehlgeschlagen: ${res.details}` }))
      }
    } catch (err: any) {
      toast.error(err.message || 'Verbindungstest fehlgeschlagen')
    } finally {
      setTestingId(null)
    }
  }

  const handleDelete = async (cal: CalendarItem) => {
    const ok = await confirm({
      message: t('profile.calendars.deleteConfirm', { name: cal.name, defaultValue: `Möchtest du den Kalender ${cal.name} wirklich entfernen?` }),
      danger: true,
      confirmText: t('profile.calendars.delete', 'Entfernen'),
    })
    if (!ok) return
    try {
      await userIntegrationsApi.deleteCalendar(cal.id)
      toast.success(t('profile.calendars.deleteSuccess', 'Kalender entfernt'))
      await loadCalendars()
    } catch (err: any) {
      toast.error(err.message || 'Fehler beim Löschen')
    }
  }

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name || !caldavUrl) {
      toast.error('Bitte Bezeichnung und CalDAV-URL angeben.')
      return
    }
    setSaving(true)
    try {
      const input: CalendarCreateInput = {
        name,
        provider_type: providerType,
        is_default: isDefault,
        caldav_url: caldavUrl.trim(),
        caldav_username: caldavUsername.trim() || undefined,
        password_or_token: password || undefined,
      }
      await userIntegrationsApi.createCalendar(input)
      toast.success(t('profile.calendars.saveSuccess', 'Kalender gespeichert'))
      setShowAddModal(false)
      setName('')
      setCaldavUrl('')
      setCaldavUsername('')
      setPassword('')
      await loadCalendars()
    } catch (err: any) {
      toast.error(err.message || 'Fehler beim Speichern')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="msm-card p-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Calendar className="h-5 w-5 text-secondary" aria-hidden="true" />
          <div>
            <h2 className="font-headline text-lg font-semibold text-on-surface">
              {t('profile.calendars.title', 'Verknüpfte Kalender (CalDAV)')}
            </h2>
            <p className="font-body-md text-sm text-on-surface-variant mt-0.5">
              {t('profile.calendars.subtitle', 'Kalender-Integration für KI-Terminprüfungen und Entwürfe von Terminen.')}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setShowAddModal(true)}
          className="msm-btn-primary px-3 py-1.5 text-xs inline-flex items-center gap-1.5"
        >
          <Plus className="w-4 h-4" />
          {t('profile.calendars.add', 'Kalender hinzufügen')}
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-20">
          <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        </div>
      ) : calendars.length === 0 ? (
        <p className="font-body-md text-sm text-on-surface-variant py-2">
          {t('profile.calendars.empty', 'Keine verknüpften Kalender vorhanden.')}
        </p>
      ) : (
        <ul className="divide-y divide-outline-variant/30">
          {calendars.map((cal) => (
            <li key={cal.id} className="py-3 first:pt-0 last:pb-0 flex items-center justify-between gap-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-label-md text-sm text-on-surface font-medium">{cal.name}</span>
                  {cal.is_default && (
                    <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary font-medium">
                      <Star className="w-3 h-3 fill-primary" />
                      Standard
                    </span>
                  )}
                  <span className="text-xs px-2 py-0.5 rounded bg-surface-variant text-on-surface-variant uppercase font-mono">
                    {cal.provider_type}
                  </span>
                </div>
                <p className="font-body-md text-xs text-on-surface-variant mt-0.5 truncate max-w-md">
                  {cal.caldav_url}
                  {cal.caldav_username && <span className="ml-2">({cal.caldav_username})</span>}
                </p>
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleTest(cal.id)}
                  disabled={testingId === cal.id}
                  className="msm-btn-secondary px-2.5 py-1 text-xs inline-flex items-center gap-1"
                >
                  {testingId === cal.id ? (
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <ShieldCheck className="w-3.5 h-3.5" />
                  )}
                  {t('profile.calendars.test', 'Testen')}
                </button>
                <button
                  type="button"
                  onClick={() => handleDelete(cal)}
                  className="msm-btn-danger px-2.5 py-1 text-xs inline-flex items-center gap-1"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  {t('profile.calendars.delete', 'Löschen')}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* Add Modal with createPortal */}
      {showAddModal &&
        typeof document !== 'undefined' &&
        createPortal(
          <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-sm overflow-y-auto"
            onClick={() => setShowAddModal(false)}
            role="dialog"
            aria-modal="true"
          >
            <div
              className="msm-card max-w-lg w-full p-6 shadow-2xl border border-outline max-h-[90vh] overflow-y-auto my-auto"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-headline text-lg font-semibold text-on-surface">
                  {t('profile.calendars.add', 'Kalender hinzufügen')}
                </h3>
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className="p-1 rounded text-on-surface-variant hover:text-on-surface hover:bg-surface-container"
                  aria-label="Schließen"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              <p className="font-body-md text-xs text-on-surface-variant mb-4">
                {t('profile.mailboxes.credentialsStoredEncrypted', 'Passwörter werden mit DIS AES-256-GCM verschlüsselt gespeichert und niemals im Klartext übertragen.')}
              </p>

              {/* Protocol explanation hint */}
              <div className="mb-4 p-3 rounded-lg bg-surface-container-high/60 border border-outline-variant/40 flex items-start gap-2.5">
                <Info className="w-4 h-4 text-primary shrink-0 mt-0.5" />
                <p className="font-body-md text-xs text-on-surface-variant">
                  {t('profile.calendars.protocolHelp', 'Wird zum Abfragen von Terminen und Vorbereiten von Termineinträgen durch den KI-Assistenten verwendet. Termine werden erst nach deiner ausdrücklichen Bestätigung erstellt oder geändert.')}
                </p>
              </div>

              <form onSubmit={handleCreate} className="space-y-4">
                <div>
                  <label className="block text-xs font-medium text-on-surface mb-1">
                    {t('profile.calendars.name', 'Bezeichnung (z. B. Team-Kalender)')}
                  </label>
                  <input
                    type="text"
                    required
                    placeholder="z. B. Mein Arbeitskalender / Nextcloud"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="msm-input w-full text-sm"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-on-surface mb-1">
                    {t('profile.calendars.caldavUrl', 'CalDAV-Server URL')}
                  </label>
                  <input
                    type="url"
                    required
                    placeholder="https://caldav.example.com/dav/calendars/user/work/"
                    value={caldavUrl}
                    onChange={(e) => setCaldavUrl(e.target.value)}
                    className="msm-input w-full text-sm font-mono"
                  />
                  <p className="text-[11px] text-on-surface-variant mt-1">
                    {t('profile.calendars.caldavHelp', 'Vollständige CalDAV-URL deines Kalenders (z. B. Nextcloud, Baïkal, mailbox.org oder Google CalDAV).')}
                  </p>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-on-surface mb-1">
                      {t('profile.calendars.caldavUsername', 'Benutzername')}
                    </label>
                    <input
                      type="text"
                      placeholder="user@example.com"
                      value={caldavUsername}
                      onChange={(e) => setCaldavUsername(e.target.value)}
                      className="msm-input w-full text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-on-surface mb-1">
                      {t('profile.calendars.password', 'Passwort / App-Passwort')}
                    </label>
                    <input
                      type="password"
                      placeholder="••••••••••••"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="msm-input w-full text-sm font-mono"
                    />
                  </div>
                </div>

                <div className="flex items-center gap-2.5 pt-2">
                  <label className="flex items-center gap-2.5 text-xs text-on-surface cursor-pointer select-none">
                    <Checkbox
                      checked={isDefault}
                      onCheckedChange={setIsDefault}
                    />
                    <span>{t('profile.calendars.isDefault', 'Als Standardkalender verwenden')}</span>
                  </label>
                </div>

                <div className="flex items-center justify-end gap-2 pt-4 border-t border-outline-variant/30">
                  <button
                    type="button"
                    onClick={() => setShowAddModal(false)}
                    className="msm-btn-secondary px-4 py-2 text-sm"
                    disabled={saving}
                  >
                    Abbrechen
                  </button>
                  <button
                    type="submit"
                    className="msm-btn-primary px-4 py-2 text-sm"
                    disabled={saving}
                  >
                    {saving ? 'Speichert...' : 'Kalender speichern'}
                  </button>
                </div>
              </form>
            </div>
          </div>,
          document.body
        )}
    </div>
  )
}

