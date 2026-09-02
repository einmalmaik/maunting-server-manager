/**
 * Die Einstellungen der Desktop-App — dieselbe Formensprache wie die
 * Panel-Einstellungen: eine Reiterleiste oben (`TabBar`, dieselbe Komponente
 * wie `/settings` und `/profile` im Panel), darunter Karten. Eigene Inhalte:
 * was dieser **Rechner** tut, nicht was das Panel tut.
 *
 * Vier Reiter: Desktop-Integration (Autostart, Hotkeys, Diagnose), Wake-Word
 * (Kalibrierung, Aktiv-Schalter), Audio (Geräteauswahl, Ducking) und die
 * Gefahrenzone. `?tab=wakeword` wählt einen Reiter vor — der Weg des
 * Neukalibrierungs-Hinweises nach einer Umbenennung.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { emit } from '@tauri-apps/api/event'
import { disable, enable, isEnabled } from '@tauri-apps/plugin-autostart'
import { open as ordnerDialog } from '@tauri-apps/plugin-dialog'
import { AlertTriangle, Camera, ExternalLink, FileSignature, Fingerprint, Mic, MonitorCog, ShieldCheck, Trash2, User, Volume2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import {
  aktuelleVerarbeitung,
  ausgabeGeraetId,
  eingabeGeraetId,
  registriereAudioGeraete,
  registriereAudioVerarbeitung,
  type AudioVerarbeitung,
} from '@/components/ai/voice/audioGeraete'
import { api } from '@/api/client'
import { usePublicLegalSettings } from '@/hooks/usePublicLegalSettings'
import { TabBar, type TabDef } from '@/components/ui/TabBar'
import { Avatar, Badge, Button, Dropdown, type DropdownOption, ProgressBar, Slider, Switch } from '@/Singra/UI'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { Gefahrenzone } from './Gefahrenzone'
import { OVERLAY_ZUSTAND_TEST } from './sprachKoordination'
import { WakewordEinrichtung } from './WakewordEinrichtung'
import { useVaultStore } from './vault/vaultStore'
import {
  audioGeraete,
  duckingSetzen,
  hotkeysSetzen,
  konfigLaden,
  konfigSpeichern,
  oeffneBrowser,
  overlayTesten,
  sandboxVerfuegbar,
  setzeStatus,
  wakewordLauschen,
  type AgentStatus,
  type AppKonfig,
  type AudioGeraete,
} from './tauri'

const STATUS_REIHE: AgentStatus[] = ['bereit', 'hoert', 'denkt', 'spricht']

/**
 * Wie lange nach der letzten Verarbeitungsänderung gewartet wird, bevor sie
 * in konfig.json landet — der Verstärkungsregler feuert je Tick. Registriert
 * (und damit hörbar) ist jede Änderung sofort, nur das Schreiben wartet.
 */
const VERARBEITUNG_SPEICHERN_MS = 400

type EinstellungsTab = 'profil' | 'desktop' | 'wakeword' | 'audio' | 'rechtliches' | 'gefahr'

const isAndroidClient = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)

const TABS: TabDef<EinstellungsTab>[] = [
  { id: 'profil', labelKey: 'profile.title', icon: User },
  {
    id: 'desktop',
    labelKey: isAndroidClient ? 'mss.einstellungen.tab.app' : 'mss.einstellungen.tab.desktop',
    icon: MonitorCog,
  },
  { id: 'wakeword', labelKey: 'mss.einstellungen.tab.wakeword', icon: Mic },
  { id: 'audio', labelKey: 'mss.einstellungen.tab.audio', icon: Volume2 },
  { id: 'rechtliches', labelKey: 'mss.einstellungen.tab.rechtliches', icon: FileSignature },
  { id: 'gefahr', labelKey: 'mss.einstellungen.tab.gefahr', icon: AlertTriangle, variant: 'danger' },
]

function tabAusSuche(suche: string): EinstellungsTab {
  const wunsch = new URLSearchParams(suche).get('tab')
  return TABS.some((tab) => tab.id === wunsch) ? (wunsch as EinstellungsTab) : 'desktop'
}

export function Einstellungen({ onKonfigAenderung }: { onKonfigAenderung?: () => void }) {
  const { t } = useTranslation()
  const ort = useLocation()
  const [tab, setTab] = useState<EinstellungsTab>(() => tabAusSuche(ort.search))

  useEffect(() => {
    setTab(tabAusSuche(ort.search))
  }, [ort.search])

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <TabBar
        tabs={TABS}
        active={tab}
        onChange={setTab}
        ariaLabel={t('mss.app.einstellungen')}
      />
      {tab === 'profil' && <ProfilEinstellungen />}
      {tab === 'desktop' && <DesktopIntegration onKonfigAenderung={onKonfigAenderung} />}
      {tab === 'wakeword' && <WakewordEinrichtung />}
      {tab === 'audio' && <AudioEinstellungen />}
      {tab === 'rechtliches' && <RechtlichesEinstellungen />}
      {tab === 'gefahr' && <Gefahrenzone />}
      <p className="text-center text-xs text-on-surface-variant/60">
        {t('mss.einstellungen.fussnote')}
      </p>
    </div>
  )
}

