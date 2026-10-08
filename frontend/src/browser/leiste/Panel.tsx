/**
 * Das Panel rechts neben der Seite. Welches offen ist, sagt die Route
 * (`module.ts`). Die Breite zieht man am linken Rand; sie bleibt gespeichert.
 *
 * Die MSM-Seiten sind dieselben wie in MSS und werden erst geladen, wenn ihr
 * Panel zum ersten Mal aufgeht: das Startbündel des Browsers trägt sie nicht.
 */
import { lazy, Suspense, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Kurzinfo } from '@/Singra/UI'
import { Spinner } from '@/components/ui/Spinner'
import { Zustandsflaeche } from '@/Singra/UI/Zustandsflaeche'

import { useEinstellungenStore } from '../services/einstellungenStore'
import { istGekoppelt, useSitzung } from '../services/sitzung'
import { BROWSER_EINTRAEGE, EINSTELLUNGEN_EINTRAG, ENTWICKLER_EINTRAG, KOPPELN_EINTRAG, MSM_EINTRAEGE, panelAusPfad, type PanelId } from './module'
import { useSichtbareModule } from './Seitenleiste'

const Ai = lazy(() => import('@/pages/Ai').then((m) => ({ default: m.Ai })))
const Messenger = lazy(() => import('@/pages/Messenger').then((m) => ({ default: m.Messenger })))
const Benutzerprofil = lazy(() => import('@/pages/Benutzerprofil').then((m) => ({ default: m.Benutzerprofil })))
const Notes = lazy(() => import('@/pages/Notes').then((m) => ({ default: m.Notes })))
const Calendar = lazy(() => import('@/pages/Calendar').then((m) => ({ default: m.Calendar })))
const VaultView = lazy(() => import('@/desktop/vault/VaultView').then((m) => ({ default: m.VaultView })))
const LesezeichenPanel = lazy(() => import('../panels/Lesezeichen').then((m) => ({ default: m.LesezeichenPanel })))
const VerlaufPanel = lazy(() => import('../panels/Verlauf').then((m) => ({ default: m.VerlaufPanel })))
const DownloadsPanel = lazy(() => import('../panels/Downloads').then((m) => ({ default: m.DownloadsPanel })))
const EinstellungenPanel = lazy(() => import('../panels/Einstellungen').then((m) => ({ default: m.EinstellungenPanel })))
const KopplungPanel = lazy(() => import('../panels/Kopplung').then((m) => ({ default: m.KopplungPanel })))
const EntwicklerPanel = lazy(() => import('../entwickler/EntwicklerPanel').then((m) => ({ default: m.EntwicklerPanel })))

export const PANEL_MIN = 320
/** So viel bleibt der Seite mindestens, samt Leiste links. */
const PLATZ_FUER_SEITE = 320

const panelMax = () => Math.max(PANEL_MIN, window.innerWidth - PLATZ_FUER_SEITE)

function Laedt() {
  return (
    <div className="flex h-full items-center justify-center">
      <Spinner />
    </div>
  )
}

/** MSM-Inhalt nur mit Kopplung; ohne Panel nur, was einen lokalen Spiegel hat. */
function MitKopplung({ id, children }: { id: PanelId; children: ReactNode }) {
  const { t } = useTranslation()
  const stand = useSitzung((s) => s.stand)
  const eintrag = MSM_EINTRAEGE.find((e) => e.id === id)
  if (stand === 'pruefen') return <Laedt />
  if (!istGekoppelt(stand)) return <KopplungPanel grund={id} />
  if (stand === 'offline' && !eintrag?.offline) {
    return <Zustandsflaeche art="offline" titel={t('browser.panel.offlineTitel')} text={t('browser.panel.offlineText')} />
  }
  return <>{children}</>
}

