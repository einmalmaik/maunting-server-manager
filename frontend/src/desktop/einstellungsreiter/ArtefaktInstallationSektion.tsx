import { useEffect, useState } from 'react'
import { open as ordnerDialog } from '@tauri-apps/plugin-dialog'
import { AlertTriangle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge, Button, Slider, Switch } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { konfigAendern, konfigLaden, sandboxVerfuegbar, type AppKonfig } from '../tauri'

export function ArtefaktInstallationSektion({ onKonfigAenderung }: { onKonfigAenderung?: () => void }) {
  const { t } = useTranslation()
  const [konfig, setKonfig] = useState<AppKonfig | null>(null)
  const [dialogOffen, setDialogOffen] = useState(false)
  const [sandboxOk, setSandboxOk] = useState<boolean | null>(null)

  useEffect(() => {
    void konfigLaden().then(setKonfig).catch(() => {})
    void sandboxVerfuegbar().then(setSandboxOk).catch(() => setSandboxOk(false))
  }, [])

  // Nur die eigenen Felder, frisch in Rust gemischt (`konfigAendern`) — nie
  // eine alte Kopie der ganzen Konfiguration, die fremde Schalter zurücksetzt.
  // Schlägt das Speichern fehl, gilt wieder der gespeicherte Stand.
  async function aendern(felder: Partial<AppKonfig>) {
    const vorher = konfig
    if (!vorher) return
    setKonfig({ ...vorher, ...felder })
    try {
      setKonfig(await konfigAendern(felder))
      onKonfigAenderung?.()
    } catch {
      setKonfig(vorher)
      toast.error(t('mss.einstellungen.speichernFehler'))
    }
  }

  async function toggle(an: boolean) {
    if (!konfig) return
    if (an) {
      setDialogOffen(true)
    } else {
      await aendern({ artifact_install_aktiv: false })
    }
  }

  async function bestaetigenAktivieren() {
    if (!konfig) return
    setDialogOffen(false)
    await aendern({ artifact_install_aktiv: true })
  }

  async function downloadLimitAendern(gib: number) {
    if (!konfig) return
    const bytes = Math.max(1, Math.min(100, gib)) * 1024 * 1024 * 1024
    await aendern({ max_download_bytes: bytes })
  }

  async function suchwurzelHinzufuegen() {
    if (!konfig) return
    let gewaehlt: string | string[] | null
    try {
      gewaehlt = await ordnerDialog({ directory: true, multiple: false })
    } catch {
      toast.error(t('mss.einstellungen.artefakte.ordnerFehler'))
      return
    }
    if (typeof gewaehlt === 'string' && gewaehlt && !konfig.search_roots.includes(gewaehlt)) {
      await aendern({ search_roots: [...konfig.search_roots, gewaehlt] })
    }
  }

  async function suchwurzelEntfernen(pfad: string) {
    if (!konfig) return
    await aendern({ search_roots: konfig.search_roots.filter((w) => w !== pfad) })
  }

  const limitGiB = Math.round((konfig?.max_download_bytes ?? 10 * 1024 * 1024 * 1024) / (1024 * 1024 * 1024))

  return (
    <>
      <div className="flex flex-col gap-3 border-t border-outline-variant/40 pt-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <p className="text-sm text-on-surface">{t('mss.einstellungen.artefakte.titel')}</p>
              {konfig?.artifact_install_aktiv ? (
                <Badge variant="success">
                  {t('mss.einstellungen.artefakte.statusAktiv')}
                </Badge>
              ) : (
                <Badge variant="default">
                  {t('mss.einstellungen.artefakte.statusDeaktiviert')}
                </Badge>
              )}
            </div>
          </div>
          <Switch
            checked={konfig?.artifact_install_aktiv === true}
            disabled={konfig === null}
            onCheckedChange={(an) => void toggle(an)}
            aria-label={t('mss.einstellungen.artefakte.titel')}
          />
        </div>

        {konfig?.artifact_install_aktiv && (
          <div className="mt-2 flex flex-col gap-4 rounded-xl border border-outline-variant/30 bg-surface-container-low/30 p-4">
            {/* Windows Sandbox Status */}
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-medium text-on-surface">{t('mss.einstellungen.artefakte.sandboxTitel')}</p>
              <Badge variant={sandboxOk ? 'success' : 'warning'}>
                {sandboxOk ? t('mss.einstellungen.artefakte.sandboxBereit') : t('mss.einstellungen.artefakte.sandboxFehlt')}
              </Badge>
            </div>

            {/* Download Limit */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-on-surface font-medium">{t('mss.einstellungen.artefakte.downloadLimitTitel')}</span>
                <span className="font-mono text-primary">{limitGiB} GiB</span>
              </div>
              <Slider
                min={1}
                max={100}
                step={1}
                value={limitGiB}
                onValueChange={(val) => void downloadLimitAendern(val)}
                ariaLabel={t('mss.einstellungen.artefakte.downloadLimitTitel')}
              />
            </div>

            {/* Freigegebene Suchwurzeln */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <p className="text-xs font-medium text-on-surface">{t('mss.einstellungen.artefakte.suchwurzelnTitel')}</p>
                <Button variant="secondary" size="sm" onClick={() => void suchwurzelHinzufuegen()}>
                  {t('mss.einstellungen.artefakte.suchwurzelHinzufuegen')}
                </Button>
              </div>
              {konfig.search_roots.length === 0 ? (
                <p className="text-xs italic text-on-surface-variant/70">
                  {t('mss.einstellungen.artefakte.keineSuchwurzeln')}
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
          aria-label={t('mss.einstellungen.artefakte.aktivierenTitel')}
        >
          <div className="msm-card flex w-full max-w-md flex-col gap-4 p-5">
            <div className="flex items-center gap-2 text-status-warning">
              <AlertTriangle className="h-5 w-5 shrink-0" aria-hidden="true" />
              <h2 className="text-base font-semibold text-on-surface">
                {t('mss.einstellungen.artefakte.aktivierenTitel')}
              </h2>
            </div>
            <p className="text-xs leading-relaxed text-on-surface-variant">
              {t('mss.einstellungen.artefakte.aktivierenWarnung')}
            </p>
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button variant="ghost" size="sm" onClick={() => setDialogOffen(false)}>
                {t('mss.einstellungen.artefakte.abbrechen')}
              </Button>
              <Button autoFocus size="sm" onClick={() => void bestaetigenAktivieren()}>
                {t('mss.einstellungen.artefakte.aktivierenBestaetigen')}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
