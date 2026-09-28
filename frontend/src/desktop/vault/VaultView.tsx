import React, { useState, useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
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
  Mail,
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
import { Button, Checkbox } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { getBrandIcon } from './brandCatalog'
import { generateTotpCode, getTotpSecondsRemaining } from './totpEngine'
import { MASTER_PASSWORT_MINDESTLAENGE, generateSecurePassword } from './vaultCrypto'
import { createDebouncedLeakChecker, type LeakCheckResult } from './leakChecker'
import { QrScannerModal } from './QrScannerModal'
import { setzeTresorSchutz } from '../tauri'
import { useVaultStore, getLocalVaultSalt, type VaultItem } from './vaultStore'
import { DisBadge } from '@/components/DisBadge'

export function VaultView() {
  const { t } = useTranslation()

  const {
    isInitialized,
    isUnlocked,
    isUnlocking,
    unlockError,
    isBiometricsEnabled,
    isBiometricsSupported,
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
    fetchVaultSalt,
  } = useVaultStore()

  // Beim Laden Server-Status und Salt nur prüfen, falls lokal noch kein Salt vorliegt (Leck-2-Schutz)
  useEffect(() => {
    if (!getLocalVaultSalt()) {
      void fetchVaultSalt()
    }
  }, [fetchVaultSalt])

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

  // Die Sperre beim Fensterwechsel meldet `DesktopApp` einmal für die ganze
  // App an (`useAutoSperre`). Hier stand bis 09/2026 dieselbe Anmeldung ein
  // zweites Mal — sie tat nichts, was die andere nicht auch tat, und wäre bei
  // der nächsten Änderung die Fassung gewesen, die jemand vergisst.

  const hasHint = useVaultStore((s) => s.hasHint)
  const checkHintStatus = useVaultStore((s) => s.checkHintStatus)

  // Ersteinrichtung
  const [skipHintSetup, setSkipHintSetup] = useState(false)

  // Nachträglicher Hinweis-Modal & Banner
  const [isHintModalOpen, setIsHintModalOpen] = useState(false)
  const [editHintInput, setEditHintInput] = useState('')
  const [isSavingHint, setIsSavingHint] = useState(false)
  const [dismissedHintReminder, setDismissedHintReminder] = useState(false)

  // UI-Zustände für Sperre & Ersteinrichtung
  const [isSetupMode, setIsSetupMode] = useState(!isInitialized)

  useEffect(() => {
    if (isInitialized) {
      setIsSetupMode(false)
    }
  }, [isInitialized])
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
      toast.success(t('mss.vault.kopiert'))
      setTimeout(() => setCopiedIdField(null), 1500)

      if (itemId) {
        void markUsed(itemId)
      }
    } catch {
      toast.error(t('mss.vault.kopierenFehlgeschlagen'))
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

  // Gedebounceter Leak-Check im Modal (mind. 400ms & >=6 Zeichen, SEC-10)
  const debouncedLeakCheck = useMemo(() => {
    return createDebouncedLeakChecker((res) => {
      setLeakCheckResult(res)
    }, 400)
  }, [])

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
    debouncedLeakCheck(newPwd)
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
      debouncedLeakCheck(item.password)
    }
  }

  // Speichern im Modal
  const handleModalSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!modalService.trim()) {
      toast.error(t('mss.vault.dienstnamePflicht'))
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
      toast.success(t('mss.vault.gespeichert'))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('mss.vault.speichernFehlgeschlagen'))
    }
  }

  // Löschen eines Eintrags
  const handleDeleteItem = async (item: VaultItem) => {
    const ok = await confirm({
      title: t('mss.vault.loeschenTitel'),
      message: t('mss.vault.loeschenFrage', { name: item.service }),
      confirmText: t('common.delete'),
      cancelText: t('common.cancel'),
      danger: true,
    })
    if (!ok) return
    try {
      await deleteItem(item.id)
      if (isModalOpen && editingItemId === item.id) {
        setIsModalOpen(false)
      }
      toast.success(t('mss.vault.geloescht'))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('mss.vault.loeschenFehlgeschlagen'))
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
    toast.success(t('mss.vault.codeUebernommen'))
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

  // ── 1. ERSTEINRICHTUNG (NUR wenn Ersteinrichtungs-Modus aktiv) ──
  if (!isUnlocked && isSetupMode) {
    const canSubmitSetup =
      masterPasswordInput.length >= MASTER_PASSWORT_MINDESTLAENGE &&
      masterPasswordInput === confirmPasswordInput &&
      (hintInput.trim().length > 0 || skipHintSetup) &&
      !isUnlocking

    return (
      <div className="flex h-full w-full items-center justify-center p-4 bg-surface">
        <div className="w-full max-w-sm p-6 space-y-5 rounded-2xl bg-surface-container border border-outline-variant/30 shadow-xl">
          <div className="text-center space-y-1.5">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary border border-primary/20">
              <KeyRound className="h-6 w-6" />
            </div>
            <h2 className="text-base font-bold text-on-surface">
              {t('mss.vault.einrichtenTitel')}
            </h2>
            <div className="flex justify-center pt-0.5">
              <DisBadge size={16} />
            </div>
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
                setSkipHintSetup(false)
                setIsSetupMode(false)
                toast.success(t('mss.vault.eingerichtet'))
              }
            }}
            className="space-y-3.5"
          >
            <div>
              <label className="block text-label-sm font-medium text-on-surface-variant mb-1">
                {t('mss.vault.neuesMasterPasswort')}
              </label>
              <div className="relative">
                <input
                  type={showMasterPassword ? 'text' : 'password'}
                  value={masterPasswordInput}
                  onChange={(e) => setMasterPasswordInput(e.target.value)}
                  placeholder={t('mss.vault.mindestlaenge', { anzahl: MASTER_PASSWORT_MINDESTLAENGE })}
                  className="msm-input pr-9 [&::-ms-reveal]:hidden [&::-ms-clear]:hidden [&::-webkit-credentials-auto-fill-button]:hidden"
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
              <label className="block text-label-sm font-medium text-on-surface-variant mb-1">
                {t('mss.vault.passwortWiederholen')}
              </label>
              <div className="relative">
                <input
                  type={showConfirmPassword ? 'text' : 'password'}
                  value={confirmPasswordInput}
                  onChange={(e) => setConfirmPasswordInput(e.target.value)}
                  placeholder={t('mss.vault.erneutEingeben')}
                  className="msm-input pr-9 [&::-ms-reveal]:hidden [&::-ms-clear]:hidden [&::-webkit-credentials-auto-fill-button]:hidden"
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
                <div className="mt-1 text-label-sm">
                  {masterPasswordInput === confirmPasswordInput ? (
                    <span className="flex items-center gap-1 text-status-success">
                      <Check className="h-3 w-3" /> {t('mss.vault.stimmtUeberein')}
                    </span>
                  ) : (
                    <span className="text-status-destructive">{t('mss.vault.stimmtNichtUeberein')}</span>
                  )}
                </div>
              )}
            </div>

            {/* Passwort-Hinweis (Pflicht / Optionale Ablehnung) */}
            <div className="space-y-1.5 pt-1 border-t border-outline-variant/20">
              <div className="flex items-center justify-between">
                <label className="block text-label-sm font-medium text-on-surface">
                  Passwort-Hinweis {!skipHintSetup && <span className="text-primary font-bold">*</span>}
                </label>
                <span className="text-label-sm text-on-surface-variant">
                  Wird bei Verlust per E-Mail gesendet
                </span>
              </div>
              <input
                type="text"
                disabled={skipHintSetup}
                value={skipHintSetup ? '' : hintInput}
                onChange={(e) => setHintInput(e.target.value)}
                placeholder={skipHintSetup ? t('mss.vault.hinweisAbgelehnt') : t('mss.vault.hinweisPlatzhalter')}
                className="msm-input disabled:opacity-50"
              />

              <label className="flex items-center gap-2 cursor-pointer pt-0.5 text-label-sm text-on-surface-variant hover:text-on-surface">
                <Checkbox
                  checked={skipHintSetup}
                  onCheckedChange={(gesetzt) => {
                    setSkipHintSetup(gesetzt)
                    if (gesetzt) setHintInput('')
                  }}
                />
                <span>{t('mss.vault.ohneHinweisFortfahren')}</span>
              </label>
            </div>

            {unlockError && (
              <div className="rounded-xl bg-status-destructive/15 border border-status-destructive/30 p-2.5 text-xs text-status-destructive">
                {unlockError}
              </div>
            )}

            <Button
              type="submit"
              disabled={!canSubmitSetup}
              className="w-full bg-primary text-on-primary hover:bg-primary-hover py-2 text-xs font-medium"
            >
              {isUnlocking
                ? t('mss.vault.richteEin')
                : !hintInput.trim() && !skipHintSetup && masterPasswordInput.length >= MASTER_PASSWORT_MINDESTLAENGE && masterPasswordInput === confirmPasswordInput
                  ? t('mss.vault.hinweisNoetig')
                  : 'Einrichten'}
            </Button>
          </form>

          <div className="text-center pt-1">
            <button
              type="button"
              onClick={() => {
                setIsSetupMode(false)
                setMasterPasswordInput('')
                setConfirmPasswordInput('')
              }}
              className="text-xs text-primary hover:underline"
            >
              {t('mss.vault.bereitsEingerichtet')}
            </button>
          </div>
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
              {t('mss.vault.titelManager')}
            </h2>
            <div className="flex justify-center pt-0.5">
              <DisBadge size={16} />
            </div>
          </div>

          <form
            onSubmit={async (e) => {
              e.preventDefault()
              if (!masterPasswordInput || isUnlocking) return
              const ok = await unlock(masterPasswordInput)
              if (ok) {
                setMasterPasswordInput('')
                toast.success(t('mss.vault.entsperrt'))
              }
            }}
            className="space-y-3.5"
          >
            {isBiometricsEnabled && isBiometricsSupported && (
              <div className="space-y-2 pb-1">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={async () => {
                    const ok = await unlockWithBiometrics()
                    if (ok) {
                      toast.success(t('mss.vault.entsperrtBiometrie'))
                    }
                  }}
                  disabled={isUnlocking}
                  className="w-full flex items-center justify-center gap-2 py-2 text-xs border border-primary/30 bg-primary/10 text-primary hover:bg-primary/20"
                >
                  <Fingerprint className="h-4 w-4" />
                  <span>{t('mss.vault.mitFingerabdruck')}</span>
                </Button>
                <div className="relative flex items-center justify-center">
                  <div className="border-t border-outline-variant/30 w-full" />
                  <span className="bg-surface-container px-2 text-label-sm text-on-surface-variant uppercase tracking-wider absolute">
                    {t('mss.vault.oderMasterPasswort')}
                  </span>
                </div>
              </div>
            )}

            <div className="relative">
              <input
                type={showMasterPassword ? 'text' : 'password'}
                value={masterPasswordInput}
                onChange={(e) => setMasterPasswordInput(e.target.value)}
                placeholder={t('mss.vault.masterPasswort')}
                className="msm-input pr-9 [&::-ms-reveal]:hidden [&::-ms-clear]:hidden [&::-webkit-credentials-auto-fill-button]:hidden"
                autoFocus={!(isBiometricsEnabled && isBiometricsSupported)}
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
              <div className="rounded-xl bg-status-destructive/15 border border-status-destructive/30 p-2.5 text-xs text-status-destructive">
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
                  <span>{t('mss.vault.entschluessle')}</span>
                </>
              ) : (
                <>
                  <Unlock className="h-3.5 w-3.5" />
                  <span>{t('mss.vault.entsperren')}</span>
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
              <span>{isRequestingHint ? t('mss.vault.sendeMail') : t('mss.vault.hinweisPerMail')}</span>
            </button>

            {!isInitialized && (
              <div>
                <button
                  type="button"
                  onClick={() => {
                    setIsSetupMode(true)
                    setMasterPasswordInput('')
                  }}
                  className="text-xs text-on-surface-variant/70 hover:text-primary hover:underline transition-colors"
                >
                  {t('mss.vault.neuenTresorEinrichten')}
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
        className="group flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 p-3 rounded-xl bg-surface-container hover:bg-surface-container-high border border-outline-variant/20 transition-all shadow-sm"
      >
        {/* Logo, Dienst, Benutzer */}
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <div className="flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-xl bg-surface border border-outline-variant/20 p-1.5 shadow-sm">
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
              <span className="text-label-sm text-on-surface-variant truncate font-mono">
                {item.username || '—'}
              </span>
              {item.username && (
                <button
                  type="button"
                  onClick={() => void handleCopy(item.username, `user-${item.id}`, item.id)}
                  className="text-on-surface-variant hover:text-on-surface p-0.5 rounded transition-colors"
                  title={t('mss.vault.benutzernameKopieren')}
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
                title={t('mss.vault.passwortKopieren')}
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
            itemTotp === 'FEHLER' ? (
              <div className="flex items-center rounded-lg bg-status-destructive/10 border border-status-destructive/20 px-2 py-0.5 gap-1 font-mono text-xs">
                <span className="text-label-sm text-status-destructive font-semibold">2FA</span>
                <span className="text-status-destructive font-medium text-label-sm">{t('mss.vault.ungueltigesSecret')}</span>
              </div>
            ) : (
              <div className="flex items-center rounded-lg bg-status-success/10 border border-status-success/20 px-2 py-0.5 gap-1 font-mono text-xs">
                <span className="text-label-sm text-status-success font-semibold">2FA</span>
                <span className="text-status-success font-bold tracking-wider">
                  {itemTotp.length === 6 ? `${itemTotp.slice(0, 3)} ${itemTotp.slice(3)}` : itemTotp}
                </span>
                <span className="text-label-sm text-status-success/70">({totpRemaining}s)</span>
                <button
                  type="button"
                  onClick={() => void handleCopy(itemTotp, `totp-${item.id}`, item.id)}
                  className="text-status-success hover:text-status-success/80 p-0.5 transition-colors"
                  title={t('mss.vault.codeKopieren')}
                >
                  {copiedIdField === `totp-${item.id}` ? (
                    <Check className="h-3.5 w-3.5 text-status-success" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                </button>
              </div>
            )
          )}

          {/* Favorit & Edit */}
          <div className="flex items-center gap-0.5 border-l border-outline-variant/20 pl-1.5">
            <button
              type="button"
              onClick={() => void toggleFavorite(item.id)}
              className={`p-1 rounded transition-colors ${
                item.isFavorite
                  ? 'text-status-warning hover:text-status-warning/80'
                  : 'text-on-surface-variant hover:text-status-warning'
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
              <h1 className="text-xs font-bold text-on-surface">{t('mss.vault.titelManager')}</h1>
              <DisBadge size={14} className="hidden sm:inline-flex py-0.5 px-2" />
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          <Button
            onClick={openNewEntryModal}
            className="flex items-center gap-1 bg-primary text-on-primary hover:bg-primary-hover shadow-sm px-2.5 py-1.5 text-xs font-medium"
          >
            <Plus className="h-3.5 w-3.5" />
            <span>{t('mss.vault.neuerEintrag')}</span>
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
            onClick={() => {
              setEditHintInput('')
              setIsHintModalOpen(true)
              void checkHintStatus()
            }}
            title={t('mss.vault.hinweisVerwalten')}
            className={`p-1.5 ${hasHint === false ? 'text-status-warning hover:text-status-warning/80' : 'text-on-surface-variant hover:text-on-surface'}`}
          >
            <KeyRound className="h-3.5 w-3.5" />
          </Button>

          <Button
            size="sm"
            variant="ghost"
            onClick={lock}
            title="Sperren"
            className="text-on-surface-variant hover:text-status-destructive p-1.5"
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
            placeholder={t('common.search')}
            className="msm-input pl-8 pr-3"
          />
        </div>
      </div>

      {/* HINWEIS-ERINNERUNG: Wenn nach dem Entsperren noch kein Hinweis hinterlegt ist */}
      {hasHint === false && !dismissedHintReminder && (
        <div className="mx-4 mt-2.5 p-3 rounded-2xl bg-status-warning/10 border border-status-warning/30 text-on-surface shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-xl bg-status-warning/20 text-status-warning">
                <KeyRound className="h-4 w-4" />
              </div>
              <div>
                <h3 className="text-xs font-bold text-on-surface">{t('mss.vault.keinHinweisHinterlegt')}</h3>
                <p className="text-label-sm text-on-surface-variant mt-0.5">
                  {t('mss.vault.hinweisErklaerungKurz')}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setDismissedHintReminder(true)}
              className="text-on-surface-variant hover:text-on-surface p-1 rounded-lg"
              title={t('common.close')}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>

          <form
            onSubmit={async (e) => {
              e.preventDefault()
              if (!editHintInput.trim() || isSavingHint) return
              setIsSavingHint(true)
              try {
                await saveHint(editHintInput.trim())
                toast.success(t('mss.vault.hinweisHinterlegt'))
                setEditHintInput('')
              } catch {
                toast.error(t('mss.vault.hinweisSpeichernFehlgeschlagen'))
              } finally {
                setIsSavingHint(false)
              }
            }}
            className="mt-2.5 flex items-center gap-2"
          >
            <input
              type="text"
              value={editHintInput}
              onChange={(e) => setEditHintInput(e.target.value)}
              placeholder={t('mss.vault.hinweisPlatzhalterLang')}
              className="msm-input flex-1"
            />
            <Button
              type="submit"
              disabled={!editHintInput.trim() || isSavingHint}
              size="sm"
              className="bg-status-warning hover:bg-status-warning/90 text-black font-semibold text-xs px-3 py-1.5 shrink-0"
            >
              {isSavingHint ? t('common.saving') : t('mss.vault.hinweisSpeichern')}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setDismissedHintReminder(true)}
              className="text-xs text-on-surface-variant hover:text-on-surface px-2.5 py-1.5 shrink-0"
            >
              {t('mss.vault.spaeter')}
            </Button>
          </form>
        </div>
      )}

      {/* LISTE / TABELLE */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {items.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-8 text-center text-on-surface-variant max-w-xs mx-auto">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-surface-container border border-outline-variant/30 mb-3 text-on-surface-variant/60">
              <KeyRound className="h-6 w-6" />
            </div>
            <h3 className="text-xs font-semibold text-on-surface mb-3">{t('mss.vault.leer')}</h3>
            <Button onClick={openNewEntryModal} className="bg-primary text-on-primary text-xs py-1.5 px-3">
              <Plus className="h-3.5 w-3.5 mr-1" />
              {t('mss.vault.passwortAnlegen')}
            </Button>
          </div>
        ) : searchedItems.length === 0 ? (
          <div className="p-8 text-center text-xs text-on-surface-variant">
            {t('mss.vault.keineTreffer')}
          </div>
        ) : (
          <>
            {/* FAVORITEN */}
            {favoriteItems.length > 0 && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-1 text-label-sm font-semibold text-status-warning">
                  <Star className="h-3 w-3 fill-current" />
                  <span>{t('mss.vault.favoriten')}</span>
                </div>
                <div className="grid grid-cols-1 gap-1.5">
                  {favoriteItems.map(renderItemRow)}
                </div>
              </div>
            )}

            {/* ZULETZT VERWENDET */}
            {recentItems.length > 0 && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-1 text-label-sm font-semibold text-primary">
                  <Clock className="h-3 w-3" />
                  <span>{t('mss.vault.zuletztVerwendet')}</span>
                </div>
                <div className="grid grid-cols-1 gap-1.5">
                  {recentItems.map(renderItemRow)}
                </div>
              </div>
            )}

            {/* ALLE ZUGÄNGE */}
            {otherItems.length > 0 && (
              <div className="space-y-1.5">
                <div className="text-label-sm font-semibold text-on-surface-variant">
                  {t('mss.vault.alle')}
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
        <div className="msm-modal-overlay">
          <div className="relative w-full max-w-md rounded-2xl bg-surface-container border border-outline-variant/30 shadow-2xl overflow-hidden animate-scale-in">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-outline-variant/20 bg-surface-container-low">
              <div className="flex items-center gap-2.5">
                <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-surface border border-outline-variant/20 p-1">
                  <ModalBrandIcon className="w-4 h-4" />
                </div>
                <h3 className="text-xs font-semibold text-on-surface">
                  {editingItemId ? t('common.edit') : t('mss.vault.neuerEintrag')}
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
                <label className="block text-label-sm font-medium text-on-surface mb-1">
                  {t('mss.vault.dienstBezeichnung')}
                </label>
                <input
                  type="text"
                  value={modalService}
                  onChange={(e) => setModalService(e.target.value)}
                  placeholder={t('mss.vault.dienstPlatzhalter')}
                  className="msm-input"
                  autoFocus
                  required
                />
              </div>

              {/* Benutzername */}
              <div>
                <label className="block text-label-sm font-medium text-on-surface mb-1">
                  {t('mss.vault.benutzernameBezeichnung')}
                </label>
                <input
                  type="text"
                  value={modalUsername}
                  onChange={(e) => setModalUsername(e.target.value)}
                  placeholder={t('mss.vault.benutzernamePlatzhalter')}
                  className="msm-input"
                />
              </div>

              {/* Passwort */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-label-sm font-medium text-on-surface">
                    {t('mss.vault.passwort')}
                  </label>
                  <button
                    type="button"
                    onClick={() => {
                      const newP = generateSecurePassword(20, true)
                      setModalPassword(newP)
                      debouncedLeakCheck(newP)
                    }}
                    className="text-label-sm text-primary hover:underline flex items-center gap-0.5"
                  >
                    <Zap className="h-3 w-3" />
                    {t('mss.vault.generieren')}
                  </button>
                </div>

                <div className="relative">
                  <input
                    type={showModalPassword ? 'text' : 'password'}
                    value={modalPassword}
                    onChange={(e) => {
                      setModalPassword(e.target.value)
                      debouncedLeakCheck(e.target.value)
                    }}
                    placeholder={t('mss.vault.passwort')}
                    className="msm-input font-mono pr-9 [&::-ms-reveal]:hidden [&::-ms-clear]:hidden [&::-webkit-credentials-auto-fill-button]:hidden"
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
                      <span className="flex items-center gap-1 text-label-sm text-status-destructive">
                        <ShieldAlert className="h-3 w-3" /> In {leakCheckResult.count.toLocaleString()} Datenlecks gefunden!
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-label-sm text-status-success">
                        <ShieldCheck className="h-3 w-3" /> Sicher
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* 2FA Schlüssel */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-label-sm font-medium text-on-surface">
                    {t('mss.vault.zweifaktorBezeichnung')}
                  </label>
                  <button
                    type="button"
                    onClick={() => setShowQrScanner(true)}
                    className="text-label-sm text-primary hover:underline flex items-center gap-0.5"
                  >
                    <QrCode className="h-3 w-3" />
                    {t('mss.vault.qr.scannen')}
                  </button>
                </div>
                <input
                  type="text"
                  value={modalTotpSecret}
                  onChange={(e) => setModalTotpSecret(e.target.value.toUpperCase())}
                  placeholder={t('mss.vault.zweifaktorPlatzhalter')}
                  className="msm-input font-mono"
                />
              </div>

              {/* Notizen */}
              <div>
                <label className="block text-label-sm font-medium text-on-surface mb-1">
                  {t('mss.vault.notizBezeichnung')}
                </label>
                <textarea
                  rows={2}
                  value={modalNotes}
                  onChange={(e) => setModalNotes(e.target.value)}
                  placeholder={t('mss.vault.notizPlatzhalter')}
                  className="msm-input resize-y"
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
                    className="text-status-destructive hover:bg-status-destructive/10 text-xs px-2 py-1"
                  >
                    <Trash2 className="h-3.5 w-3.5 mr-1" />
                    {t('common.delete')}
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
                    {t('common.cancel')}
                  </Button>
                  <Button
                    type="submit"
                    className="bg-primary text-on-primary hover:bg-primary-hover text-xs px-3 py-1.5"
                  >
                    {t('common.save')}
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

      {/* Modal: Passwort-Hinweis verwalten */}
      {isHintModalOpen && (
        <div className="msm-modal-overlay">
          <div className="w-full max-w-sm rounded-2xl bg-surface-container border border-outline-variant/30 p-5 space-y-4 shadow-xl">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary/10 text-primary">
                  <KeyRound className="h-4 w-4" />
                </div>
                <div>
                  <h3 className="text-xs font-bold text-on-surface">{t('mss.vault.hinweisTitel')}</h3>
                  <p className="text-label-sm text-on-surface-variant">
                    {hasHint ? t('mss.vault.hinweisVorhanden') : t('mss.vault.hinweisFehlt')}
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setIsHintModalOpen(false)}
                className="text-on-surface-variant hover:text-on-surface p-1 rounded-lg"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form
              onSubmit={async (e) => {
                e.preventDefault()
                if (!editHintInput.trim() || isSavingHint) return
                setIsSavingHint(true)
                try {
                  await saveHint(editHintInput.trim())
                  toast.success(t('mss.vault.hinweisGespeichert'))
                  setEditHintInput('')
                  setIsHintModalOpen(false)
                } catch {
                  toast.error(t('mss.vault.hinweisSpeichernFehlgeschlagen'))
                } finally {
                  setIsSavingHint(false)
                }
              }}
              className="space-y-3"
            >
              <div>
                <label className="block text-label-sm font-medium text-on-surface mb-1">
                  {hasHint ? t('mss.vault.hinweisAktualisieren') : t('mss.vault.hinweisAnlegen')}
                </label>
                <input
                  type="text"
                  value={editHintInput}
                  onChange={(e) => setEditHintInput(e.target.value)}
                  placeholder={t('mss.vault.hinweisPlatzhalterLang')}
                  className="msm-input"
                  autoFocus
                />
                <p className="text-label-sm text-on-surface-variant/80 mt-1 leading-relaxed">
                  {t('mss.vault.hinweisErklaerungLang')}
                </p>
              </div>

              <div className="flex items-center justify-between pt-1">
                {hasHint && (
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={isRequestingHint}
                    onClick={async () => {
                      setIsRequestingHint(true)
                      const res = await requestHintEmail()
                      setIsRequestingHint(false)
                      if (res.ok) {
                        toast.success(res.message)
                      } else {
                        toast.error(res.message)
                      }
                    }}
                    className="text-label-sm py-1.5 px-2.5 flex items-center gap-1.5"
                  >
                    <Mail className="h-3.5 w-3.5" />
                    <span>{isRequestingHint ? 'Sende...' : t('mss.vault.perMailTesten')}</span>
                  </Button>
                )}
                {!hasHint && <div />}

                <div className="flex items-center gap-1.5">
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setIsHintModalOpen(false)}
                    className="text-xs text-on-surface-variant px-2.5 py-1.5"
                  >
                    {t('common.close')}
                  </Button>
                  <Button
                    type="submit"
                    disabled={!editHintInput.trim() || isSavingHint}
                    className="bg-primary text-on-primary hover:bg-primary-hover text-xs px-3 py-1.5"
                  >
                    {isSavingHint ? t('common.saving') : t('common.save')}
                  </Button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
