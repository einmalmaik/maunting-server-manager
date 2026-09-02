import React, { useState, useEffect, useMemo } from 'react'
import {
  Check,
  Clock,
  Copy,
  Edit2,
  ExternalLink,
  Eye,
  EyeOff,
  Fingerprint,
  HelpCircle,
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
    isBiometricsEnabled,
    items,
    searchQuery,
    syncStatus,
    initializeVault,
    unlock,
    unlockWithBiometrics,
    lock,
    setSearchQuery,
    saveItem,
    deleteItem,
    toggleFavorite,
    markUsed,
    syncWithServer,
    saveHint,
    requestHintEmail,
    checkBiometricsSupport,
  } = useVaultStore()

  // Biometrie-Verfügbarkeit (Windows Hello / Fingerabdruck) beim Laden abfragen
  useEffect(() => {
    void checkBiometricsSupport()
  }, [checkBiometricsSupport])

  // Hardware- und Software-Schutz vor Windows Computer-Use KI-Screenshots
  useEffect(() => {
    void setzeTresorSchutz(isUnlocked)
    return () => {
      void setzeTresorSchutz(false)
    }
  }, [isUnlocked])

  // Sofortige Sperre beim Verlassen des Fensters (Minimieren, Alt+Tab, Klick auf anderes Fenster)
  useEffect(() => {
    if (!isUnlocked) return

    const handleBlurLock = () => {
      const state = useVaultStore.getState()
      if (state.lockOnWindowBlur && state.isUnlocked && !state.isUnlocking) {
        state.lock()
      }
    }

    window.addEventListener('blur', handleBlurLock)
    const handleVis = () => {
      if (document.hidden) handleBlurLock()
    }
    document.addEventListener('visibilitychange', handleVis)

    return () => {
      window.removeEventListener('blur', handleBlurLock)
      document.removeEventListener('visibilitychange', handleVis)
    }
  }, [isUnlocked])

  // UI-Zustände für Sperre & Ersteinrichtung
  const [isSetupMode, setIsSetupMode] = useState(!isInitialized)
  const [masterPasswordInput, setMasterPasswordInput] = useState('')
  const [confirmPasswordInput, setConfirmPasswordInput] = useState('')
  const [hintInput, setHintInput] = useState('')
  const [showMasterPassword, setShowMasterPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [isRequestingHint, setIsRequestingHint] = useState(false)

  // Feedback für kopierte Felder
  const [copiedIdField, setCopiedIdField] = useState<string | null>(null)
  const [revealedPasswordId, setRevealedPasswordId] = useState<string | null>(null)

  // Live TOTP Takt (Sekunden-Ticker für 2FA)
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

  // Kopieren mit sofortigem Feedback
  const handleCopy = async (text: string, fieldKey: string, itemId?: string) => {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopiedIdField(fieldKey)
      toast.success('Kopiert')
      setTimeout(() => setCopiedIdField(null), 1500)

      if (itemId) {
        void markUsed(itemId)
      }
    } catch {
      toast.error('Kopieren fehlgeschlagen')
    }
  }

  // Passwort kurz aufdecken
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
    try {
      const res = await checkPasswordLeak(pwd)
      setLeakCheckResult(res)
    } catch {
      setLeakCheckResult(null)
    }
  }

  // Speichern im Modal
  const handleModalSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!modalService.trim()) {
      toast.error('Bitte Dienstname angeben.')
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
      toast.success('Gespeichert')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Fehler beim Speichern')
    }
  }

  // Löschen eines Eintrags
  const handleDeleteItem = async (item: VaultItem) => {
    if (!window.confirm(`"${item.service}" löschen?`)) {
      return
    }
    try {
      await deleteItem(item.id)
      if (isModalOpen && editingItemId === item.id) {
        setIsModalOpen(false)
      }
      toast.success('Gelöscht')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Fehler beim Löschen')
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
    toast.success('2FA-Code übernommen')
  }

  // Hinweis per E-Mail anfordern (max. 1x alle 10 Minuten)
  const handleRequestHint = async () => {
    if (isRequestingHint) return
    setIsRequestingHint(true)
    try {
      const res = await requestHintEmail()
      if (res.ok) {
        toast.success(res.message)
      } else {
        toast.error(res.message)
      }
    } finally {
      setIsRequestingHint(false)
    }
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

  // 2. Zuletzt verwendet
  const recentItems = useMemo(() => {
    return searchedItems
      .filter((item) => !item.isFavorite && typeof item.lastUsedAt === 'number' && item.lastUsedAt > 0)
      .sort((a, b) => (b.lastUsedAt || 0) - (a.lastUsedAt || 0))
      .slice(0, 5)
  }, [searchedItems])

  // 3. Alle anderen Einträge
  const otherItems = useMemo(() => {
    const favoriteIds = new Set(favoriteItems.map((i) => i.id))
    const recentIds = new Set(recentItems.map((i) => i.id))
    return searchedItems
      .filter((item) => !favoriteIds.has(item.id) && !recentIds.has(item.id))
      .sort((a, b) => a.service.localeCompare(b.service))
  }, [searchedItems, favoriteItems, recentItems])

  const ModalBrandIcon = getBrandIcon(modalService, modalUrl)

  // ── 1. ERSTEINRICHTUNG (NUR wenn noch NIE eingerichtet) ──
  if (!isUnlocked && (!isInitialized || isSetupMode)) {
    const canSubmitSetup =
      masterPasswordInput.length >= 8 &&
      masterPasswordInput === confirmPasswordInput &&
      !isUnlocking

    return (
      <div className="flex h-full w-full items-center justify-center p-4 bg-surface">
        <div className="w-full max-w-sm p-6 space-y-5 rounded-2xl bg-surface-container border border-outline-variant/30 shadow-xl">
          <div className="text-center space-y-1.5">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary border border-primary/20">
              <KeyRound className="h-6 w-6" />
            </div>
            <h2 className="text-base font-bold text-on-surface">
              Passwort-Manager einrichten
            </h2>
          </div>

          <form
            onSubmit={async (e) => {
              e.preventDefault()
              if (!canSubmitSetup) return
              const ok = await initializeVault(masterPasswordInput)
              if (ok) {
                if (hintInput.trim()) {
                  void saveHint(hintInput)
                }
                setMasterPasswordInput('')
                setConfirmPasswordInput('')
                setHintInput('')
                setIsSetupMode(false)
                toast.success('Passwort-Manager eingerichtet')
              }
            }}
            className="space-y-3.5"
          >
            <div>
              <label className="block text-[11px] font-medium text-on-surface-variant mb-1">
                Neues Master-Passwort
              </label>
              <div className="relative">
                <input
                  type={showMasterPassword ? 'text' : 'password'}
                  value={masterPasswordInput}
                  onChange={(e) => setMasterPasswordInput(e.target.value)}
                  placeholder="Mindestens 8 Zeichen"
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3 py-2 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary pr-9 [&::-ms-reveal]:hidden [&::-ms-clear]:hidden [&::-webkit-credentials-auto-fill-button]:hidden"
                  autoFocus
                />
                <button
                  type="button"
                  onClick={() => setShowMasterPassword(!showMasterPassword)}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface"
                  tabIndex={-1}
                >
                  {showMasterPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            <div>
              <label className="block text-[11px] font-medium text-on-surface-variant mb-1">
                Passwort wiederholen
              </label>
              <div className="relative">
                <input
                  type={showConfirmPassword ? 'text' : 'password'}
                  value={confirmPasswordInput}
                  onChange={(e) => setConfirmPasswordInput(e.target.value)}
                  placeholder="Erneut eingeben"
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3 py-2 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary pr-9 [&::-ms-reveal]:hidden [&::-ms-clear]:hidden [&::-webkit-credentials-auto-fill-button]:hidden"
                />
                <button
                  type="button"
                  onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface"
                  tabIndex={-1}
                >
                  {showConfirmPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>

              {confirmPasswordInput.length > 0 && (
                <div className="mt-1 text-[11px]">
                  {masterPasswordInput === confirmPasswordInput ? (
                    <span className="flex items-center gap-1 text-status-success">
                      <Check className="h-3 w-3" /> Stimmt überein
                    </span>
                  ) : (
                    <span className="text-status-error">Stimmt nicht überein</span>
                  )}
                </div>
              )}
            </div>

            {/* Optionaler Passwort-Hinweis */}
            <div>
              <label className="block text-[11px] font-medium text-on-surface-variant mb-1">
                Passwort-Hinweis (optional)
              </label>
              <input
                type="text"
                value={hintInput}
                onChange={(e) => setHintInput(e.target.value)}
                placeholder="Erinnerungshilfe, z. B. erste Schule"
                className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3 py-2 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary"
              />
            </div>

            {unlockError && (
              <div className="rounded-xl bg-status-error/15 border border-status-error/30 p-2.5 text-xs text-status-error">
                {unlockError}
              </div>
            )}

            <Button
              type="submit"
              disabled={!canSubmitSetup}
              className="w-full bg-primary text-on-primary hover:bg-primary-hover py-2 text-xs"
            >
              {isUnlocking ? 'Richte ein...' : 'Einrichten'}
            </Button>
          </form>

          {isInitialized && (
            <div className="text-center">
              <button
                type="button"
                onClick={() => {
                  setIsSetupMode(false)
                  setMasterPasswordInput('')
                  setConfirmPasswordInput('')
                }}
                className="text-xs text-primary hover:underline"
              >
                Abbrechen
              </button>
            </div>
          )}
        </div>
      </div>
    )
  }

  // ── 2. ENTSPERREN (Minimaler, absolut aufgeräumter Standard-Zustand) ──
  if (!isUnlocked) {
    return (
      <div className="flex h-full w-full items-center justify-center p-4 bg-surface">
        <div className="w-full max-w-sm p-6 space-y-5 rounded-2xl bg-surface-container border border-outline-variant/30 shadow-xl">
          <div className="text-center space-y-1.5">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary border border-primary/20">
              <Lock className="h-6 w-6" />
            </div>
            <h2 className="text-base font-bold text-on-surface">
              Passwort-Manager
            </h2>
          </div>

          <form
            onSubmit={async (e) => {
              e.preventDefault()
              if (!masterPasswordInput || isUnlocking) return
              const ok = await unlock(masterPasswordInput)
              if (ok) {
                setMasterPasswordInput('')
                toast.success('Entsperrt')
              }
            }}
            className="space-y-3.5"
          >
            {isBiometricsEnabled && (
              <div className="space-y-2 pb-1">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={async () => {
                    const ok = await unlockWithBiometrics()
                    if (ok) {
                      toast.success('Per Biometrie entsperrt')
                    }
                  }}
                  disabled={isUnlocking}
                  className="w-full flex items-center justify-center gap-2 py-2 text-xs border border-primary/30 bg-primary/10 text-primary hover:bg-primary/20"
                >
                  <Fingerprint className="h-4 w-4" />
                  <span>Mit Windows Hello / Fingerabdruck</span>
                </Button>
                <div className="relative flex items-center justify-center">
                  <div className="border-t border-outline-variant/30 w-full" />
                  <span className="bg-surface-container px-2 text-[10px] text-on-surface-variant uppercase tracking-wider absolute">
                    Oder Master-Passwort
                  </span>
                </div>
              </div>
            )}

            <div className="relative">
              <input
                type={showMasterPassword ? 'text' : 'password'}
                value={masterPasswordInput}
                onChange={(e) => setMasterPasswordInput(e.target.value)}
                placeholder="Master-Passwort"
                className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3 py-2 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary pr-9 [&::-ms-reveal]:hidden [&::-ms-clear]:hidden [&::-webkit-credentials-auto-fill-button]:hidden"
                autoFocus={!isBiometricsEnabled}
              />
              <button
                type="button"
                onClick={() => setShowMasterPassword(!showMasterPassword)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface"
                tabIndex={-1}
              >
                {showMasterPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>

            {unlockError && (
              <div className="rounded-xl bg-status-error/15 border border-status-error/30 p-2.5 text-xs text-status-error">
                {unlockError}
              </div>
            )}

            <Button
              type="submit"
              disabled={isUnlocking || !masterPasswordInput}
              className="w-full bg-primary text-on-primary hover:bg-primary-hover py-2 flex items-center justify-center gap-1.5 text-xs"
            >
              {isUnlocking ? (
                <>
                  <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                  <span>Entschlüssle...</span>
                </>
              ) : (
                <>
                  <Unlock className="h-3.5 w-3.5" />
                  <span>Entsperren</span>
                </>
              )}
            </Button>
          </form>

          {/* Hinweis per E-Mail anfordern */}
          <div className="text-center space-y-2 pt-1">
            <button
              type="button"
              onClick={handleRequestHint}
              disabled={isRequestingHint}
              className="text-xs text-on-surface-variant hover:text-primary transition-colors flex items-center justify-center gap-1 mx-auto disabled:opacity-50"
            >
              <HelpCircle className="h-3.5 w-3.5" />
              <span>{isRequestingHint ? 'Sende E-Mail...' : 'Hinweis per E-Mail anfordern'}</span>
            </button>

            {/* Link zum Einrichten NUR WENN noch gar nicht eingerichtet */}
            {!isInitialized && (
              <div>
                <button
                  type="button"
                  onClick={() => {
                    setIsSetupMode(true)
                    setMasterPasswordInput('')
                  }}
                  className="text-xs text-primary hover:underline"
                >
                  Jetzt neu einrichten
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  // ── HILFSKOMPONENTE: ZEILE IN LISTE / TABELLE ──
  const renderItemRow = (item: VaultItem) => {
    const ItemBrand = getBrandIcon(item.service, item.url)
    const isRevealed = revealedPasswordId === item.id
    const itemTotp = totpCodes[item.id]

    return (
      <div
        key={item.id}
        className="group flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 p-3 rounded-xl bg-surface-container hover:bg-surface-container-high border border-outline-variant/20 transition-all shadow-xs"
      >
        {/* Logo, Dienst, Benutzer */}
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <div className="flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-xl bg-surface border border-outline-variant/20 p-1.5 shadow-xs">
            <ItemBrand className="w-5 h-5" />
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-semibold text-on-surface truncate">
                {item.service}
              </span>
              {item.url && (
                <a
                  href={item.url.startsWith('http') ? item.url : `https://${item.url}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-on-surface-variant hover:text-primary transition-colors"
                >
                  <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>

            <div className="flex items-center gap-1.5 mt-0.5">
              <span className="text-[11px] text-on-surface-variant truncate font-mono">
                {item.username || '—'}
              </span>
              {item.username && (
                <button
                  type="button"
                  onClick={() => void handleCopy(item.username, `user-${item.id}`, item.id)}
                  className="text-on-surface-variant hover:text-on-surface p-0.5 rounded transition-colors"
                  title="Benutzername kopieren"
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

        {/* Schnell-Aktionen (Passwort & 2FA) */}
        <div className="flex flex-wrap items-center gap-2 sm:justify-end">
          {item.password && (
            <div className="flex items-center rounded-lg bg-surface-container-low border border-outline-variant/20 px-2 py-0.5 gap-1 font-mono text-xs">
              <span className="text-on-surface select-none">
                {isRevealed ? item.password : '••••••••'}
              </span>
              <button
                type="button"
                onClick={() => handleToggleRevealPassword(item.id)}
                className="text-on-surface-variant hover:text-on-surface p-0.5 transition-colors"
              >
                {isRevealed ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
              <button
                type="button"
                onClick={() => void handleCopy(item.password, `pwd-${item.id}`, item.id)}
                className="text-primary hover:text-primary-hover p-0.5 transition-colors"
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

          {item.totpSecret && itemTotp && (
            <div className="flex items-center rounded-lg bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 gap-1 font-mono text-xs">
              <span className="text-[10px] text-emerald-400 font-semibold">2FA</span>
              <span className="text-emerald-400 font-bold tracking-wider">
                {itemTotp.slice(0, 3)} {itemTotp.slice(3)}
              </span>
              <span className="text-[10px] text-emerald-400/70">({totpRemaining}s)</span>
              <button
                type="button"
                onClick={() => void handleCopy(itemTotp, `totp-${item.id}`, item.id)}
                className="text-emerald-400 hover:text-emerald-300 p-0.5 transition-colors"
                title="Code kopieren"
              >
                {copiedIdField === `totp-${item.id}` ? (
                  <Check className="h-3.5 w-3.5 text-status-success" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
          )}

          {/* Favorit & Edit */}
          <div className="flex items-center gap-0.5 border-l border-outline-variant/20 pl-1.5">
            <button
              type="button"
              onClick={() => void toggleFavorite(item.id)}
              className={`p-1 rounded transition-colors ${
                item.isFavorite
                  ? 'text-amber-400 hover:text-amber-300'
                  : 'text-on-surface-variant hover:text-amber-400'
              }`}
            >
              <Star className={`h-3.5 w-3.5 ${item.isFavorite ? 'fill-current' : ''}`} />
            </button>

            <button
              type="button"
              onClick={() => openEditEntryModal(item)}
              className="p-1 rounded text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest transition-colors"
            >
              <Edit2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>
    )
  }

  // ── 3. HAUPTANSICHT: RADIKAL AUFGERÄUMT ──
  return (
    <div className="flex flex-col h-full bg-surface text-on-surface overflow-hidden">
      {/* KOPFZEILE */}
      <div className="flex items-center justify-between border-b border-outline-variant/20 bg-surface-container-low px-4 py-2.5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary/10 text-primary border border-primary/20">
            <Shield className="h-4 w-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xs font-bold text-on-surface">Passwort-Manager</h1>
              <span className="text-[10px] text-on-surface-variant">
                ({items.length})
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          <Button
            onClick={openNewEntryModal}
            className="flex items-center gap-1 bg-primary text-on-primary hover:bg-primary-hover shadow-xs px-2.5 py-1.5 text-xs font-medium"
          >
            <Plus className="h-3.5 w-3.5" />
            <span>Neues Passwort</span>
          </Button>

          <Button
            size="sm"
            variant="ghost"
            onClick={() => void syncWithServer()}
            title="Synchronisieren"
            className="text-on-surface-variant hover:text-on-surface p-1.5"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${syncStatus === 'syncing' ? 'animate-spin' : ''}`} />
          </Button>

          <Button
            size="sm"
            variant="ghost"
            onClick={lock}
            title="Sperren"
            className="text-on-surface-variant hover:text-status-error p-1.5"
          >
            <Lock className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* SUCH-LEISTE */}
      <div className="px-4 py-2 border-b border-outline-variant/15 bg-surface-container-low/40">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-on-surface-variant" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Suchen..."
            className="w-full rounded-xl bg-surface-container border border-outline-variant/30 pl-8 pr-3 py-1.5 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary"
          />
        </div>
      </div>

      {/* LISTE / TABELLE */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {items.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-8 text-center text-on-surface-variant max-w-xs mx-auto">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-surface-container border border-outline-variant/30 mb-3 text-on-surface-variant/60">
              <KeyRound className="h-6 w-6" />
            </div>
            <h3 className="text-xs font-semibold text-on-surface mb-3">Keine Passwörter hinterlegt</h3>
            <Button onClick={openNewEntryModal} className="bg-primary text-on-primary text-xs py-1.5 px-3">
              <Plus className="h-3.5 w-3.5 mr-1" />
              Passwort anlegen
            </Button>
          </div>
        ) : searchedItems.length === 0 ? (
          <div className="p-8 text-center text-xs text-on-surface-variant">
            Keine Treffer
          </div>
        ) : (
          <>
            {/* FAVORITEN */}
            {favoriteItems.length > 0 && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-1 text-[11px] font-semibold text-amber-400">
                  <Star className="h-3 w-3 fill-current" />
                  <span>Favoriten</span>
                </div>
                <div className="grid grid-cols-1 gap-1.5">
                  {favoriteItems.map(renderItemRow)}
                </div>
              </div>
            )}

            {/* ZULETZT VERWENDET */}
            {recentItems.length > 0 && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-1 text-[11px] font-semibold text-primary">
                  <Clock className="h-3 w-3" />
                  <span>Zuletzt verwendet</span>
                </div>
                <div className="grid grid-cols-1 gap-1.5">
                  {recentItems.map(renderItemRow)}
                </div>
              </div>
            )}

            {/* ALLE ZUGÄNGE */}
            {otherItems.length > 0 && (
              <div className="space-y-1.5">
                <div className="text-[11px] font-semibold text-on-surface-variant">
                  Alle
                </div>
                <div className="grid grid-cols-1 gap-1.5">
                  {otherItems.map(renderItemRow)}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* ── 4. MODAL: PASSWORT ANLEGEN / BEARBEITEN ── */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-3 bg-black/60 backdrop-blur-xs">
          <div className="relative w-full max-w-md rounded-2xl bg-surface-container border border-outline-variant/30 shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-outline-variant/20 bg-surface-container-low">
              <div className="flex items-center gap-2.5">
                <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-surface border border-outline-variant/20 p-1">
                  <ModalBrandIcon className="w-4 h-4" />
                </div>
                <h3 className="text-xs font-semibold text-on-surface">
                  {editingItemId ? 'Bearbeiten' : 'Neues Passwort'}
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setIsModalOpen(false)}
                className="rounded-lg p-1 text-on-surface-variant hover:bg-surface hover:text-on-surface"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Formular */}
            <form onSubmit={handleModalSave} className="p-4 space-y-3 max-h-[80vh] overflow-y-auto">
              {/* Dienstname */}
              <div>
                <label className="block text-[11px] font-medium text-on-surface mb-1">
                  Dienst / Website
                </label>
                <input
                  type="text"
                  value={modalService}
                  onChange={(e) => setModalService(e.target.value)}
                  placeholder="z. B. Google, Steam, Discord"
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3 py-1.5 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary"
                  autoFocus
                  required
                />
              </div>

              {/* Benutzername */}
              <div>
                <label className="block text-[11px] font-medium text-on-surface mb-1">
                  Benutzername / E-Mail
                </label>
                <input
                  type="text"
                  value={modalUsername}
                  onChange={(e) => setModalUsername(e.target.value)}
                  placeholder="name@domain.de"
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3 py-1.5 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary"
                />
              </div>

              {/* Passwort */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-[11px] font-medium text-on-surface">
                    Passwort
                  </label>
                  <button
                    type="button"
                    onClick={() => {
                      const newP = generateSecurePassword(20, true)
                      setModalPassword(newP)
                      void runLeakCheck(newP)
                    }}
                    className="text-[11px] text-primary hover:underline flex items-center gap-0.5"
                  >
                    <Zap className="h-3 w-3" />
                    Generieren
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
                    placeholder="Passwort"
                    className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3 py-1.5 text-xs text-on-surface font-mono placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary pr-9 [&::-ms-reveal]:hidden [&::-ms-clear]:hidden [&::-webkit-credentials-auto-fill-button]:hidden"
                    required
                  />
                  <button
                    type="button"
                    onClick={() => setShowModalPassword(!showModalPassword)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-on-surface"
                    tabIndex={-1}
                  >
                    {showModalPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>

                {leakCheckResult && leakCheckResult.checked && (
                  <div className="mt-1">
                    {leakCheckResult.isLeaked ? (
                      <span className="flex items-center gap-1 text-[11px] text-status-error">
                        <ShieldAlert className="h-3 w-3" /> In {leakCheckResult.count.toLocaleString()} Datenlecks gefunden!
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-[11px] text-status-success">
                        <ShieldCheck className="h-3 w-3" /> Sicher
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* 2FA Schlüssel */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-[11px] font-medium text-on-surface">
                    2FA-Schlüssel (optional)
                  </label>
                  <button
                    type="button"
                    onClick={() => setShowQrScanner(true)}
                    className="text-[11px] text-primary hover:underline flex items-center gap-0.5"
                  >
                    <QrCode className="h-3 w-3" />
                    QR-Code scannen
                  </button>
                </div>
                <input
                  type="text"
                  value={modalTotpSecret}
                  onChange={(e) => setModalTotpSecret(e.target.value.toUpperCase())}
                  placeholder="Base32-Schlüssel"
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3 py-1.5 text-xs text-on-surface font-mono placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary"
                />
              </div>

              {/* Notizen */}
              <div>
                <label className="block text-[11px] font-medium text-on-surface mb-1">
                  Notiz (optional)
                </label>
                <textarea
                  rows={2}
                  value={modalNotes}
                  onChange={(e) => setModalNotes(e.target.value)}
                  placeholder="Zusätzliche Infos..."
                  className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 p-2.5 text-xs text-on-surface placeholder:text-on-surface-variant/40 focus:outline-none focus:border-primary resize-y"
                />
              </div>

              {/* Aktionen */}
              <div className="pt-2 flex items-center justify-between">
                {editingItemId ? (
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => {
                      const item = items.find((i) => i.id === editingItemId)
                      if (item) void handleDeleteItem(item)
                    }}
                    className="text-status-error hover:bg-status-error/10 text-xs px-2 py-1"
                  >
                    <Trash2 className="h-3.5 w-3.5 mr-1" />
                    Löschen
                  </Button>
                ) : (
                  <div />
                )}

                <div className="flex items-center gap-1.5">
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setIsModalOpen(false)}
                    className="text-xs text-on-surface-variant px-2.5 py-1"
                  >
                    Abbrechen
                  </Button>
                  <Button
                    type="submit"
                    className="bg-primary text-on-primary hover:bg-primary-hover text-xs px-3 py-1.5"
                  >
                    Speichern
                  </Button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* QR-Code Scanner */}
      <QrScannerModal
        isOpen={showQrScanner}
        onClose={() => setShowQrScanner(false)}
        onDetected={handleQrDetected}
      />
    </div>
  )
}
