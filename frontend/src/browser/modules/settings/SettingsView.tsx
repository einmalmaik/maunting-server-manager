import { useState } from 'react'
import {
  Shield,
  Palette,
  Link,
  Trash2,
  Check,
} from 'lucide-react'
import { useBrowserStore } from '../../services/browserStore'
import { SEARCH_ENGINES } from '../../services/searchEngines'
import { GlobeIcon } from '../newtab/brandIcons'

export function SettingsView() {
  const {
    searchEngine,
    setSearchEngine,
    searxngUrl,
    setSearxngUrl,
    adblockEnabled,
    toggleAdblock,
    forgetOnClose,
    isCoupled,
    backendUrl,
    pairedUser,
    setCouplingData,
    decouple,
    clearBrowserData,
  } = useBrowserStore()

  const [activeTab, setActiveTab] = useState<'suche' | 'datenschutz' | 'design' | 'kopplung'>('suche')
  const [pairingCode, setPairingCode] = useState('')
  const [serverUrlInput, setServerUrlInput] = useState('')
  const [isPairingLoading, setIsPairingLoading] = useState(false)

  const handlePair = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!pairingCode.trim()) return

    setIsPairingLoading(true)
    setTimeout(() => {
      setCouplingData(serverUrlInput || 'https://msm.lan', 'dummy-device-token-12345', {
        username: 'Admin',
        email: 'admin@mauntingstudios.com',
      })
      setIsPairingLoading(false)
    }, 1000)
  }

  return (
    <div className="flex flex-col h-full text-foreground select-none overflow-hidden">
      {/* Sub-Nav Tabs */}
      <div className="flex border-b border-border bg-muted/20 text-xs overflow-x-auto no-scrollbar">
        <button
          onClick={() => setActiveTab('suche')}
          className={`flex items-center gap-1.5 px-3 py-2 border-b-2 font-medium transition-colors shrink-0 ${
            activeTab === 'suche'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <GlobeIcon className="w-3.5 h-3.5" />
          <span>Suche</span>
        </button>

        <button
          onClick={() => setActiveTab('datenschutz')}
          className={`flex items-center gap-1.5 px-3 py-2 border-b-2 font-medium transition-colors shrink-0 ${
            activeTab === 'datenschutz'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <Shield className="w-3.5 h-3.5" />
          <span>Schutz</span>
        </button>

        <button
          onClick={() => setActiveTab('design')}
          className={`flex items-center gap-1.5 px-3 py-2 border-b-2 font-medium transition-colors shrink-0 ${
            activeTab === 'design'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <Palette className="w-3.5 h-3.5" />
          <span>Design</span>
        </button>

        <button
          onClick={() => setActiveTab('kopplung')}
          className={`flex items-center gap-1.5 px-3 py-2 border-b-2 font-medium transition-colors shrink-0 ${
            activeTab === 'kopplung'
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
        >
          <Link className="w-3.5 h-3.5" />
          <span>Kopplung</span>
        </button>
      </div>

      {/* Tab Inhalt */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs select-text">
        {/* REITER 1: SUCHE */}
        {activeTab === 'suche' && (
          <div className="space-y-4">
            <div>
              <h4 className="font-semibold text-sm mb-1">Standard-Suchmaschine</h4>
              <p className="text-muted-foreground text-label-sm mb-3">
                Wird bei Suchanfragen in der Omnibox und auf neuen Tabs verwendet.
              </p>

              <div className="space-y-2">
                {Object.values(SEARCH_ENGINES).map((engine) => (
                  <label
                    key={engine.id}
                    className={`flex items-center justify-between p-2.5 rounded-xl border cursor-pointer transition-colors ${
                      searchEngine === engine.id
                        ? 'border-primary bg-primary/10 text-primary font-medium'
                        : 'border-border bg-card hover:bg-muted/50'
                    }`}
                  >
                    <div className="flex items-center gap-2.5">
                      <span className="text-lg">{engine.icon}</span>
                      <div>
                        <div>{engine.name}</div>
                        <div className="text-label-sm text-muted-foreground">{engine.description}</div>
                      </div>
                    </div>
                    <input
                      type="radio"
                      name="searchEngine"
                      checked={searchEngine === engine.id}
                      onChange={() => setSearchEngine(engine.id)}
                      className="accent-primary"
                    />
                  </label>
                ))}
              </div>
            </div>

            {searchEngine === 'searxng' && (
              <div className="p-3 bg-muted/40 border border-border rounded-xl space-y-2">
                <label className="text-label-sm font-semibold text-foreground">
                  URL deiner MSM SearXNG Instanz:
                </label>
                <input
                  type="text"
                  value={searxngUrl || ''}
                  onChange={(e) => setSearxngUrl(e.target.value)}
                  placeholder="https://search.deine-domain.de"
                  className="w-full bg-background border border-border rounded-lg p-2 text-xs outline-none focus:border-primary"
                />
              </div>
            )}
          </div>
        )}

        {/* REITER 2: DATENSCHUTZ & SCHUTZ */}
        {activeTab === 'datenschutz' && (
          <div className="space-y-4">
            <div>
              <h4 className="font-semibold text-sm mb-1">Integrierter Werbe- & Trackerschutz</h4>
              <p className="text-muted-foreground text-label-sm mb-3">
                Basiert auf der nativen Brave adblock-rs Filter-Engine in Rust.
              </p>

              <div className="space-y-2">
                <div className="flex items-center justify-between p-3 rounded-xl border border-border bg-card">
                  <div>
                    <div className="font-semibold">Werbung & Tracker blockieren</div>
                    <div className="text-label-sm text-muted-foreground">
                      Unterdrückt Werbung, Popups und Telemetrie
                    </div>
                  </div>
                  <button
                    onClick={toggleAdblock}
                    className={`w-10 h-6 rounded-full transition-colors relative ${
                      adblockEnabled ? 'bg-status-success' : 'bg-muted'
                    }`}
                  >
                    <span
                      className={`block w-4 h-4 rounded-full bg-white transition-transform ${
                        adblockEnabled ? 'translate-x-5' : 'translate-x-1'
                      }`}
                    />
                  </button>
                </div>

                <div className="flex items-center justify-between p-3 rounded-xl border border-border bg-card">
                  <div>
                    <div className="font-semibold">Vergiss mich beim Schließen</div>
                    <div className="text-label-sm text-muted-foreground">
                      Löscht Cookies und LocalStorage eines Tabs beim Beenden
                    </div>
                  </div>
                  <input
                    type="checkbox"
                    checked={forgetOnClose}
                    onChange={() => {}}
                    className="accent-primary w-4 h-4"
                  />
                </div>
              </div>
            </div>

            <div className="pt-2 border-t border-border">
              <h4 className="font-semibold text-xs mb-2">Browserdaten bereinigen</h4>
              <button
                onClick={() => {
                  clearBrowserData({ history: true, cache: true })
                  alert('Verlauf und Cache wurden erfolgreich bereinigt!')
                }}
                className="flex items-center gap-2 px-3 py-2 rounded-xl bg-status-destructive/10 text-status-destructive hover:bg-status-destructive/20 border border-status-destructive/20 transition-colors font-medium text-xs w-full justify-center"
              >
                <Trash2 className="w-4 h-4" />
                <span>Verlauf & Cache jetzt leeren</span>
              </button>
            </div>
          </div>
        )}

        {/* REITER 3: DESIGN */}
        {activeTab === 'design' && (
          <div className="space-y-4">
            <div>
              <h4 className="font-semibold text-sm mb-1">Farbschema</h4>
              <p className="text-muted-foreground text-label-sm mb-3">
                Das Maunting-Design ist auf das dunkle Farbschema optimiert. Ein heller Modus wird nicht unterstützt.
              </p>

              <div className="p-3 rounded-xl border border-border bg-card flex items-center justify-between">
                <div>
                  <div className="text-xs font-semibold text-foreground">Dunkles Theme</div>
                  <div className="text-label-sm text-muted-foreground">Standardmäßig aktiv</div>
                </div>
                <div className="px-2.5 py-1 rounded-md bg-primary/10 text-primary text-xs font-medium border border-primary/20">
                  Aktiv
                </div>
              </div>
            </div>

            <div className="pt-2 border-t border-border">
              <h4 className="font-semibold text-sm mb-1">Individuelle Anpassung</h4>
              <p className="text-muted-foreground text-label-sm">
                Akzentfarben, benutzerdefinierte Hintergrundbilder und Layout-Optionen werden hier im nächsten Schritt konfiguriert.
              </p>
            </div>
          </div>
        )}

        {/* REITER 4: KOPPLUNG */}
        {activeTab === 'kopplung' && (
          <div className="space-y-4">
            {isCoupled ? (
              <div className="p-4 rounded-2xl bg-card border border-border space-y-3">
                <div className="flex items-center gap-2 text-status-success font-semibold text-sm">
                  <Check className="w-4 h-4" />
                  <span>Mit MSM-Server verbunden</span>
                </div>
                <div className="text-label-sm text-muted-foreground space-y-1">
                  <div>Server: <span className="font-mono text-foreground">{backendUrl}</span></div>
                  <div>Benutzer: <span className="font-semibold text-foreground">{pairedUser?.username}</span></div>
                </div>

                <div className="pt-2 border-t border-border flex justify-between items-center">
                  <span className="text-xs text-muted-foreground">Kopplung aufheben</span>
                  <button
                    onClick={decouple}
                    className="px-3 py-1.5 rounded-lg bg-status-destructive/10 text-status-destructive hover:bg-status-destructive/20 font-medium text-xs transition-colors"
                  >
                    Trennen
                  </button>
                </div>
              </div>
            ) : (
              <form onSubmit={handlePair} className="space-y-3">
                <div>
                  <h4 className="font-semibold text-sm mb-1">Gerät mit MSM koppeln</h4>
                  <p className="text-muted-foreground text-label-sm mb-3">
                    Öffne im MSM-Panel „Profil → Geräte → Gerät koppeln“ und gib den 12-stelligen
                    Code hier ein. Dadurch wird dein DIS-Passworttresor, Messenger und die KI
                    freigeschaltet.
                  </p>
                </div>

                <div>
                  <label className="text-label-sm font-semibold text-muted-foreground block mb-1">
                    MSM Server-Adresse:
                  </label>
                  <input
                    type="text"
                    value={serverUrlInput}
                    onChange={(e) => setServerUrlInput(e.target.value)}
                    placeholder="https://msm.deine-domain.de"
                    className="w-full bg-background border border-border rounded-xl p-2.5 text-xs outline-none focus:border-primary"
                  />
                </div>

                <div>
                  <label className="text-label-sm font-semibold text-muted-foreground block mb-1">
                    12-stelliger Kopplungscode:
                  </label>
                  <input
                    type="text"
                    value={pairingCode}
                    onChange={(e) => setPairingCode(e.target.value.toUpperCase())}
                    placeholder="ABCD-1234-EFGH"
                    maxLength={14}
                    className="w-full bg-background border border-border rounded-xl p-2.5 text-xs font-mono uppercase tracking-widest outline-none focus:border-primary"
                  />
                </div>

                <button
                  type="submit"
                  disabled={!pairingCode.trim() || isPairingLoading}
                  className="w-full py-2.5 rounded-xl bg-primary text-primary-foreground font-semibold text-xs hover:bg-primary/90 transition-colors disabled:opacity-40 shadow-sm"
                >
                  {isPairingLoading ? 'Kopplung wird geprüft...' : 'Jetzt koppeln'}
                </button>
              </form>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
