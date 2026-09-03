/**
 * Hauptfenster der Desktop-App — die Panel-Oberfläche in einer Tauri-Hülle.
 *
 * Ablauf beim Start: Konfiguration laden → ohne Adresse oder Einrichtung den
 * Assistenten zeigen → sonst still über den OS-Tresor anmelden → gelingt das
 * nicht, nur den Kopplungsschritt zeigen; fehlt danach nur der Sandbox-Ordner,
 * nur diesen einen Schritt. Angemeldet rendert die App die
 * **echte** KI-Seite des Panels (`pages/Ai`) — Chat, Realtime, Guardian,
 * Aufgaben, Worker — plus die Desktop-Einstellungen.
 *
 * Der Router ist ein MemoryRouter mit Start `/ai`: die Panel-Komponenten
 * navigieren mit `navigate('/ai?ansicht=…')` (Glocke, Guardian, Aufgaben),
 * und genau diese Route gibt es hier. Eine Adressleiste hat das Fenster nicht.
 */
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { MemoryRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { listen } from '@tauri-apps/api/event'
import { BrainCircuit, Calendar as CalendarIcon, Eye, KeyRound, LogOut, Menu, MessageSquare, Settings as SettingsIcon, ShieldAlert, StickyNote, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { api } from '@/api/client'
import { AiMemoryManager } from '@/components/ai/AiMemoryManager'
import { AiRunNotice } from '@/components/ai/AiRunNotice'
import { ServerIncidentNotifier } from '@/components/notifications/ServerIncidentNotifier'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { PromptDialog } from '@/components/ui/PromptDialog'
import { ToastContainer } from '@/components/ui/ToastContainer'
import { PanelPopupModal } from '@/components/popups/PanelPopupModal'
import { Avatar, BenachrichtigungsGlocke, Button, ProfileDropdown, type ProfileDropdownItem } from '@/Singra/UI'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Ai } from '@/pages/Ai'
import { Calendar } from '@/pages/Calendar'
import { Notes } from '@/pages/Notes'
import { Privacy } from '@/pages/Privacy'
import { useAuthStore } from '@/stores/authStore'
import { abmelden } from './auth'
import { Einstellungen } from './Einstellungen'
import { Splash } from './Splash'
import { OverlayFenster } from './OverlayFenster'
import { Aufraeumkarte } from './Aufraeumkarte'
import { DesktopAktionKarte } from './DesktopAktionKarte'
import { Uebernahmekarte } from './Uebernahmekarte'
import { Wizard } from './Wizard'
import { VaultView } from './vault/VaultView'
import { useVaultStore } from './vault/vaultStore'
import {
  beiFremdemSprachstart,
  sprachstartMelden,
  sprachzustandVerdrahten,
} from './sprachKoordination'
import {
  appBeenden,
  hauptfensterVerstecken,
  konfigLaden,
  konfigSpeichern,
  wakewordStand,
  type AppKonfig,
} from './tauri'
import { stillAnmeldenDetail } from './transport'
import { useAuftragsschleife } from './useAuftragsschleife'

type Phase = 'laedt' | 'einrichtung' | 'kopplung' | 'sandbox' | 'bereit'

const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)
const SPLASH_GESEHEN_KEY = 'mss:splash_gesehen'