function ProfilEinstellungen() {
  const { t } = useTranslation()
  const user = useAuthStore((s) => s.user)
  const updateUser = useAuthStore((s) => s.updateUser)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploadingAvatar, setUploadingAvatar] = useState(false)

  const {
    autoLockMinutes,
    lockOnWindowBlur,
    isBiometricsSupported,
    isBiometricsEnabled,
    setAutoLockMinutes,
    setLockOnWindowBlur,
    enableBiometrics,
    disableBiometrics,
  } = useVaultStore()

  const [biometricsModalOpen, setBiometricsModalOpen] = useState(false)
  const [masterPasswordInput, setMasterPasswordInput] = useState('')
  const [biometricsLoading, setBiometricsLoading] = useState(false)

  const handleAvatarChange = async (file?: File | null) => {
    if (!file) return
    if (file.size > 5 * 1024 * 1024) {
      toast.error(t('profile.avatarSizeLimit', 'Das Profilbild darf maximal 5 MB groß sein.'))
      return
    }
    const allowedTypes = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
    if (!allowedTypes.includes(file.type)) {
      toast.error(t('profile.avatarInvalidType', 'Erlaubte Formate sind JPEG, PNG, WebP und GIF.'))
      return
    }

    setUploadingAvatar(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await api<{ avatar_url: string }>('/auth/me/avatar', {
        method: 'POST',
        body: formData,
      })
      updateUser({ avatar_url: res.avatar_url })
      toast.success(t('profile.avatarUpdated', 'Profilbild erfolgreich aktualisiert.'))
    } catch (err: any) {
      toast.error(err?.detail || t('profile.avatarUpdateFailed', 'Profilbild konnte nicht hochgeladen werden.'))
    } finally {
      setUploadingAvatar(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDeleteAvatar = async () => {
    if (!user?.avatar_url) return
    setUploadingAvatar(true)
    try {
      await api('/auth/me/avatar', { method: 'DELETE' })
      updateUser({ avatar_url: null })
      toast.success(t('profile.avatarRemoved', 'Profilbild wurde entfernt.'))
    } catch (err: any) {
      toast.error(err?.detail || t('profile.avatarRemoveFailed', 'Profilbild konnte nicht entfernt werden.'))
    } finally {
      setUploadingAvatar(false)
    }
  }

  const autoLockOptions: DropdownOption[] = [
    { value: '0', label: t('mss.vault.autolock.disabled', 'Sofort beim Verlassen') },
    { value: '5', label: t('mss.vault.autolock.5min', '5 Minuten Inaktivität') },
    { value: '10', label: t('mss.vault.autolock.10min', '10 Minuten Inaktivität') },
    { value: '15', label: t('mss.vault.autolock.15min', '15 Minuten Inaktivität') },
    { value: '30', label: t('mss.vault.autolock.30min', '30 Minuten Inaktivität') },
    { value: '60', label: t('mss.vault.autolock.60min', '1 Stunde Inaktivität') },
  ]

  const handleBiometricsToggle = async (checked: boolean) => {
    if (!checked) {
      await disableBiometrics()
      toast.success('Biometrischer Schnelleinstieg deaktiviert')
      return
    }
    setMasterPasswordInput('')
    setBiometricsModalOpen(true)
  }

  const handleConfirmBiometrics = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!masterPasswordInput) return
    setBiometricsLoading(true)
    try {
      const ok = await enableBiometrics(masterPasswordInput)
      if (ok) {
        toast.success('Biometrischer Schnelleinstieg aktiviert')
        setBiometricsModalOpen(false)
        setMasterPasswordInput('')
      } else {
        toast.error('Konnte Biometrie nicht aktivieren. Prüfe das Master-Passwort.')
      }
    } finally {
      setBiometricsLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* 1. Profil & Avatar */}
      <div className="msm-card p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <User className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-on-surface">Benutzerprofil</h2>
            <p className="text-xs text-on-surface-variant">
              Verwalte dein Profilbild und deine Kontodaten für diese Desktop-App.
            </p>
          </div>
        </div>

        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4 pt-2 border-t border-outline-variant/30">
          <Avatar
            src={user?.avatar_url}
            name={user?.username}
            size="lg"
          />

          <div className="space-y-1.5 flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-sm text-on-surface">{user?.username}</span>
              {user?.is_owner && (
                <Badge variant="default">Owner</Badge>
              )}
            </div>
            <p className="text-xs text-on-surface-variant truncate">{user?.email || '—'}</p>

            <div className="flex flex-wrap items-center gap-2 pt-1">
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp,image/gif"
                className="hidden"
                onChange={(e) => void handleAvatarChange(e.target.files?.[0])}
              />
              <Button
                size="sm"
                variant="secondary"
                disabled={uploadingAvatar}
                onClick={() => fileInputRef.current?.click()}
              >
                <Camera className="h-3.5 w-3.5 mr-1.5" />
                {user?.avatar_url ? 'Bild ändern' : 'Bild hochladen'}
              </Button>

              {user?.avatar_url && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={uploadingAvatar}
                  onClick={() => void handleDeleteAvatar()}
                  className="text-status-error hover:bg-status-error/10"
                >
                  <Trash2 className="h-3.5 w-3.5 mr-1.5" />
                  Entfernen
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* 2. Passwort-Manager & Automatische Sperre (Auto-Lock) */}
      <div className="msm-card p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-on-surface">Tresor-Sicherheit & Auto-Lock</h2>
            <p className="text-xs text-on-surface-variant">
              Automatische Speichersperre nach Inaktivität zum Schutz vor unbefugtem Zugriff.
            </p>
          </div>
        </div>

        <div className="space-y-4 pt-2 border-t border-outline-variant/30">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div>
              <label className="text-xs font-medium text-on-surface">Automatische Sperre</label>
              <p className="text-[11px] text-on-surface-variant">
                Nach wie vielen Minuten Inaktivität der Tresor gesperrt wird.
              </p>
            </div>
            <div className="w-full sm:w-56">
              <Dropdown
                options={autoLockOptions}
                value={String(autoLockMinutes)}
                onChange={(val) => setAutoLockMinutes(Number(val))}
              />
            </div>
          </div>

          <div className="flex items-center justify-between gap-4 pt-2 border-t border-outline-variant/20">
            <div>
              <span className="text-xs font-medium text-on-surface">Beim Fenster-Wechsel sperren</span>
              <p className="text-[11px] text-on-surface-variant">
                Sperrt den Passwort-Manager sofort, sobald das Fenster minimiert oder gewechselt wird.
              </p>
            </div>
            <Switch
              checked={lockOnWindowBlur}
              onCheckedChange={setLockOnWindowBlur}
            />
          </div>
        </div>
      </div>

      {/* 3. Biometrischer Schnelleinstieg */}
      <div className="msm-card p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Fingerprint className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-on-surface">Biometrischer Schnelleinstieg</h2>
            <p className="text-xs text-on-surface-variant">
              Windows Hello oder Fingerabdruck zum schnellen und sicheren Entsperren des Tresors nutzen.
            </p>
          </div>
        </div>

        <div className="pt-2 border-t border-outline-variant/30 space-y-3">
          <div className="flex items-center justify-between gap-4">
            <div>
              <span className="text-xs font-medium text-on-surface">
                Biometrische Authentifizierung aktivieren
              </span>
              <p className="text-[11px] text-on-surface-variant">
                Schlüssel wird gerätegebunden per Hardware-Schutz (TPM / Keystore) geschützt.
              </p>
            </div>
            <Switch
              checked={isBiometricsEnabled}
              onCheckedChange={(checked: boolean) => void handleBiometricsToggle(checked)}
              disabled={!isBiometricsSupported}
            />
          </div>

          {!isBiometricsSupported && (
            <div className="p-2.5 rounded-xl bg-surface-container-high border border-outline-variant/30 text-xs text-on-surface-variant">
              Auf diesem Gerät ist aktuell kein biometrischer Sensor (Windows Hello / Fingerabdruck) verfügbar.
            </div>
          )}
        </div>
      </div>

      {/* Biometrie Aktivierungs-Modal */}
      {biometricsModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-3 bg-black/60 backdrop-blur-xs">
          <div className="w-full max-w-sm rounded-2xl bg-surface-container border border-outline-variant/30 p-5 shadow-2xl space-y-4">
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <Fingerprint className="h-4 w-4" />
              </div>
              <h3 className="text-sm font-semibold text-on-surface">Biometrie einrichten</h3>
            </div>
            <p className="text-xs text-on-surface-variant">
              Bitte gib dein Master-Passwort ein, um den biometrischen Schnelleinstieg auf diesem Gerät zu autorisieren.
            </p>
            <form onSubmit={handleConfirmBiometrics} className="space-y-3">
              <input
                type="password"
                value={masterPasswordInput}
                onChange={(e) => setMasterPasswordInput(e.target.value)}
                placeholder="Master-Passwort"
                className="w-full rounded-xl bg-surface-container-low border border-outline-variant/30 px-3 py-2 text-xs text-on-surface focus:outline-none focus:border-primary"
                autoFocus
                required
              />
              <div className="flex items-center justify-end gap-2 pt-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setBiometricsModalOpen(false)}
                  disabled={biometricsLoading}
                >
                  Abbrechen
                </Button>
                <Button
                  type="submit"
                  disabled={biometricsLoading || !masterPasswordInput}
                >
                  {biometricsLoading ? 'Prüfe...' : 'Aktivieren'}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

function DesktopIntegration({ onKonfigAenderung }: { onKonfigAenderung?: () => void }) {
  const { t } = useTranslation()
  const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)
  const [autostart, setAutostart] = useState<boolean | null>(null)
  const [status, setStatus] = useState<AgentStatus>('bereit')

  useEffect(() => {
    if (isAndroid) {
      void konfigLaden()
        .then((cfg) => {
          setAutostart(cfg.autostart_aktiv ?? true)
        })
        .catch(() => setAutostart(true))
    } else {
      void isEnabled()
        .then(setAutostart)
        .catch(() => setAutostart(null))
    }
  }, [isAndroid])

  async function autostartUmschalten(an: boolean) {
    try {
      if (isAndroid) {
        const akt = await konfigLaden().catch(() => null)
        if (akt) {
          await konfigSpeichern({ ...akt, autostart_aktiv: an })
        }
        setAutostart(an)
        onKonfigAenderung?.()
        toast.success(
          an
            ? 'Hintergrundüberwachung bei Handystart aktiviert'
            : 'Hintergrundüberwachung bei Handystart deaktiviert'
        )
      } else {
        if (an) {
          await enable()
        } else {
          await disable()
        }
        const akt = await konfigLaden().catch(() => null)
        if (akt) {
          await konfigSpeichern({ ...akt, autostart_aktiv: an })
        }
        setAutostart(an)
        onKonfigAenderung?.()
      }
    } catch {
      toast.error(t('mss.einstellungen.autostartFehler'))
    }
  }

  async function statusWechseln(neu: AgentStatus) {
    setStatus(neu)
    await setzeStatus(neu).catch(() => {})
    // Das Schaufenster-Ereignis kommt von hier und nur von hier — nicht aus
    // `setze_status` in Rust: den Befehl ruft auch die Zustandsverdrahtung
    // echter Sitzungen, und die Blase im Schaufenster folgte dann der
    // fremden Sitzung statt der geklickten Diagnose-Farbe.
    await emit(OVERLAY_ZUSTAND_TEST, neu).catch(() => {})
  }

  return (
    <section className="msm-card flex flex-col gap-4 p-5">
      <h2 className="text-sm font-medium text-on-surface">
        {isAndroid ? 'Geräteintegration & App-Status' : t('mss.einstellungen.desktopIntegration')}
      </h2>

      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-on-surface">
            {isAndroid ? 'Beim Handystart ausführen' : t('mss.einstellungen.autostart')}
          </p>
          <p className="text-xs text-on-surface-variant">
            {isAndroid
              ? 'Startet die Hintergrundüberwachung für Server-Alarme und Terminerinnerungen automatisch beim Einschalten des Smartphones.'
              : t('mss.einstellungen.autostartHinweis')}
          </p>
        </div>
        <Switch
          checked={autostart === true}
          disabled={autostart === null}
          onCheckedChange={(an) => void autostartUmschalten(an)}
          aria-label={isAndroid ? 'Beim Handystart ausführen' : t('mss.einstellungen.autostart')}
        />
      </div>

      {!isAndroid && (
        <>
          <ArtefaktInstallationSektion onKonfigAenderung={onKonfigAenderung} />

          <Hotkeys />

          <Systembereich />
        </>
      )}

      <ComputerUseSektion onKonfigAenderung={onKonfigAenderung} />

      <div className={isAndroid ? '' : 'border-t border-outline-variant/40 pt-4'}>
        <p className="text-sm text-on-surface">{t('mss.einstellungen.diagnose')}</p>
        <p className="mb-3 text-xs text-on-surface-variant">
          {t('mss.einstellungen.diagnoseHinweis')}
        </p>
        <div className="flex flex-wrap gap-2">
          {STATUS_REIHE.map((s) => (
            <button
              key={s}
              onClick={() => void statusWechseln(s)}
              className={`rounded-lg border px-3.5 py-2 text-sm transition-colors ${
                status === s
                  ? 'border-primary/40 bg-primary/10 text-primary'
                  : 'border-outline-variant/40 bg-surface-container-low/40 text-on-surface-variant hover:text-on-surface'
              }`}
            >
              {t(`mss.einstellungen.status.${s}`)}
            </button>
          ))}
        </div>
        <div className="mt-3">
          {/* Das Schaufenster: zeigt das Overlay ohne Mikrofon und ohne
              Sitzung — die Diagnose-Knöpfe oben färben dann die Blase.
              Zweiter Druck (oder X/ESC am Fenster) schließt wieder. */}
          <Button variant="secondary" onClick={() => void overlayTesten().catch(() => {})}>
            {t('mss.einstellungen.overlayTesten')}
          </Button>
          <p className="mt-1 text-xs text-on-surface-variant">
            {t('mss.einstellungen.overlayTestenHinweis')}
          </p>
        </div>
      </div>
    </section>
  )
}

function ComputerUseSektion({ onKonfigAenderung }: { onKonfigAenderung?: () => void }) {
  const { t } = useTranslation()
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  const [dialogOffen, setDialogOffen] = useState(false)

  useEffect(() => {
    void konfigLaden().then(setKonfig).catch(() => {})
  }, [])

  async function toggle(an: boolean) {
    if (!konfig) return
    if (an) {
      setDialogOffen(true)
    } else {
      const neu = { ...konfig, computer_use_aktiv: false }
      setKonfig(neu)
      await konfigSpeichern(neu).catch(() => {})
      onKonfigAenderung?.()
    }
  }

  async function bestaetigenAktivieren() {
    if (!konfig) return
    const neu = { ...konfig, computer_use_aktiv: true }
    setKonfig(neu)
    setDialogOffen(false)
    await konfigSpeichern(neu).catch(() => {})
    onKonfigAenderung?.()
  }

  const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)

  return (
    <>
      <div className="flex items-center justify-between gap-3 border-t border-outline-variant/40 pt-4">
        <div>
          <div className="flex items-center gap-2">
            <p className="text-sm text-on-surface">{t('mss.einstellungen.computerUse.titel')}</p>
            {isAndroid ? (
              <Badge variant="default">
                {t('mss.einstellungen.computerUse.statusNichtVerfuegbar')}
              </Badge>
            ) : konfig?.computer_use_aktiv ? (
              <Badge variant="success">
                {t('mss.einstellungen.computerUse.statusAktiv')}
              </Badge>
            ) : (
              <Badge variant="default">
                {t('mss.einstellungen.computerUse.statusDeaktiviert')}
              </Badge>
            )}
          </div>
          <p className="text-xs text-on-surface-variant">
            {isAndroid
              ? t('mss.einstellungen.computerUse.androidHinweis')
              : t('mss.einstellungen.computerUse.beschreibung')}
          </p>
        </div>
        <Switch
          checked={!isAndroid && konfig?.computer_use_aktiv === true}
          disabled={isAndroid || konfig === null}
          onCheckedChange={(an) => void toggle(an)}
          aria-label={t('mss.einstellungen.computerUse.titel')}
        />
      </div>

      {dialogOffen && (
        <div
          className="msm-modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={t('mss.einstellungen.computerUse.aktivierenTitel')}
        >
          <div className="msm-card flex w-full max-w-md flex-col gap-4 p-5">
            <div className="flex items-center gap-2 text-status-warning">
              <AlertTriangle className="h-5 w-5 shrink-0" aria-hidden="true" />
              <h2 className="text-base font-semibold text-on-surface">
                {t('mss.einstellungen.computerUse.aktivierenTitel')}
              </h2>
            </div>
            <p className="text-xs leading-relaxed text-on-surface-variant">
              {t('mss.einstellungen.computerUse.aktivierenWarnung')}
            </p>
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button variant="ghost" size="sm" onClick={() => setDialogOffen(false)}>
                {t('mss.einstellungen.computerUse.abbrechen')}
              </Button>
              <Button autoFocus size="sm" onClick={() => void bestaetigenAktivieren()}>
                {t('mss.einstellungen.computerUse.aktivierenBestaetigen')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

function ArtefaktInstallationSektion({ onKonfigAenderung }: { onKonfigAenderung?: () => void }) {
  const { t } = useTranslation()
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  const [dialogOffen, setDialogOffen] = useState(false)
  const [sandboxOk, setSandboxOk] = useState<boolean | null>(null)

  useEffect(() => {
    void konfigLaden().then(setKonfig).catch(() => {})
    void sandboxVerfuegbar().then(setSandboxOk).catch(() => setSandboxOk(false))
  }, [])

  async function toggle(an: boolean) {
    if (!konfig) return
    if (an) {
      setDialogOffen(true)
    } else {
      const neu = { ...konfig, artifact_install_aktiv: false }
      setKonfig(neu)
      await konfigSpeichern(neu).catch(() => {})
      onKonfigAenderung?.()
    }
  }

  async function bestaetigenAktivieren() {
    if (!konfig) return
    const neu = { ...konfig, artifact_install_aktiv: true }
    setKonfig(neu)
    setDialogOffen(false)
    await konfigSpeichern(neu).catch(() => {})
    onKonfigAenderung?.()
  }

  async function downloadLimitAendern(gib: number) {
    if (!konfig) return
    const bytes = Math.max(1, Math.min(100, gib)) * 1024 * 1024 * 1024
    const neu = { ...konfig, max_download_bytes: bytes }
    setKonfig(neu)
    await konfigSpeichern(neu).catch(() => {})
    onKonfigAenderung?.()
  }

  async function suchwurzelHinzufuegen() {
    if (!konfig) return
    try {
      const gewaehlt = await ordnerDialog({ directory: true, multiple: false })
      if (typeof gewaehlt === 'string' && gewaehlt && !konfig.search_roots.includes(gewaehlt)) {
        const neu = { ...konfig, search_roots: [...konfig.search_roots, gewaehlt] }
        setKonfig(neu)
        await konfigSpeichern(neu).catch(() => {})
        onKonfigAenderung?.()
      }
    } catch {
      toast.error(t('mss.einstellungen.artefakte.ordnerFehler', 'Ordner konnte nicht ausgewählt werden.'))
    }
  }

  async function suchwurzelEntfernen(pfad: string) {
    if (!konfig) return
    const neu = { ...konfig, search_roots: konfig.search_roots.filter((w) => w !== pfad) }
    setKonfig(neu)
    await konfigSpeichern(neu).catch(() => {})
    onKonfigAenderung?.()
  }

  const limitGiB = Math.round((konfig?.max_download_bytes ?? 10 * 1024 * 1024 * 1024) / (1024 * 1024 * 1024))

  return (
    <>
      <div className="flex flex-col gap-3 border-t border-outline-variant/40 pt-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <p className="text-sm text-on-surface">{t('mss.einstellungen.artefakte.titel', 'Artefakt-Installationen & Quarantäne')}</p>
              {konfig?.artifact_install_aktiv ? (
                <Badge variant="success">
                  {t('mss.einstellungen.artefakte.statusAktiv', 'Aktiviert')}
                </Badge>
              ) : (
                <Badge variant="default">
                  {t('mss.einstellungen.artefakte.statusDeaktiviert', 'Deaktiviert')}
                </Badge>
              )}
            </div>
            <p className="text-xs text-on-surface-variant">
              {t('mss.einstellungen.artefakte.beschreibung', 'Erlaubt der KI das Herunterladen, Prüfen (Defender & Sandbox), Deployen und Rollbacken von Software, Mods und Installern.')}
            </p>
          </div>
          <Switch
            checked={konfig?.artifact_install_aktiv === true}
            disabled={konfig === null}
            onCheckedChange={(an) => void toggle(an)}
            aria-label={t('mss.einstellungen.artefakte.titel', 'Artefakt-Installationen')}
          />
        </div>

        {konfig?.artifact_install_aktiv && (
          <div className="mt-2 flex flex-col gap-4 rounded-xl border border-outline-variant/30 bg-surface-container-low/30 p-4">
            {/* Windows Sandbox Status */}
            <div className="flex items-center justify-between gap-2">
              <div>
                <p className="text-xs font-medium text-on-surface">{t('mss.einstellungen.artefakte.sandboxTitel', 'Windows Sandbox Isolation')}</p>
                <p className="text-xs text-on-surface-variant">
                  {sandboxOk
                    ? t('mss.einstellungen.artefakte.sandboxVerfuegbar', 'Flüchtige Windows Sandbox ist auf diesem Rechner verfügbar und aktiv.')
                    : t('mss.einstellungen.artefakte.sandboxNichtVerfuegbar', 'Windows Sandbox ist nicht aktiviert oder nicht unterstützt (Hyper-V / BIOS Virtualisierung nötig).')}
                </p>
              </div>
              <Badge variant={sandboxOk ? 'success' : 'warning'}>
                {sandboxOk ? t('mss.einstellungen.artefakte.sandboxBereit', 'Bereit') : t('mss.einstellungen.artefakte.sandboxFehlt', 'Nicht verfügbar')}
              </Badge>
            </div>

            {/* Download Limit */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-on-surface font-medium">{t('mss.einstellungen.artefakte.downloadLimitTitel', 'Download-Limit pro Datei')}</span>
                <span className="font-mono text-primary">{limitGiB} GiB</span>
              </div>
              <Slider
                min={1}
                max={100}
                step={1}
                value={limitGiB}
                onValueChange={(val) => void downloadLimitAendern(val)}
                ariaLabel={t('mss.einstellungen.artefakte.downloadLimitTitel', 'Download-Limit')}
              />
              <p className="text-xs text-on-surface-variant">
                {t('mss.einstellungen.artefakte.downloadLimitHinweis', 'Standard 10 GiB, konfigurierbar bis 100 GiB. Größere Downloads werden aus Sicherheitsgründen sofort abgebrochen.')}
              </p>
            </div>

            {/* Freigegebene Suchwurzeln */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-on-surface">{t('mss.einstellungen.artefakte.suchwurzelnTitel', 'Freigegebene Suchbereiche für Spiele & Software')}</p>
                <Button variant="secondary" size="sm" onClick={() => void suchwurzelHinzufuegen()}>
                  {t('mss.einstellungen.artefakte.suchwurzelHinzufuegen', '+ Ordner freigeben')}
                </Button>
              </div>
              {konfig.search_roots.length === 0 ? (
                <p className="text-xs italic text-on-surface-variant/70">
                  {t('mss.einstellungen.artefakte.keineSuchwurzeln', 'Keine benutzerdefinierten Suchordner hinzugefügt. Standard-Steam-Bibliotheken werden automatisch erkannt.')}
                </p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {konfig.search_roots.map((wurzel) => (
                    <li key={wurzel} className="flex items-center justify-between gap-2 rounded-lg bg-surface-container px-3 py-1.5 text-xs">
                      <span className="break-all font-mono text-on-surface">{wurzel}</span>
                      <Button variant="ghost" size="sm" onClick={() => void suchwurzelEntfernen(wurzel)}>
                        ✕
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </div>

      {dialogOffen && (
        <div
          className="msm-modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={t('mss.einstellungen.artefakte.aktivierenTitel', 'Artefakt-Installationen aktivieren')}
        >
          <div className="msm-card flex w-full max-w-md flex-col gap-4 p-5">
            <div className="flex items-center gap-2 text-status-warning">
              <AlertTriangle className="h-5 w-5 shrink-0" aria-hidden="true" />
              <h2 className="text-base font-semibold text-on-surface">
                {t('mss.einstellungen.artefakte.aktivierenTitel', 'Artefakt-Installationen aktivieren')}
              </h2>
            </div>
            <p className="text-xs leading-relaxed text-on-surface-variant">
              {t(
                'mss.einstellungen.artefakte.aktivierenWarnung',
                'Wenn du diese Funktion aktivierst, kann die KI auf deinen Wunsch hin Dateien (z. B. Spiel-Mods oder Software-Installer) herunterladen. Alle Downloads durchlaufen eine isolierte Quarantäne, SHA-256-Prüfung, Microsoft Defender Scan und eine flüchtige Windows Sandbox vor der eigentlichen Installation.',
              )}
            </p>
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button variant="ghost" size="sm" onClick={() => setDialogOffen(false)}>
                {t('mss.einstellungen.artefakte.abbrechen', 'Abbrechen')}
              </Button>
              <Button autoFocus size="sm" onClick={() => void bestaetigenAktivieren()}>
                {t('mss.einstellungen.artefakte.aktivierenBestaetigen', 'Aktivieren')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

/** Die drei Stufen, in der Reihenfolge, in der sie mehr erlauben. */
const SYSTEMBEREICHE = ['aus', 'lesen', 'schreiben'] as const
type Systembereichswert = (typeof SYSTEMBEREICHE)[number]

/**
 * Wie weit die KI in Windows selbst greifen darf.
 *
 * Der Wert liegt am **Konto**, nicht in `konfig.json`: das Panel muss ihn
 * kennen, wenn es einen Aufräumauftrag zusammenstellt, und ein Wert, den nur
 * dieser Rechner kennt, wäre für einen Auftrag aus dem Panel unsichtbar.
 * Deshalb hier eine Panelabfrage und kein Rust-Command.
 *
 * Ausserhalb dieses Bereichs — im eigenen Profil, auf Datenlaufwerken —
 * arbeitet die KI ohne diese Einstellung; die Bestätigungsfrage dort hängt
 * allein am autonomen Modus. Diese Stufen gelten nur für das, was Windows
 * selbst gehört.
 */
function Systembereich() {
  const { t } = useTranslation()
  const [wert, setWert] = useState<Systembereichswert | null>(null)
  const [sendet, setSendet] = useState(false)

  useEffect(() => {
    void api<{ systembereich: Systembereichswert }>('/ai/settings/desktop')
      .then((daten) => setWert(daten.systembereich))
      // Kein Recht, kein Panel, keine Anmeldung: dann gibt es hier nichts zu
      // entscheiden, und ein Fehlertoast wäre nur Lärm.
      .catch(() => setWert(null))
  }, [])

  async function waehlen(neu: Systembereichswert) {
    if (sendet || neu === wert) return
    const vorher = wert
    setWert(neu)
    setSendet(true)
    try {
      await api('/ai/settings/desktop', {
        method: 'PUT',
        body: JSON.stringify({ systembereich: neu }),
      })
    } catch {
      setWert(vorher)
      toast.error(t('mss.systembereich.fehler'))
    } finally {
      setSendet(false)
    }
  }

  if (wert === null) {
    return null
  }

  return (
    <div className="border-t border-outline-variant/40 pt-4">
      <p className="text-sm text-on-surface">{t('mss.systembereich.titel')}</p>
      <p className="mb-3 text-xs text-on-surface-variant">
        {t('mss.systembereich.hinweis')}
      </p>
      <div className="flex flex-wrap gap-2">
        {SYSTEMBEREICHE.map((stufe) => (
          <button
            key={stufe}
            disabled={sendet}
            onClick={() => void waehlen(stufe)}
            className={`rounded-lg border px-3.5 py-2 text-sm transition-colors ${
              wert === stufe
                ? 'border-primary/40 bg-primary/10 text-primary'
                : 'border-outline-variant/40 bg-surface-container-low/40 text-on-surface-variant hover:text-on-surface'
            }`}
          >
            {t(`mss.systembereich.stufe.${stufe}`)}
          </button>
        ))}
      </div>
      <p className="mt-2 text-xs text-on-surface-variant">
        {t(`mss.systembereich.erklaerung.${wert}`)}
      </p>
    </div>
  )
}

/**
 * Der Audio-Reiter: welches Mikrofon hört, welcher Lautsprecher spricht —
 * unabhängig vom Windows-Standard. Gespeichert wird nur der Gerätename
 * (konfig.json); „Windows-Standard" heißt: dem System folgen, auch wenn es
 * sich ändert. Ein gewähltes Gerät, das gerade fehlt, fällt still auf den
 * Standard zurück — ein abgezogenes USB-Mikrofon legt nichts lahm.
 */
function AudioEinstellungen() {
  const { t } = useTranslation()
  const [geraete, setGeraete] = useState<AudioGeraete | null>(null)
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  const [duckt, setDuckt] = useState(false)
  // Bündelt das Speichern der Verarbeitung. Beim Unmount bewusst NICHT
  // geräumt: die Timeout-Schließung ist in sich geschlossen (frisches Laden,
  // Speichern, kein React-State) — räumen hieße, die letzte Änderung des
  // Benutzers wegzuwerfen.
  const speicherTimer = useRef<number | null>(null)

  useEffect(() => {
    void audioGeraete()
      .then(setGeraete)
      .catch(() => setGeraete(null))
    void konfigLaden()
      .then(setKonfig)
      .catch(() => setKonfig(null))
  }, [])

  async function waehlen(feld: 'audio_eingabe' | 'audio_ausgabe', wert: string) {
    if (!konfig) return
    const neu: AppKonfig = { ...konfig, [feld]: wert === '' ? null : wert }
    setKonfig(neu)
    try {
      await konfigSpeichern(neu)
      // Sofort wirksam für Sitzungen in diesem Fenster; das Overlay lädt die
      // Wahl bei jedem Sitzungsstart frisch, Rust (Wake-Word) liest sie je
      // Aufnahme selbst.
      registriereAudioGeraete(neu.audio_eingabe, neu.audio_ausgabe)
      // Der Lausch-Thread hält sein Mikrofon offen, bis er endet — läuft er,
      // einmal durchstarten, damit das neue Gerät auch wirklich hört.
      if (feld === 'audio_eingabe' && neu.wakeword_aktiv) {
        await wakewordLauschen(false)
        await wakewordLauschen(true)
      }
    } catch (fehler) {
      toast.error(String(fehler))
    }
  }

  const auswahl = (
    feld: 'audio_eingabe' | 'audio_ausgabe',
    liste: string[],
    standard: string | null,
  ) => {
    const wert = konfig?.[feld] ?? ''
    const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)
    const standardLabel = isAndroid
      ? standard
        ? t('mss.audio.standardSystemMit', { name: standard })
        : t('mss.audio.standardSystem')
      : standard
        ? t('mss.audio.standardMit', { name: standard })
        : t('mss.audio.standard')

    const options: DropdownOption[] = [
      { value: '', label: standardLabel },
      ...(wert !== '' && !liste.includes(wert)
        ? [{ value: wert, label: t('mss.audio.fehlt', { name: wert }) }]
        : []),
      ...liste.map((name) => ({ value: name, label: name })),
    ]

    return (
      <div className="w-full">
        <Dropdown
          value={wert}
          onChange={(val) => void waehlen(feld, val)}
          options={options}
          disabled={konfig === null}
          aria-label={t(`mss.audio.${feld === 'audio_eingabe' ? 'eingabe' : 'ausgabe'}`)}
        />
      </div>
    )
  }

  async function duckingTesten() {
    setDuckt(true)
    try {
      await duckingSetzen(true)
      await new Promise((fertig) => setTimeout(fertig, 3000))
      await duckingSetzen(false)
    } finally {
      setDuckt(false)
    }
  }

  /**
   * Ein Verarbeitungsfeld stellen: sofort registrieren (wirkt live in Sitzung
   * und Testhören), gebündelt speichern — der Verstärkungsregler feuert je
   * Tick, und jeder Tick wäre sonst ein Dateischreiben. Alles lokal —
   * Chromiums eigene Kette, kein Ton verlässt dafür den Rechner. Das
   * Wake-Word ist nicht betroffen (eigene Rust-Kette), darum kein Neustart.
   *
   * Gespeichert wird über einen frischen Konfigurationsstand, in den nur die
   * vier eigenen Felder gemischt werden: der React-State stammt vom Mount,
   * und der Wake-Word-Neustart beim Gerätewechsel schreibt `wakeword_aktiv`
   * parallel in dieselbe Datei.
   */
  function verarbeitungSetzen(
    feld: 'audio_echo' | 'audio_rauschen' | 'audio_autogain' | 'audio_verstaerkung',
    wert: boolean | number,
  ) {
    if (!konfig) return
    const neu: AppKonfig = { ...konfig, [feld]: wert }
    setKonfig(neu)
    registriereAudioVerarbeitung({
      echo: neu.audio_echo,
      rauschen: neu.audio_rauschen,
      autogain: neu.audio_autogain,
      verstaerkung: neu.audio_verstaerkung,
    })
    if (speicherTimer.current !== null) window.clearTimeout(speicherTimer.current)
    speicherTimer.current = window.setTimeout(() => {
      speicherTimer.current = null
      void (async () => {
        try {
          const aktuell = await konfigLaden()
          await konfigSpeichern({
            ...aktuell,
            audio_echo: neu.audio_echo,
            audio_rauschen: neu.audio_rauschen,
            audio_autogain: neu.audio_autogain,
            audio_verstaerkung: neu.audio_verstaerkung,
          })
        } catch (fehler) {
          toast.error(String(fehler))
        }
      })()
    }, VERARBEITUNG_SPEICHERN_MS)
  }

  const verarbeitungsZeile = (
    feld: 'audio_echo' | 'audio_rauschen' | 'audio_autogain',
  ) => {
    const kurz = feld.slice('audio_'.length)
    return (
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-on-surface">{t(`mss.audio.${kurz}`)}</p>
          <p className="text-xs text-on-surface-variant">{t(`mss.audio.${kurz}Hinweis`)}</p>
        </div>
        <Switch
          checked={konfig?.[feld] ?? true}
          disabled={konfig === null}
          onCheckedChange={(an) => void verarbeitungSetzen(feld, an)}
          aria-label={t(`mss.audio.${kurz}`)}
        />
      </div>
    )
  }

  return (
    <section className="msm-card flex flex-col gap-4 p-5">
      <h2 className="text-sm font-medium text-on-surface">{t('mss.audio.titel')}</h2>
      <p className="text-xs text-on-surface-variant">{t('mss.audio.hinweis')}</p>

      <div className="flex flex-col gap-1.5">
        <label className="text-sm text-on-surface">{t('mss.audio.eingabe')}</label>
        {auswahl('audio_eingabe', geraete?.eingaenge ?? [], geraete?.standard_eingang ?? null)}
        <p className="text-xs text-on-surface-variant">{t('mss.audio.eingabeHinweis')}</p>
      </div>

      <div className="flex flex-col gap-1.5">
        <label className="text-sm text-on-surface">{t('mss.audio.ausgabe')}</label>
        {auswahl('audio_ausgabe', geraete?.ausgaenge ?? [], geraete?.standard_ausgang ?? null)}
        <p className="text-xs text-on-surface-variant">{t('mss.audio.ausgabeHinweis')}</p>
      </div>

      <div className="flex flex-col gap-3 border-t border-outline-variant/40 pt-4">
        <div>
          <p className="text-sm text-on-surface">{t('mss.audio.verarbeitung')}</p>
          <p className="text-xs text-on-surface-variant">{t('mss.audio.verarbeitungHinweis')}</p>
        </div>
        {verarbeitungsZeile('audio_echo')}
        {verarbeitungsZeile('audio_rauschen')}
        {verarbeitungsZeile('audio_autogain')}
        <Slider
          value={Math.round((konfig?.audio_verstaerkung ?? 1) * 100)}
          min={25}
          max={400}
          step={5}
          disabled={konfig === null}
          onValueChange={(prozent) => void verarbeitungSetzen('audio_verstaerkung', prozent / 100)}
          label={t('mss.audio.verstaerkung')}
          hint={`${Math.round((konfig?.audio_verstaerkung ?? 1) * 100)} %`}
        />
        <p className="-mt-2 text-xs text-on-surface-variant">
          {t('mss.audio.verstaerkungHinweis')}
        </p>
      </div>

      <Testhoeren
        verarbeitung={{
          echo: konfig?.audio_echo ?? true,
          rauschen: konfig?.audio_rauschen ?? true,
          autogain: konfig?.audio_autogain ?? true,
          verstaerkung: konfig?.audio_verstaerkung ?? 1,
        }}
      />

      {!isAndroidClient && (
        <div className="border-t border-outline-variant/40 pt-4">
          <p className="text-sm text-on-surface">{t('mss.audio.ducking')}</p>
          <p className="mb-3 text-xs text-on-surface-variant">{t('mss.audio.duckingHinweis')}</p>
          <Button variant="secondary" onClick={() => void duckingTesten()} disabled={duckt}>
            {duckt
              ? t('mss.einstellungen.duckingLaeuft')
              : t('mss.einstellungen.duckingTesten')}
          </Button>
        </div>
      )}
    </section>
  )
}

/**
 * Testhören wie in Discord: das eigene Mikrofon auf den gewählten Lautsprecher
 * legen und dabei den Pegel sehen — mit genau der Verarbeitung, die auch die
 * Sprachsitzung nähme. Einzige Abweichung: die Echounterdrückung ist im Test
 * aus, weil sie sonst die eigene Wiedergabe als „Echo" erkennt und wegfiltert —
 * man hörte sich leiser werden, je länger man spricht. Alles bleibt im
 * Chromium-Prozess, nichts davon geht ins Netz.
 */
function Testhoeren({ verarbeitung }: { verarbeitung: AudioVerarbeitung }) {
  const { t } = useTranslation()
  const [laeuft, setLaeuft] = useState(false)
  const [pegel, setPegel] = useState(0)
  const [fehler, setFehler] = useState<string | null>(null)
  const aufraeumen = useRef<(() => void) | null>(null)
  const gainKnoten = useRef<GainNode | null>(null)
  // Ob die Komponente noch lebt: `starten` hat zwei awaits, bevor es sein
  // Aufräumen hinterlegt. Wer in diesem Fenster den Reiter wechselt, träfe
  // ein Unmount-Cleanup auf `null` — und das Mikrofon bliebe offen, ohne
  // dass irgendetwas es noch schließen könnte.
  const verlassen = useRef(false)
  // Laufende Nummer des jüngsten Starts. Zwei schnelle Klicks (oder Klick +
  // Schalter-Neustart) liefen sonst nebeneinander durch getUserMedia, und der
  // langsamere überschriebe das Aufräumen des schnelleren — dessen Mikrofon
  // bliebe offen und wäre durch nichts mehr stoppbar.
  const startNummer = useRef(0)

  const stoppen = useCallback(() => {
    startNummer.current += 1
    aufraeumen.current?.()
    aufraeumen.current = null
    gainKnoten.current = null
    setLaeuft(false)
    setPegel(0)
  }, [])

  const starten = useCallback(async () => {
    const nummer = startNummer.current + 1
    startNummer.current = nummer
    aufraeumen.current?.()
    aufraeumen.current = null
    setFehler(null)
    try {
      const geraet = await eingabeGeraetId().catch(() => null)
      const strom = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: verarbeitung.rauschen,
          autoGainControl: verarbeitung.autogain,
          ...(geraet ? { deviceId: { ideal: geraet } } : {}),
        },
      })
      if (verlassen.current || nummer !== startNummer.current) {
        strom.getTracks().forEach((spur) => spur.stop())
        return
      }
      const kontext = new AudioContext()
      if (kontext.state === 'suspended') {
        await kontext.resume().catch(() => {})
      }
      // Dieselbe Gerätewahl wie die Stimme der KI (`audioWiedergabe`):
      // `setSinkId` gibt es erst seit Chromium 110; wo es fehlt, bleibt der
      // Systemstandard — Ton geht vor Gerätetreue.
      void ausgabeGeraetId()
        .then((sink) => {
          const mitSink = kontext as AudioContext & {
            setSinkId?: (id: string) => Promise<void>
          }
          if (sink && mitSink.setSinkId) return mitSink.setSinkId(sink)
        })
        .catch(() => undefined)
      const quelle = kontext.createMediaStreamSource(strom)
      const gain = kontext.createGain()
      gain.gain.value = aktuelleVerarbeitung().verstaerkung
      gainKnoten.current = gain
      // Der Analyser hängt als Abgriff hinter der Verstärkung: der Balken
      // zeigt, was am Lautsprecher ankommt, nicht das rohe Mikrofon.
      const analyser = kontext.createAnalyser()
      quelle.connect(gain)
      gain.connect(analyser)
      gain.connect(kontext.destination)
      const puffer = new Float32Array(analyser.fftSize)
      const takt = window.setInterval(() => {
        analyser.getFloatTimeDomainData(puffer)
        let summe = 0
        for (let i = 0; i < puffer.length; i += 1) summe += puffer[i] * puffer[i]
        // Dieselbe Skalierung wie der Sitzungspegel (`audioAufnahme`): RMS ×4
        // holt gesprochene Sprache in einen sichtbaren Bereich.
        setPegel(Math.min(1, Math.sqrt(summe / puffer.length) * 4))
      }, 100)
      aufraeumen.current = () => {
        window.clearInterval(takt)
        quelle.disconnect()
        gain.disconnect()
        analyser.disconnect()
        strom.getTracks().forEach((spur) => spur.stop())
        void kontext.close().catch(() => undefined)
      }
      setLaeuft(true)
    } catch {
      setFehler(t('mss.audio.testhoerenFehler'))
      setLaeuft(false)
    }
  }, [verarbeitung.rauschen, verarbeitung.autogain, t])

  // Die Verstärkung wirkt live in den laufenden Test — der Regler daneben
  // soll hörbar sein, ohne neu zu starten.
  useEffect(() => {
    if (gainKnoten.current) gainKnoten.current.gain.value = aktuelleVerarbeitung().verstaerkung
  }, [verarbeitung.verstaerkung])

  // Die Schalter dagegen sind getUserMedia-Constraints: ein laufender Test
  // startet neu, damit man hört, was man umgelegt hat.
  useEffect(() => {
    if (aufraeumen.current) void starten()
  }, [starten])

  // Beim Verlassen des Reiters geht das Mikrofon zu — ein Testton, der ohne
  // sichtbaren Ursprung weiterläuft, wäre genau das falsche Gefühl für eine
  // App, die mithören kann. `verlassen` fängt den Fall, dass `starten` noch
  // in seinen awaits steckt und sein Aufräumen erst danach hinterlegte.
  useEffect(() => () => {
    verlassen.current = true
    aufraeumen.current?.()
  }, [])

  return (
    <div className="flex flex-col gap-3 border-t border-outline-variant/40 pt-4">
      <div>
        <p className="text-sm text-on-surface">{t('mss.audio.testhoeren')}</p>
        <p className="text-xs text-on-surface-variant">{t('mss.audio.testhoerenHinweis')}</p>
      </div>
      <div className="flex items-center gap-3">
        <Button
          variant="secondary"
          onClick={() => (laeuft ? stoppen() : void starten())}
        >
          {laeuft ? t('mss.audio.testhoerenStopp') : t('mss.audio.testhoerenStart')}
        </Button>
        <ProgressBar
          value={laeuft ? Math.round(pegel * 100) : null}
          ariaLabel={t('mss.audio.testhoerenPegel')}
          className="flex-1"
        />
      </div>
      {fehler && <p className="msm-alert-warning">{fehler}</p>}
    </div>
  )
}

/**
 * Baut aus einem Tastendruck die Kombination im Format der Registrierung
 * („Ctrl+Shift+K"). `null` heißt: nur Modifier gedrückt — weiter warten.
 * Der eigentliche Prüfer sitzt in Rust (`hotkey_pruefen`); was er ablehnt,
 * kommt als Fehlermeldung zurück, und der alte Hotkey bleibt.
 */
function komboAusEreignis(ereignis: KeyboardEvent): string | null {
  const code = ereignis.code
  if (!code || /^(Control|Alt|Shift|Meta)/.test(code)) return null
  const teile: string[] = []
  if (ereignis.ctrlKey) teile.push('Ctrl')
  if (ereignis.altKey) teile.push('Alt')
  if (ereignis.shiftKey) teile.push('Shift')
  if (ereignis.metaKey) teile.push('Super')
  const taste = code.startsWith('Key')
    ? code.slice(3)
    : code.startsWith('Digit')
      ? code.slice(5)
      : code
  return [...teile, taste].join('+')
}

/** Die Vorgaben — dieselben Werte wie `konfig::Default` in Rust. */
const HOTKEY_VORGABEN = { fenster: 'Alt+Space', sprache: 'Alt+Shift+Space' } as const

type HotkeyArt = 'fenster' | 'sprache'

/**
 * Zwei Hotkeys, je einzeln abschaltbar: einer fürs Hauptfenster, einer für
 * die Sprachsitzung im Overlay. Geändert wird per Aufnahme (nächster
 * Tastendruck), registriert und gespeichert in Rust (`hotkeys_setzen`) —
 * eine belegte Kombination kommt als Fehler zurück, nichts stellt sich um.
 */
function Hotkeys() {
  const { t } = useTranslation()
  const [werte, setWerte] = useState<Record<HotkeyArt, string | null> | null>(null)
  const [aufnahme, setAufnahme] = useState<HotkeyArt | null>(null)
  // Die letzte Kombination je Seite: der Aktiv-Schalter stellt sie wieder
  // her — nicht die Werksvorgabe, die der Benutzer längst ersetzt hat.
  const zuletzt = useRef({ ...HOTKEY_VORGABEN } as Record<HotkeyArt, string>)

  useEffect(() => {
    void konfigLaden()
      .then((konfig) => {
        const geladen = { fenster: konfig.hotkey_fenster, sprache: konfig.hotkey_sprache }
        if (geladen.fenster) zuletzt.current.fenster = geladen.fenster
        if (geladen.sprache) zuletzt.current.sprache = geladen.sprache
        setWerte(geladen)
      })
      .catch(() => setWerte(null))
  }, [])

  async function anwenden(neu: Record<HotkeyArt, string | null>) {
    try {
      await hotkeysSetzen(neu.fenster, neu.sprache)
      if (neu.fenster) zuletzt.current.fenster = neu.fenster
      if (neu.sprache) zuletzt.current.sprache = neu.sprache
      setWerte(neu)
    } catch (fehler) {
      toast.error(String(fehler))
    }
  }

  useEffect(() => {
    if (!aufnahme || !werte) return
    const taste = (ereignis: KeyboardEvent) => {
      ereignis.preventDefault()
      ereignis.stopPropagation()
      if (ereignis.key === 'Escape') {
        setAufnahme(null)
        return
      }
      const kombi = komboAusEreignis(ereignis)
      if (!kombi) return
      setAufnahme(null)
      void anwenden({ ...werte, [aufnahme]: kombi })
    }
    window.addEventListener('keydown', taste, true)
    return () => window.removeEventListener('keydown', taste, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aufnahme, werte])

  const zeile = (art: HotkeyArt) => {
    const wert = werte?.[art] ?? null
    return (
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-on-surface">{t(`mss.einstellungen.hotkey.${art}`)}</p>
          <p className="text-xs text-on-surface-variant">
            {wert ? <kbd className="font-mono">{wert}</kbd> : t('mss.einstellungen.hotkey.aus')}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={!werte || wert === null}
            onClick={() => setAufnahme(aufnahme === art ? null : art)}
          >
            {aufnahme === art
              ? t('mss.einstellungen.hotkey.druecken')
              : t('mss.einstellungen.hotkey.aendern')}
          </Button>
          <Switch
            checked={wert !== null}
            disabled={!werte}
            onCheckedChange={(an) => {
              if (!werte) return
              setAufnahme(null)
              void anwenden({ ...werte, [art]: an ? zuletzt.current[art] : null })
            }}
            aria-label={t(`mss.einstellungen.hotkey.${art}`)}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 border-t border-outline-variant/40 pt-4">
      <div>
        <p className="text-sm text-on-surface">{t('mss.einstellungen.hotkey.titel')}</p>
        <p className="text-xs text-on-surface-variant">
          {t('mss.einstellungen.hotkey.hinweis')}
        </p>
      </div>
      {zeile('fenster')}
      {zeile('sprache')}
    </div>
  )
}

function RechtlichesEinstellungen() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const legal = usePublicLegalSettings()

  async function impressumOeffnen(url: string) {
    try {
      await oeffneBrowser(url)
    } catch {
      window.open(url, '_blank', 'noopener,noreferrer')
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Slogan & Philosophie */}
      <section className="msm-card bg-surface-container-low/40 p-5">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-primary/10 p-2.5 text-primary">
            <ShieldCheck className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h2 className="font-headline text-base font-semibold text-on-surface">
              {t('mss.einstellungen.rechtliches.slogan', 'Maunting Studios — Sicherheit braucht Vertrauen')}
            </h2>
            <p className="mt-1 text-xs text-on-surface-variant">
              {t('mss.einstellungen.rechtliches.beschreibung', 'Vollständige Transparenz, echte Datenhoheit und kein unbemerktes Handeln auf Ihren Systemen.')}
            </p>
          </div>
        </div>
      </section>

      {/* Datenschutzerklärung */}
      <section className="msm-card flex flex-col gap-4 p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-medium text-on-surface">
                {t('mss.einstellungen.rechtliches.datenschutzTitel', 'Datenschutzerklärung')}
              </h3>
              <Badge variant="default">
                {t('mss.einstellungen.rechtliches.datenschutzVersion', { version: 'v2.7' })}
              </Badge>
            </div>
            <p className="mt-1 text-xs text-on-surface-variant max-w-xl">
              {t('mss.einstellungen.rechtliches.datenschutzDesc', 'Erfahren Sie im Detail, wie Ihre Daten, Einstellungen und Sitzungen geschützt und minimiert verarbeitet werden.')}
            </p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => navigate('/privacy')}
            className="shrink-0"
          >
            {t('mss.einstellungen.rechtliches.datenschutzOeffnen', 'Datenschutzerklärung öffnen')}
          </Button>
        </div>
      </section>

      {/* Impressum */}
      <section className="msm-card flex flex-col gap-4 p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-medium text-on-surface">
                {t('mss.einstellungen.rechtliches.impressumTitel', 'Betreiber-Impressum')}
              </h3>
              <Badge
                variant={legal.imprint_enabled && legal.imprint_url ? 'success' : 'default'}
              >
                {legal.imprint_enabled && legal.imprint_url
                  ? t('mss.einstellungen.rechtliches.impressumAktiv', 'Aktiviert')
                  : t('mss.einstellungen.rechtliches.impressumInaktiv', 'Nicht konfiguriert')}
              </Badge>
            </div>
            <p className="mt-1 text-xs text-on-surface-variant">
              {t('mss.einstellungen.rechtliches.impressumDesc', 'Rechtliche Angaben und Kontaktinformationen des Betreibers dieser Server-Manager-Instanz.')}
            </p>
            {legal.imprint_enabled && legal.imprint_url ? (
              <p className="mt-2 text-xs font-mono text-primary truncate max-w-md">
                {legal.imprint_url}
              </p>
            ) : (
              <p className="mt-2 text-xs italic text-on-surface-variant/70">
                {t('mss.einstellungen.rechtliches.impressumKeinHinweis', 'Für diese Instanz wurde kein externes Betreiber-Impressum hinterlegt.')}
              </p>
            )}
          </div>
          {legal.imprint_enabled && legal.imprint_url && (
            <Button
              variant="primary"
              size="sm"
              onClick={() => void impressumOeffnen(legal.imprint_url)}
              className="shrink-0"
            >
              <ExternalLink className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              {t('mss.einstellungen.rechtliches.impressumOeffnen', 'Impressum im Browser öffnen')}
            </Button>
          )}
        </div>
      </section>
    </div>
  )
}

