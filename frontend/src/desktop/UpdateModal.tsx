import { useState, useEffect } from 'react'
import { listen } from '@tauri-apps/api/event'
import { Download, RefreshCw, CheckCircle2, AlertCircle, X, Sparkles } from 'lucide-react'
import { Button } from '@/Singra/UI'
import {
  updatePruefen,
  updateInstallieren,
  appNeuStarten,
  type UpdateInfo,
  type UpdateStatusEvent,
} from './tauri'

export function UpdateModal() {
  const [info, setInfo] = useState<UpdateInfo | null>(null)
  const [sichtbar, setSichtbar] = useState(false)
  const [status, setStatus] = useState<'verfuegbar' | 'laedt' | 'bereit' | 'installiert_android' | 'fehler'>('verfuegbar')
  const [prozent, setProzent] = useState(0)
  const [fehlerText, setFehlerText] = useState<string | null>(null)

  useEffect(() => {
    // 1. Beim App-Start sofort prüfen
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const res = await updatePruefen()
          if (res.verfuegbar) {
            setInfo(res)
            setStatus('verfuegbar')
            setSichtbar(true)
          }
        } catch {
          // Im Offline-Modus oder bei Verbindungsfehler unaufdringlich schweigen
        }
      })()
    }, 2000)

    // 2. Event-Listener für Hintergrund-Prüfungen
    let unlistenVerfuegbar: (() => void) | undefined
    let unlistenBereit: (() => void) | undefined
    let unlistenStatus: (() => void) | undefined

    void (async () => {
      unlistenVerfuegbar = await listen<{ version: string; apk_url?: string; notizen?: string }>(
        'mss:update-verfuegbar',
        (event) => {
          setInfo((prev) => ({
            verfuegbar: true,
            aktuelle_version: prev?.aktuelle_version ?? '',
            neue_version: event.payload.version,
            download_url: event.payload.apk_url ?? null,
            notizen: event.payload.notizen ?? null,
            ist_android: typeof navigator !== 'undefined' && /android/i.test(navigator.userAgent),
          }))
          setStatus('verfuegbar')
          setSichtbar(true)
        }
      )

      unlistenBereit = await listen<{ version: string }>('mss:update-bereit', (event) => {
        setInfo((prev) => prev ? { ...prev, neue_version: event.payload.version } : null)
        setStatus('bereit')
        setSichtbar(true)
      })

      unlistenStatus = await listen<UpdateStatusEvent>('mss:update-status', (event) => {
        if (event.payload.status === 'laedt') {
          setStatus('laedt')
          if (event.payload.prozent !== undefined) {
            setProzent(event.payload.prozent)
          }
        } else if (event.payload.status === 'bereit') {
          setStatus('bereit')
        } else if (event.payload.status === 'installiert_android') {
          setStatus('installiert_android')
        } else if (event.payload.status === 'fehler') {
          setStatus('fehler')
          setFehlerText(event.payload.fehler ?? 'Download fehlgeschlagen')
        }
      })
    })()

    return () => {
      clearTimeout(timer)
      if (unlistenVerfuegbar) unlistenVerfuegbar()
      if (unlistenBereit) unlistenBereit()
      if (unlistenStatus) unlistenStatus()
    }
  }, [])

  if (!sichtbar || !info) return null

  async function handleInstallieren() {
    setFehlerText(null)
    setStatus('laedt')
    setProzent(0)
    try {
      await updateInstallieren()
    } catch (e) {
      setStatus('fehler')
      setFehlerText(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-scrim/40 backdrop-blur-sm animate-fade-in">
      <div className="relative w-full max-w-md rounded-2xl bg-surface-container p-6 shadow-2xl border border-outline-variant/30 text-on-surface">
        {status !== 'laedt' && (
          <button
            type="button"
            onClick={() => setSichtbar(false)}
            className="absolute top-4 right-4 p-1.5 rounded-full text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high transition-colors"
            title="Später erinnern"
          >
            <X className="w-5 h-5" />
          </button>
        )}

        <div className="flex items-center gap-3 mb-4">
          <div className="flex items-center justify-center w-11 h-11 rounded-xl bg-primary/15 text-primary">
            {status === 'bereit' ? (
              <CheckCircle2 className="w-6 h-6 text-primary" />
            ) : status === 'fehler' ? (
              <AlertCircle className="w-6 h-6 text-error" />
            ) : (
              <Sparkles className="w-6 h-6 text-primary" />
            )}
          </div>
          <div>
            <h3 className="font-headline text-headline-sm font-semibold text-on-surface">
              {status === 'bereit'
                ? 'Update bereit'
                : status === 'laedt'
                ? 'Update wird geladen'
                : status === 'installiert_android'
                ? 'Installation gestartet'
                : 'Update verfügbar'}
            </h3>
            <p className="text-xs text-on-surface-variant">
              Version v{info.neue_version} (aktuell: v{info.aktuelle_version})
            </p>
          </div>
        </div>

        {status === 'verfuegbar' && (
          <div className="space-y-4">
            <p className="text-sm text-on-surface-variant leading-relaxed">
              Eine neue Version von Maunting Smart System steht bereit.
              {info.ist_android
                ? ' Die Aktualisierung kann direkt heruntergeladen und installiert werden.'
                : ' Das Update wird im Hintergrund vorbereitet und beim nächsten Start aktiviert.'}
            </p>

            {info.notizen && (
              <div className="max-h-36 overflow-y-auto rounded-xl bg-surface-container-low p-3 text-xs text-on-surface-variant border border-outline-variant/20 whitespace-pre-wrap">
                {info.notizen}
              </div>
            )}

            <div className="flex items-center justify-end gap-2 pt-2">
              <Button variant="secondary" onClick={() => setSichtbar(false)}>
                Später
              </Button>
              <Button onClick={() => void handleInstallieren()} className="gap-2">
                <Download className="w-4 h-4" />
                {info.ist_android ? 'Herunterladen & Installieren' : 'Jetzt aktualisieren'}
              </Button>
            </div>
          </div>
        )}

        {status === 'laedt' && (
          <div className="space-y-4 py-2">
            <p className="text-sm text-on-surface-variant">
              Das Updatepaket wird heruntergeladen...
            </p>
            <div className="w-full bg-surface-container-high rounded-full h-2.5 overflow-hidden">
              <div
                className="bg-primary h-2.5 rounded-full transition-all duration-300"
                style={{ width: `${Math.max(5, prozent)}%` }}
              />
            </div>
            <p className="text-right text-xs text-on-surface-variant">{prozent}%</p>
          </div>
        )}

        {status === 'bereit' && (
          <div className="space-y-4">
            <p className="text-sm text-on-surface-variant leading-relaxed">
              Die Version v{info.neue_version} wurde erfolgreich vorbereitet. Starte die Anwendung neu, um das Update abzuschließen.
            </p>
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button variant="secondary" onClick={() => setSichtbar(false)}>
                Beim nächsten Start
              </Button>
              <Button onClick={() => void appNeuStarten()} className="gap-2">
                <RefreshCw className="w-4 h-4" />
                Jetzt neu starten
              </Button>
            </div>
          </div>
        )}

        {status === 'installiert_android' && (
          <div className="space-y-4">
            <p className="text-sm text-on-surface-variant leading-relaxed">
              Der Android-Paketmanager wurde geöffnet. Bitte bestätige die Aktualisierung im System-Dialog.
            </p>
            <div className="flex justify-end pt-2">
              <Button onClick={() => setSichtbar(false)}>
                Schließen
              </Button>
            </div>
          </div>
        )}

        {status === 'fehler' && (
          <div className="space-y-4">
            <p className="text-sm text-error leading-relaxed">
              {fehlerText ?? 'Bei der Vorbereitung des Updates ist ein Fehler aufgetreten.'}
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="secondary" onClick={() => setSichtbar(false)}>
                Schließen
              </Button>
              <Button onClick={() => void handleInstallieren()}>
                Erneut versuchen
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}