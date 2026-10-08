import React, { useState, useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Archive,
  ArchiveRestore,
  Check,
  Clock,
  Copy,
  Edit2,
  ExternalLink,
  Eye,
  EyeOff,
  File as FileIcon,
  Fingerprint,
  Folder,
  CreditCard,
  Landmark,
  FolderLock,
  Images,
  HelpCircle,
  KeyRound,
  Lock,
  Mail,
  Plus,
  QrCode,
  RefreshCw,
  RotateCcw,
  Search,
  SearchX,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Star,
  Trash2,
  Unlock,
  WifiOff,
  Zap,
  X,
} from 'lucide-react'
import { Button, Checkbox, Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, Input, Textarea, Zustandsflaeche } from '@/Singra/UI'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { getBrandIcon } from './brandCatalog'
import { generateTotpCode, getTotpSecondsRemaining } from './totpEngine'
import { MASTER_PASSWORT_MINDESTLAENGE, generateSecurePassword } from './vaultCrypto'
import { createDebouncedLeakChecker, type LeakCheckResult } from './leakChecker'
import { QrScannerModal } from './QrScannerModal'
import { TresorZuruecksetzen } from './TresorZuruecksetzen'
import { TresorDateiBereich } from './TresorDateiBereich'
import { TresorGalerie } from './TresorGalerie'
import { TresorZahlung } from './TresorZahlung'
import { verdeckt } from './zahlung'
import { inhaltVon } from './tresorOrdner'
import { useMiniatur } from './tresorMiniaturen'
import { formatBytes } from '@/lib/format'
import { setzeTresorSchutz } from '../tauri'
import { useShallow } from 'zustand/react/shallow'
import { getLocalVaultSalt, useVaultStore } from './vaultStore'
import { PAPIERKORB_TAGE, istBekannteKategorie, istPasswortKategorie, type VaultItem } from './vaultEintrag'
import { DisBadge } from '@/components/DisBadge'
import { fehlerText } from './tresorFehler'

type Ansicht = 'tresor' | 'fotos' | 'dateien' | 'zahlung' | 'archiv' | 'papierkorb'

const TAG_MS = 24 * 60 * 60 * 1000

/** Miniatur einer Datei im Archiv oder Papierkorb, sonst das Symbol ihrer Art. */
function ZeilenBild({ item, Symbol }: { item: VaultItem; Symbol: React.ComponentType<{ className?: string }> }) {
  const url = useMiniatur(item.datei?.miniatur, item.id, true)
  if (url) return <img src={url} alt="" className="h-9 w-9 shrink-0 rounded-xl object-cover" />
  return (
    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-outline-variant/20 bg-surface p-1.5 shadow-sm">
      <Symbol className="h-5 w-5" />
    </div>
  )
}