function Inhalt({ id, pfad }: { id: PanelId; pfad: string }) {
  switch (id) {
    case 'ai':
      return <MitKopplung id={id}><Ai /></MitKopplung>
    case 'chat':
      return (
        <MitKopplung id={id}>
          {pfad.startsWith('/user/') ? (
            <div className="h-full overflow-y-auto"><Benutzerprofil /></div>
          ) : (
            <div className="flex h-full min-h-0 flex-col overflow-hidden bg-surface"><Messenger /></div>
          )}
        </MitKopplung>
      )
    case 'notizen':
      return <MitKopplung id={id}><div className="h-full overflow-y-auto pb-8"><Notes /></div></MitKopplung>
    case 'kalender':
      return <MitKopplung id={id}><div className="h-full overflow-y-auto pb-8"><Calendar /></div></MitKopplung>
    case 'tresor':
      return <MitKopplung id={id}><div className="flex h-full min-h-0 flex-col overflow-hidden"><VaultView /></div></MitKopplung>
    case 'lesezeichen':
      return <LesezeichenPanel />
    case 'verlauf':
      return <VerlaufPanel />
    case 'downloads':
      return <DownloadsPanel />
    case 'einstellungen':
      return <EinstellungenPanel />
    case 'koppeln':
      return <KopplungPanel />
    case 'entwickler':
      return <EntwicklerPanel />
  }
}

function useBreiteZiehen() {
  const setzen = useEinstellungenStore((s) => s.setzen)
  const start = useRef<{ x: number; breite: number } | null>(null)
  return {
    onPointerDown: (e: React.PointerEvent) => {
      e.currentTarget.setPointerCapture(e.pointerId)
      start.current = { x: e.clientX, breite: useEinstellungenStore.getState().panelBreite }
    },
    onPointerMove: (e: React.PointerEvent) => {
      if (!start.current) return
      const breite = start.current.breite + (start.current.x - e.clientX)
      setzen({ panelBreite: Math.max(PANEL_MIN, Math.min(panelMax(), breite)) })
    },
    onPointerUp: () => {
      start.current = null
    },
    onKeyDown: (e: React.KeyboardEvent) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      e.preventDefault()
      // Mit gezogener Breite über dem Höchstwert zählt die sichtbare Breite.
      const jetzt = Math.min(useEinstellungenStore.getState().panelBreite, panelMax())
      const breite = jetzt + (e.key === 'ArrowLeft' ? 24 : -24)
      setzen({ panelBreite: Math.max(PANEL_MIN, Math.min(panelMax(), breite)) })
    },
  }
}

export function Panel() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const breite = useEinstellungenStore((s) => s.panelBreite)
  const sichtbar = useSichtbareModule()
  const ziehen = useBreiteZiehen()
  const id = panelAusPfad(pathname)
  if (!id) return null
  const eintrag = [...MSM_EINTRAEGE, ...BROWSER_EINTRAEGE, EINSTELLUNGEN_EINTRAG, KOPPELN_EINTRAG, ENTWICKLER_EINTRAG].find((e) => e.id === id)
  // Ein ausgeblendetes oder abgeschaltetes Modul bleibt zu, auch wenn eine Seite dorthin springt.
  if (eintrag?.modul && !sichtbar.has(id)) return null
  const titel = eintrag ? t(eintrag.name) : ''

  return (
    <aside
      aria-label={titel}
      style={{ width: `max(${PANEL_MIN}px, min(${breite}px, 100vw - ${PLATZ_FUER_SEITE}px))` }}
      className="relative flex shrink-0 flex-col border-l border-outline-variant bg-surface-container-low"
    >
      {/* Ganz im Panel: links davon liegt die Seite, ein Fenster über der Oberfläche. */}
      <div
        role="separator"
        tabIndex={0}
        aria-orientation="vertical"
        aria-valuenow={breite}
        aria-valuemin={PANEL_MIN}
        aria-label={t('browser.panel.breite')}
        className="group/griff absolute inset-y-0 left-0 z-10 flex w-2 cursor-col-resize touch-none items-center justify-center outline-none hover:bg-primary/10 focus-visible:bg-primary/10 active:bg-primary/15"
        {...ziehen}
      >
        <span aria-hidden="true" className="h-10 w-1 rounded-full bg-outline transition-colors group-hover/griff:bg-primary group-focus-visible/griff:bg-primary group-active/griff:bg-primary" />
      </div>
      <div className="flex h-10 shrink-0 items-center justify-between border-b border-outline-variant px-3">
        <h2 className="text-title-sm text-on-surface">{titel}</h2>
        <Kurzinfo text={t('browser.panel.schliessen')} seite="ende">
          <button
            type="button"
            onClick={() => navigate('/')}
            aria-label={t('browser.panel.schliessen')}
            className="flex h-7 w-7 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </Kurzinfo>
      </div>
      <div className="msb-bereich min-h-0 flex-1 overflow-hidden">
        <Suspense fallback={<Laedt />}>
          <Inhalt id={id} pfad={pathname} />
        </Suspense>
      </div>
    </aside>
  )
}
