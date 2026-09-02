import React, { useState, useEffect, useMemo } from 'react'
import {
  Check,
  Clock,
  Cloud,
  CloudOff,
  Copy,
  Edit2,
  ExternalLink,
  Eye,
  EyeOff,
  KeyRound,
  Lock,
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
  Zap,
  X,
} from 'lucide-react'
import { Button } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { getBrandIcon } from './brandCatalog'
import { generateTotpCode, getTotpSecondsRemaining } from './totpEngine'
import { generateSecurePassword } from './vaultCrypto'
import { checkPasswordLeak, type LeakCheckResult } from './leakChecker'
import { QrScannerModal } from './QrScannerModal'
import { setzeTresorSchutz } from '../tauri'
import { useVaultStore, type VaultItem } from './vaultStore'

export function VaultView() {
  const {
    isInitialized,
    isUnlocked,
    isUnlocking,
    unlockError,
    items,
    searchQuery,
    syncStatus,
    lastSyncTime,
    initializeVault,
    unlock,
    lock,
    setSearchQuery,
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

  // UI-Zustände
  const [isSetupMode, setIsSetupMode] = useState(!isInitialized)
  const [masterPasswordInput, setMasterPasswordInput] = useState('')
  const [confirmPasswordInput, setConfirmPasswordInput] = useState('')
  const [showMasterPassword, setShowMasterPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)

  // Feedback für kopierte Felder
  const [copiedIdField, setCopiedIdField] = useState<string | null>(null)
  const [revealedPasswordId, setRevealedPasswordId] = useState<string | null>(null)

  // Live TOTP Takt (Aktualisierung jede Sekunde für alle sichtbaren 2FA Codes)
  const [totpRemaining, setTotpRemaining] = useState<number>(30)
  const [totpCodes, setTotpCodes] = useState<Record<string, string>>({})

  // Modal: Bearbeiten / Neu erstellen
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [editingItemId, setEditingItemId] = useState<string | null>(null)
  const [modalService, setModalService] = useState('')
  const [modalUsername, setModalUsername] = useState('')
  const [modalPassword, setModalPassword] = useState('')
  const [modalUrl, setModalUrl] = useState('')
  const [modalNotes, setModalNotes] = useState('')
  const [modalTotpSecret, setModalTotpSecret] = useState('')
  const [showModalPassword, setShowModalPassword] = useState(false)
  const [showQrScanner, setShowQrScanner] = useState(false)
  const [leakCheckResult, setLeakCheckResult] = useState<LeakCheckResult | null>(null)
  const [isCheckingLeak, setIsCheckingLeak] = useState(false)

  // TOTP-Ticker für alle Einträge mit 2FA-Secret
  useEffect(() => {
    if (!isUnlocked) return

    let isMounted = true
    const updateTotp = async () => {
      const remaining = getTotpSecondsRemaining(30)
      if (isMounted) setTotpRemaining(remaining)

      const newCodes: Record<string, string> = {}
      for (const item of items) {
        if (item.totpSecret) {
          try {
            const code = await generateTotpCode(item.totpSecret, 30)
            newCodes[item.id] = code
          } catch {
            newCodes[item.id] = 'FEHLER'
          }
        }
      }
      if (isMounted) setTotpCodes(newCodes)
    }

    void updateTotp()
    const interval = setInterval(() => void updateTotp(), 1000)

    return () => {
      isMounted = false
      clearInterval(interval)
    }
  }, [items, isUnlocked])

  // Kopieren mit visuellem Feedback
  const handleCopy = async (text: string, fieldKey: string, itemId?: string) => {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopiedIdField(fieldKey)
      toast.success('In die Zwischenablage kopiert')
      setTimeout(() => setCopiedIdField(null), 1800)

      if (itemId) {
        void markUsed(itemId)
      }
    } catch {
      toast.error('Kopieren fehlgeschlagen')
    }
  }

  // Passwort für 5 Sekunden aufdecken
  const handleToggleRevealPassword = (itemId: string) => {
    if (revealedPasswordId === itemId) {
      setRevealedPasswordId(null)
    } else {
      setRevealedPasswordId(itemId)
      setTimeout(() => {
        setRevealedPasswordId((cur) => (cur === itemId ? null : cur))
      }, 5000)
    }
  }

  // Modal öffnen für neuen Eintrag
  const openNewEntryModal = () => {
    setEditingItemId(null)
    setModalService('')
    setModalUsername('')
    const newPwd = generateSecurePassword(20, true)
    setModalPassword(newPwd)
    setModalUrl('')
    setModalNotes('')
    setModalTotpSecret('')
    setShowModalPassword(false)
    setLeakCheckResult(null)
    setIsModalOpen(true)
    void runLeakCheck(newPwd)
  }

  // Modal öffnen für bestehenden Eintrag
  const openEditEntryModal = (item: VaultItem) => {
    setEditingItemId(item.id)
    setModalService(item.service)
    setModalUsername(item.username)
    setModalPassword(item.password)
    setModalUrl(item.url || '')
    setModalNotes(item.notes || '')
    setModalTotpSecret(item.totpSecret || '')
    setShowModalPassword(false)
    setLeakCheckResult(null)
    setIsModalOpen(true)
    if (item.password) {
      void runLeakCheck(item.password)
    }
  }

  // Leak-Check im Modal
  const runLeakCheck = async (pwd: string) => {
    if (!pwd || pwd.length < 3) {
      setLeakCheckResult(null)
      return
    }
    setIsCheckingLeak(true)
    try {
      const res = await checkPasswordLeak(pwd)
      setLeakCheckResult(res)
    } finally {
      setIsCheckingLeak(false)
    }
  }

  // Speichern im Modal
  const handleModalSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!modalService.trim()) {
      toast.error('Bitte gib den Namen des Dienstes an.')
      return
    }

    try {
      await saveItem({
        id: editingItemId || undefined,
        service: modalService.trim(),
        username: modalUsername.trim(),
        password: modalPassword,
        url: modalUrl.trim(),
        notes: modalNotes.trim(),
        totpSecret: modalTotpSecret.trim().toUpperCase(),
      })
      setIsModalOpen(false)
      toast.success('Passwort sicher verschlüsselt gespeichert')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Speichern fehlgeschlagen')
    }
  }

  // Löschen eines Eintrags
  const handleDeleteItem = async (item: VaultItem) => {
    if (!window.confirm(`Möchtest du "${item.service}" wirklich unwiderruflich löschen?`)) {
      return
    }
    try {
      await deleteItem(item.id)
      if (isModalOpen && editingItemId === item.id) {
        setIsModalOpen(false)
      }
      toast.success('Eintrag gelöscht')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Löschen fehlgeschlagen')
    }
  }

  // QR-Code Scan Ergebnis übernehmen
  const handleQrDetected = (payload: { secret: string; issuer?: string; account?: string }) => {
    setModalTotpSecret(payload.secret)
    if (payload.issuer && !modalService) {
      setModalService(payload.issuer)
    }
    if (payload.account && !modalUsername) {
      setModalUsername(payload.account)
    }
    toast.success('2FA-Schlüssel aus QR-Code übernommen')
  }

  // Filterung nach Suchbegriff
  const searchedItems = useMemo(() => {
    if (!searchQuery.trim()) return items
    const q = searchQuery.toLowerCase()
    return items.filter(
      (item) =>
        item.service.toLowerCase().includes(q) ||
        item.username.toLowerCase().includes(q) ||
        (item.url && item.url.toLowerCase().includes(q)) ||
        (item.notes && item.notes.toLowerCase().includes(q))
    )
  }, [items, searchQuery])

  // 1. Favoriten
  const favoriteItems = useMemo(() => {
    return searchedItems.filter((item) => item.isFavorite)
  }, [searchedItems])

  // 2. Zuletzt verwendet (letzte 5, die nicht bereits in den Favoriten sind)
  const recentItems = useMemo(() => {
    return searchedItems
      .filter((item) => !item.isFavorite && typeof item.lastUsedAt === 'number' && item.lastUsedAt > 0)
      .sort((a, b) => (b.lastUsedAt || 0) - (a.lastUsedAt || 0))
      .slice(0, 5)
  }, [searchedItems])

  // 3. Alle anderen Einträge (alphabetisch)
  const otherItems = useMemo(() => {
    const favoriteIds = new Set(favoriteItems.map((i) => i.id))
    const recentIds = new Set(recentItems.map((i) => i.id))
    return searchedItems
      .filter((item) => !favoriteIds.has(item.id) && !recentIds.has(item.id))
      .sort((a, b) => a.service.localeCompare(b.service))
  }, [searchedItems, favoriteItems, recentItems])

  const ModalBrandIcon = getBrandIcon(modalService, modalUrl)

  // ── 1. ERSTEINRICHTUNG (Wenn noch kein Master-Passwort hinterlegt ist) ──
  if (!isUnlocked && isSetupMode) {
    const canSubmitSetup =
      masterPasswordInput.length >= 8 &&
      masterPasswordInput === confirmPasswordInput &&
      !isUnlocking

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
              Erstelle dein persönliches Master-Passwort zum Schutz deiner Zugangsdaten und 2FA-Schlüssel.
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
                  type={showMasterPassword ? 'text' : 'password'}
                  value={masterPasswordInput}
                  onChange={(e) => setMasterPasswordInput(e.target.value)}
                  placeholder="Mindestens 8 Zeichen..."
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3.5 py-2.5 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary pr-10"
                  autoFocus
                />
                <button
                  type="button"
                  onClick={() => setShowMasterPassword(!showMasterPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface transition-colors"
                >
                  {showMasterPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
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
                >
                  {showConfirmPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>

              {confirmPasswordInput.length > 0 && (
                <div className="mt-1.5 flex items-center gap-1.5 text-[11px]">
                  {masterPasswordInput === confirmPasswordInput ? (
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
                <span>Zero-Knowledge Architektur</span>
              </div>
              <p>
                Deine Daten werden auf dem Endgerät mit AES-256-GCM verschlüsselt. Niemand außer dir
                kann deine Passwörter einsehen oder zurücksetzen.
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

  // ── 2. ENTSPERREN (Standard-Zustand beim Öffnen) ──
  if (!isUnlocked) {
    return (
      <div className="flex h-full w-full items-center justify-center p-4 bg-surface">
        <div className="w-full max-w-sm p-6 sm:p-8 space-y-6 rounded-2xl bg-surface-container border border-outline-variant/30 shadow-xl">
          <div className="text-center space-y-2">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary border border-primary/20">
              <Lock className="h-7 w-7" />
            </div>
            <h2 className="text-title-lg font-headline font-bold text-on-surface">
              Passwort-Manager
            </h2>
            <p className="text-xs text-on-surface-variant">
              Gib dein Master-Passwort ein, um deine Passwörter und 2FA-Codes zu entsperren.
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
                type={showMasterPassword ? 'text' : 'password'}
                value={masterPasswordInput}
                onChange={(e) => setMasterPasswordInput(e.target.value)}
                placeholder="Master-Passwort..."
                className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3.5 py-2.5 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary pr-10"
                autoFocus
              />
              <button
                type="button"
                onClick={() => setShowMasterPassword(!showMasterPassword)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface transition-colors"
              >
                {showMasterPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
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

  // ── HILFSKOMPONENTE: EINTRAGS-ZEILE (TABELLE / LISTE) ──
  const renderItemRow = (item: VaultItem) => {
    const ItemBrand = getBrandIcon(item.service, item.url)
    const isRevealed = revealedPasswordId === item.id
    const itemTotp = totpCodes[item.id]

    return (
      <div
        key={item.id}
        className="group flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3.5 rounded-xl bg-surface-container hover:bg-surface-container-high border border-outline-variant/20 transition-all shadow-sm"
      >
        {/* Linke Seite: Logo, Dienstname, Benutzername */}
        <div className="flex items-center gap-3.5 min-w-0 flex-1">
          <div className="flex-shrink-0 w-10 h-10 flex items-center justify-center rounded-xl bg-surface border border-outline-variant/20 p-2 shadow-xs">
            <ItemBrand className="w-6 h-6" />
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-on-surface truncate">
                {item.service}
              </h3>
              {item.url && (
                <a
                  href={item.url.startsWith('http') ? item.url : `https://${item.url}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-on-surface-variant hover:text-primary transition-colors"
                  title="Website öffnen"
                >
                  <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>

            <div className="flex items-center gap-2 mt-0.5">
              <span className="text-xs text-on-surface-variant truncate font-mono">
                {item.username || 'Kein Benutzername'}
              </span>
              {item.username && (
                <button
                  type="button"
                  onClick={() => void handleCopy(item.username, `user-${item.id}`, item.id)}
                  title="Benutzername kopieren"
                  className="text-on-surface-variant hover:text-on-surface p-0.5 rounded transition-colors"
                >
                  {copiedIdField === `user-${item.id}` ? (
                    <Check className="h-3 w-3 text-status-success" />
                  ) : (
                    <Copy className="h-3 w-3" />
                  )}
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Mittlere Seite: Passwort & 2FA Schnell-Kopieren */}
        <div className="flex flex-wrap items-center gap-2.5 sm:justify-end">
          {/* Passwort Schnell-Aktion */}
          {item.password && (
            <div className="flex items-center rounded-lg bg-surface-container-low border border-outline-variant/20 px-2.5 py-1 gap-1.5 font-mono text-xs">
              <span className="text-on-surface select-none">
                {isRevealed ? item.password : '••••••••••••'}
              </span>
              <button
                type="button"
                onClick={() => handleToggleRevealPassword(item.id)}
                className="text-on-surface-variant hover:text-on-surface p-1 transition-colors"
                title={isRevealed ? 'Verbergen' : '5 Sek. anzeigen'}
              >
                {isRevealed ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
              <button
                type="button"
                onClick={() => void handleCopy(item.password, `pwd-${item.id}`, item.id)}
                className="text-primary hover:text-primary-hover p-1 transition-colors"
                title="Passwort kopieren"
              >
                {copiedIdField === `pwd-${item.id}` ? (
                  <Check className="h-3.5 w-3.5 text-status-success" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
          )}

          {/* 2FA Einmal-Code Schnell-Aktion (falls vorhanden) */}
          {item.totpSecret && itemTotp && (
            <div className="flex items-center rounded-lg bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 gap-1.5 font-mono text-xs">
              <span className="text-[10px] text-emerald-400/70 font-semibold uppercase">2FA</span>
              <span className="text-emerald-400 font-bold tracking-wider">
                {itemTotp.slice(0, 3)} {itemTotp.slice(3)}
              </span>
              <span className="text-[10px] text-emerald-400/70">({totpRemaining}s)</span>
              <button
                type="button"
                onClick={() => void handleCopy(itemTotp, `totp-${item.id}`, item.id)}
                className="text-emerald-400 hover:text-emerald-300 p-1 transition-colors"
                title="2FA-Code kopieren"
              >
                {copiedIdField === `totp-${item.id}` ? (
                  <Check className="h-3.5 w-3.5 text-status-success" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
          )}

          {/* Favoriten-Stern & Bearbeiten-Aktion */}
          <div className="flex items-center gap-1 border-l border-outline-variant/20 pl-2">
            <button
              type="button"
              onClick={() => void toggleFavorite(item.id)}
              className={`p-1.5 rounded-lg transition-colors ${
                item.isFavorite
                  ? 'text-amber-400 hover:text-amber-300'
                  : 'text-on-surface-variant hover:text-amber-400'
              }`}
              title={item.isFavorite ? 'Favorit entfernen' : 'Zu Favoriten hinzufügen'}
            >
              <Star className={`h-4 w-4 ${item.isFavorite ? 'fill-current' : ''}`} />
            </button>

            <button
              type="button"
              onClick={() => openEditEntryModal(item)}
              className="p-1.5 rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest transition-colors"
              title="Details & Bearbeiten"
            >
              <Edit2 className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    )
  }

  // ── 3. HAUPTANSICHT: AUFGERÄUMTE VOLLBILD-STRUKTUR ──
  return (
    <div className="flex flex-col h-full bg-surface text-on-surface overflow-hidden">
      {/* KOPFZEILE */}
      <div className="flex items-center justify-between border-b border-outline-variant/20 bg-surface-container-low px-4 sm:px-6 py-3">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary border border-primary/20">
            <Shield className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-bold text-on-surface">Passwort-Manager</h1>
              <span className="hidden sm:inline-flex items-center gap-1 rounded-full bg-surface-container px-2 py-0.5 text-[10px] text-on-surface-variant border border-outline-variant/20">
                <ShieldCheck className="h-3 w-3 text-status-success" />
                Zero-Knowledge
              </span>
            </div>
            <div className="flex items-center gap-2 text-[11px] text-on-surface-variant">
              <span>{items.length} gesicherte Zugänge</span>
              {syncStatus === 'synced' && (
                <span className="hidden md:flex items-center gap-1 text-status-success">
                  • <Cloud className="h-3 w-3" /> Synchronisiert
                </span>
              )}
              {syncStatus === 'syncing' && (
                <span className="hidden md:flex items-center gap-1 text-primary">
                  • <RefreshCw className="h-3 w-3 animate-spin" /> Synchronisiere...
                </span>
              )}
              {syncStatus === 'offline' && (
                <span className="hidden md:flex items-center gap-1 text-amber-500">
                  • <CloudOff className="h-3 w-3" /> Offline
                </span>
              )}
              {lastSyncTime && (
                <span className="hidden lg:inline">
                  • Zuletzt: {new Date(lastSyncTime).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* DER HAUPT-BUTTON: NEUES PASSWORT */}
          <Button
            onClick={openNewEntryModal}
            className="flex items-center gap-1.5 bg-primary text-on-primary hover:bg-primary-hover shadow-sm px-3.5 py-2 text-xs font-medium"
          >
            <Plus className="h-4 w-4" />
            <span>Neues Passwort</span>
          </Button>

          <Button
            size="sm"
            variant="ghost"
            onClick={() => void syncWithServer()}
            title="Jetzt synchronisieren"
            className="hidden sm:flex text-on-surface-variant hover:text-on-surface p-2"
          >
            <RefreshCw className={`h-4 w-4 ${syncStatus === 'syncing' ? 'animate-spin' : ''}`} />
          </Button>

          <Button
            size="sm"
            variant="ghost"
            onClick={lock}
            title="Passwort-Manager sperren"
            className="text-on-surface-variant hover:text-status-error p-2"
          >
            <Lock className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* SUCH-LEISTE */}
      <div className="px-4 sm:px-6 py-3 border-b border-outline-variant/15 bg-surface-container-low/50">
        <div className="relative max-w-xl">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-on-surface-variant" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Dienst, Website oder Benutzer suchen..."
            className="w-full rounded-xl bg-surface-container border border-outline-variant/30 pl-10 pr-4 py-2 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary transition-colors"
          />
        </div>
      </div>

      {/* HAUPTINHALT: VOLLBILD-TABELLE / KARTENLISTE */}
      <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-4 space-y-6">
        {items.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-12 text-center text-on-surface-variant max-w-md mx-auto">
            <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-surface-container border border-outline-variant/30 mb-4 text-on-surface-variant/60">
              <KeyRound className="h-8 w-8" />
            </div>
            <h3 className="text-base font-semibold text-on-surface">Noch keine Passwörter angelegt</h3>
            <p className="text-xs text-on-surface-variant mt-1.5 mb-5">
              Erstelle deinen ersten verschlüsselten Zugang mit sicherem Passwort und optionalem 2FA-Code.
            </p>
            <Button onClick={openNewEntryModal} className="bg-primary text-on-primary">
              <Plus className="h-4 w-4 mr-1.5" />
              Erstes Passwort anlegen
            </Button>
          </div>
        ) : searchedItems.length === 0 ? (
          <div className="p-12 text-center text-xs text-on-surface-variant">
            Keine Passwörter gefunden für „{searchQuery}“.
          </div>
        ) : (
          <>
            {/* ABSCHNITT 1: ⭐ FAVORITEN */}
            {favoriteItems.length > 0 && (
              <div className="space-y-2.5">
                <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-amber-400">
                  <Star className="h-3.5 w-3.5 fill-current" />
                  <span>Favoriten ({favoriteItems.length})</span>
                </div>
                <div className="grid grid-cols-1 gap-2.5">
                  {favoriteItems.map(renderItemRow)}
                </div>
              </div>
            )}

            {/* ABSCHNITT 2: 🕒 ZULETZT VERWENDET */}
            {recentItems.length > 0 && (
              <div className="space-y-2.5">
                <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-primary">
                  <Clock className="h-3.5 w-3.5" />
                  <span>Zuletzt verwendet ({recentItems.length})</span>
                </div>
                <div className="grid grid-cols-1 gap-2.5">
                  {recentItems.map(renderItemRow)}
                </div>
              </div>
            )}

            {/* ABSCHNITT 3: ALLE ANDEREN PASSWÖRTER */}
            {otherItems.length > 0 && (
              <div className="space-y-2.5">
                <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                  <KeyRound className="h-3.5 w-3.5" />
                  <span>Alle Zugänge ({otherItems.length})</span>
                </div>
                <div className="grid grid-cols-1 gap-2.5">
                  {otherItems.map(renderItemRow)}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* ── 4. MODAL: NEUES PASSWORT / EINTRAG BEARBEITEN ── */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs">
          <div className="relative w-full max-w-lg rounded-2xl bg-surface-container border border-outline-variant/30 shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-outline-variant/20 bg-surface-container-low">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface border border-outline-variant/20 p-1.5">
                  <ModalBrandIcon className="w-5 h-5" />
                </div>
                <h3 className="text-base font-semibold text-on-surface">
                  {editingItemId ? 'Passwort bearbeiten' : 'Neues Passwort anlegen'}
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setIsModalOpen(false)}
                className="rounded-lg p-1.5 text-on-surface-variant hover:bg-surface hover:text-on-surface transition-colors"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {/* Formular */}
            <form onSubmit={handleModalSave} className="p-6 space-y-4 max-h-[80vh] overflow-y-auto">
              {/* DIENSTNAME */}
              <div>
                <label className="block text-xs font-medium text-on-surface mb-1">
                  Dienstname / Website <span className="text-status-error">*</span>
                </label>
                <input
                  type="text"
                  value={modalService}
                  onChange={(e) => setModalService(e.target.value)}
                  placeholder="z. B. Google, Discord, Steam, GitHub..."
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3.5 py-2 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary"
                  autoFocus
                  required
                />
              </div>

              {/* BENUTZERNAME / E-MAIL */}
              <div>
                <label className="block text-xs font-medium text-on-surface mb-1">
                  Benutzername / E-Mail
                </label>
                <input
                  type="text"
                  value={modalUsername}
                  onChange={(e) => setModalUsername(e.target.value)}
                  placeholder="name@domain.de"
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3.5 py-2 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary"
                />
              </div>

              {/* PASSWORT MIT GENERATOR & LEAK-CHECK */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-xs font-medium text-on-surface">
                    Passwort <span className="text-status-error">*</span>
                  </label>
                  <button
                    type="button"
                    onClick={() => {
                      const newP = generateSecurePassword(20, true)
                      setModalPassword(newP)
                      void runLeakCheck(newP)
                      toast.success('Neues Passwort generiert')
                    }}
                    className="text-xs text-primary hover:underline flex items-center gap-1 font-medium"
                  >
                    <Zap className="h-3 w-3" />
                    Neu generieren
                  </button>
                </div>

                <div className="relative">
                  <input
                    type={showModalPassword ? 'text' : 'password'}
                    value={modalPassword}
                    onChange={(e) => {
                      setModalPassword(e.target.value)
                      void runLeakCheck(e.target.value)
                    }}
                    placeholder="Sicheres Passwort..."
                    className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3.5 py-2 text-xs text-on-surface font-mono placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary pr-10"
                    required
                  />
                  <button
                    type="button"
                    onClick={() => setShowModalPassword(!showModalPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface"
                  >
                    {showModalPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>

                {/* Leak-Check Feedback */}
                {isCheckingLeak && (
                  <div className="mt-1 flex items-center gap-1 text-[11px] text-on-surface-variant">
                    <RefreshCw className="h-3 w-3 animate-spin text-primary" />
                    <span>Prüfe Passwörter auf bekannte Leaks...</span>
                  </div>
                )}
                {leakCheckResult && leakCheckResult.checked && (
                  <div className="mt-1.5">
                    {leakCheckResult.isLeaked ? (
                      <div className="flex items-center gap-1.5 rounded-lg bg-status-error/15 border border-status-error/30 px-2.5 py-1 text-xs text-status-error">
                        <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
                        <span>In {leakCheckResult.count.toLocaleString()} Datenlecks gefunden!</span>
                      </div>
                    ) : (
                      <div className="flex items-center gap-1.5 rounded-lg bg-status-success/15 border border-status-success/30 px-2.5 py-1 text-xs text-status-success">
                        <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
                        <span>Keine bekannten Datenlecks</span>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* 2FA AUTHENTICATOR (OPTIONAL) */}
              <div className="p-3.5 rounded-xl bg-surface-container-low border border-outline-variant/20 space-y-2">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-medium text-on-surface flex items-center gap-1.5">
                    <KeyRound className="h-3.5 w-3.5 text-emerald-400" />
                    <span>2FA Authenticator Schlüssel (optional)</span>
                  </label>
                  <button
                    type="button"
                    onClick={() => setShowQrScanner(true)}
                    className="text-xs text-primary hover:underline flex items-center gap-1 font-medium"
                  >
                    <QrCode className="h-3.5 w-3.5" />
                    <span>QR-Code scannen</span>
                  </button>
                </div>
                <input
                  type="text"
                  value={modalTotpSecret}
                  onChange={(e) => setModalTotpSecret(e.target.value.toUpperCase())}
                  placeholder="Base32-Schlüssel (z. B. JBSWY3DPEHPK3PXP)"
                  className="w-full rounded-lg bg-surface border border-outline-variant/20 px-3 py-1.5 text-xs text-on-surface font-mono placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary"
                />
              </div>

              {/* WEBSITE URL (OPTIONAL) */}
              <div>
                <label className="block text-xs font-medium text-on-surface mb-1">
                  Website / URL (optional)
                </label>
                <input
                  type="text"
                  value={modalUrl}
                  onChange={(e) => setModalUrl(e.target.value)}
                  placeholder="https://..."
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3.5 py-2 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary"
                />
              </div>

              {/* NOTIZEN (OPTIONAL) */}
              <div>
                <label className="block text-xs font-medium text-on-surface mb-1">
                  Sichere Notizen (optional)
                </label>
                <textarea
                  rows={3}
                  value={modalNotes}
                  onChange={(e) => setModalNotes(e.target.value)}
                  placeholder="Backup-Codes, Sicherheitsfragen..."
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 p-3 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary resize-y"
                />
              </div>

              {/* ACTIONS */}
              <div className="pt-2 flex items-center justify-between gap-3">
                {editingItemId ? (
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => {
                      const item = items.find((i) => i.id === editingItemId)
                      if (item) void handleDeleteItem(item)
                    }}
                    className="text-status-error hover:bg-status-error/10 text-xs px-3"
                  >
                    <Trash2 className="h-3.5 w-3.5 mr-1" />
                    Löschen
                  </Button>
                ) : (
                  <div />
                )}

                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setIsModalOpen(false)}
                    className="text-xs text-on-surface-variant"
                  >
                    Abbrechen
                  </Button>
                  <Button
                    type="submit"
                    className="bg-primary text-on-primary hover:bg-primary-hover text-xs px-4 py-2"
                  >
                    Sicher speichern
                  </Button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* QR-CODE SCANNER MODAL */}
      <QrScannerModal
        isOpen={showQrScanner}
        onClose={() => setShowQrScanner(false)}
        onDetected={handleQrDetected}
      />
    </div>
  )
}