export function DesktopApp() {
  const [phase, setPhase] = useState<Phase>('laedt')
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  const [isOffline, setIsOffline] = useState(false)
  const [splash, setSplash] = useState(() => {
    try {
      return localStorage.getItem(SPLASH_GESEHEN_KEY) !== 'true'
    } catch {
      return false
    }
  })
  const angemeldet = useAuthStore((s) => s.isAuthenticated)
  const sitzungSteht = phase === 'bereit' || phase === 'sandbox'
  const offeneUebernahme = useAuftragsschleife(sitzungSteht && !isAndroid)

  const isUnlocked = useVaultStore((s) => s.isUnlocked)
  const lockOnWindowBlur = useVaultStore((s) => s.lockOnWindowBlur)
  const checkAutoLock = useVaultStore((s) => s.checkAutoLock)
  const recordActivity = useVaultStore((s) => s.recordActivity)
  const lockVault = useVaultStore((s) => s.lock)

  // Automatische Tresor-Sperre bei Inaktivität oder Fenster-Wechsel
  useEffect(() => {
    if (!isUnlocked) return

    const handleActivity = () => {
      recordActivity()
    }

    const events = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'pointerdown']
    events.forEach((evt) => window.addEventListener(evt, handleActivity, { passive: true }))

    const interval = setInterval(() => {
      checkAutoLock()
    }, 10000)

    const handleWindowBlur = () => {
      const state = useVaultStore.getState()
      if (state.lockOnWindowBlur && state.isUnlocked && !state.isUnlocking) {
        state.lock()
      }
    }

    const handleVisibilityChange = () => {
      const state = useVaultStore.getState()
      if (document.hidden && state.lockOnWindowBlur && state.isUnlocked && !state.isUnlocking) {
        state.lock()
      }
    }

    window.addEventListener('blur', handleWindowBlur)
    document.addEventListener('visibilitychange', handleVisibilityChange)

    let unlistenTauriBlur: (() => void) | undefined
    if (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window) {
      listen('tauri://blur', handleWindowBlur)
        .then((unlisten) => {
          unlistenTauriBlur = unlisten
        })
        .catch(() => {})
    }

    return () => {
      events.forEach((evt) => window.removeEventListener(evt, handleActivity))
      clearInterval(interval)
      window.removeEventListener('blur', handleWindowBlur)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      if (unlistenTauriBlur) unlistenTauriBlur()
    }
  }, [isUnlocked, lockOnWindowBlur, recordActivity, checkAutoLock, lockVault])

  const ladeKonfigNeu = useCallback(async () => {
    try {
      const geladen = await konfigLaden()
      setKonfig(geladen)
    } catch {}
  }, [])

  const splashBeenden = useCallback(() => {
    setSplash(false)
    try {
      localStorage.setItem(SPLASH_GESEHEN_KEY, 'true')
    } catch {}
    void konfigLaden().then((k) => {
      if (!k.splash_gesehen) {
        void konfigSpeichern({ ...k, splash_gesehen: true })
      }
    }).catch(() => {})
  }, [])

  useEffect(() => {
    const handleOnline = () => {
      setIsOffline(false)
      void (async () => {
        const res = await stillAnmeldenDetail(2500)
        if (res.status === 'erfolg') {
          void useAuthStore.getState().checkAuth()
        }
      })()
    }
    const handleOffline = () => {
      setIsOffline(true)
    }
    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)
    return () => {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [])

  useEffect(() => {
    void (async () => {
      try {
        const geladen = await konfigLaden()
        setKonfig(geladen)
        if (geladen.splash_gesehen) {
          setSplash(false)
          try {
            localStorage.setItem(SPLASH_GESEHEN_KEY, 'true')
          } catch {}
        }
        if (!geladen.backend_url || !geladen.eingerichtet) {
          setPhase('einrichtung')
          return
        }

        // Stille Anmeldung mit kurzem Timeout (2.5s)
        const anmeldung = await stillAnmeldenDetail(2500)
        if (anmeldung.status === 'abgelehnt') {
          setPhase('kopplung')
          return
        }

        if (anmeldung.status === 'offline') {
          setIsOffline(true)
          if (!isAndroid && !geladen.sandbox_pfad) {
            setPhase('sandbox')
            return
          }
          setPhase('bereit')
          return
        }

        // Bei 'erfolg': checkAuth mit Timeout (2.5s) absichern
        try {
          await Promise.race([
            useAuthStore.getState().checkAuth(),
            new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 2500)),
          ])
        } catch {
          setIsOffline(true)
        }

        if (!useAuthStore.getState().isAuthenticated && anmeldung.status !== 'erfolg') {
          setPhase('kopplung')
          return
        }

        if (!isAndroid && !geladen.sandbox_pfad) {
          setPhase('sandbox')
          return
        }
        setPhase('bereit')
      } catch {
        setPhase('einrichtung')
      }
    })()
  }, [])

  useEffect(() => {
    if (phase === 'bereit' && !angemeldet && !isOffline) {
      setPhase('kopplung')
    }
  }, [phase, angemeldet, isOffline])

  useEffect(() => {
    if (!sitzungSteht) return
    return sprachzustandVerdrahten()
  }, [sitzungSteht])

  function fertig(neueKonfig?: AppKonfig) {
    if (neueKonfig) setKonfig(neueKonfig)
    setPhase('bereit')
  }

  let inhalt: ReactNode
  if (phase === 'laedt' || konfig === null) {
    inhalt = <Startbild />
  } else if (phase === 'einrichtung' || phase === 'kopplung' || phase === 'sandbox') {
    inhalt = (
      <Wizard
        konfig={konfig}
        startSchritt={
          phase === 'sandbox'
            ? 'sandbox'
            : phase === 'kopplung' || konfig.backend_url
              ? 'kopplung'
              : 'backend'
        }
        nurDieserSchritt={phase !== 'einrichtung'}
        onFertig={fertig}
      />
    )
  } else {
    inhalt = (
      <Routes>
        <Route
          path="/ai"
          element={
            <Hauptseite
              bereich="ki"
              konfig={konfig}
              offeneUebernahme={offeneUebernahme}
              onKonfigAenderung={ladeKonfigNeu}
            />
          }
        />
        <Route
          path="/kalender"
          element={
            <Hauptseite
              bereich="kalender"
              konfig={konfig}
              offeneUebernahme={offeneUebernahme}
              onKonfigAenderung={ladeKonfigNeu}
            />
          }
        />
        <Route path="/calendar" element={<Navigate to="/kalender" replace />} />
        <Route
          path="/notizen"
          element={
            <Hauptseite
              bereich="notizen"
              konfig={konfig}
              offeneUebernahme={offeneUebernahme}
              onKonfigAenderung={ladeKonfigNeu}
            />
          }
        />
        <Route path="/notes" element={<Navigate to="/notizen" replace />} />
        <Route
          path="/gedaechtnis"
          element={
            <Hauptseite
              bereich="gedaechtnis"
              konfig={konfig}
              offeneUebernahme={offeneUebernahme}
              onKonfigAenderung={ladeKonfigNeu}
            />
          }
        />
        <Route
          path="/einstellungen"
          element={
            <Hauptseite
              bereich="einstellungen"
              konfig={konfig}
              offeneUebernahme={offeneUebernahme}
              onKonfigAenderung={ladeKonfigNeu}
            />
          }
        />
        <Route
          path="/tresor"
          element={
            <Hauptseite
              bereich="tresor"
              konfig={konfig}
              offeneUebernahme={offeneUebernahme}
              onKonfigAenderung={ladeKonfigNeu}
            />
          }
        />
        <Route path="/vault" element={<Navigate to="/tresor" replace />} />
        <Route
          path="/privacy"
          element={
            <div className="mx-auto w-full max-w-4xl p-4 md:p-6">
              <Privacy />
            </div>
          }
        />
        <Route path="*" element={<Navigate to="/ai" replace />} />
      </Routes>
    )
  }

  return (
    <MemoryRouter initialEntries={['/ai']}>
      <NavigationEmpfaenger />
      <div className="relative h-[100dvh] max-h-[100dvh] w-full overflow-hidden bg-background text-on-surface pb-[env(safe-area-inset-bottom,0px)] pl-[env(safe-area-inset-left,0px)] pr-[env(safe-area-inset-right,0px)] flex flex-col">
        <div className="msm-deep-grid pointer-events-none absolute inset-0 opacity-30" />
        <div className="relative z-10 flex h-full max-h-full min-h-0 flex-1 flex-col overflow-hidden">{inhalt}</div>

        {phase === 'bereit' && (
          <>
            <SprachwacheHaupt />
            <KalibrierungsHinweis />
            <AiRunNotice />
            <ServerIncidentNotifier />
            <PanelPopupModal />
            {isAndroid && <OverlayFenster inApp={true} />}
          </>
        )}
        <DesktopAktionKarte offenerAuftragId={offeneUebernahme} />
        <Uebernahmekarte offenerAuftragId={offeneUebernahme} />
        <Aufraeumkarte offenerAuftragId={offeneUebernahme} />
        <SchliessenDialog />
        <ToastContainer />
        <ConfirmDialog />
        <PromptDialog />
        {splash && <Splash onFertig={splashBeenden} />}
      </div>
    </MemoryRouter>
  )
}

