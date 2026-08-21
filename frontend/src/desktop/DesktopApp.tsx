/**
 * Hauptfenster der Desktop-App — die Panel-Oberfläche in einer Tauri-Hülle.
 *
 * Ablauf beim Start: Konfiguration laden → ohne Adresse oder Einrichtung den
 * Assistenten zeigen → sonst still über den OS-Tresor anmelden → gelingt das
 * nicht, nur den Kopplungsschritt zeigen. Angemeldet rendert die App die
 * **echte** KI-Seite des Panels (`pages/Ai`) — Chat, Realtime, Guardian,
 * Aufgaben, Worker — plus die Desktop-Einstellungen.
 *
 * Der Router ist ein MemoryRouter mit Start `/ai`: die Panel-Komponenten
 * navigieren mit `navigate('/ai?ansicht=…')` (Glocke, Guardian, Aufgaben),
 * und genau diese Route gibt es hier. Eine Adressleiste hat das Fenster nicht.
 */
import { useEffect, useState, type ReactNode } from 'react'
import { MemoryRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { listen } from '@tauri-apps/api/event'
import { useTranslation } from 'react-i18next'

import { AiMemoryManager } from '@/components/ai/AiMemoryManager'
import { AiRunNotice } from '@/components/ai/AiRunNotice'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { PromptDialog } from '@/components/ui/PromptDialog'
import { ToastContainer } from '@/components/ui/ToastContainer'
import { Button } from '@/Singra/UI'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Ai } from '@/pages/Ai'
import { useAuthStore } from '@/stores/authStore'
import { abmelden } from './auth'
import { Einstellungen } from './Einstellungen'
import { Splash } from './Splash'
import { Uebernahmekarte } from './Uebernahmekarte'
import { Wizard } from './Wizard'
import {
  beiFremdemSprachstart,
  sprachstartMelden,
  sprachzustandVerdrahten,
} from './sprachKoordination'
import {
  appBeenden,
  hauptfensterVerstecken,
  konfigLaden,
  wakewordStand,
  type AppKonfig,
} from './tauri'
import { stillAnmelden } from './transport'
import { useAuftragsschleife } from './useAuftragsschleife'

type Phase = 'laedt' | 'einrichtung' | 'kopplung' | 'bereit'

