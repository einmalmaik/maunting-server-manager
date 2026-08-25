import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Mail, Plus, Trash2, RefreshCw, Star, ShieldCheck } from 'lucide-react'
import { userIntegrationsApi, type MailboxItem, type MailboxCreateInput } from '@/api/userIntegrations'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'

export function ConnectedMailboxesSection() {
  const { t } = useTranslation()
  const [mailboxes, setMailboxes] = useState<MailboxItem[]>([])
  const [loading, setLoading] = useState(true)
  const [showAddModal, setShowAddModal] = useState(false)
  const [testingId, setTestingId] = useState<number | null>(null)

  // Form State
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const providerType = 'custom'
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
        toast.error(t('profile.mailboxes.testFailed', { details: res.details, defaultValue: `Fehlgeschlagen: ${res.details}` }))
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
    setSaving(true)
    try {
      const input: MailboxCreateInput = {
        name,
        email,
        provider_type: providerType,
        is_default: isDefault,
        imap_host: imapHost || undefined,
        imap_port: imapPort || undefined,
        imap_use_ssl: imapSsl,
        smtp_host: smtpHost || undefined,
        smtp_port: smtpPort || undefined,
        smtp_use_tls: smtpTls,
        imap_username: username || undefined,
        smtp_username: username || undefined,
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
      await loadMailboxes()
    } catch (err: any) {
      toast.error(err.message || 'Fehler beim Speichern')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="msm-card p-6 mt-6">
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

      {/* Add Modal */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-sm">
          <div className="msm-card max-w-lg w-full p-6 shadow-xl border border-outline">
            <h3 className="font-headline text-lg font-semibold text-on-surface mb-2">
              {t('profile.mailboxes.add', 'Postfach hinzufügen')}
            </h3>
            <p className="font-body-md text-xs text-on-surface-variant mb-4">
              {t('profile.mailboxes.credentialsStoredEncrypted', 'Passwörter werden mit DIS AES-256-GCM verschlüsselt gespeichert und niemals im Klartext übertragen.')}
            </p>

            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-on-surface mb-1">
                  {t('profile.mailboxes.name', 'Bezeichnung')}
                </label>
                <input
                  type="text"
                  required
                  placeholder="z. B. Arbeits-Mail / Gmail"
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

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-on-surface mb-1">
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
                  <label className="block text-xs font-medium text-on-surface mb-1">
                    {t('profile.mailboxes.imapPort', 'IMAP-Port')}
                  </label>
                  <input
                    type="number"
                    value={imapPort}
                    onChange={(e) => setImapPort(parseInt(e.target.value) || 993)}
                    className="msm-input w-full text-sm font-mono"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-on-surface mb-1">
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
                  <label className="block text-xs font-medium text-on-surface mb-1">
                    {t('profile.mailboxes.smtpPort', 'SMTP-Port')}
                  </label>
                  <input
                    type="number"
                    value={smtpPort}
                    onChange={(e) => setSmtpPort(parseInt(e.target.value) || 587)}
                    className="msm-input w-full text-sm font-mono"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-on-surface mb-1">
                    {t('profile.mailboxes.username', 'Benutzername')}
                  </label>
                  <input
                    type="text"
                    placeholder="user@example.com"
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

              <div className="flex items-center gap-4 pt-2">
                <label className="flex items-center gap-2 text-xs text-on-surface cursor-pointer">
                  <input
                    type="checkbox"
                    checked={isDefault}
                    onChange={(e) => setIsDefault(e.target.checked)}
                    className="rounded border-outline text-primary focus:ring-primary"
                  />
                  {t('profile.mailboxes.isDefault', 'Als Standardpostfach festlegen')}
                </label>
                <label className="flex items-center gap-2 text-xs text-on-surface cursor-pointer">
                  <input
                    type="checkbox"
                    checked={syncEnabled}
                    onChange={(e) => setSyncEnabled(e.target.checked)}
                    className="rounded border-outline text-primary focus:ring-primary"
                  />
                  {t('profile.mailboxes.syncEnabled', 'Sync / Benachrichtigungen')}
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
        </div>
      )}
    </div>
  )
}