function NavigationEmpfaenger() {
  const navigate = useNavigate()
  useEffect(() => {
    const unlisten = listen<string>('mss:navigiere-zu', (event) => {
      if (event.payload) {
        navigate(event.payload)
      }
    })
    return () => {
      void unlisten.then((u) => u())
    }
  }, [navigate])
  return null
}

function Startbild() {
  const { t } = useTranslation()
  return (
    <main className="flex flex-1 items-center justify-center text-on-surface-variant">
      <p className="text-sm">{t('mss.app.startet')}</p>
    </main>
  )
}

/**
 * Der kleine Dialog hinter dem X. Rust hält das Fenster an
 * (`mss:schliessen-angefragt`) und hier entscheidet der Mensch: in den
 * Hintergrund (Standard — ein Companion, der beim Wegklicken stirbt, wäre
 * keiner) oder wirklich beenden. Escape heißt: nichts von beidem.
 */
function SchliessenDialog() {
  const { t } = useTranslation()
  const [offen, setOffen] = useState(false)

  useEffect(() => {
    const abo = listen('mss:schliessen-angefragt', () => setOffen(true))
    return () => {
      void abo.then((weg) => weg())
    }
  }, [])

  useEffect(() => {
    if (!offen) return
    const taste = (ereignis: KeyboardEvent) => {
      if (ereignis.key === 'Escape') setOffen(false)
    }
    window.addEventListener('keydown', taste)
    return () => window.removeEventListener('keydown', taste)
  }, [offen])

  if (!offen) return null

  function hintergrund() {
    setOffen(false)
    void hauptfensterVerstecken()
  }

  return (
    <div
      className="msm-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={t('mss.schliessen.titel')}
    >
      <div className="msm-card w-full max-w-md p-5">
        <h2 className="text-sm font-medium text-on-surface">{t('mss.schliessen.titel')}</h2>
        <p className="mt-1 text-xs text-on-surface-variant">{t('mss.schliessen.frage')}</p>
        <div className="mt-4 flex items-center justify-between gap-2">
          <Button variant="ghost" size="sm" onClick={() => setOffen(false)}>
            {t('mss.schliessen.abbrechen')}
          </Button>
          <Button variant="destructive" size="sm" onClick={() => void appBeenden()}>
            {t('mss.schliessen.beenden')}
          </Button>
          <Button autoFocus size="sm" onClick={hintergrund}>
            {t('mss.schliessen.hintergrund')}
          </Button>
        </div>
      </div>
    </div>
  )
}

