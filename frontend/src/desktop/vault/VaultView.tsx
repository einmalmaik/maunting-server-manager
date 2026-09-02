import React, { useState, useEffect, useMemo } from 'react'
import {
  Check,
  Clock,
  Cloud,
  CloudOff,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  Lock,
  Plus,
  QrCode,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  Unlock,
  Zap,
} from 'lucide-react'
import { Button } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { getBrandIcon } from './brandCatalog'
import { generateTotpCode, getTotpSecondsRemaining, parseOtpauthUri } from './totpEngine'
import { generateSecurePassword } from './vaultCrypto'
import { useVaultStore, type VaultItem } from './vaultStore'

export function VaultView() {
  const {
    isUnlocked,
    isUnlocking,
    unlockError,
    items,
    selectedItemId,
    searchQuery,
    syncStatus,
    lastSyncTime,
    unlock,
    lock,
    setSearchQuery,
    setSelectedItemId,
    createQuickPasswordEntry,
    saveItem,
    deleteItem,
    syncWithServer,
  } = useVaultStore()

  const [masterPasswordInput, setMasterPasswordInput] = useState('')
  const [selectedItem, setSelectedItem] = useState<VaultItem | null>(null)
  const [showPassword, setShowPassword] = useState(false)
  const [totpCode, setTotpCode] = useState<string>('')
  const [totpRemaining, setTotpRemaining] = useState<number>(30)
  const [copiedField, setCopiedField] = useState<string | null>(null)
  const [showQrModal, setShowQrModal] = useState(false)
  const [qrRawInput, setQrRawInput] = useState('')

  // Formular-State für den ausgewählten Eintrag
  const [formService, setFormService] = useState('')
  const [formUsername, setFormUsername] = useState('')
  const [formPassword, setFormPassword] = useState('')
  const [formUrl, setFormUrl] = useState('')
  const [formNotes, setFormNotes] = useState('')
  const [formTotpSecret, setFormTotpSecret] = useState('')

  // Synchronisiere Formularfelder mit dem ausgewählten Eintrag
  useEffect(() => {
    const active = items.find((i) => i.id === selectedItemId) || null
    setSelectedItem(active)
    if (active) {
      setFormService(active.service)
      setFormUsername(active.username)
      setFormPassword(active.password)
      setFormUrl(active.url || '')
      setFormNotes(active.notes || '')
      setFormTotpSecret(active.totpSecret || '')
      setShowPassword(false)
    } else {
      setFormService('')
      setFormUsername('')
      setFormPassword('')
      setFormUrl('')
      setFormNotes('')
      setFormTotpSecret('')
    }
  }, [selectedItemId, items])

  // Live TOTP Takt (Aktualisierung jede Sekunde)
  useEffect(() => {
    if (!formTotpSecret) {
      setTotpCode('')
      return
    }

    let isMounted = true
    const updateTotp = async () => {
      try {
        const code = await generateTotpCode(formTotpSecret)
        if (isMounted) {
          setTotpCode(code)
          setTotpRemaining(getTotpSecondsRemaining(30))
        }
      } catch {
        if (isMounted) setTotpCode('')
      }
    }

    void updateTotp()
    const interval = setInterval(() => {
      void updateTotp()
    }, 1000)

    return () => {
      isMounted = false
      clearInterval(interval)
    }
  }, [formTotpSecret])

  // Filterung nach Suchbegriff
  const filteredItems = useMemo(() => {
    if (!searchQuery.trim()) return items
    const q = searchQuery.toLowerCase()
    return items.filter(
      (item) =>
        item.service.toLowerCase().includes(q) ||
        item.username.toLowerCase().includes(q) ||
        (item.url && item.url.toLowerCase().includes(q)),
    )
  }, [items, searchQuery])

  const handleCopy = (text: string, fieldName: string) => {
    if (!text) return
    void navigator.clipboard.writeText(text)
    setCopiedField(fieldName)
    toast.success('In die Zwischenablage kopiert')
    setTimeout(() => setCopiedField(null), 2000)
  }

  const handleQuickCreate = async () => {
    try {
      const item = await createQuickPasswordEntry('Neuer Dienst')
      toast.success('Neuer Eintrag mit starkem Passwort erstellt (<= 2 Klicks)')
      setSelectedItemId(item.id)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Erstellung fehlgeschlagen')
    }
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!formService.trim()) {
      toast.error('Bitte einen Namen für den Dienst angeben')
      return
    }

    try {
      await saveItem({
        id: selectedItem?.id,
        service: formService.trim(),
        username: formUsername.trim(),
        password: formPassword,
        url: formUrl.trim() || undefined,
        notes: formNotes.trim() || undefined,
        totpSecret: formTotpSecret.trim() || undefined,
      })
      toast.success('Eintrag verschlüsselt gespeichert')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Speichern fehlgeschlagen')
    }
  }

  const handleDelete = async () => {
    if (!selectedItem) return
    try {
      await deleteItem(selectedItem.id)
      toast.success('Eintrag gelöscht')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Löschen fehlgeschlagen')
    }
  }

  const handleApplyOtpUri = () => {
    const parsed = parseOtpauthUri(qrRawInput)
    if (parsed) {
      setFormTotpSecret(parsed.secret)
      if (parsed.issuer && !formService) {
        setFormService(parsed.issuer)
      }
      if (parsed.label && !formUsername) {
        setFormUsername(parsed.label)
      }
      setShowQrModal(false)
      setQrRawInput('')
      toast.success('Authenticator-Schlüssel erfolgreich übernommen')
    } else {
      toast.error('Ungültiger otpauth-URI oder Base32-Schlüssel')
    }
  }

  // ── GESPERRTER ZUSTAND ──
  if (!isUnlocked) {
    return (
      <div className="flex h-full w-full items-center justify-center p-4">
        <div className="msm-card w-full max-w-md p-6 sm:p-8 space-y-6">
          <div className="text-center space-y-2">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary border border-primary/20">
              <ShieldCheck className="h-7 w-7" />
            </div>
            <h2 className="text-title-lg font-headline font-bold text-on-surface">
              Singra Vault Tresor
            </h2>
            <p className="text-xs text-on-surface-variant max-w-xs mx-auto">
              Zero-Knowledge Passwort-Manager & 2FA Authenticator. Geschützt mit clientseitiger DIS AES-GCM Verschlüsselung.
            </p>
          </div>

          <form
            onSubmit={async (e) => {
              e.preventDefault()
              if (!masterPasswordInput) return
              const ok = await unlock(masterPasswordInput)
              if (ok) {
                setMasterPasswordInput('')
              }
            }}
            className="space-y-4"
          >
            <div>
              <label className="block text-xs font-medium text-on-surface-variant mb-1">
                Master-Passwort
              </label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={masterPasswordInput}
                  onChange={(e) => setMasterPasswordInput(e.target.value)}
                  placeholder="Master-Passwort eingeben..."
                  className="msm-input w-full pr-10"
                  autoFocus
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface transition-colors"
                  aria-label="Passwort anzeigen"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            {unlockError && (
              <div className="rounded-xl border border-status-error/40 bg-status-error/10 p-3 text-xs text-status-error">
                {unlockError}
              </div>
            )}

            <Button
              type="submit"
              size="md"
              className="w-full flex items-center justify-center gap-2"
              disabled={isUnlocking || !masterPasswordInput}
            >
              {isUnlocking ? (
                <RefreshCw className="h-4 w-4 animate-spin" />
              ) : (
                <Unlock className="h-4 w-4" />
              )}
              {isUnlocking ? 'Wird entschlüsselt...' : 'Tresor entsperren'}
            </Button>
          </form>

          <div className="rounded-xl border border-outline-variant/30 bg-surface-container-low p-3 text-[11px] text-on-surface-variant space-y-1">
            <div className="font-semibold text-on-surface flex items-center gap-1.5">
              <Lock className="h-3.5 w-3.5 text-primary" />
              <span>Höchste Sicherheitsstufe</span>
            </div>
            <p>
              In der Datenbank liegen ausschließlich verschlüsselte Blobs ohne Metadaten. Keine KI hat Zugriff auf diesen Bereich.
            </p>
          </div>
        </div>
      </div>
    )
  }

  // ── ENTSPERRTER ZUSTAND ──
  const ActiveBrandIcon = selectedItem
    ? getBrandIcon(formService || selectedItem.service, formUrl || selectedItem.url)
    : KeyRound

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-background">
      {/* OBERE KOPFLEISTE */}
      <div className="flex items-center justify-between border-b border-outline-variant/30 px-4 py-3 bg-surface-container-lowest">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary border border-primary/20">
            <KeyRound className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-on-surface flex items-center gap-2">
              <span>Tresor & Authenticator</span>
              <span className="text-[11px] font-normal text-on-surface-variant">
                ({items.length} {items.length === 1 ? 'Eintrag' : 'Einträge'})
              </span>
            </h2>
            <div className="flex items-center gap-2 text-[11px] text-on-surface-variant">
              {syncStatus === 'synced' && (
                <span className="flex items-center gap-1 text-status-success">
                  <Cloud className="h-3 w-3" />
                  <span>Synchronisiert</span>
                </span>
              )}
              {syncStatus === 'syncing' && (
                <span className="flex items-center gap-1 text-primary animate-pulse">
                  <RefreshCw className="h-3 w-3 animate-spin" />
                  <span>Wird synchronisiert...</span>
                </span>
              )}
              {syncStatus === 'offline' && (
                <span className="flex items-center gap-1 text-status-warning">
                  <CloudOff className="h-3 w-3" />
                  <span>Offline-Modus</span>
                </span>
              )}
              {lastSyncTime && (
                <span>• Zuletzt: {new Date(lastSyncTime).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void syncWithServer()}
            title="Jetzt synchronisieren"
            className="hidden sm:flex items-center gap-1.5"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${syncStatus === 'syncing' ? 'animate-spin' : ''}`} />
            <span>Sync</span>
          </Button>

          {/* SCHNELLE PASSSWORTERSTELLUNG (<= 2 KLICKS) */}
          <Button
            size="sm"
            onClick={() => void handleQuickCreate()}
            className="flex items-center gap-1.5 bg-primary text-on-primary hover:bg-primary-hover shadow-sm"
          >
            <Zap className="h-3.5 w-3.5" />
            <span>Neues Passwort</span>
          </Button>

          <Button
            size="sm"
            variant="ghost"
            onClick={lock}
            title="Tresor sperren"
            className="text-on-surface-variant hover:text-status-error"
          >
            <Lock className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* HAUPTBEREICH (2 SPALTEN) */}
      <div className="grid grid-cols-1 md:grid-cols-12 flex-1 min-h-0 overflow-hidden">
        {/* LINKE SPALTE: LISTE & SUCHE */}
        <div className="md:col-span-5 lg:col-span-4 flex flex-col border-r border-outline-variant/30 bg-surface-container-low min-h-0">
          <div className="p-3 border-b border-outline-variant/20">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-on-surface-variant" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Dienst oder Benutzer suchen..."
                className="msm-input w-full pl-9 text-xs py-1.5"
              />
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
            {filteredItems.length === 0 ? (
              <div className="p-6 text-center text-xs text-on-surface-variant">
                Keine Einträge gefunden.
              </div>
            ) : (
              filteredItems.map((item) => {
                const ItemBrand = getBrandIcon(item.service, item.url)
                const isSelected = item.id === selectedItemId

                return (
                  <div
                    key={item.id}
                    onClick={() => setSelectedItemId(item.id)}
                    className={`group relative flex items-center justify-between p-2.5 rounded-xl border transition-all cursor-pointer ${
                      isSelected
                        ? 'border-primary/50 bg-primary/10 shadow-sm'
                        : 'border-outline-variant/20 bg-surface-container hover:bg-surface-container-high'
                    }`}
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-lg bg-surface border border-outline-variant/20 p-1">
                        <ItemBrand className="w-5 h-5" />
                      </div>
                      <div className="min-w-0">
                        <h4 className="text-xs font-semibold text-on-surface truncate">
                          {item.service}
                        </h4>
                        <p className="text-[11px] text-on-surface-variant truncate">
                          {item.username || 'Kein Benutzername'}
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center gap-1 opacity-80 group-hover:opacity-100">
                      {item.totpSecret && (
                        <span className="rounded-md bg-emerald-500/10 text-emerald-500 border border-emerald-500/20 px-1.5 py-0.5 text-[10px] font-mono">
                          2FA
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleCopy(item.password, `list-${item.id}`)
                        }}
                        className="p-1.5 rounded-lg text-on-surface-variant hover:text-primary hover:bg-primary/10 transition-colors"
                        title="Passwort kopieren"
                      >
                        {copiedField === `list-${item.id}` ? (
                          <Check className="h-3.5 w-3.5 text-status-success" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )}
                      </button>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </div>

        {/* RECHTE SPALTE: DETAIL & BEARBEITEN */}
        <div className="md:col-span-7 lg:col-span-8 flex flex-col min-h-0 bg-surface-container-lowest overflow-y-auto p-4 md:p-6">
          {selectedItem ? (
            <form onSubmit={handleSave} className="space-y-6 max-w-xl mx-auto w-full">
              {/* BRAND & TITEL */}
              <div className="flex items-center gap-4 border-b border-outline-variant/30 pb-4">
                <div className="w-12 h-12 flex items-center justify-center rounded-2xl bg-surface-container border border-outline-variant/30 p-2 shadow-sm">
                  <ActiveBrandIcon className="w-7 h-7" />
                </div>
                <div className="flex-1 min-w-0">
                  <input
                    type="text"
                    value={formService}
                    onChange={(e) => setFormService(e.target.value)}
                    placeholder="Name des Dienstes (z. B. Gmail, Discord)"
                    className="font-headline text-title-md font-bold text-on-surface bg-transparent border-b border-transparent hover:border-outline-variant focus:border-primary outline-none transition-colors w-full"
                    required
                  />
                  <p className="text-xs text-on-surface-variant mt-0.5">
                    Modulare Markenerkennung via lokalen SVGs
                  </p>
                </div>
              </div>

              {/* BENUTZERNAME / E-MAIL */}
              <div className="space-y-1">
                <label className="block text-xs font-medium text-on-surface-variant">
                  Benutzername / E-Mail
                </label>
                <div className="relative flex items-center">
                  <input
                    type="text"
                    value={formUsername}
                    onChange={(e) => setFormUsername(e.target.value)}
                    placeholder="name@domain.de"
                    className="msm-input w-full pr-10 text-xs"
                  />
                  <button
                    type="button"
                    onClick={() => handleCopy(formUsername, 'username')}
                    className="absolute right-2 p-1.5 rounded-lg text-on-surface-variant hover:text-primary transition-colors"
                    title="Benutzername kopieren"
                  >
                    {copiedField === 'username' ? <Check className="h-3.5 w-3.5 text-status-success" /> : <Copy className="h-3.5 w-3.5" />}
                  </button>
                </div>
              </div>

              {/* PASSWORT MIT SOFORTGENERATOR */}
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <label className="block text-xs font-medium text-on-surface-variant">
                    Passwort
                  </label>
                  <button
                    type="button"
                    onClick={() => {
                      const newPw = generateSecurePassword(20, true)
                      setFormPassword(newPw)
                      toast.success('Neues starkes Passwort generiert')
                    }}
                    className="text-xs text-primary hover:underline flex items-center gap-1 font-medium"
                  >
                    <RefreshCw className="h-3 w-3" />
                    <span>Neu generieren</span>
                  </button>
                </div>
                <div className="relative flex items-center">
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={formPassword}
                    onChange={(e) => setFormPassword(e.target.value)}
                    placeholder="Sicheres Passwort..."
                    className="msm-input w-full pr-20 text-xs font-mono"
                    required
                  />
                  <div className="absolute right-2 flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="p-1.5 rounded-lg text-on-surface-variant hover:text-on-surface transition-colors"
                      title={showPassword ? 'Verbergen' : 'Anzeigen'}
                    >
                      {showPassword ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleCopy(formPassword, 'password')}
                      className="p-1.5 rounded-lg text-on-surface-variant hover:text-primary transition-colors"
                      title="Passwort kopieren"
                    >
                      {copiedField === 'password' ? <Check className="h-3.5 w-3.5 text-status-success" /> : <Copy className="h-3.5 w-3.5" />}
                    </button>
                  </div>
                </div>
              </div>

              {/* TOTP AUTHENTICATOR (2FA) */}
              <div className="rounded-2xl border border-outline-variant/40 bg-surface-container p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Clock className="h-4 w-4 text-emerald-500" />
                    <span className="text-xs font-semibold text-on-surface">
                      Authenticator (TOTP / 2FA)
                    </span>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    type="button"
                    onClick={() => setShowQrModal(true)}
                    className="text-xs flex items-center gap-1 text-primary"
                  >
                    <QrCode className="h-3.5 w-3.5" />
                    <span>Code / URI einfügen</span>
                  </Button>
                </div>

                {totpCode ? (
                  <div className="flex items-center justify-between rounded-xl bg-surface-container-low p-3 border border-outline-variant/30">
                    <div>
                      <div className="text-[10px] text-on-surface-variant font-medium uppercase tracking-wider">
                        Aktueller Einmal-Code
                      </div>
                      <div className="text-2xl font-mono font-bold text-on-surface tracking-widest mt-0.5">
                        {totpCode.slice(0, 3)} {totpCode.slice(3)}
                      </div>
                    </div>

                    <div className="flex items-center gap-3">
                      {/* Countdown Ring */}
                      <div className="relative flex items-center justify-center w-8 h-8">
                        <svg className="w-8 h-8 transform -rotate-90" viewBox="0 0 36 36">
                          <path
                            className="text-surface-container-high"
                            strokeWidth="3"
                            stroke="currentColor"
                            fill="none"
                            d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                          />
                          <path
                            className={`${totpRemaining <= 5 ? 'text-status-error' : 'text-emerald-500'} transition-all duration-1000 ease-linear`}
                            strokeDasharray={`${(totpRemaining / 30) * 100}, 100`}
                            strokeWidth="3"
                            strokeLinecap="round"
                            stroke="currentColor"
                            fill="none"
                            d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                          />
                        </svg>
                        <span className="absolute text-[10px] font-mono font-bold text-on-surface">
                          {totpRemaining}
                        </span>
                      </div>

                      <button
                        type="button"
                        onClick={() => handleCopy(totpCode, 'totp')}
                        className="p-2 rounded-xl bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20 transition-colors"
                        title="Code kopieren"
                      >
                        {copiedField === 'totp' ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div>
                    <input
                      type="text"
                      value={formTotpSecret}
                      onChange={(e) => setFormTotpSecret(e.target.value.toUpperCase())}
                      placeholder="Base32 Secret eingeben (z. B. JBSWY3DPEHPK3PXP)"
                      className="msm-input w-full text-xs font-mono"
                    />
                  </div>
                )}
              </div>

              {/* WEBSITE / URL */}
              <div className="space-y-1">
                <label className="block text-xs font-medium text-on-surface-variant">
                  Webadresse / URL
                </label>
                <input
                  type="text"
                  value={formUrl}
                  onChange={(e) => setFormUrl(e.target.value)}
                  placeholder="https://example.com"
                  className="msm-input w-full text-xs"
                />
              </div>

              {/* NOTIZEN */}
              <div className="space-y-1">
                <label className="block text-xs font-medium text-on-surface-variant">
                  Sichere Notizen
                </label>
                <textarea
                  rows={3}
                  value={formNotes}
                  onChange={(e) => setFormNotes(e.target.value)}
                  placeholder="Zusätzliche verschlüsselte Anmerkungen..."
                  className="msm-input w-full text-xs"
                />
              </div>

              {/* AKTIONEN */}
              <div className="flex items-center justify-between pt-4 border-t border-outline-variant/30">
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  onClick={() => void handleDelete()}
                  className="flex items-center gap-1.5"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  <span>Eintrag löschen</span>
                </Button>

                <Button type="submit" size="sm" className="flex items-center gap-1.5">
                  <Check className="h-3.5 w-3.5" />
                  <span>Änderungen speichern</span>
                </Button>
              </div>
            </form>
          ) : (
            <div className="flex h-full flex-col items-center justify-center text-center p-8 text-on-surface-variant space-y-3">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-surface-container border border-outline-variant/30">
                <KeyRound className="h-6 w-6 text-on-surface-variant" />
              </div>
              <p className="text-xs max-w-xs">
                Wähle links einen Eintrag aus oder erstelle mit einem Klick ein neues Passwort.
              </p>
              <Button size="sm" onClick={() => void handleQuickCreate()} className="flex items-center gap-1.5">
                <Plus className="h-3.5 w-3.5" />
                <span>Neues Passwort anlegen</span>
              </Button>
            </div>
          )}
        </div>
      </div>

      {/* MODAL: QR / TOTP URI EINGEBEN */}
      {showQrModal && (
        <div className="msm-modal-overlay" role="dialog" aria-modal="true">
          <div className="msm-card w-full max-w-md p-5 space-y-4">
            <h3 className="text-sm font-semibold text-on-surface flex items-center gap-2">
              <QrCode className="h-4 w-4 text-primary" />
              <span>Authenticator-Schlüssel einrichten</span>
            </h3>
            <p className="text-xs text-on-surface-variant">
              Füge den Authenticator-Setup-Schlüssel oder den gesamten `otpauth://`-Link ein:
            </p>
            <textarea
              rows={3}
              value={qrRawInput}
              onChange={(e) => setQrRawInput(e.target.value)}
              placeholder="otpauth://totp/... oder Base32-Schlüssel"
              className="msm-input w-full text-xs font-mono"
              autoFocus
            />
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button size="sm" variant="ghost" onClick={() => setShowQrModal(false)}>
                Abbrechen
              </Button>
              <Button size="sm" onClick={handleApplyOtpUri}>
                Übernehmen
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
