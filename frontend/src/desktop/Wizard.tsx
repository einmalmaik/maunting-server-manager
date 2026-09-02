/**
 * Der Einrichtungs-Assistent: Adresse → Kopplung → Personalisierung →
 * Sandbox → Wake-Word → fertig.
 *
 * Jeder Schritt ist eine kleine lokale Komponente mit einem einzigen
 * Auftrag; der Wizard hält nur den Schrittzeiger und reicht Ergebnisse
 * weiter. Persistiert wird sofort nach jedem Schritt (`konfig_speichern`) —
 * ein Abbruch mittendrin verliert nichts außer dem Rest des Weges.
 *
 * Eine Eigenheit hat der erste Schritt: nach dem Speichern der Adresse lädt
 * die App **neu** (`location.reload`). Der API-Origin steht im Modulgraph
 * fest (`config/api.ts`), und der Neustart ist der eine ehrliche Weg, ihn zu
 * setzen — der Bootstrap sieht danach die gespeicherte Adresse und steigt
 * direkt beim Kopplungsschritt wieder ein.
 */
import { useEffect, useState } from 'react'
import { open as ordnerDialog } from '@tauri-apps/plugin-dialog'
import { Camera } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, Dropdown } from '@/Singra/UI'
import { Input } from '@/components/ui/Input'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { QrScannerModal } from './QrScannerModal'
import { WakewordEinrichtung } from './WakewordEinrichtung'
import { erreichbar, koppeln } from './auth'
import { konfigSpeichern, type AppKonfig } from './tauri'

export type Schritt = 'backend' | 'kopplung' | 'personalisierung' | 'sandbox' | 'wakeword'

const isAndroid = typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent)
const REIHENFOLGE: Schritt[] = isAndroid
  ? ['backend', 'kopplung', 'personalisierung', 'wakeword']
  : ['backend', 'kopplung', 'personalisierung', 'sandbox', 'wakeword']

interface WizardProps {
  konfig: AppKonfig
  startSchritt?: Schritt
  /**
   * Nur `startSchritt`, dann zurück in die App — für alles, was einer
   * eingerichteten Installation nachträglich fehlt: der verlorene Zugang
   * (koppeln) und der weggefallene Sandbox-Ordner. Den Rest hat der Benutzer
   * längst hinter sich, und ein zweiter Kopplungscode für einen Ordner wäre
   * absurd.
   */
  nurDieserSchritt?: boolean
  onFertig: (konfig: AppKonfig) => void
}