function KalibrierungsHinweis() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const agentName = useAuthStore((s) => s.user?.agent_name?.trim() || 'Assistent')
  const [altesWort, setAltesWort] = useState<string | null>(null)

  useEffect(() => {
    void wakewordStand()
      .then((stand) => {
        if (stand.trainiert && stand.wort && stand.wort !== agentName) {
          setAltesWort(stand.wort)
        }
      })
      .catch(() => undefined)
  }, [])

  if (altesWort === null) return null

  return (
    <div
      className="msm-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={t('mss.kalibrierung.titel')}
    >
      <div className="msm-card w-full max-w-sm p-5">
        <h2 className="text-sm font-medium text-on-surface">{t('mss.kalibrierung.titel')}</h2>
        <p className="mt-1 text-xs text-on-surface-variant">
          {t('mss.kalibrierung.frage', { neu: agentName, alt: altesWort })}
        </p>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={() => setAltesWort(null)}>
            {t('mss.kalibrierung.spaeter')}
          </Button>
          <Button
            autoFocus
            onClick={() => {
              setAltesWort(null)
              navigate('/einstellungen?tab=wakeword')
            }}
          >
            {t('mss.kalibrierung.jetzt')}
          </Button>
        </div>
      </div>
    </div>
  )
}

function SprachwacheHaupt() {
  const ort = useLocation()
  const navigate = useNavigate()
  const inSprache =
    ort.pathname.startsWith('/ai') &&
    new URLSearchParams(ort.search).get('ansicht') === 'sprache'

  useEffect(() => {
    if (inSprache) {
      void sprachstartMelden('haupt')
    }
  }, [inSprache])

  useEffect(
    () =>
      beiFremdemSprachstart('haupt', () => {
        if (inSprache) {
          navigate('/ai', { replace: true })
        }
      }),
    [inSprache, navigate],
  )

  return null
}

/**
 * Kopfleiste + Inhalt.
 * Aufgeräumte Topbar mit Profil-Avatar-Dropdown und daumenfreundlicher Bedienung.
 */