export function DesktopApp() {
  const [phase, setPhase] = useState<Phase>('laedt')
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  // Die Boot-Sequenz läuft über allem, während darunter Konfiguration und
  // die stille Sitzungserneuerung schon laden — wie bei einem Spielstart.
  const [splash, setSplash] = useState(true)
  const angemeldet = useAuthStore((s) => s.isAuthenticated)
  // Die Aufträge des Panels holt der Rechner selbst ab — aber erst, wenn
  // das Gerät gekoppelt ist: vorher gibt es kein Token und jede Frage wäre
  // ein 401. Läuft weiter, auch wenn das Fenster im Tray liegt.
  const offeneUebernahme = useAuftragsschleife(phase === 'bereit')

  useEffect(() => {
    void (async () => {
      try {
        const geladen = await konfigLaden()
        setKonfig(geladen)
        if (!geladen.backend_url || !geladen.eingerichtet) {
          setPhase('einrichtung')
          return
        }
        if (await stillAnmelden()) {
          await useAuthStore.getState().checkAuth()
          setPhase(useAuthStore.getState().isAuthenticated ? 'bereit' : 'kopplung')
        } else {
          setPhase('kopplung')
        }
      } catch {
        setPhase('einrichtung')
      }
    })()
  }, [])

  // Endet die Sitzung im Betrieb (Refresh verbrannt, Zugang entzogen), führt
  // jeder Weg über `clearSession` — und von dort zurück zur Kopplung.
  useEffect(() => {
    if (phase === 'bereit' && !angemeldet) {
      setPhase('kopplung')
    }
  }, [phase, angemeldet])

  // Das erkannte Wake-Word öffnet das Overlay direkt in Rust
  // (wakeword.rs → sprachsitzung_starten) — ein verstecktes Hauptfenster
  // darf gedrosselt sein, das Wake-Word muss trotzdem tragen. Hier läuft
  // deshalb kein Listener mehr.

  // Tray-Farbe und Ducking folgen auch einer Sitzung in diesem Fenster.
  useEffect(() => {
    if (phase !== 'bereit') return
    return sprachzustandVerdrahten()
  }, [phase])

  function fertig(neueKonfig: AppKonfig) {
    setKonfig(neueKonfig)
    setPhase('bereit')
  }

  let inhalt: ReactNode
  if (phase === 'laedt' || konfig === null) {
    inhalt = <Startbild />
  } else if (phase === 'einrichtung' || phase === 'kopplung') {
    inhalt = (
      <Wizard
        konfig={konfig}
        // Nach dem Adress-Schritt lädt die App neu (der API-Origin steht im
        // Modulgraph fest); eine gespeicherte Adresse heißt deshalb: dort
        // weitermachen, wo der Neustart unterbrochen hat.
        startSchritt={
          phase === 'kopplung' || konfig.backend_url ? 'kopplung' : 'backend'
        }
        nurKopplung={phase === 'kopplung'}
        onFertig={fertig}
      />
    )
  } else {
    inhalt = (
      <Routes>
        <Route path="/ai" element={<Hauptseite bereich="ki" />} />
        <Route path="/gedaechtnis" element={<Hauptseite bereich="gedaechtnis" />} />
        <Route path="/einstellungen" element={<Hauptseite bereich="einstellungen" />} />
        <Route path="*" element={<Navigate to="/ai" replace />} />
      </Routes>
    )
  }

  return (
    <MemoryRouter initialEntries={['/ai']}>
      <div className="relative min-h-screen overflow-x-clip bg-background text-on-surface">
        {/* Dasselbe Gitter wie im Panel-Shell — exakt dieselben Werte. */}
        <div className="msm-deep-grid pointer-events-none absolute inset-0 opacity-30" />
        <div className="relative z-10 flex min-h-screen flex-col">{inhalt}</div>

        {phase === 'bereit' && (
          <>
            <SprachwacheHaupt />
            <KalibrierungsHinweis />
            {/* Die Glocke ist mehr als eine Meldung: ihr Takt feuert das
                Zustell-Ereignis, über das der offene Chat Fremdes nachlädt. */}
            <AiRunNotice />
          </>
        )}
        {/* Über allem außer der Boot-Sequenz: eine Bitte um die Übernahme von
            Maus und Tastatur darf nicht hinter einem Reiter verschwinden. */}
        <Uebernahmekarte offenerAuftragId={offeneUebernahme} />
        {/* Immer gemountet, nicht erst ab `bereit`: das X gibt es auch im
            Assistenten. */}
        <SchliessenDialog />
        <ToastContainer />
        <ConfirmDialog />
        <PromptDialog />
        {splash && <Splash onFertig={() => setSplash(false)} />}
      </div>
    </MemoryRouter>
  )
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
        {/* Eine Zeile, drei Plätze: Abbrechen links, Beenden in der Mitte,
            Hintergrund rechts — kein Umbruch, der die Knöpfe stapelt. */}
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

/**
 * Der freundliche Hinweis nach einer Umbenennung: das Wake-Word ist immer der
 * Name des Assistenten — wurde er geändert (im Chat, im Panel-Profil, egal
 * wo), ist das trainierte Modell noch auf den alten Namen kalibriert. Einmal
 * je App-Start wird das angeboten, nie erzwungen: „Später" heißt später, und
 * das alte Wort funktioniert weiter.
 */
function KalibrierungsHinweis() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const agentName = useAuthStore((s) => s.user?.agent_name?.trim() || 'Singra')
  const [altesWort, setAltesWort] = useState<string | null>(null)

  useEffect(() => {
    void wakewordStand()
      .then((stand) => {
        if (stand.trainiert && stand.wort && stand.wort !== agentName) {
          setAltesWort(stand.wort)
        }
      })
      .catch(() => undefined)
    // Bewusst nur einmal je Start — nicht bei jedem Namenswechsel im Store.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

/**
 * Wache über den Sprachort dieses Fensters.
 *
 * Öffnet jemand hier die Sprachansicht, erfahren es alle Fenster — das
 * Overlay beendet dann seine Sitzung. Beginnt umgekehrt das Overlay, wird die
 * Sprachansicht hier verlassen: ihr Unmount beendet Mikrofon und Leitung
 * (useSprachsitzung räumt beim Verlassen auf).
 */
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
        navigate('/ai', { replace: true })
      }),
    [navigate],
  )

  return null
}