export function Wizard({
  konfig, startSchritt = 'backend', nurDieserSchritt = false, onFertig,
}: WizardProps) {
  const { t } = useTranslation()
  const [schritt, setSchritt] = useState<Schritt>(startSchritt)
  const [stand, setStand] = useState<AppKonfig>(konfig)

  async function weiter(neuerStand?: AppKonfig) {
    const k = neuerStand ?? stand
    if (neuerStand) setStand(neuerStand)

    if (nurDieserSchritt) {
      onFertig(k)
      return
    }
    const index = REIHENFOLGE.indexOf(schritt)
    let naechsterIdx = index + 1
    let naechster = REIHENFOLGE[naechsterIdx]

    // Falls der Assistenten-Name bereits existiert, Schritt Personalisierung überspringen
    const benutzer = useAuthStore.getState().user
    if (naechster === 'personalisierung' && benutzer?.agent_name) {
      naechsterIdx += 1
      naechster = REIHENFOLGE[naechsterIdx]
    }

    // Sandbox auf Android auslassen
    if (naechster === 'sandbox' && isAndroid) {
      naechsterIdx += 1
      naechster = REIHENFOLGE[naechsterIdx]
    }

    if (naechster) {
      setSchritt(naechster)
      return
    }
    const fertig = { ...k, eingerichtet: true }
    await konfigSpeichern(fertig)
    onFertig(fertig)
  }

  useEffect(() => {
    // Falls direkt beim Startschritt Personalisierung eingestiegen wird, aber Name schon vorliegt
    const benutzer = useAuthStore.getState().user
    if (schritt === 'personalisierung' && benutzer?.agent_name && !nurDieserSchritt) {
      void weiter()
    }
  }, [])

  return (
    <main className="flex flex-1 items-center justify-center p-4 sm:p-6 pt-[max(1.5rem,env(safe-area-inset-top,0px))] pb-[max(1.5rem,env(safe-area-inset-bottom,0px))] pl-[max(1rem,env(safe-area-inset-left,0px))] pr-[max(1rem,env(safe-area-inset-right,0px))]">
      <div className="flex w-full max-w-lg flex-col gap-4">
        <header className="flex flex-col items-center gap-1">
          <h1 className="font-headline text-headline-sm text-on-surface">
            Maunting Smart System
          </h1>
          <p className="text-xs text-on-surface-variant">
            {t('mss.wizard.schrittVonBis', {
              nummer: REIHENFOLGE.indexOf(schritt) + 1,
              gesamt: REIHENFOLGE.length,
              titel: t(`mss.wizard.titel.${schritt}`),
            })}
          </p>
        </header>
        <div className="msm-card p-6">
          {schritt === 'backend' && <SchrittBackend stand={stand} />}
          {schritt === 'kopplung' && <SchrittKopplung onWeiter={weiter} />}
          {schritt === 'personalisierung' && <SchrittPersonalisierung onWeiter={weiter} />}
          {schritt === 'sandbox' && <SchrittSandbox stand={stand} onWeiter={weiter} />}
          {schritt === 'wakeword' && (
            <div className="flex flex-col gap-4">
              <WakewordEinrichtung />
              <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={() => void weiter()}>
                  {t('mss.wizard.spaeterEinrichten')}
                </Button>
                <Button onClick={() => void weiter()}>{t('mss.wizard.fertigstellen')}</Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </main>
  )
}

function Fehlerzeile({ text }: { text: string | null }) {
  if (!text) return null
  return <p className="msm-field-error">{text}</p>
}

// ── Schritt 1: API-Adresse ────────────────────────────────────────────────

/** Die Namen, unter denen der eigene Rechner sich selbst anspricht. */
const LOKALE_HOSTS = ['localhost', '127.0.0.1', '[::1]']

/**
 * Dieselbe Regel wie `konfig.rs::backend_url_verboten`: `https://` ist
 * Pflicht, `http://` nur auf dem eigenen Rechner.
 *
 * Über diese Adresse gehen der Kopplungscode und das Refresh-Token — ohne TLS
 * liest sie jeder im selben Netz mit. Rust lehnt so eine Adresse beim
 * Speichern ohnehin ab; hier fällt die Absage dort, wo der Mensch sie
 * eintippt, statt hinter einer Erreichbarkeitsprüfung, die vorher noch
 * bestätigt, dass dort ein Panel steht.
 */
function adresseErlaubt(url: string): boolean {
  if (!/^https?:\/\/.+/.test(url)) return false
  if (url.startsWith('https://')) return true
  const host = url.slice('http://'.length).split(/[/?#]/)[0].toLowerCase()
  // `http://localhost@fremder.example` wäre sonst „lokal": alles vor dem `@`
  // ist Benutzername und nicht der Host, den die App anspricht.
  if (host.includes('@')) return false
  return LOKALE_HOSTS.some((lokal) => host === lokal || host.startsWith(`${lokal}:`))
}

function SchrittBackend({ stand }: { stand: AppKonfig }) {
  const { t } = useTranslation()
  const [url, setUrl] = useState(stand.backend_url ?? '')
  const [fehler, setFehler] = useState<string | null>(null)
  const [prueft, setPrueft] = useState(false)

  async function verbinden() {
    setFehler(null)
    setPrueft(true)
    try {
      const bereinigt = url.trim().replace(/\/+$/, '')
      if (!adresseErlaubt(bereinigt)) {
        throw new Error(t('mss.wizard.adresseSchema'))
      }
      // Erreichbarkeit gegen die ausdrückliche Adresse prüfen — und dass dort
      // wirklich die API antwortet, nicht eine Oberfläche mit SPA-Fallback.
      try {
        await erreichbar(bereinigt)
      } catch (e) {
        throw e instanceof SyntaxError ? new Error(t('mss.wizard.antwortIstWebseite')) : e
      }
      await konfigSpeichern({ ...stand, backend_url: bereinigt })
      // Neustart statt Weiterreichen: siehe Kopfkommentar.
      window.location.reload()
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e))
      setPrueft(false)
    }
  }

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault()
        void verbinden()
      }}
    >
      <p className="text-sm text-on-surface-variant">{t('mss.wizard.adresseErklaerung')}</p>
      <div>
        <Input
          id="mss-adresse"
          label={t('mss.wizard.adresseLabel')}
          placeholder="https://panel.example.com"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          autoFocus
        />
        <p className="msm-field-help">{t('mss.wizard.adresseHinweis')}</p>
      </div>
      <Fehlerzeile text={fehler} />
      <div className="flex justify-end">
        <Button type="submit" disabled={prueft || url.trim() === ''}>
          {prueft ? t('mss.wizard.prueft') : t('mss.wizard.verbinden')}
        </Button>
      </div>
    </form>
  )
}

