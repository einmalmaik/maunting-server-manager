import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Mail, Plus, Trash2, RefreshCw, Star, ShieldCheck, HelpCircle, Info, X } from 'lucide-react'
import { userIntegrationsApi, type MailboxItem, type MailboxCreateInput } from '@/api/userIntegrations'
import { Checkbox, Dropdown, type DropdownOption } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'

export function ConnectedMailboxesSection() {
  const { t } = useTranslation()
  const [mailboxes, setMailboxes] = useState<MailboxItem[]>([])
  const [loading, setLoading] = useState(true)
  const [showAddModal, setShowAddModal] = useState(false)
  const [testingId, setTestingId] = useState<number | null>(null)

  // Form State
  const [preset, setPreset] = useState('custom')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [isDefault, setIsDefault] = useState(false)
  const [imapHost, setImapHost] = useState('')
  const [imapPort, setImapPort] = useState(993)
  const imapSsl = true
  const [smtpHost, setSmtpHost] = useState('')
  const [smtpPort, setSmtpPort] = useState(587)
  const smtpTls = true
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [syncEnabled, setSyncEnabled] = useState(true)
  const [saving, setSaving] = useState(false)

  const providerOptions: DropdownOption[] = [
    { value: 'custom', label: t('profile.mailboxes.templateCustom', 'Benutzerdefiniert (IMAP / SMTP)') },
    { value: 'gmail', label: t('profile.mailboxes.templateGmail', 'Google Mail / Gmail (App-Passwort)') },
    { value: 'outlook', label: t('profile.mailboxes.templateOutlook', 'Microsoft Outlook / Office 365') },
    { value: 'gmx', label: t('profile.mailboxes.templateGmx', 'GMX Mail') },
    { value: 'webde', label: t('profile.mailboxes.templateWebde', 'WEB.DE') },
    { value: 'icloud', label: t('profile.mailboxes.templateIcloud', 'Apple iCloud (App-spezifisch)') },
  ]

  const handlePresetChange = (value: string) => {
    setPreset(value)
    if (value === 'gmail') {
      setImapHost('imap.gmail.com')
      setImapPort(993)
      setSmtpHost('smtp.gmail.com')
      setSmtpPort(587)
      if (!name) setName('Gmail')
    } else if (value === 'outlook') {
      setImapHost('outlook.office365.com')
      setImapPort(993)
      setSmtpHost('smtp.office365.com')
      setSmtpPort(587)
      if (!name) setName('Outlook')
    } else if (value === 'gmx') {
      setImapHost('imap.gmx.net')
      setImapPort(993)
      setSmtpHost('mail.gmx.net')
      setSmtpPort(587)
      if (!name) setName('GMX')
    } else if (value === 'webde') {
      setImapHost('imap.web.de')
      setImapPort(993)
      setSmtpHost('smtp.web.de')
      setSmtpPort(587)
      if (!name) setName('WEB.DE')
    } else if (value === 'icloud') {
      setImapHost('imap.mail.me.com')
      setImapPort(993)
      setSmtpHost('smtp.mail.me.com')
      setSmtpPort(587)
      if (!name) setName('iCloud')
    }
  }

  const loadMailboxes = async () => {
    try {
      setLoading(true)
      const list = await userIntegrationsApi.getMailboxes()
      setMailboxes(list)
    } catch (err: any) {
      toast.error(err.message || 'Fehler beim Laden der Postfächer')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadMailboxes()
  }, [])

  const handleTest = async (id: number) => {
    setTestingId(id)
    try {
      const res = await userIntegrationsApi.testMailbox(id)
      if (res.ok) {
        toast.success(t('profile.mailboxes.testSuccess', 'Verbindungstest erfolgreich!'))
      } else {
        const fehlerText = res.message || res.details || 'Verbindung fehlgeschlagen'
        toast.error(t('profile.mailboxes.testFailed', { details: fehlerText, defaultValue: `Fehlgeschlagen: ${fehlerText}` }))
      }
    } catch (err: any) {
      toast.error(err.message || 'Verbindungstest fehlgeschlagen')
    } finally {
      setTestingId(null)
    }
  }

  const handleDelete = async (mb: MailboxItem) => {
    const ok = await confirm({
      message: t('profile.mailboxes.deleteConfirm', { email: mb.email, defaultValue: `Möchtest du das Postfach ${mb.email} wirklich entfernen?` }),
      danger: true,
      confirmText: t('profile.mailboxes.delete', 'Entfernen'),
    })
    if (!ok) return
    try {
      await userIntegrationsApi.deleteMailbox(mb.id)
      toast.success(t('profile.mailboxes.deleteSuccess', 'Postfach entfernt'))
      await loadMailboxes()
    } catch (err: any) {
      toast.error(err.message || 'Fehler beim Löschen')
    }
  }

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email || !name) {
      toast.error('Bitte E-Mail und Bezeichnung angeben.')
      return
    }
    if (!imapHost && !smtpHost) {
      toast.error('Bitte mindestens IMAP-Host (für Empfang) oder SMTP-Host (für Versand) konfigurieren.')
      return
    }

    setSaving(true)
    try {
      const input: MailboxCreateInput = {
        name,
        email,
        provider_type: preset === 'gmail' ? 'google' : preset === 'custom' ? 'custom' : preset,
        is_default: isDefault,
        imap_host: imapHost.trim() || undefined,
        imap_port: imapPort || undefined,
        imap_use_ssl: imapSsl,
        smtp_host: smtpHost.trim() || undefined,
        smtp_port: smtpPort || undefined,
        smtp_use_tls: smtpTls,
        imap_username: (username || email).trim() || undefined,
        smtp_username: (username || email).trim() || undefined,
        password_or_token: password || undefined,
        sync_enabled: syncEnabled,
      }
      await userIntegrationsApi.createMailbox(input)
      toast.success(t('profile.mailboxes.saveSuccess', 'Postfach gespeichert'))
      setShowAddModal(false)
      setName('')
      setEmail('')
      setPassword('')
      setUsername('')
      setImapHost('')
      setSmtpHost('')
      setPreset('custom')
      await loadMailboxes()
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
          <Mail className="h-5 w-5 text-secondary" aria-hidden="true" />
          <div>
            <h2 className="font-headline text-lg font-semibold text-on-surface">
              {t('profile.mailboxes.title', 'Verknüpfte Postfächer (E-Mail)')}
            </h2>
            <p className="font-body-md text-sm text-on-surface-variant mt-0.5">
              {t('profile.mailboxes.subtitle', 'E-Mail-Konten für KI-Assistenten zum sicheren Lesen und Vorbereiten von E-Mails.')}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setShowAddModal(true)}
          className="msm-btn-primary px-3 py-1.5 text-xs inline-flex items-center gap-1.5"
        >
          <Plus className="w-4 h-4" />
          {t('profile.mailboxes.add', 'Postfach hinzufügen')}
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-20">
          <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        </div>
      ) : mailboxes.length === 0 ? (
        <p className="font-body-md text-sm text-on-surface-variant py-2">
          {t('profile.mailboxes.empty', 'Keine verknüpften Postfächer vorhanden.')}
        </p>
      ) : (
        <ul className="divide-y divide-outline-variant/30">
          {mailboxes.map((mb) => (
            <li key={mb.id} className="py-3 first:pt-0 last:pb-0 flex items-center justify-between gap-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-label-md text-sm text-on-surface font-medium">{mb.name}</span>
                  {mb.is_default && (
                    <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary font-medium">
                      <Star className="w-3 h-3 fill-primary" />
                      Standard
                    </span>
                  )}
                  <span className="text-xs px-2 py-0.5 rounded bg-surface-variant text-on-surface-variant uppercase font-mono">
                    {mb.provider_type}
                  </span>
                </div>
                <p className="font-body-md text-xs text-on-surface-variant mt-0.5">
                  {mb.email}
                  {mb.smtp_host && <span className="ml-2">· SMTP: {mb.smtp_host}</span>}
                  {mb.imap_host && <span className="ml-2">· IMAP: {mb.imap_host}</span>}
                </p>
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleTest(mb.id)}
                  disabled={testingId === mb.id}
                  className="msm-btn-secondary px-2.5 py-1 text-xs inline-flex items-center gap-1"
                >
                  {testingId === mb.id ? (
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <ShieldCheck className="w-3.5 h-3.5" />
                  )}
                  {t('profile.mailboxes.test', 'Testen')}
                </button>
                <button
                  type="button"
                  onClick={() => handleDelete(mb)}
                  className="msm-btn-danger px-2.5 py-1 text-xs inline-flex items-center gap-1"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  {t('profile.mailboxes.delete', 'Löschen')}
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
                  {t('profile.mailboxes.add', 'Postfach hinzufügen')}
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
                  {t('profile.mailboxes.protocolHelp', 'Du kannst nur IMAP (nur Lesen), nur SMTP (nur Senden) oder beides zusammen eintragen. Mindestens ein Protokoll ist erforderlich.')}
                </p>
              </div>

              <form onSubmit={handleCreate} className="space-y-4">
                <div>
                  <label className="block text-xs font-medium text-on-surface mb-1">
                    {t('profile.mailboxes.template', 'Anbieter-Vorlage')}
                  </label>
                  <Dropdown
                    value={preset}
                    onChange={handlePresetChange}
                    options={providerOptions}
                  />
                  {preset === 'gmail' && (
                    <p className="text-[11px] text-tertiary mt-1.5 flex items-center gap-1">
                      <HelpCircle className="w-3 h-3 shrink-0" />
                      {t('profile.mailboxes.gmailNotice', 'Hinweis für Gmail: Google erfordert ein 16-stelliges App-Passwort, sofern 2-Faktor-Authentifizierung aktiv ist.')}
                    </p>
                  )}
                </div>

                <div>
                  <label className="block text-xs font-medium text-on-surface mb-1">
                    {t('profile.mailboxes.name', 'Bezeichnung (z. B. Arbeit / Privat)')}
                  </label>
                  <input
                    type="text"
                    required
                    placeholder="z. B. Mein Arbeitskonto"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="msm-input w-full text-sm"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-on-surface mb-1">
                    {t('profile.mailboxes.email', 'E-Mail-Adresse')}
                  </label>
                  <input
                    type="email"
                    required
                    placeholder="user@example.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="msm-input w-full text-sm"
                  />
                </div>

                {/* IMAP Group */}
                <div className="p-3 rounded-lg border border-outline-variant/30 bg-surface-container/30 space-y-2.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-on-surface flex items-center gap-1.5">
                      IMAP (Posteingang lesen & suchen)
                    </span>
                    <span
                      className="text-[11px] text-on-surface-variant cursor-help flex items-center gap-1 hover:text-primary transition-colors"
                      title={t('profile.mailboxes.imapHelp', 'Wird zum Suchen und Lesen von E-Mails durch den KI-Assistenten benötigt. Kann freigelassen werden, wenn du nur E-Mails versenden möchtest.')}
                    >
                      <HelpCircle className="w-3.5 h-3.5" />
                      Optional
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <div className="col-span-2">
                      <label className="block text-[11px] text-on-surface-variant mb-1">
                        {t('profile.mailboxes.imapHost', 'IMAP-Host')}
                      </label>
                      <input
                        type="text"
                        placeholder="imap.example.com"
                        value={imapHost}
                        onChange={(e) => setImapHost(e.target.value)}
                        className="msm-input w-full text-sm font-mono"
                      />
                    </div>
                    <div>
                      <label className="block text-[11px] text-on-surface-variant mb-1">
                        {t('profile.mailboxes.imapPort', 'Port')}
                      </label>
                      <input
                        type="number"
                        value={imapPort}
                        onChange={(e) => setImapPort(parseInt(e.target.value) || 993)}
                        className="msm-input w-full text-sm font-mono"
                      />
                    </div>
                  </div>
                </div>

                {/* SMTP Group */}
                <div className="p-3 rounded-lg border border-outline-variant/30 bg-surface-container/30 space-y-2.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-on-surface flex items-center gap-1.5">
                      SMTP (E-Mails vorbereiten & versenden)
                    </span>
                    <span
                      className="text-[11px] text-on-surface-variant cursor-help flex items-center gap-1 hover:text-primary transition-colors"
                      title={t('profile.mailboxes.smtpHelp', 'Wird zum Vorbereiten und Absenden von E-Mails durch den KI-Assistenten benötigt. Kann freigelassen werden, wenn du nur E-Mails lesen möchtest.')}
                    >
                      <HelpCircle className="w-3.5 h-3.5" />
                      Optional
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <div className="col-span-2">
                      <label className="block text-[11px] text-on-surface-variant mb-1">
                        {t('profile.mailboxes.smtpHost', 'SMTP-Host')}
                      </label>
                      <input
                        type="text"
                        placeholder="smtp.example.com"
                        value={smtpHost}
                        onChange={(e) => setSmtpHost(e.target.value)}
                        className="msm-input w-full text-sm font-mono"
                      />
                    </div>
                    <div>
                      <label className="block text-[11px] text-on-surface-variant mb-1">
                        {t('profile.mailboxes.smtpPort', 'Port')}
                      </label>
                      <input
                        type="number"
                        value={smtpPort}
                        onChange={(e) => setSmtpPort(parseInt(e.target.value) || 587)}
                        className="msm-input w-full text-sm font-mono"
                      />
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-on-surface mb-1">
                      {t('profile.mailboxes.username', 'Benutzername (falls abweichend)')}
                    </label>
                    <input
                      type="text"
                      placeholder={email || 'user@example.com'}
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      className="msm-input w-full text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-on-surface mb-1">
                      {t('profile.mailboxes.password', 'Passwort / App-Passwort')}
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

                <div className="flex flex-col gap-2.5 pt-2">
                  <label className="flex items-center gap-2.5 text-xs text-on-surface cursor-pointer select-none">
                    <Checkbox
                      checked={isDefault}
                      onCheckedChange={setIsDefault}
                    />
                    <span>{t('profile.mailboxes.isDefault', 'Als Standardpostfach verwenden')}</span>
                  </label>
                  <label className="flex items-center gap-2.5 text-xs text-on-surface cursor-pointer select-none">
                    <Checkbox
                      checked={syncEnabled}
                      onCheckedChange={setSyncEnabled}
                    />
                    <span>{t('profile.mailboxes.syncEnabled', 'Automatische Benachrichtigung bei neuen E-Mails')}</span>
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
                    {saving ? 'Speichert...' : 'Postfach speichern'}
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