/**
 * Kopfleiste + Inhalt. Die Leiste ist bewusst 4 rem hoch wie die Panel-Topbar:
 * `pages/Ai` rechnet seine Höhe als `100dvh − (Topbar + Seitenränder)`, und
 * dieselbe Rechnung soll hier aufgehen.
 */
function Hauptseite({ bereich }: { bereich: 'ki' | 'gedaechtnis' | 'einstellungen' }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const user = useAuthStore((s) => s.user)
  const darfChatten = useHasPermission('ai.chat.use')
  // Im Panel wohnt die Ansicht unter Profil → KI; die App hat kein Profil,
  // also bekommt sie einen eigenen Reiter. Ohne das Recht rendert die
  // Komponente ohnehin nichts — dann lieber gar kein Reiter.
  const darfGedaechtnis = useHasPermission('ai.memory.use')
  const agentName = user?.agent_name?.trim() || 'Singra'

  return (
    <>
      <header className="msm-topbar flex h-16 items-center justify-between px-4 md:px-6">
        <div className="min-w-0">
          <h1 className="truncate font-headline text-title-lg text-on-surface">{agentName}</h1>
          <p className="truncate text-xs text-on-surface-variant">
            {user ? t('mss.app.angemeldetAls', { name: user.username }) : ''}
          </p>
        </div>
        <nav className="flex items-center gap-2" aria-label={t('mss.app.bereiche')}>
          <Reiter
            aktiv={bereich === 'ki'}
            onClick={() => navigate('/ai')}
            label={t('mss.app.chat')}
          />
          {darfGedaechtnis && (
            <Reiter
              aktiv={bereich === 'gedaechtnis'}
              onClick={() => navigate('/gedaechtnis')}
              label={t('mss.app.gedaechtnis')}
            />
          )}
          <Reiter
            aktiv={bereich === 'einstellungen'}
            onClick={() => navigate('/einstellungen')}
            label={t('mss.app.einstellungen')}
          />
          <Button variant="secondary" size="sm" onClick={() => void abmelden()}>
            {t('mss.app.abmelden')}
          </Button>
        </nav>
      </header>
      <main className="p-margin-mobile md:p-margin-desktop relative flex flex-1 flex-col">
        <div className="relative z-10 w-full flex-1">
          {bereich === 'ki' ? (
            darfChatten ? (
              <Ai />
            ) : (
              <KeinChatrecht />
            )
          ) : bereich === 'gedaechtnis' ? (
            // Dieselbe Komponente wie im Panel unter Profil → KI, Standard-
            // Scope „user": die persönlichen Einträge samt Servernotizen.
            <div className="mx-auto w-full max-w-3xl">
              <AiMemoryManager />
            </div>
          ) : (
            <Einstellungen />
          )}
        </div>
      </main>
    </>
  )
}

/** Dieselbe Pill-Optik wie die Ansichts-Umschalter auf der KI-Seite. */
function Reiter({ aktiv, onClick, label }: { aktiv: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={aktiv}
      className={`rounded-lg border px-3.5 py-2 text-sm transition-colors ${
        aktiv
          ? 'border-primary/40 bg-primary/10 text-primary'
          : 'border-outline-variant/40 bg-surface-container-low/40 text-on-surface-variant hover:text-on-surface'
      }`}
    >
      {label}
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