// ── Schritt 2: Kopplung ──────────────────────────────────────────────────

function SchrittKopplung({ onWeiter }: { onWeiter: () => Promise<void> }) {
  const { t } = useTranslation()
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [fehler, setFehler] = useState<string | null>(null)
  const [laeuft, setLaeuft] = useState(false)
  const [scannerOffen, setScannerOffen] = useState(false)

  async function absenden(manuellerCode?: string) {
    const zielCode = (manuellerCode ?? code).trim()
    if (!zielCode) return
    setFehler(null)
    setLaeuft(true)
    try {
      await koppeln(zielCode, name.trim())
      await onWeiter()
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e))
    } finally {
      setLaeuft(false)
    }
  }

  function onQrGefunden(gescannterCode: string) {
    const sauber = gescannterCode.trim()
    setCode(sauber)
    void absenden(sauber)
  }

  return (
    <>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          void absenden()
        }}
      >
        <p className="text-sm text-on-surface-variant">{t('mss.wizard.kopplungErklaerung')}</p>
        <div>
          <label htmlFor="mss-kopplungscode" className="text-sm font-medium text-foreground mb-1.5 block">
            {t('mss.wizard.codeLabel')}
          </label>
          <div className="flex items-center gap-2">
            <input
              id="mss-kopplungscode"
              className="msm-input h-10 flex-1 font-mono tracking-wider uppercase"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="ABCD-EFGH-JKLM"
              autoFocus
            />
            <Button
              type="button"
              variant="secondary"
              className="px-3 h-10 inline-flex items-center gap-1.5 shrink-0"
              onClick={() => setScannerOffen(true)}
              title={t('mss.wizard.qrCodeScannen', 'QR-Code per Kamera scannen')}
            >
              <Camera className="h-4 w-4 text-primary" />
              <span className="hidden sm:inline text-xs">{t('mss.wizard.scannen', 'Scannen')}</span>
            </Button>
          </div>
          <p className="msm-field-help">{t('mss.wizard.codeHinweis')}</p>
        </div>
        <div>
          <Input
            id="mss-geraetename"
            label={t('mss.wizard.geraetenameLabel')}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('mss.wizard.geraetenameBeispiel')}
          />
          <p className="msm-field-help">{t('mss.wizard.geraetenameHinweis')}</p>
        </div>
        <Fehlerzeile text={fehler} />
        <div className="flex justify-end">
          <Button type="submit" disabled={laeuft || code.trim() === ''}>
            {laeuft ? t('mss.wizard.koppelt') : t('mss.wizard.koppeln')}
          </Button>
        </div>
      </form>

      <QrScannerModal
        offen={scannerOffen}
        onSchliessen={() => setScannerOffen(false)}
        onCodeGefunden={onQrGefunden}
      />
    </>
  )
}

// ── Schritt 3: Personalisierung (Agent-Name + Zeitzone) ──────────────────

