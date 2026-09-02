import { useState, useEffect, useMemo, useRef } from 'react'
import {
  Check,
  Clock,
  Cloud,
  CloudOff,
  Copy,
  Eye,
  EyeOff,
  FileText,
  KeyRound,
  Lock,
  Paperclip,
  Plus,
  QrCode,
  RefreshCw,
  Search,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Star,
  Trash2,
  Unlock,
  Upload,
  Zap,
} from 'lucide-react'
import { Button } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { getBrandIcon } from './brandCatalog'
import { generateTotpCode, getTotpSecondsRemaining } from './totpEngine'
import { generateSecurePassword } from './vaultCrypto'
import { checkPasswordLeak, type LeakCheckResult } from './leakChecker'
import { QrScannerModal } from './QrScannerModal'
import { setzeTresorSchutz } from '../tauri'
import {
  useVaultStore,
  type VaultItem,
  type VaultAttachment,
} from './vaultStore'

type VaultViewTab = 'all' | 'passwords' | 'authenticator' | 'notes'
type FilterMode = 'all' | 'favorites' | 'recent'

export function VaultView() {
  const {
    isInitialized,
    isUnlocked,
    isUnlocking,
    unlockError,
    items,
    selectedItemId,
    searchQuery,
    syncStatus,
    lastSyncTime,
    initializeVault,
    unlock,
    lock,
    setSearchQuery,
    setSelectedItemId,
    createQuickPasswordEntry,
    saveItem,
    deleteItem,
    toggleFavorite,
    markUsed,
    syncWithServer,
  } = useVaultStore()

  // Hardware- und Software-Schutz vor Windows Computer-Use KI-Screenshots
  useEffect(() => {
    void setzeTresorSchutz(isUnlocked)
    return () => {
      void setzeTresorSchutz(false)
    }
  }, [isUnlocked])

  const [activeTab, setActiveTab] = useState<VaultViewTab>('all')
  const [filterMode, setFilterMode] = useState<FilterMode>('all')
  const [isSetupMode, setIsSetupMode] = useState(!isInitialized)
  const [masterPasswordInput, setMasterPasswordInput] = useState('')
  const [confirmPasswordInput, setConfirmPasswordInput] = useState('')
  const [selectedItem, setSelectedItem] = useState<VaultItem | null>(null)
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [totpCode, setTotpCode] = useState<string>('')
  const [totpRemaining, setTotpRemaining] = useState<number>(30)
  const [copiedField, setCopiedField] = useState<string | null>(null)
  const [showQrModal, setShowQrModal] = useState(false)

  // Leak-Check Status
  const [leakCheck, setLeakCheck] = useState<LeakCheckResult | null>(null)
  const [isCheckingLeak, setIsCheckingLeak] = useState(false)

  // Formular-State für den ausgewählten Eintrag
  const [formService, setFormService] = useState('')
  const [formUsername, setFormUsername] = useState('')
  const [formPassword, setFormPassword] = useState('')
  const [formUrl, setFormUrl] = useState('')
  const [formNotes, setFormNotes] = useState('')
  const [formTotpSecret, setFormTotpSecret] = useState('')
  const [formCategory, setFormCategory] = useState<'login' | 'authenticator' | 'secure_note'>('login')
  const [formAttachments, setFormAttachments] = useState<VaultAttachment[]>([])

  const fileInputRef = useRef<HTMLInputElement | null>(null)

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
      setFormCategory(active.category || 'login')
      setFormAttachments(active.attachments || [])
      setShowPassword(false)
      setLeakCheck(null)

      // Automatische Leak-Prüfung wenn Passwort vorhanden
      if (active.password) {
        void runLeakCheck(active.password)
      }
    } else {
      setFormService('')
      setFormUsername('')
      setFormPassword('')
      setFormUrl('')
      setFormNotes('')
      setFormTotpSecret('')
      setFormCategory('login')
      setFormAttachments([])
      setLeakCheck(null)
    }
  }, [selectedItemId, items])

  // Live TOTP Takt (Aktualisierung jede Sekunde)
  useEffect(() => {
    if (!formTotpSecret) {
      setTotpCode('')
      return
    }

    let isMounted = true
    const updateCode = async () => {
      try {
        const remaining = getTotpSecondsRemaining(30)
        setTotpRemaining(remaining)
        const code = await generateTotpCode(formTotpSecret, 30)
        if (isMounted) {
          setTotpCode(code)
        }
      } catch {
        if (isMounted) setTotpCode('FEHLER')
      }
    }

    void updateCode()
    const timer = setInterval(() => void updateCode(), 1000)

    return () => {
      isMounted = false
      clearInterval(timer)
    }
  }, [formTotpSecret])

  // Leak-Check Funktion
  const runLeakCheck = async (pwd: string) => {
    if (!pwd || pwd.length < 3) {
      setLeakCheck(null)
      return
    }
    setIsCheckingLeak(true)
    try {
      const res = await checkPasswordLeak(pwd)
      setLeakCheck(res)
    } finally {
      setIsCheckingLeak(false)
    }
  }

  // Kopieren mit Haptik und Last-Used Aktualisierung
  const copyToClipboard = async (text: string, fieldName: string, itemId?: string) => {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopiedField(fieldName)
      toast.success(`${fieldName} in die Zwischenablage kopiert`)
      setTimeout(() => setCopiedField(null), 2000)

      if (itemId) {
        void markUsed(itemId)
      }
    } catch {
      toast.error('Kopieren fehlgeschlagen')
    }
  }

  // Filterung und Sortierung der Einträge
  const filteredItems = useMemo(() => {
    return items
      .filter((item) => {
        // Tab-Filter
        if (activeTab === 'passwords' && item.category !== 'login' && item.category !== undefined) return false
        if (activeTab === 'authenticator' && !item.totpSecret && item.category !== 'authenticator') return false
        if (activeTab === 'notes' && item.category !== 'secure_note') return false

        // Favoriten / Zuletzt verwendet
        if (filterMode === 'favorites' && !item.isFavorite) return false
        if (filterMode === 'recent' && !item.lastUsedAt) return false

        // Suche
        if (!searchQuery) return true
        const q = searchQuery.toLowerCase()
        return (
          item.service.toLowerCase().includes(q) ||
          item.username.toLowerCase().includes(q) ||
          (item.notes && item.notes.toLowerCase().includes(q)) ||
          (item.url && item.url.toLowerCase().includes(q))
        )
      })
      .sort((a, b) => {
        // Favoriten immer zuerst
        if (a.isFavorite && !b.isFavorite) return -1
        if (!a.isFavorite && b.isFavorite) return 1

        // Bei Filter "Zuletzt verwendet": Nach Timestamp sortieren
        if (filterMode === 'recent') {
          return (b.lastUsedAt || 0) - (a.lastUsedAt || 0)
        }

        // Standard: Alphabetisch
        return a.service.localeCompare(b.service)
      })
  }, [items, searchQuery, activeTab, filterMode])

  // Schnelle Passworterstellung (<= 2 Klicks)
  const handleQuickCreate = async (category: 'login' | 'authenticator' | 'secure_note' = 'login') => {
    try {
      const defaultName =
        category === 'authenticator'
          ? 'Neuer Authenticator'
          : category === 'secure_note'
          ? 'Neue Notiz'
          : 'Neuer Eintrag'
      const item = await createQuickPasswordEntry(defaultName)
      if (category !== 'login') {
        await saveItem({ ...item, category })
      }
      setSelectedItemId(item.id)
      toast.success(`${defaultName} erstellt`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Fehler beim Erstellen')
    }
  }

  // Speichern des aktuellen Eintrags
  const handleSave = async () => {
    if (!formService.trim()) {
      toast.error('Bitte gib einen Namen an.')
      return
    }

    try {
      await saveItem({
        id: selectedItem ? selectedItem.id : undefined,
        service: formService.trim(),
        username: formUsername.trim(),
        password: formPassword,
        url: formUrl.trim(),
        notes: formNotes.trim(),
        totpSecret: formTotpSecret.trim().toUpperCase(),
        category: formCategory,
        attachments: formAttachments,
      })
      toast.success('Eintrag sicher verschlüsselt gespeichert')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Speichern fehlgeschlagen')
    }
  }

  // Datei-Upload für verschlüsselte Anhänge
  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return

    const file = files[0]
    // 25 MB Sicherheitslimit pro Datei
    if (file.size > 25 * 1024 * 1024) {
      toast.error('Dateien dürfen maximal 25 MB groß sein.')
      return
    }

    const reader = new FileReader()
    reader.onload = () => {
      const base64 = reader.result as string
      const newAttachment: VaultAttachment = {
        id: window.crypto.randomUUID(),
        name: file.name,
        size: file.size,
        mimeType: file.type || 'application/octet-stream',
        dataBase64: base64,
      }
      setFormAttachments((prev) => [...prev, newAttachment])
      toast.success(`Datei "${file.name}" angehängt (wird beim Speichern verschlüsselt)`)
    }
    reader.readAsDataURL(file)
  }

  const handleDownloadAttachment = (att: VaultAttachment) => {
    const link = document.createElement('a')
    link.href = att.dataBase64
    link.download = att.name
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  const handleDeleteAttachment = (attId: string) => {
    setFormAttachments((prev) => prev.filter((a) => a.id !== attId))
  }

  // Löschen des aktuellen Eintrags
  const handleDelete = async () => {
    if (!selectedItem) return
    if (!window.confirm(`Möchtest du "${selectedItem.service}" wirklich unwiderruflich löschen?`)) {
      return
    }

    try {
      await deleteItem(selectedItem.id)
      toast.success('Eintrag gelöscht')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Löschen fehlgeschlagen')
    }
  }

  // Erkennung aus dem QR-Scanner
  const handleQrDetected = (payload: { secret: string; issuer?: string; account?: string }) => {
    setFormTotpSecret(payload.secret)
    if (payload.issuer && (!formService || formService === 'Neuer Eintrag' || formService === 'Neuer Authenticator')) {
      setFormService(payload.issuer)
    }
    if (payload.account && !formUsername) {
      setFormUsername(payload.account)
    }
    toast.success('2FA-Schlüssel aus QR-Code übernommen')
  }

  const isPasswordLongEnough = masterPasswordInput.length >= 8
  const doPasswordsMatch =
    masterPasswordInput.length > 0 && masterPasswordInput === confirmPasswordInput
  const canSubmitSetup = isPasswordLongEnough && doPasswordsMatch && !isUnlocking

  // ── GESPERRTER ZUSTAND / ERSTEINRICHTUNG ──
  if (!isUnlocked) {
    if (isSetupMode) {
      return (
        <div className="flex h-full w-full items-center justify-center p-4 bg-surface">
          <div className="w-full max-w-md p-6 sm:p-8 space-y-6 rounded-2xl bg-surface-container border border-outline-variant/30 shadow-xl">
            <div className="text-center space-y-2">
              <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary border border-primary/20">
                <KeyRound className="h-7 w-7" />
              </div>
              <h2 className="text-title-lg font-headline font-bold text-on-surface">
                Passwort-Manager einrichten
              </h2>
              <p className="text-xs text-on-surface-variant max-w-xs mx-auto">
                Erstelle dein persönliches Master-Passwort zum Schutz deiner Zugangsdaten, Notizen und 2FA-Schlüssel.
              </p>
            </div>

            <form
              onSubmit={async (e) => {
                e.preventDefault()
                if (!canSubmitSetup) return
                const ok = await initializeVault(masterPasswordInput)
                if (ok) {
                  setMasterPasswordInput('')
                  setConfirmPasswordInput('')
                  toast.success('Passwort-Manager erfolgreich eingerichtet')
                }
              }}
              className="space-y-4"
            >
              <div>
                <label className="block text-xs font-medium text-on-surface-variant mb-1">
                  Neues Master-Passwort festlegen
                </label>
                <div className="relative">
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={masterPasswordInput}
                    onChange={(e) => setMasterPasswordInput(e.target.value)}
                    placeholder="Mindestens 8 Zeichen..."
                    className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3.5 py-2.5 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary pr-10"
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

              <div>
                <label className="block text-xs font-medium text-on-surface-variant mb-1">
                  Master-Passwort wiederholen
                </label>
                <div className="relative">
                  <input
                    type={showConfirmPassword ? 'text' : 'password'}
                    value={confirmPasswordInput}
                    onChange={(e) => setConfirmPasswordInput(e.target.value)}
                    placeholder="Passwort erneut eingeben..."
                    className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3.5 py-2.5 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface transition-colors"
                    aria-label="Passwort bestätigen anzeigen"
                  >
                    {showConfirmPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>

                {confirmPasswordInput.length > 0 && (
                  <div className="mt-1.5 flex items-center gap-1.5 text-[11px]">
                    {doPasswordsMatch ? (
                      <span className="flex items-center gap-1 text-status-success">
                        <Check className="h-3 w-3" /> Passwörter stimmen überein
                      </span>
                    ) : (
                      <span className="text-status-error">Passwörter stimmen nicht überein</span>
                    )}
                  </div>
                )}
              </div>

              {unlockError && (
                <div className="rounded-xl bg-status-error/15 border border-status-error/30 p-3 text-xs text-status-error">
                  {unlockError}
                </div>
              )}

              <div className="rounded-xl bg-surface-container-low border border-outline-variant/20 p-3 space-y-1 text-[11px] text-on-surface-variant">
                <div className="flex items-center gap-1.5 font-medium text-on-surface">
                  <ShieldCheck className="h-3.5 w-3.5 text-primary" />
                  <span>Zero-Knowledge Sicherheits-Garantie</span>
                </div>
                <p>
                  Dein Master-Passwort wird niemals an den Server übertragen. Es existiert keine
                  „Passwort vergessen“-Funktion. Bewahre dein Master-Passwort sicher auf.
                </p>
              </div>

              <Button
                type="submit"
                disabled={!canSubmitSetup}
                className="w-full bg-primary text-on-primary hover:bg-primary-hover py-2.5"
              >
                {isUnlocking ? 'Richte ein...' : 'Passwort-Manager anlegen'}
              </Button>
            </form>

            <div className="text-center pt-2">
              <button
                type="button"
                onClick={() => {
                  setIsSetupMode(false)
                  setMasterPasswordInput('')
                  setConfirmPasswordInput('')
                }}
                className="text-xs text-primary hover:underline"
              >
                Bereits eingerichtet? Bestehendes Master-Passwort eingeben
              </button>
            </div>
          </div>
        </div>
      )
    }

    // Standard-Entsperrmaske
    return (
      <div className="flex h-full w-full items-center justify-center p-4 bg-surface">
        <div className="w-full max-w-sm p-6 sm:p-8 space-y-6 rounded-2xl bg-surface-container border border-outline-variant/30 shadow-xl">
          <div className="text-center space-y-2">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary border border-primary/20">
              <Lock className="h-7 w-7" />
            </div>
            <h2 className="text-title-lg font-headline font-bold text-on-surface">
              Passwort-Manager gesperrt
            </h2>
            <p className="text-xs text-on-surface-variant">
              Gib dein Master-Passwort ein, um deine Zugangsdaten und 2FA-Codes zu entschlüsseln.
            </p>
          </div>

          <form
            onSubmit={async (e) => {
              e.preventDefault()
              if (!masterPasswordInput || isUnlocking) return
              const ok = await unlock(masterPasswordInput)
              if (ok) {
                setMasterPasswordInput('')
                toast.success('Passwort-Manager entsperrt')
              }
            }}
            className="space-y-4"
          >
            <div className="relative">
              <input
                type={showPassword ? 'text' : 'password'}
                value={masterPasswordInput}
                onChange={(e) => setMasterPasswordInput(e.target.value)}
                placeholder="Master-Passwort eingeben..."
                className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3.5 py-2.5 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary pr-10"
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

            {unlockError && (
              <div className="rounded-xl bg-status-error/15 border border-status-error/30 p-3 text-xs text-status-error">
                {unlockError}
              </div>
            )}

            <Button
              type="submit"
              disabled={isUnlocking || !masterPasswordInput}
              className="w-full bg-primary text-on-primary hover:bg-primary-hover py-2.5 flex items-center justify-center gap-2"
            >
              {isUnlocking ? (
                <>
                  <RefreshCw className="h-4 w-4 animate-spin" />
                  <span>Entschlüssle...</span>
                </>
              ) : (
                <>
                  <Unlock className="h-4 w-4" />
                  <span>Entsperren</span>
                </>
              )}
            </Button>
          </form>

          <div className="text-center pt-2">
            <button
              type="button"
              onClick={() => {
                setIsSetupMode(true)
                setMasterPasswordInput('')
              }}
              className="text-xs text-on-surface-variant hover:text-primary transition-colors"
            >
              Noch kein Master-Passwort? Jetzt neu einrichten
            </button>
          </div>
        </div>
      </div>
    )
  }

  const ActiveBrandIcon = selectedItem ? getBrandIcon(formService, formUrl) : KeyRound

  // ── ENTSPERRTER ZUSTAND: VOLLE OBERFLÄCHE ──
  return (
    <div className="flex flex-col h-full bg-surface text-on-surface overflow-hidden">
      {/* OBERE LEISTE */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-outline-variant/20 bg-surface-container-low px-4 py-2.5">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary border border-primary/20">
            <Shield className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-on-surface">Passwort-Manager</h2>
              <span className="inline-flex items-center gap-1 rounded-full bg-surface-container px-2 py-0.5 text-[10px] text-on-surface-variant border border-outline-variant/20">
                <ShieldCheck className="h-3 w-3 text-status-success" />
                Zero-Knowledge
              </span>
            </div>
            <div className="flex items-center gap-2 text-[11px] text-on-surface-variant">
              {syncStatus === 'synced' && (
                <span className="flex items-center gap-1 text-status-success">
                  <Cloud className="h-3 w-3" /> Synchronisiert
                </span>
              )}
              {syncStatus === 'syncing' && (
                <span className="flex items-center gap-1 text-primary">
                  <RefreshCw className="h-3 w-3 animate-spin" /> Synchronisiere...
                </span>
              )}
              {syncStatus === 'offline' && (
                <span className="flex items-center gap-1 text-amber-500">
                  <CloudOff className="h-3 w-3" /> Offline-Modus
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
            onClick={() => void handleQuickCreate('login')}
            className="flex items-center gap-1.5 bg-primary text-on-primary hover:bg-primary-hover shadow-sm"
          >
            <Zap className="h-3.5 w-3.5" />
            <span>Neues Passwort</span>
          </Button>

          <Button
            size="sm"
            variant="ghost"
            onClick={lock}
            title="Passwort-Manager sperren"
            className="text-on-surface-variant hover:text-status-error"
          >
            <Lock className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* KATEGORIE-TABS & SCHNELLFILTER */}
      <div className="flex flex-wrap items-center justify-between border-b border-outline-variant/20 bg-surface-container-low/60 px-4 py-2 gap-2">
        {/* Haupt-Tabs */}
        <div className="flex items-center gap-1 bg-surface-container p-1 rounded-xl border border-outline-variant/20">
          <button
            type="button"
            onClick={() => setActiveTab('all')}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              activeTab === 'all'
                ? 'bg-surface text-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            Alle ({items.length})
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('passwords')}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              activeTab === 'passwords'
                ? 'bg-surface text-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            Passwörter
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('authenticator')}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              activeTab === 'authenticator'
                ? 'bg-surface text-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            2FA Authenticator
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('notes')}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              activeTab === 'notes'
                ? 'bg-surface text-primary shadow-sm'
                : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            Sichere Notizen
          </button>
        </div>

        {/* Schnellfilter: Alle / Favoriten / Zuletzt verwendet */}
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setFilterMode('all')}
            className={`px-2.5 py-1 text-xs rounded-lg transition-colors ${
              filterMode === 'all'
                ? 'bg-surface-container text-on-surface font-medium border border-outline-variant/30'
                : 'text-on-surface-variant hover:text-on-surface'
            }`}
          >
            Alle
          </button>
          <button
            type="button"
            onClick={() => setFilterMode('favorites')}
            className={`flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg transition-colors ${
              filterMode === 'favorites'
                ? 'bg-amber-500/15 text-amber-400 font-medium border border-amber-500/30'
                : 'text-on-surface-variant hover:text-amber-400'
            }`}
          >
            <Star className="h-3 w-3 fill-current" />
            <span>Favoriten</span>
          </button>
          <button
            type="button"
            onClick={() => setFilterMode('recent')}
            className={`flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg transition-colors ${
              filterMode === 'recent'
                ? 'bg-primary/15 text-primary font-medium border border-primary/30'
                : 'text-on-surface-variant hover:text-primary'
            }`}
          >
            <Clock className="h-3 w-3" />
            <span>Zuletzt</span>
          </button>
        </div>
      </div>

      {/* HAUPTBEREICH (2 SPALTEN) */}
      <div className="grid grid-cols-1 md:grid-cols-12 flex-1 min-h-0 overflow-hidden">
        {/* LINKE SPALTE: LISTE & SUCHE */}
        <div className="md:col-span-5 lg:col-span-4 flex flex-col border-r border-outline-variant/20 bg-surface-container-low min-h-0">
          <div className="p-3 border-b border-outline-variant/20 flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-on-surface-variant" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Suchen nach Dienst, Login, Notiz..."
                className="w-full rounded-xl bg-surface-container border border-outline-variant/20 pl-9 pr-3 py-1.5 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary"
              />
            </div>
            <button
              type="button"
              onClick={() => {
                if (activeTab === 'authenticator') {
                  void handleQuickCreate('authenticator')
                } else if (activeTab === 'notes') {
                  void handleQuickCreate('secure_note')
                } else {
                  void handleQuickCreate('login')
                }
              }}
              title="Neuer Eintrag"
              className="p-1.5 rounded-xl bg-surface-container hover:bg-surface-container-high border border-outline-variant/20 text-on-surface transition-colors"
            >
              <Plus className="h-4 w-4" />
            </button>
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
                        <div className="flex items-center gap-1.5">
                          <h4 className="text-xs font-semibold text-on-surface truncate">
                            {item.service}
                          </h4>
                          {item.isFavorite && (
                            <Star className="h-3 w-3 text-amber-400 fill-amber-400 shrink-0" />
                          )}
                        </div>
                        <p className="text-[11px] text-on-surface-variant truncate">
                          {item.username || (item.notes ? 'Sichere Notiz' : 'Kein Benutzername')}
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center gap-1.5 opacity-80 group-hover:opacity-100">
                      {item.totpSecret && (
                        <span className="rounded-md bg-emerald-500/10 text-emerald-500 border border-emerald-500/20 px-1.5 py-0.5 text-[10px] font-mono">
                          2FA
                        </span>
                      )}
                      {item.attachments && item.attachments.length > 0 && (
                        <span className="rounded-md bg-sky-500/10 text-sky-400 border border-sky-500/20 px-1.5 py-0.5 text-[10px] flex items-center gap-0.5">
                          <Paperclip className="h-2.5 w-2.5" />
                          {item.attachments.length}
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          void toggleFavorite(item.id)
                        }}
                        title={item.isFavorite ? 'Favorit entfernen' : 'Zu Favoriten hinzufügen'}
                        className="p-1 rounded-lg hover:bg-surface-container text-on-surface-variant hover:text-amber-400 transition-colors"
                      >
                        <Star
                          className={`h-3.5 w-3.5 ${item.isFavorite ? 'text-amber-400 fill-amber-400' : ''}`}
                        />
                      </button>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </div>

        {/* RECHTE SPALTE: DETAILS & BEARBEITEN */}
        <div className="md:col-span-7 lg:col-span-8 flex flex-col bg-surface overflow-y-auto min-h-0">
          {selectedItem ? (
            <div className="p-5 sm:p-6 space-y-6 max-w-2xl">
              {/* HEADER DES EINTRAGS */}
              <div className="flex items-center justify-between pb-4 border-b border-outline-variant/20">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 flex items-center justify-center rounded-xl bg-surface-container border border-outline-variant/30 p-1.5">
                    <ActiveBrandIcon className="w-6 h-6" />
                  </div>
                  <div>
                    <input
                      type="text"
                      value={formService}
                      onChange={(e) => setFormService(e.target.value)}
                      placeholder="Dienst / Name..."
                      className="font-headline text-title-md font-bold text-on-surface bg-transparent border-b border-transparent hover:border-outline-variant/30 focus:border-primary outline-none transition-colors w-full"
                    />
                    <div className="flex items-center gap-2 mt-0.5 text-[11px] text-on-surface-variant">
                      <span>Typ:</span>
                      <select
                        value={formCategory}
                        onChange={(e) => setFormCategory(e.target.value as typeof formCategory)}
                        className="bg-surface-container border border-outline-variant/20 rounded-md px-1.5 py-0.5 text-[11px] text-on-surface outline-none"
                      >
                        <option value="login">Login / Passwort</option>
                        <option value="authenticator">Authenticator</option>
                        <option value="secure_note">Sichere Notiz</option>
                      </select>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => void toggleFavorite(selectedItem.id)}
                    className={`p-2 rounded-xl border transition-colors ${
                      selectedItem.isFavorite
                        ? 'bg-amber-500/10 border-amber-500/30 text-amber-400'
                        : 'border-outline-variant/20 hover:bg-surface-container text-on-surface-variant'
                    }`}
                    title="Favorit umschalten"
                  >
                    <Star className={`h-4 w-4 ${selectedItem.isFavorite ? 'fill-current' : ''}`} />
                  </button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => void handleDelete()}
                    className="text-on-surface-variant hover:text-status-error"
                    title="Eintrag löschen"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              {/* BENUTZERNAME / E-MAIL */}
              {formCategory !== 'secure_note' && (
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
                      className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3.5 py-2 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary pr-10"
                    />
                    <button
                      type="button"
                      onClick={() => void copyToClipboard(formUsername, 'Benutzername', selectedItem.id)}
                      className="absolute right-2 p-1.5 rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container transition-colors"
                      title="Benutzername kopieren"
                    >
                      {copiedField === 'Benutzername' ? (
                        <Check className="h-3.5 w-3.5 text-status-success" />
                      ) : (
                        <Copy className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </div>
                </div>
              )}

              {/* PASSWORT & LEAK-CHECKER */}
              {formCategory !== 'secure_note' && (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label className="block text-xs font-medium text-on-surface-variant">
                      Passwort
                    </label>
                    <button
                      type="button"
                      onClick={() => {
                        const newPwd = generateSecurePassword(20, true)
                        setFormPassword(newPwd)
                        void runLeakCheck(newPwd)
                        toast.success('Neues sicheres Passwort generiert')
                      }}
                      className="text-xs text-primary hover:underline flex items-center gap-1"
                    >
                      <Zap className="h-3 w-3" />
                      Neu generieren
                    </button>
                  </div>

                  <div className="relative flex items-center">
                    <input
                      type={showPassword ? 'text' : 'password'}
                      value={formPassword}
                      onChange={(e) => {
                        setFormPassword(e.target.value)
                        void runLeakCheck(e.target.value)
                      }}
                      placeholder="Passwort..."
                      className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3.5 py-2 text-xs text-on-surface font-mono placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary pr-20"
                    />
                    <div className="absolute right-2 flex items-center gap-0.5">
                      <button
                        type="button"
                        onClick={() => setShowPassword(!showPassword)}
                        className="p-1.5 rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container transition-colors"
                        title={showPassword ? 'Verbergen' : 'Anzeigen'}
                      >
                        {showPassword ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                      </button>
                      <button
                        type="button"
                        onClick={() => void copyToClipboard(formPassword, 'Passwort', selectedItem.id)}
                        className="p-1.5 rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container transition-colors"
                        title="Passwort kopieren"
                      >
                        {copiedField === 'Passwort' ? (
                          <Check className="h-3.5 w-3.5 text-status-success" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )}
                      </button>
                    </div>
                  </div>

                  {/* LEAK-CHECK STATUS-INDIKATOR */}
                  {isCheckingLeak && (
                    <div className="flex items-center gap-1.5 text-[11px] text-on-surface-variant">
                      <RefreshCw className="h-3 w-3 animate-spin text-primary" />
                      <span>Prüfe Passwörter auf bekannte Leaks (k-Anonymität)...</span>
                    </div>
                  )}

                  {leakCheck && leakCheck.checked && (
                    <div className="pt-0.5">
                      {leakCheck.isLeaked ? (
                        <div className="flex items-center gap-1.5 rounded-xl bg-status-error/15 border border-status-error/30 px-3 py-2 text-xs text-status-error">
                          <ShieldAlert className="h-4 w-4 shrink-0" />
                          <span>
                            Warnung: In {leakCheck.count.toLocaleString()} Datenlecks gefunden! Bitte ändern.
                          </span>
                        </div>
                      ) : (
                        <div className="flex items-center gap-1.5 rounded-xl bg-status-success/15 border border-status-success/30 px-3 py-1.5 text-xs text-status-success">
                          <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
                          <span>Keine bekannten Datenlecks gefunden</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* WEBSITE-URL */}
              <div className="space-y-1">
                <label className="block text-xs font-medium text-on-surface-variant">
                  Website / URL
                </label>
                <input
                  type="text"
                  value={formUrl}
                  onChange={(e) => setFormUrl(e.target.value)}
                  placeholder="https://example.com"
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3.5 py-2 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary"
                />
              </div>

              {/* AUTHENTICATOR (TOTP 2FA) */}
              <div className="space-y-2 rounded-xl border border-outline-variant/20 bg-surface-container-low p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <KeyRound className="h-4 w-4 text-emerald-400" />
                    <span className="text-xs font-semibold text-on-surface">
                      2FA Authenticator (TOTP)
                    </span>
                  </div>

                  <button
                    type="button"
                    onClick={() => setShowQrModal(true)}
                    className="flex items-center gap-1.5 text-xs text-primary hover:underline font-medium"
                  >
                    <QrCode className="h-3.5 w-3.5" />
                    <span>QR-Code scannen</span>
                  </button>
                </div>

                {formTotpSecret ? (
                  <div className="flex items-center justify-between rounded-xl bg-surface-container border border-outline-variant/30 p-3.5">
                    <div>
                      <div className="text-[10px] uppercase font-mono text-on-surface-variant">
                        Aktueller Code
                      </div>
                      <div className="text-2xl font-mono font-bold tracking-widest text-emerald-400">
                        {totpCode.slice(0, 3)} {totpCode.slice(3)}
                      </div>
                    </div>

                    <div className="flex items-center gap-3">
                      {/* Countdown Timer */}
                      <div className="relative flex h-8 w-8 items-center justify-center">
                        <svg className="h-8 w-8 -rotate-90 transform">
                          <circle
                            cx="16"
                            cy="16"
                            r="13"
                            stroke="currentColor"
                            strokeWidth="2.5"
                            className="text-surface-container-high"
                            fill="transparent"
                          />
                          <circle
                            cx="16"
                            cy="16"
                            r="13"
                            stroke="currentColor"
                            strokeWidth="2.5"
                            className={`transition-all duration-1000 ease-linear ${
                              totpRemaining <= 5 ? 'text-status-error' : 'text-emerald-400'
                            }`}
                            fill="transparent"
                            strokeDasharray={81.68}
                            strokeDashoffset={81.68 - (81.68 * totpRemaining) / 30}
                          />
                        </svg>
                        <span className="absolute text-[10px] font-mono font-semibold text-on-surface">
                          {totpRemaining}
                        </span>
                      </div>

                      <button
                        type="button"
                        onClick={() => void copyToClipboard(totpCode, '2FA-Code', selectedItem.id)}
                        className="flex items-center gap-1 rounded-lg bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 px-3 py-1.5 text-xs font-medium transition-colors"
                      >
                        {copiedField === '2FA-Code' ? (
                          <>
                            <Check className="h-3.5 w-3.5 text-status-success" />
                            <span>Kopiert</span>
                          </>
                        ) : (
                          <>
                            <Copy className="h-3.5 w-3.5" />
                            <span>Kopieren</span>
                          </>
                        )}
                      </button>
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-on-surface-variant">
                    Scanne einen QR-Code per Kamera oder lade einen Screenshot hoch, um 6-stellige Einmalcodes zu generieren.
                  </p>
                )}

                <div className="pt-1">
                  <input
                    type="text"
                    value={formTotpSecret}
                    onChange={(e) => setFormTotpSecret(e.target.value.toUpperCase())}
                    placeholder="Schlüssel manuell eingeben (z. B. JBSWY3DPEHPK3PXP)"
                    className="w-full rounded-lg bg-surface border border-outline-variant/20 px-3 py-1.5 text-xs text-on-surface font-mono placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary"
                  />
                </div>
              </div>

              {/* SICHERE NOTIZEN */}
              <div className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <FileText className="h-3.5 w-3.5 text-primary" />
                  <label className="block text-xs font-medium text-on-surface-variant">
                    Sichere Notizen (Verschlüsselt)
                  </label>
                </div>
                <textarea
                  rows={4}
                  value={formNotes}
                  onChange={(e) => setFormNotes(e.target.value)}
                  placeholder="Vertrauliche Notizen, Backup-Codes, Wiederherstellungsschlüssel..."
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 p-3 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary resize-y"
                />
              </div>

              {/* VERSCHLÜSSELTE DATEIANHÄNGE */}
              <div className="space-y-2 rounded-xl border border-outline-variant/20 bg-surface-container-low p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Paperclip className="h-4 w-4 text-primary" />
                    <span className="text-xs font-semibold text-on-surface">
                      Verschlüsselte Dateien ({formAttachments.length})
                    </span>
                  </div>

                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="flex items-center gap-1.5 text-xs text-primary hover:underline font-medium"
                  >
                    <Upload className="h-3.5 w-3.5" />
                    <span>Datei anhängen</span>
                  </button>
                  <input
                    ref={fileInputRef}
                    type="file"
                    onChange={handleFileUpload}
                    className="hidden"
                  />
                </div>

                {formAttachments.length > 0 ? (
                  <div className="space-y-2 pt-1">
                    {formAttachments.map((att) => (
                      <div
                        key={att.id}
                        className="flex items-center justify-between rounded-lg bg-surface-container border border-outline-variant/20 p-2.5 text-xs"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <Paperclip className="h-3.5 w-3.5 text-on-surface-variant shrink-0" />
                          <div className="min-w-0">
                            <span className="font-medium text-on-surface truncate block">
                              {att.name}
                            </span>
                            <span className="text-[10px] text-on-surface-variant">
                              {(att.size / 1024).toFixed(1)} KB
                            </span>
                          </div>
                        </div>

                        <div className="flex items-center gap-1 shrink-0">
                          <button
                            type="button"
                            onClick={() => handleDownloadAttachment(att)}
                            className="px-2 py-1 rounded-md bg-surface-container-high hover:bg-surface-container-highest text-primary text-[11px] transition-colors"
                          >
                            Herunterladen
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDeleteAttachment(att.id)}
                            className="p-1 text-on-surface-variant hover:text-status-error transition-colors"
                            title="Anhang entfernen"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-on-surface-variant">
                    Sichere Dokumente, Zertifikate oder Backups direkt verschlüsselt im Eintrag hinterlegen (max. 25 MB).
                  </p>
                )}
              </div>

              {/* SPEICHERN BUTTON */}
              <div className="pt-2">
                <Button
                  onClick={() => void handleSave()}
                  className="w-full bg-primary text-on-primary hover:bg-primary-hover py-2.5"
                >
                  Änderungen verschlüsselt speichern
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center p-8 text-center text-on-surface-variant">
              <KeyRound className="h-12 w-12 text-outline-variant/40 mb-3" />
              <p className="text-sm font-medium text-on-surface">Kein Eintrag ausgewählt</p>
              <p className="text-xs text-on-surface-variant mt-1 max-w-xs">
                Wähle links einen Eintrag aus oder erstelle mit einem Klick einen neuen Zugang.
              </p>
              <Button
                size="sm"
                onClick={() => void handleQuickCreate('login')}
                className="mt-4 bg-primary text-on-primary"
              >
                Neuen Eintrag anlegen
              </Button>
            </div>
          )}
        </div>
      </div>

      {/* MODAL: QR-CODE SCANNER (KAMERA & BILD-UPLOAD) */}
      <QrScannerModal
        isOpen={showQrModal}
        onClose={() => setShowQrModal(false)}
        onDetected={handleQrDetected}
      />
    </div>
  )
}