/** Ganze Tage, bis ein Eintrag im Papierkorb endgültig gelöscht wird (mindestens 0). */
export function restTageImPapierkorb(trashedAt: number, jetzt = Date.now()): number {
  return Math.max(0, Math.ceil((trashedAt + PAPIERKORB_TAGE * TAG_MS - jetzt) / TAG_MS))
}

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
    zurueckgesetzt,
    initializeVault,
    unlock,
    unlockWithBiometrics,
    lock,
    setSearchQuery,
    saveItem,
    trashItem,
    restoreItem,
    setArchived,
    deleteItem,
    emptyTrash,
    toggleFavorite,
    markUsed,
    syncWithServer,
    saveHint,
    requestHintEmail,
    checkBiometricsSupport,
    fetchVaultSalt,
  } = useVaultStore(
    // Nur diese Felder: `lastActivityTime` ändert sich bei jeder Mausbewegung.
    useShallow((s) => ({ isInitialized: s.isInitialized, isUnlocked: s.isUnlocked, isUnlocking: s.isUnlocking, unlockError: s.unlockError, isBiometricsEnabled: s.isBiometricsEnabled, isBiometricsSupported: s.isBiometricsSupported, items: s.items, searchQuery: s.searchQuery, syncStatus: s.syncStatus, zurueckgesetzt: s.zurueckgesetzt, initializeVault: s.initializeVault, unlock: s.unlock, unlockWithBiometrics: s.unlockWithBiometrics, lock: s.lock, setSearchQuery: s.setSearchQuery, saveItem: s.saveItem, trashItem: s.trashItem, restoreItem: s.restoreItem, setArchived: s.setArchived, deleteItem: s.deleteItem, emptyTrash: s.emptyTrash, toggleFavorite: s.toggleFavorite, markUsed: s.markUsed, syncWithServer: s.syncWithServer, saveHint: s.saveHint, requestHintEmail: s.requestHintEmail, checkBiometricsSupport: s.checkBiometricsSupport, fetchVaultSalt: s.fetchVaultSalt })),
  )

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

  const [ansicht, setAnsicht] = useState<Ansicht>('tresor')

  // Nachträglicher Hinweis-Modal & Banner
  const [isHintModalOpen, setIsHintModalOpen] = useState(false)
  const [editHintInput, setEditHintInput] = useState('')
  const [isSavingHint, setIsSavingHint] = useState(false)
  const [dismissedHintReminder, setDismissedHintReminder] = useState(false)

  // UI-Zustände für Sperre & Ersteinrichtung
  const [isSetupMode, setIsSetupMode] = useState(!isInitialized)
  const [zuruecksetzen, setZuruecksetzen] = useState(false)

  useEffect(() => {
    if (isInitialized) {
      setIsSetupMode(false)
    }
  }, [isInitialized])
  const [masterPasswordInput, setMasterPasswordInput] = useState('')
  const [confirmPasswordInput, setConfirmPasswordInput] = useState('')
  const [hintInput, setHintInput] = useState('')
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
  const [showQrScanner, setShowQrScanner] = useState(false)
  // Stand beim Öffnen: Schließen per Tippen daneben oder Zurück fragt nur, wenn
  // seither etwas getippt wurde. Vorher war das Getippte dann kommentarlos weg.
  const [modalAnfang, setModalAnfang] = useState('')
  const modalStand = JSON.stringify([modalService, modalUsername, modalPassword, modalUrl, modalNotes, modalTotpSecret])
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
    setModalAnfang(JSON.stringify(['', '', newPwd, '', '', '']))
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
    setModalAnfang(
      JSON.stringify([item.service, item.username, item.password, item.url || '', item.notes || '', item.totpSecret || '']),
    )
    setLeakCheckResult(null)
    setIsModalOpen(true)
    if (item.password) {
      debouncedLeakCheck(item.password)
    }
  }

  const modalSchliessen = async () => {
    if (modalStand !== modalAnfang) {
      const verwerfen = await confirm({
        message: t('mss.vault.verwerfenFrage'),
        confirmText: t('mss.vault.verwerfen'),
        cancelText: t('mss.vault.weiterBearbeiten'),
        danger: true,
      })
      if (!verwerfen) return
    }
    setIsModalOpen(false)
  }

  const hinweisModalSchliessen = async () => {
    if (editHintInput.trim()) {
      const verwerfen = await confirm({
        message: t('mss.vault.verwerfenFrage'),
        confirmText: t('mss.vault.verwerfen'),
        cancelText: t('mss.vault.weiterBearbeiten'),
        danger: true,
      })
      if (!verwerfen) return
    }
    setIsHintModalOpen(false)
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
      toast.error(fehlerText(err, t('mss.vault.speichernFehlgeschlagen')))
    }
  }

  // Löschen legt in den Papierkorb; das lässt sich zurücknehmen und fragt deshalb nicht.
  const handleTrashItem = async (item: VaultItem) => {
    try {
      await trashItem(item.id)
      if (isModalOpen && editingItemId === item.id) {
        setIsModalOpen(false)
      }
      toast.success(t('mss.vault.inPapierkorbGelegt'), {
        label: t('common.undo'),
        ausfuehren: () => void handleRestoreItem(item),
      })
    } catch (err) {
      toast.error(fehlerText(err, t('mss.vault.loeschenFehlgeschlagen')))
    }
  }

  const handleArchiv = async (item: VaultItem, archiviert: boolean) => {
    try {
      await setArchived(item.id, archiviert)
      if (isModalOpen && editingItemId === item.id) {
        setIsModalOpen(false)
      }
      toast.success(t(archiviert ? 'mss.vault.archiviert' : 'mss.vault.ausArchivGeholt'), {
        label: t('common.undo'),
        ausfuehren: () => void handleArchiv(item, !archiviert),
      })
    } catch (err) {
      toast.error(fehlerText(err, t('mss.vault.speichernFehlgeschlagen')))
    }
  }

  const handleRestoreItem = async (item: VaultItem) => {
    try {
      await restoreItem(item.id)
      toast.success(t('mss.vault.wiederhergestellt'))
    } catch (err) {
      toast.error(fehlerText(err, t('mss.vault.speichernFehlgeschlagen')))
    }
  }

  // Endgültig löschen gibt es nur im Papierkorb, und nur nach Rückfrage.
  const handleDeleteItem = async (item: VaultItem) => {
    const inhalt = item.category === 'ordner' ? inhaltVon(item.id, items).length : 0
    const ok = await confirm({
      title: t('mss.vault.loeschenTitel'),
      // Ein Ordner nimmt seinen Inhalt mit; das muss vor dem Klick dastehen.
      message: inhalt
        ? t('mss.vault.loeschenFrageOrdner', { name: item.service, count: inhalt })
        : t('mss.vault.loeschenFrage', { name: item.service }),
      confirmText: t('mss.vault.endgueltigLoeschen'),
      cancelText: t('common.cancel'),
      danger: true,
    })
    if (!ok) return
    try {
      await deleteItem(item.id)
      toast.success(t('mss.vault.geloescht'))
    } catch (err) {
      toast.error(fehlerText(err, t('mss.vault.loeschenFehlgeschlagen')))
    }
  }

  const handleEmptyTrash = async () => {
    const ok = await confirm({
      title: t('mss.vault.papierkorbLeeren'),
      message: t('mss.vault.papierkorbLeerenFrage'),
      confirmText: t('mss.vault.papierkorbLeeren'),
      cancelText: t('common.cancel'),
      danger: true,
    })
    if (!ok) return
    try {
      await emptyTrash()
      toast.success(t('mss.vault.papierkorbGeleert'))
    } catch (err) {
      toast.error(fehlerText(err, t('mss.vault.loeschenFehlgeschlagen')))
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
      } else if (res.message) {
        toast.error(res.message)
      }
    } finally {
      setIsRequestingHint(false)
    }
  }

  // Einträge unbekannter Art (von einer neueren App) bleiben unsichtbar.
  const ansichtsItems = useMemo(() => {
    const bekannt = items.filter((item) => istBekannteKategorie(item.category))
    if (ansicht === 'papierkorb') {
      return bekannt.filter((item) => item.trashedAt).sort((a, b) => (b.trashedAt || 0) - (a.trashedAt || 0))
    }
    if (ansicht === 'archiv') {
      return bekannt
        .filter((item) => !item.trashedAt && item.archivedAt)
        .sort((a, b) => a.service.localeCompare(b.service))
    }
    return bekannt.filter((item) => !item.trashedAt && !item.archivedAt && istPasswortKategorie(item.category))
  }, [items, ansicht])

  const anzahlImPapierkorb = useMemo(
    () => items.filter((item) => item.trashedAt && istBekannteKategorie(item.category)).length,
    [items],
  )

  const ansichten: TabDef<Ansicht>[] = [
    { id: 'tresor', labelKey: 'mss.vault.ansicht.tresor', icon: Shield },
    { id: 'fotos', labelKey: 'mss.vault.ansicht.fotos', icon: Images },
    { id: 'dateien', labelKey: 'mss.vault.ansicht.dateien', icon: FolderLock },
    { id: 'zahlung', labelKey: 'mss.vault.ansicht.zahlung', icon: CreditCard },
    { id: 'archiv', labelKey: 'mss.vault.ansicht.archiv', icon: Archive },
    {
      id: 'papierkorb',
      labelKey: 'mss.vault.ansicht.papierkorb',
      icon: Trash2,
      badge: anzahlImPapierkorb > 0 ? anzahlImPapierkorb : undefined,
    },
  ]

  // Filterung nach Suchbegriff
  const searchedItems = useMemo(() => {
    if (!searchQuery.trim()) return ansichtsItems
    const q = searchQuery.toLowerCase()
    return ansichtsItems.filter(
      (item) =>
        item.service.toLowerCase().includes(q) ||
        item.username.toLowerCase().includes(q) ||
        (item.url && item.url.toLowerCase().includes(q)) ||
        (item.notes && item.notes.toLowerCase().includes(q))
    )
  }, [ansichtsItems, searchQuery])

  // 1. Favoriten (nur in der Tresor-Ansicht; Archiv und Papierkorb sind schlichte Listen)
  const favoriteItems = useMemo(() => {
    if (ansicht !== 'tresor') return []
    return searchedItems.filter((item) => item.isFavorite)
  }, [searchedItems, ansicht])

  // 2. Zuletzt verwendet
  const recentItems = useMemo(() => {
    if (ansicht !== 'tresor') return []
    return searchedItems
      .filter((item) => !item.isFavorite && typeof item.lastUsedAt === 'number' && item.lastUsedAt > 0)
      .sort((a, b) => (b.lastUsedAt || 0) - (a.lastUsedAt || 0))
      .slice(0, 5)
  }, [searchedItems, ansicht])

  // 3. Alle anderen Einträge
  const otherItems = useMemo(() => {
    if (ansicht !== 'tresor') return searchedItems
    const favoriteIds = new Set(favoriteItems.map((i) => i.id))
    const recentIds = new Set(recentItems.map((i) => i.id))
    return searchedItems
      .filter((item) => !favoriteIds.has(item.id) && !recentIds.has(item.id))
      .sort((a, b) => a.service.localeCompare(b.service))
  }, [searchedItems, favoriteItems, recentItems, ansicht])

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
            <PasswordInput
              id="tresor-neues-master"
              label={t('mss.vault.neuesMasterPasswort')}
              value={masterPasswordInput}
              onChange={(e) => setMasterPasswordInput(e.target.value)}
              placeholder={t('mss.vault.mindestlaenge', { anzahl: MASTER_PASSWORT_MINDESTLAENGE })}
              autoComplete="new-password"
              autoFocus
            />

            <div>
              <PasswordInput
                id="tresor-master-wiederholen"
                label={t('mss.vault.passwortWiederholen')}
                value={confirmPasswordInput}
                onChange={(e) => setConfirmPasswordInput(e.target.value)}
                placeholder={t('mss.vault.erneutEingeben')}
                autoComplete="new-password"
              />

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
              <div className="flex flex-wrap items-center justify-between gap-x-2">
                <label htmlFor="tresor-hinweis-neu" className="block text-label-sm font-medium text-on-surface">
                  {t('mss.vault.hinweisBezeichnung')} {!skipHintSetup && <span className="text-primary font-bold">*</span>}
                </label>
                <span className="text-label-sm text-on-surface-variant">{t('mss.vault.hinweisWozu')}</span>
              </div>
              <Input
                id="tresor-hinweis-neu"
                type="text"
                disabled={skipHintSetup}
                value={skipHintSetup ? '' : hintInput}
                onChange={(e) => setHintInput(e.target.value)}
                placeholder={skipHintSetup ? t('mss.vault.hinweisAbgelehnt') : t('mss.vault.hinweisPlatzhalter')}
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
                  : t('mss.vault.einrichten')}
            </Button>
          </form>

          <div className="text-center pt-1">
            <Button
              fingerziel
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setIsSetupMode(false)
                setMasterPasswordInput('')
                setConfirmPasswordInput('')
              }}
              className="text-primary"
            >
              {t('mss.vault.bereitsEingerichtet')}
            </Button>
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
          {zuruecksetzen ? (
            <TresorZuruecksetzen
              onAbbrechen={() => setZuruecksetzen(false)}
              onFertig={() => {
                setZuruecksetzen(false)
                setIsSetupMode(true)
              }}
            />
          ) : (
          <>
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

            <PasswordInput
              value={masterPasswordInput}
              onChange={(e) => setMasterPasswordInput(e.target.value)}
              placeholder={t('mss.vault.masterPasswort')}
              aria-label={t('mss.vault.masterPasswort')}
              autoComplete="current-password"
              autoFocus={!(isBiometricsEnabled && isBiometricsSupported)}
            />

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
          <div className="flex flex-col items-center gap-1 pt-1">
            <Button
              fingerziel
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleRequestHint}
              disabled={isRequestingHint}
              className="text-on-surface-variant hover:text-primary"
            >
              <HelpCircle className="h-3.5 w-3.5" />
              <span>{isRequestingHint ? t('mss.vault.sendeMail') : t('mss.vault.hinweisPerMail')}</span>
            </Button>

            {!isInitialized && (
              <Button
                fingerziel
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setIsSetupMode(true)
                  setMasterPasswordInput('')
                }}
                className="text-on-surface-variant hover:text-primary"
              >
                {t('mss.vault.neuenTresorEinrichten')}
              </Button>
            )}

            {isInitialized && (
              <Button
                fingerziel
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setZuruecksetzen(true)
                  setMasterPasswordInput('')
                }}
                className="text-on-surface-variant hover:text-status-destructive"
              >
                {t('mss.vault.zuruecksetzen.link')}
              </Button>
            )}
          </div>
          </>
          )}
        </div>
      </div>
    )
  }

  // ── HILFSKOMPONENTE: ZEILE IN LISTE / TABELLE ──
  const renderItemRow = (item: VaultItem) => {
    const istDatei = !istPasswortKategorie(item.category)
    const ItemBrand =
      item.category === 'ordner'
        ? Folder
        : item.category === 'album'
          ? Images
          : item.zahlung
            ? item.zahlung.art === 'konto' ? Landmark : CreditCard
            : istDatei
              ? FileIcon
              : getBrandIcon(item.service, item.url)
    const isRevealed = revealedPasswordId === item.id
    const itemTotp = totpCodes[item.id]

    return (
      <div
        key={item.id}
        className="group flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 p-3 rounded-xl bg-surface-container hover:bg-surface-container-high border border-outline-variant/20 transition-all shadow-sm"
      >
        {/* Logo, Dienst, Benutzer */}
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <ZeilenBild item={item} Symbol={ItemBrand} />

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
                  aria-label={t('mss.vault.seiteOeffnen', { name: item.service })}
                  className="text-on-surface-variant hover:text-primary transition-colors"
                >
                  <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>

            {istDatei ? (
              <div className="mt-0.5 text-label-sm text-on-surface-variant">
                {item.zahlung
                  ? verdeckt(item.zahlung)
                  : item.category === 'ordner'
                  ? t('mss.vault.dateien.ordner')
                  : item.album
                    ? t('mss.vault.fotos.album', { count: item.album.eintraege.length })
                    : item.datei
                      ? formatBytes(item.datei.original.echt)
                      : ''}
              </div>
            ) : (
            <div className="flex items-center gap-1.5 mt-0.5">
              <span className="text-label-sm text-on-surface-variant truncate font-mono">
                {item.username || '—'}
              </span>
              {item.username && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  fingerziel
                  onClick={() => void handleCopy(item.username, `user-${item.id}`, item.id)}
                  className="text-on-surface-variant hover:text-on-surface"
                  aria-label={t('mss.vault.benutzernameKopieren')}
                >
                  {copiedIdField === `user-${item.id}` ? (
                    <Check className="h-3 w-3 text-status-success" />
                  ) : (
                    <Copy className="h-3 w-3" />
                  )}
                </Button>
              )}
            </div>
            )}
          </div>
        </div>

        {/* Schnell-Aktionen (Passwort & 2FA) */}
        <div className="flex flex-wrap items-center gap-2 sm:justify-end">
          {item.password && (
            <div className="flex items-center rounded-lg bg-surface-container-low border border-outline-variant/20 px-2 py-0.5 gap-1 font-mono text-xs">
              <span className="text-on-surface select-none">
                {isRevealed ? item.password : '••••••••'}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                fingerziel
                onClick={() => handleToggleRevealPassword(item.id)}
                aria-label={t(isRevealed ? 'common.hidePassword' : 'common.showPassword')}
                aria-pressed={isRevealed}
                className="text-on-surface-variant hover:text-on-surface"
              >
                {isRevealed ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                fingerziel
                onClick={() => void handleCopy(item.password, `pwd-${item.id}`, item.id)}
                className="text-primary hover:text-primary-hover"
                aria-label={t('mss.vault.passwortKopieren')}
              >
                {copiedIdField === `pwd-${item.id}` ? (
                  <Check className="h-3.5 w-3.5 text-status-success" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
              </Button>
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
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  fingerziel
                  onClick={() => void handleCopy(itemTotp, `totp-${item.id}`, item.id)}
                  className="text-status-success hover:text-status-success/80"
                  aria-label={t('mss.vault.codeKopieren')}
                >
                  {copiedIdField === `totp-${item.id}` ? (
                    <Check className="h-3.5 w-3.5 text-status-success" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                </Button>
              </div>
            )
          )}

          {ansicht === 'papierkorb' ? (
            <div className="flex items-center gap-1 border-l border-outline-variant/20 pl-1.5">
              {item.trashedAt && (
                <span className="text-label-sm text-on-surface-variant">
                  {t('mss.vault.nochTage', { count: restTageImPapierkorb(item.trashedAt) })}
                </span>
              )}
              <Button
                fingerziel
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => void handleRestoreItem(item)}
                className="text-xs px-2 py-1 text-primary"
              >
                <RotateCcw className="h-3.5 w-3.5 mr-1" />
                {t('mss.vault.wiederherstellen')}
              </Button>
              <Button
                fingerziel
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => void handleDeleteItem(item)}
                className="text-xs px-2 py-1 text-status-destructive hover:bg-status-destructive/10"
              >
                <Trash2 className="h-3.5 w-3.5 mr-1" />
                {t('mss.vault.endgueltigLoeschen')}
              </Button>
            </div>
          ) : (
          /* Favorit & Edit */
          <div className="flex items-center gap-0.5 border-l border-outline-variant/20 pl-1.5">
            {ansicht === 'archiv' && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                fingerziel
                onClick={() => void handleArchiv(item, false)}
                aria-label={t('mss.vault.ausArchiv')}
                className="text-on-surface-variant hover:text-primary"
              >
                <ArchiveRestore className="h-3.5 w-3.5" />
              </Button>
            )}
            {!istDatei && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              fingerziel
              onClick={() => void toggleFavorite(item.id)}
              aria-label={t(item.isFavorite ? 'mss.vault.favoritEntfernen' : 'mss.vault.favoritSetzen', { name: item.service })}
              aria-pressed={!!item.isFavorite}
              className={`${
                item.isFavorite
                  ? 'text-status-warning hover:text-status-warning/80'
                  : 'text-on-surface-variant hover:text-status-warning'
              }`}
            >
              <Star className={`h-3.5 w-3.5 ${item.isFavorite ? 'fill-current' : ''}`} />
            </Button>

            )}
            {!istDatei && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              fingerziel
              onClick={() => openEditEntryModal(item)}
              aria-label={t('mss.vault.eintragBearbeiten', { name: item.service })}
              className="text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest"
            >
              <Edit2 className="h-3.5 w-3.5" />
            </Button>
            )}
          </div>
          )}
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

        <div className="flex items-center gap-1">
          {/* Neue Einträge gibt es nur unter „Tresor“; Archiv und Papierkorb nehmen nur auf, was schon da ist. */}
          {ansicht === 'tresor' && (
          <Button
            fingerziel
            onClick={openNewEntryModal}
            aria-label={t('mss.vault.neuerEintrag')}
            className="flex items-center gap-1 bg-primary text-on-primary hover:bg-primary-hover shadow-sm px-2.5 py-1.5 text-xs font-medium max-md:w-11 max-md:px-0"
          >
            <Plus className="h-3.5 w-3.5" />
            <span className="max-md:hidden">{t('mss.vault.neuerEintrag')}</span>
          </Button>
          )}

          <Button
            fingerziel
            size="icon"
            variant="ghost"
            onClick={() => void syncWithServer()}
            disabled={syncStatus === 'syncing'}
            aria-label={t('mss.vault.abgleichen')}
            className="text-on-surface-variant hover:text-on-surface"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${syncStatus === 'syncing' ? 'animate-spin' : ''}`} />
          </Button>

          <Button
            fingerziel
            size="icon"
            variant="ghost"
            onClick={() => {
              setEditHintInput('')
              setIsHintModalOpen(true)
              void checkHintStatus()
            }}
            aria-label={t('mss.vault.hinweisVerwalten')}
            className={`${hasHint === false ? 'text-status-warning hover:text-status-warning/80' : 'text-on-surface-variant hover:text-on-surface'}`}
          >
            <KeyRound className="h-3.5 w-3.5" />
          </Button>

          <Button
            fingerziel
            size="icon"
            variant="ghost"
            onClick={lock}
            aria-label={t('mss.vault.sperren')}
            className="text-on-surface-variant hover:text-status-destructive"
          >
            <Lock className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Ohne diese Zeile sah man nicht, dass Gespeichertes noch nicht beim Server ist. */}
      {!zurueckgesetzt && (syncStatus === 'offline' || syncStatus === 'error') && (
        <div
          role="status"
          className={`flex items-center gap-2 border-b px-4 py-1.5 text-label-sm ${
            syncStatus === 'error'
              ? 'border-status-destructive/30 bg-status-destructive/10 text-status-destructive'
              : 'border-outline-variant/20 bg-surface-container-low text-on-surface-variant'
          }`}
        >
          {syncStatus === 'error' ? <ShieldAlert className="h-3.5 w-3.5 shrink-0" /> : <WifiOff className="h-3.5 w-3.5 shrink-0" />}
          <span className="min-w-0 flex-1">{t(syncStatus === 'error' ? 'mss.vault.abgleichAbgelehnt' : 'mss.vault.offlineHinweis')}</span>
          <Button fingerziel type="button" variant="ghost" size="sm" onClick={() => void syncWithServer()} className="shrink-0">
            {t('common.retry')}
          </Button>
        </div>
      )}

      {/* SUCH-LEISTE UND ANSICHTEN */}
      <div className="px-4 py-2 border-b border-outline-variant/15 bg-surface-container-low/40 flex flex-col gap-2 md:flex-row md:flex-wrap md:items-center">
        <TabBar tabs={ansichten} active={ansicht} onChange={setAnsicht} embedded einzeilig ariaLabel={t('mss.vault.titelManager')} />
        <div className="relative w-full md:max-w-md md:flex-1 md:min-w-[12rem]">
          <Search className="pointer-events-none absolute left-3 top-1/2 z-10 -translate-y-1/2 h-3.5 w-3.5 text-on-surface-variant" />
          <Input
            type="search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t('common.search')}
            aria-label={t('common.search')}
            className="pl-8 pr-3"
          />
        </div>
      </div>

      {zurueckgesetzt && (
        <div role="alert" className="mx-4 mt-2.5 flex items-start gap-2.5 rounded-2xl border border-status-destructive/30 bg-status-destructive/10 p-3">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-xl bg-status-destructive/20 text-status-destructive">
            <ShieldAlert className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-xs font-bold text-on-surface">{t('mss.vault.anderswoZurueckgesetzt')}</h3>
            <p className="mt-0.5 text-label-sm text-on-surface-variant">{t('mss.vault.anderswoZurueckgesetztText')}</p>
          </div>
        </div>
      )}

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
            <Button
              type="button"
              variant="ghost"
              size="icon"
              fingerziel
              onClick={() => setDismissedHintReminder(true)}
              className="text-on-surface-variant hover:text-on-surface"
              aria-label={t('common.close')}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
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
            className="mt-2.5 flex flex-wrap items-center gap-2"
          >
            <div className="min-w-[12rem] flex-1">
              <Input
                type="text"
                value={editHintInput}
                onChange={(e) => setEditHintInput(e.target.value)}
                placeholder={t('mss.vault.hinweisPlatzhalterLang')}
                aria-label={t('mss.vault.hinweisBezeichnung')}
              />
            </div>
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
      {ansicht === 'fotos' ? (
        <TresorGalerie suche={searchQuery} />
      ) : ansicht === 'dateien' ? (
        <TresorDateiBereich suche={searchQuery} />
      ) : ansicht === 'zahlung' ? (
        <div className="flex-1 overflow-y-auto px-4 py-3">
          <TresorZahlung suche={searchQuery} />
        </div>
      ) : (
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {ansicht === 'papierkorb' && ansichtsItems.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-surface-container-low border border-outline-variant/20 px-3 py-2">
            <span className="text-label-sm text-on-surface-variant">
              {t('mss.vault.papierkorbHinweis', { tage: PAPIERKORB_TAGE })}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => void handleEmptyTrash()}
              className="text-xs px-2 py-1 text-status-destructive hover:bg-status-destructive/10"
            >
              <Trash2 className="h-3.5 w-3.5 mr-1" />
              {t('mss.vault.papierkorbLeeren')}
            </Button>
          </div>
        )}
        {ansicht === 'archiv' && ansichtsItems.length > 0 && (
          <p className="text-label-sm text-on-surface-variant">{t('mss.vault.archivHinweis')}</p>
        )}
        {ansicht !== 'tresor' && ansichtsItems.length === 0 ? (
          <Zustandsflaeche art="leer" text={t(ansicht === 'archiv' ? 'mss.vault.archivLeer' : 'mss.vault.papierkorbLeer')} />
        ) : ansichtsItems.length === 0 ? (
          <Zustandsflaeche art="leer" icon={<KeyRound className="h-10 w-10" />} titel={t('mss.vault.leer')}>
            <Button size="sm" className="max-sm:min-h-11" onClick={openNewEntryModal}>
              <Plus className="h-3.5 w-3.5 mr-1" />
              {t('mss.vault.passwortAnlegen')}
            </Button>
          </Zustandsflaeche>
        ) : searchedItems.length === 0 ? (
          <Zustandsflaeche art="leer" ansagen icon={<SearchX className="h-10 w-10" />} text={t('mss.vault.keineTreffer')} />
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
      )}

      {/* ── 4. DIALOG: PASSWORT ANLEGEN / BEARBEITEN ── */}
      <Dialog open={isModalOpen} onOpenChange={(offen) => !offen && void modalSchliessen()}>
        <DialogContent className="max-w-md max-h-[90dvh]">
          <DialogHeader className="px-4 py-3 pr-14">
            <div className="flex items-center gap-2.5">
              <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-surface border border-outline-variant/20 p-1">
                <ModalBrandIcon className="w-4 h-4" />
              </div>
              <DialogTitle className="text-sm">
                {editingItemId ? t('common.edit') : t('mss.vault.neuerEintrag')}
              </DialogTitle>
            </div>
          </DialogHeader>

          <form onSubmit={handleModalSave} className="flex min-h-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
              <Input
                id="tresor-dienst"
                label={t('mss.vault.dienstBezeichnung')}
                type="text"
                value={modalService}
                onChange={(e) => setModalService(e.target.value)}
                placeholder={t('mss.vault.dienstPlatzhalter')}
                autoFocus
                required
              />

              <Input
                id="tresor-benutzer"
                label={t('mss.vault.benutzernameBezeichnung')}
                type="text"
                value={modalUsername}
                onChange={(e) => setModalUsername(e.target.value)}
                placeholder={t('mss.vault.benutzernamePlatzhalter')}
                autoComplete="off"
              />

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label htmlFor="tresor-passwort" className="text-sm font-medium text-foreground">
                    {t('mss.vault.passwort')}
                  </label>
                  <Button
                    fingerziel
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      const newP = generateSecurePassword(20, true)
                      setModalPassword(newP)
                      debouncedLeakCheck(newP)
                    }}
                    className="text-primary"
                  >
                    <Zap className="h-3 w-3" />
                    {t('mss.vault.generieren')}
                  </Button>
                </div>

                <PasswordInput
                  id="tresor-passwort"
                  value={modalPassword}
                  onChange={(e) => {
                    setModalPassword(e.target.value)
                    debouncedLeakCheck(e.target.value)
                  }}
                  placeholder={t('mss.vault.passwort')}
                  autoComplete="new-password"
                  className="font-mono"
                  required
                />

                {leakCheckResult && leakCheckResult.checked && (
                  <div className="mt-1" role="status">
                    {leakCheckResult.isLeaked ? (
                      <span className="flex items-center gap-1 text-label-sm text-status-destructive">
                        <ShieldAlert className="h-3 w-3" />
                        {t('mss.vault.leckGefunden', { count: leakCheckResult.count, anzahl: leakCheckResult.count.toLocaleString() })}
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-label-sm text-status-success">
                        <ShieldCheck className="h-3 w-3" /> {t('mss.vault.keinLeck')}
                      </span>
                    )}
                  </div>
                )}
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label htmlFor="tresor-totp" className="text-sm font-medium text-foreground">
                    {t('mss.vault.zweifaktorBezeichnung')}
                  </label>
                  <Button
                    fingerziel
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowQrScanner(true)}
                    className="text-primary"
                  >
                    <QrCode className="h-3 w-3" />
                    {t('mss.vault.qr.scannen')}
                  </Button>
                </div>
                <Input
                  id="tresor-totp"
                  type="text"
                  value={modalTotpSecret}
                  onChange={(e) => setModalTotpSecret(e.target.value.toUpperCase())}
                  placeholder={t('mss.vault.zweifaktorPlatzhalter')}
                  autoComplete="off"
                  className="font-mono"
                />
              </div>

              <Textarea
                id="tresor-notiz"
                label={t('mss.vault.notizBezeichnung')}
                rows={2}
                value={modalNotes}
                onChange={(e) => setModalNotes(e.target.value)}
                placeholder={t('mss.vault.notizPlatzhalter')}
              />
            </div>

            <DialogFooter className="flex-wrap justify-between">
              {editingItemId ? (
                <div className="flex items-center gap-1">
                  <Button
                    fingerziel
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      const item = items.find((i) => i.id === editingItemId)
                      if (item) void handleTrashItem(item)
                    }}
                    className="text-status-destructive hover:bg-status-destructive/10"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    {t('mss.vault.inPapierkorb')}
                  </Button>
                  <Button
                    fingerziel
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      const item = items.find((i) => i.id === editingItemId)
                      if (item) void handleArchiv(item, !item.archivedAt)
                    }}
                    className="text-on-surface-variant"
                  >
                    {items.find((i) => i.id === editingItemId)?.archivedAt ? (
                      <>
                        <ArchiveRestore className="h-3.5 w-3.5" />
                        {t('mss.vault.ausArchiv')}
                      </>
                    ) : (
                      <>
                        <Archive className="h-3.5 w-3.5" />
                        {t('mss.vault.archivieren')}
                      </>
                    )}
                  </Button>
                </div>
              ) : (
                <div />
              )}

              <div className="flex items-center gap-1.5">
                <Button type="button" variant="ghost" onClick={() => void modalSchliessen()}>
                  {t('common.cancel')}
                </Button>
                <Button type="submit">{t('common.save')}</Button>
              </div>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* QR-Code Scanner */}
      <QrScannerModal
        isOpen={showQrScanner}
        onClose={() => setShowQrScanner(false)}
        onDetected={handleQrDetected}
      />

      {/* Dialog: Passwort-Hinweis verwalten */}
      <Dialog open={isHintModalOpen} onOpenChange={(offen) => !offen && void hinweisModalSchliessen()}>
        <DialogContent className="max-w-sm">
          <DialogHeader className="px-5 py-4 pr-14">
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <KeyRound className="h-4 w-4" />
              </div>
              <div>
                <DialogTitle className="text-sm">{t('mss.vault.hinweisTitel')}</DialogTitle>
                <p className="text-label-sm text-on-surface-variant">
                  {hasHint ? t('mss.vault.hinweisVorhanden') : t('mss.vault.hinweisFehlt')}
                </p>
              </div>
            </div>
          </DialogHeader>

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
          >
            <div className="space-y-1 p-5">
              <Input
                id="tresor-hinweis"
                label={hasHint ? t('mss.vault.hinweisAktualisieren') : t('mss.vault.hinweisAnlegen')}
                type="text"
                value={editHintInput}
                onChange={(e) => setEditHintInput(e.target.value)}
                placeholder={t('mss.vault.hinweisPlatzhalterLang')}
                autoFocus
              />
              <p className="text-label-sm text-on-surface-variant/80 leading-relaxed">
                {t('mss.vault.hinweisErklaerungLang')}
              </p>
            </div>

            <DialogFooter className="flex-wrap justify-between">
              {hasHint ? (
                <Button
                  fingerziel
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={isRequestingHint}
                  onClick={() => void handleRequestHint()}
                  
                >
                  <Mail className="h-3.5 w-3.5" />
                  <span>{isRequestingHint ? t('mss.vault.sendeMail') : t('mss.vault.perMailTesten')}</span>
                </Button>
              ) : (
                <div />
              )}

              <div className="flex items-center gap-1.5">
                <Button type="button" variant="ghost" onClick={() => void hinweisModalSchliessen()}>
                  {t('common.close')}
                </Button>
                <Button type="submit" disabled={!editHintInput.trim() || isSavingHint}>
                  {isSavingHint ? t('common.saving') : t('common.save')}
                </Button>
              </div>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