function SchrittPersonalisierung({ onWeiter }: { onWeiter: () => Promise<void> }) {
  const { t } = useTranslation()
  const benutzer = useAuthStore((s) => s.user)
  const updateUser = useAuthStore((s) => s.updateUser)
  const [name, setName] = useState(benutzer?.agent_name ?? '')
  const [zeitzone, setZeitzone] = useState(
    benutzer?.time_zone ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
  )
  const [fehler, setFehler] = useState<string | null>(null)
  const [laeuft, setLaeuft] = useState(false)

  useEffect(() => {
    if (!benutzer) {
      void useAuthStore.getState().checkAuth()
    }
  }, [benutzer])

  useEffect(() => {
    if (benutzer?.agent_name && !name) {
      setName(benutzer.agent_name)
    }
    if (benutzer?.time_zone) {
      setZeitzone(benutzer.time_zone)
    }
  }, [benutzer, name])

  async function speichern() {
    setFehler(null)
    setLaeuft(true)
    try {
      const agentName = name.trim() || null
      await api('/auth/me/agent-name', {
        method: 'PATCH',
        body: JSON.stringify({ agent_name: agentName }),
      })
      await api('/auth/me/timezone', {
        method: 'PATCH',
        body: JSON.stringify({ time_zone: zeitzone || null }),
      })
      updateUser({ agent_name: agentName, time_zone: zeitzone || null })
      await onWeiter()
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e))
    } finally {
      setLaeuft(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-on-surface-variant">{t('mss.wizard.personalisierungErklaerung')}</p>
      <div>
        <Input
          id="mss-agentname"
          label={t('mss.wizard.agentnameLabel')}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <p className="msm-field-help">{t('mss.wizard.agentnameHinweis')}</p>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="text-sm font-medium text-foreground">{t('mss.wizard.zeitzoneLabel')}</span>
        <Dropdown
          value={zeitzone}
          onChange={(wert) => setZeitzone(wert ?? '')}
          options={(() => {
            try {
              if (typeof Intl !== 'undefined' && typeof Intl.supportedValuesOf === 'function') {
                return Intl.supportedValuesOf('timeZone').map((tz) => ({ value: tz, label: tz }))
              }
            } catch {}
            return [
              'Europe/Berlin',
              'Europe/London',
              'Europe/Paris',
              'UTC',
              'America/New_York',
              'America/Los_Angeles',
              'Asia/Tokyo',
            ].map((tz) => ({ value: tz, label: tz }))
          })()}
          searchable
          aria-label={t('mss.wizard.zeitzoneLabel')}
        />
      </div>
      <Fehlerzeile text={fehler} />
      <div className="flex justify-end">
        <Button onClick={() => void speichern()} disabled={laeuft}>
          {laeuft ? t('mss.wizard.speichert') : t('mss.wizard.weiter')}
        </Button>
      </div>
    </div>
  )
}

// ── Schritt 4: Sandbox-Ordner ─────────────────────────────────────────────

function SchrittSandbox({
  stand,
  onWeiter,
}: {
  stand: AppKonfig
  onWeiter: (stand: AppKonfig) => Promise<void>
}) {
  const { t } = useTranslation()
  const [pfad, setPfad] = useState(stand.sandbox_pfad ?? '')
  const [fehler, setFehler] = useState<string | null>(null)

  async function waehlen() {
    const auswahl = await ordnerDialog({ directory: true, multiple: false })
    if (typeof auswahl === 'string') {
      setPfad(auswahl)
    }
  }

  async function speichern() {
    setFehler(null)
    try {
      const neu = { ...stand, sandbox_pfad: pfad || null }
      await konfigSpeichern(neu)
      await onWeiter(neu)
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-on-surface-variant">{t('mss.wizard.sandboxErklaerung')}</p>
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <Input
            id="mss-sandbox"
            label={t('mss.wizard.sandboxLabel')}
            value={pfad}
            onChange={(e) => setPfad(e.target.value)}
            placeholder="C:\\Users\\du\\MSS-Sandbox"
          />
        </div>
        <Button variant="secondary" onClick={() => void waehlen()}>
          {t('mss.wizard.ordnerWaehlen')}
        </Button>
      </div>
      <Fehlerzeile text={fehler} />
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={() => void onWeiter(stand)}>
          {t('mss.wizard.spaeterFestlegen')}
        </Button>
        <Button onClick={() => void speichern()} disabled={pfad === ''}>
          {t('mss.wizard.weiter')}
        </Button>
      </div>
    </div>
  )
}