function Hauptseite({
  bereich,
  konfig,
  offeneUebernahme,
  onKonfigAenderung,
}: {
  bereich: 'ki' | 'kalender' | 'notizen' | 'gedaechtnis' | 'tresor' | 'einstellungen'
  konfig: AppKonfig | null
  offeneUebernahme: string | null
  onKonfigAenderung?: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const user = useAuthStore((s) => s.user)
  const darfChatten = useHasPermission('ai.chat.use')
  const darfKalender = useHasPermission('ai.calendar.use')
  const darfNotizen = useHasPermission('ai.notes.use')
  const darfGedaechtnis = useHasPermission('ai.memory.use')
  const [darfTresor, setDarfTresor] = useState(true)
  const [mobileMenuOffen, setMobileMenuOffen] = useState(false)

  useEffect(() => {
    let active = true
    api<{ vault_enabled?: boolean }>('/api/panel/settings/public')
      .then((res) => {
        if (active && res && typeof res.vault_enabled === 'boolean') {
          setDarfTresor(res.vault_enabled)
        }
      })
      .catch(() => {})
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!darfTresor && bereich === 'tresor') {
      navigate('/ai')
    }
  }, [darfTresor, bereich, navigate])

  const agentName = user?.agent_name?.trim() || 'Assistent'
  const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)

  const profileItems: ProfileDropdownItem[] = [
    {
      key: 'settings',
      label: t('mss.app.einstellungen', 'Einstellungen'),
      icon: <SettingsIcon className="h-4 w-4" />,
      onClick: () => navigate('/einstellungen'),
    },
    {
      key: 'logout',
      label: t('mss.app.abmelden', 'Abmelden'),
      icon: <LogOut className="h-4 w-4" />,
      onClick: () => void abmelden(),
      tone: 'danger',
    },
  ]

  useEffect(() => {
    onKonfigAenderung?.()
  }, [location.pathname, onKonfigAenderung])

  return (
    <>
      <header className="msm-topbar flex pt-[env(safe-area-inset-top,0px)] h-[calc(3.5rem+env(safe-area-inset-top,0px))] sm:h-[calc(4rem+env(safe-area-inset-top,0px))] items-center justify-between px-3 sm:px-4 md:px-6">
        {/* Linke Seite: Agent-Name & Aktivitätsanzeige */}
        <div className="flex min-w-0 items-center gap-2 sm:gap-3">
          <div className="min-w-0">
            <h1 className="truncate font-headline text-base sm:text-title-lg font-bold text-on-surface">{agentName}</h1>
          </div>
          {offeneUebernahme && (
            <div className="flex items-center gap-1 rounded-full border border-status-warning/40 bg-status-warning/10 px-2 py-0.5 text-[11px] font-medium text-status-warning animate-pulse">
              <Eye className="h-3 w-3" aria-hidden="true" />
              <span>{t('mss.einstellungen.banner.aktivitaetLaeuft')}</span>
            </div>
          )}
        </div>

        {/* Desktop-Navigation mit Schnellzugriff */}
        <nav className="hidden md:flex items-center gap-1.5" aria-label={t('mss.app.bereiche')}>
          <Reiter
            aktiv={bereich === 'ki'}
            onClick={() => navigate('/ai')}
            icon={<MessageSquare className="h-4 w-4" />}
            label={t('mss.app.chat')}
          />
          {darfKalender && (
            <Reiter
              aktiv={bereich === 'kalender'}
              onClick={() => navigate('/kalender')}
              icon={<CalendarIcon className="h-4 w-4" />}
              label={t('mss.app.kalender')}
            />
          )}
          {darfNotizen && (
            <Reiter
              aktiv={bereich === 'notizen'}
              onClick={() => navigate('/notizen')}
              icon={<StickyNote className="h-4 w-4" />}
              label={t('mss.app.notizen', 'Notizen')}
            />
          )}
          {darfGedaechtnis && (
            <Reiter
              aktiv={bereich === 'gedaechtnis'}
              onClick={() => navigate('/gedaechtnis')}
              icon={<BrainCircuit className="h-4 w-4" />}
              label={t('mss.app.gedaechtnis')}
            />
          )}
          {darfTresor && (
            <Reiter
              aktiv={bereich === 'tresor'}
              onClick={() => navigate('/tresor')}
              icon={<KeyRound className="h-4 w-4" />}
              label={t('mss.app.tresor', 'Passwort-Manager')}
            />
          )}
        </nav>

        {/* Rechte Seite: Glocke + Profil-Avatar mit Dropdown-Menü */}
        <div className="flex items-center gap-2">
          <BenachrichtigungsGlocke />

          {/* Profil-Avatar Dropdown */}
          <ProfileDropdown
            user={user}
            items={profileItems}
            placement="bottom-right"
          />

          {/* Mobile Menü-Knopf */}
          <button
            type="button"
            onClick={() => setMobileMenuOffen(!mobileMenuOffen)}
            className="p-2 rounded-xl border border-outline-variant/40 bg-surface-container-low text-on-surface hover:bg-surface-container md:hidden transition-colors"
            aria-label={t('mss.app.menueOeffnen')}
            aria-expanded={mobileMenuOffen}
          >
            {mobileMenuOffen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>
      </header>

      {/* Mobile Drawer / Overlay */}
      {mobileMenuOffen && (
        <div className="fixed inset-0 z-50 flex flex-col justify-end bg-background/80 backdrop-blur-sm md:hidden animate-fade-in">
          <div className="fixed inset-0" onClick={() => setMobileMenuOffen(false)} />
          <div className="relative z-10 w-full max-h-[85vh] overflow-y-auto overscroll-contain rounded-t-2xl border-t border-outline-variant/50 bg-surface-container-low p-4 pb-8 shadow-2xl space-y-3">
            <div className="flex items-center justify-between pb-3 border-b border-outline-variant/40">
              <div className="flex items-center gap-3 min-w-0">
                <Avatar
                  src={user?.avatar_url}
                  name={user?.username}
                  size="md"
                />
                <div className="min-w-0">
                  <span className="font-headline font-semibold text-sm text-on-surface block truncate">{user?.username || agentName}</span>
                  <p className="text-xs text-on-surface-variant truncate">{user?.email}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setMobileMenuOffen(false)}
                className="p-1 rounded-lg text-on-surface-variant hover:text-on-surface"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <nav className="flex flex-col gap-1 pt-1" aria-label={t('mss.app.bereiche')}>
              <button
                type="button"
                onClick={() => { navigate('/ai'); setMobileMenuOffen(false); }}
                className={`flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-colors ${
                  bereich === 'ki'
                    ? 'bg-primary/15 text-primary border border-primary/30'
                    : 'text-on-surface hover:bg-surface-container-high'
                }`}
              >
                <MessageSquare className="h-4 w-4" />
                <span>{t('mss.app.chat')}</span>
              </button>

              {darfKalender && (
                <button
                  type="button"
                  onClick={() => { navigate('/kalender'); setMobileMenuOffen(false); }}
                  className={`flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-colors ${
                    bereich === 'kalender'
                      ? 'bg-primary/15 text-primary border border-primary/30'
                      : 'text-on-surface hover:bg-surface-container-high'
                  }`}
                >
                  <CalendarIcon className="h-4 w-4" />
                  <span>{t('mss.app.kalender')}</span>
                </button>
              )}

              {darfNotizen && (
                <button
                  type="button"
                  onClick={() => { navigate('/notizen'); setMobileMenuOffen(false); }}
                  className={`flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-colors ${
                    bereich === 'notizen'
                      ? 'bg-primary/15 text-primary border border-primary/30'
                      : 'text-on-surface hover:bg-surface-container-high'
                  }`}
                >
                  <StickyNote className="h-4 w-4" />
                  <span>{t('mss.app.notizen', 'Notizen')}</span>
                </button>
              )}

              {darfGedaechtnis && (
                <button
                  type="button"
                  onClick={() => { navigate('/gedaechtnis'); setMobileMenuOffen(false); }}
                  className={`flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-colors ${
                    bereich === 'gedaechtnis'
                      ? 'bg-primary/15 text-primary border border-primary/30'
                      : 'text-on-surface hover:bg-surface-container-high'
                  }`}
                >
                  <BrainCircuit className="h-4 w-4" />
                  <span>{t('mss.app.gedaechtnis')}</span>
                </button>
              )}

              {darfTresor && (
                <button
                  type="button"
                  onClick={() => { navigate('/tresor'); setMobileMenuOffen(false); }}
                  className={`flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-colors ${
                    bereich === 'tresor'
                      ? 'bg-primary/15 text-primary border border-primary/30'
                      : 'text-on-surface hover:bg-surface-container-high'
                  }`}
                >
                  <KeyRound className="h-4 w-4" />
                  <span>{t('mss.app.tresor', 'Passwort-Manager')}</span>
                </button>
              )}

              <button
                type="button"
                onClick={() => { navigate('/einstellungen'); setMobileMenuOffen(false); }}
                className={`flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-colors ${
                  bereich === 'einstellungen'
                    ? 'bg-primary/15 text-primary border border-primary/30'
                    : 'text-on-surface hover:bg-surface-container-high'
                }`}
              >
                <SettingsIcon className="h-4 w-4" />
                <span>{t('mss.app.einstellungen')}</span>
              </button>
            </nav>

            <div className="pt-2 border-t border-outline-variant/40">
              <button
                type="button"
                onClick={() => { setMobileMenuOffen(false); void abmelden(); }}
                className="flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium text-status-error hover:bg-status-error/10 transition-colors"
              >
                <LogOut className="h-4 w-4" />
                <span>{t('mss.app.abmelden')}</span>
              </button>
            </div>
          </div>
        </div>
      )}
      <main className={`relative flex flex-1 min-h-0 flex-col overflow-hidden ${bereich === 'ki' || bereich === 'tresor' ? 'p-0 bg-surface' : 'p-margin-mobile md:p-margin-desktop'}`}>
        <div className="relative z-10 flex h-full w-full flex-1 min-h-0 flex-col overflow-hidden">
          {bereich === 'ki' ? (
            darfChatten ? (
              <div className="flex h-full w-full flex-1 min-h-0 flex-col overflow-hidden bg-surface">
                {konfig && !isAndroid && !konfig.computer_use_aktiv && (
                  <div className="m-2 sm:m-3 mb-0 shrink-0 flex items-center justify-between rounded-lg border border-outline-variant/40 bg-surface-container-low/60 p-3 text-xs">
                    <div className="flex items-center gap-2 text-on-surface-variant">
                      <ShieldAlert className="h-4 w-4 shrink-0 text-status-warning" aria-hidden="true" />
                      <span>{t('mss.einstellungen.banner.computerUseHinweis')}</span>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => navigate('/einstellungen?tab=desktop')}
                    >
                      {t('mss.einstellungen.banner.computerUseLink')}
                    </Button>
                  </div>
                )}
                <div className="flex-1 min-h-0 overflow-hidden">
                  <Ai />
                </div>
              </div>
            ) : (
              <KeinChatrecht />
            )
          ) : bereich === 'kalender' ? (
            <div className="mx-auto w-full max-w-6xl flex-1 min-h-0 overflow-y-auto pb-8">
              <Calendar />
            </div>
          ) : bereich === 'notizen' ? (
            <div className="mx-auto w-full max-w-6xl flex-1 min-h-0 overflow-y-auto pb-8">
              <Notes />
            </div>
          ) : bereich === 'gedaechtnis' ? (
            <div className="mx-auto w-full max-w-3xl flex-1 min-h-0 overflow-y-auto pb-8">
              <AiMemoryManager />
            </div>
          ) : bereich === 'tresor' ? (
            <div className="flex h-full w-full flex-1 min-h-0 flex-col overflow-hidden">
              <VaultView />
            </div>
          ) : (
            <div className="mx-auto w-full max-w-3xl flex-1 min-h-0 overflow-y-auto pb-8">
              <Einstellungen onKonfigAenderung={onKonfigAenderung} />
            </div>
          )}
        </div>
      </main>
    </>
  )
}

/** Pill-Optik mit Icon und Label. */
function Reiter({ aktiv, onClick, label, icon }: { aktiv: boolean; onClick: () => void; label: string; icon?: ReactNode }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={aktiv}
      className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
        aktiv
          ? 'border-primary/40 bg-primary/10 text-primary'
          : 'border-outline-variant/40 bg-surface-container-low/40 text-on-surface-variant hover:text-on-surface'
      }`}
    >
      {icon}
      <span>{label}</span>
    </button>
  )
}

function KeinChatrecht() {
  const { t } = useTranslation()
  return (
    <div className="msm-card mx-auto mt-16 max-w-md p-6 text-center">
      <p className="text-sm text-on-surface-variant">{t('mss.app.keinChatrecht')}</p>
    </div>
  )
}
