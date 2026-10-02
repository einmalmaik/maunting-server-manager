/**
 * Was die beiden Lichtboxen des Tresors teilen: die Datei-Ansicht
 * (`TresorDateiAnsicht`) und die Foto-Lichtbox der Galerie.
 *
 * Was sie laden, unterscheidet sich (Original samt Inhalt hier, Vorschau und
 * Original beim Zoomen dort); wie sie scheitern, nicht. Ohne Netz sagen beide,
 * dass die Datei offline nicht da ist, und laden von selbst, sobald es zurück
 * ist; sonst gibt es „Erneut laden“. Dazu kommen Infoleiste und frühere
 * Fassungen.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { RotateCw, WifiOff } from 'lucide-react'
import { Button, Versionsliste } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { useVaultStore, type VaultItem } from './vaultStore'
import { fassungenVon } from './tresorOrdner'
import { fehlerText } from './tresorFehler'

/** Warum der Inhalt nicht kam: ohne Netz (liegt nicht auf dem Gerät) oder sonst gescheitert. */
export type LadeFehler = 'offline' | 'laden'

/** Ordnet einen gescheiterten Ladeversuch ein. */
export function ladeFehler(): LadeFehler {
  return typeof navigator !== 'undefined' && navigator.onLine === false ? 'offline' : 'laden'
}

/** Kommt das Netz zurück, lädt, was ohne Netz nicht kam, von selbst noch einmal. */
export function useNeuBeiNetz(fehler: LadeFehler | false, erneut: () => void) {
  useEffect(() => {
    if (fehler !== 'offline') return
    window.addEventListener('online', erneut)
    return () => window.removeEventListener('online', erneut)
  }, [fehler, erneut])
}

interface HinweisProps {
  fehler: LadeFehler
  /** Wie die Datei auch ohne Netz aufgeht. */
  offlineHinweis: string
  /** Was beim Laden mit Netz schiefging. */
  text: string
  onErneut: () => void
}

/** Statt des Inhalts, wenn er nicht kam. Ohne Netz ohne Knopf: geladen wird von selbst. */
export function LadeFehlerHinweis({ fehler, offlineHinweis, text, onErneut }: HinweisProps) {
  const { t } = useTranslation()
  if (fehler === 'offline') {
    return (
      <div className="flex max-w-sm flex-col items-center gap-3 px-6 text-center" role="status">
        <WifiOff className="h-10 w-10 text-white/60" aria-hidden />
        <p className="font-semibold text-white">{t('mss.vault.dateien.ohneNetzTitel')}</p>
        <p className="text-sm text-white/70">{offlineHinweis}</p>
      </div>
    )
  }
  return (
    <div className="flex max-w-sm flex-col items-center gap-4 px-6 text-center" role="alert">
      <p className="text-sm text-white/70">{text}</p>
      <Button type="button" variant="secondary" className="min-h-11 sm:min-h-10" onClick={onErneut}>
        <RotateCw className="h-4 w-4" aria-hidden />
        {t('mss.vault.dateien.erneutLaden')}
      </Button>
    </div>
  )
}

interface InfoProps {
  item: VaultItem
  /** Name und Wert; leere Werte fallen weg. */
  angaben: [string, string | null | undefined][]
}

/** Infoleiste der Lichtbox: Angaben zur Datei und ihre früheren Fassungen zum Zurückholen. */
export function TresorDateiInfo({ item, angaben }: InfoProps) {
  const { t } = useTranslation()
  const fassungZurueckholen = useVaultStore((s) => s.fassungZurueckholen)
  const [holt, setHolt] = useState<string | null>(null)
  const versionen = fassungenVon(item)

  const zurueckholen = async (originalId: string) => {
    setHolt(originalId)
    try {
      await fassungZurueckholen(item.id, originalId)
      toast.success(t('mss.vault.dateien.fassungZurueck'))
    } catch (err) {
      toast.error(fehlerText(err, t('mss.vault.bearbeiten.fehler')))
    } finally {
      setHolt(null)
    }
  }

  return (
    <div className="space-y-6">
      <dl className="space-y-3">
        {angaben
          .filter(([, wert]) => wert)
          .map(([name, wert]) => (
            <div key={name}>
              <dt className="text-label-sm text-white/55">{name}</dt>
              <dd className="break-words text-white/90">{wert}</dd>
            </div>
          ))}
      </dl>
      {/* Bearbeitete und ersetzte Dateien sind neue Fassungen; die früheren stehen hier. */}
      <section aria-label={t('common.versionen.titel')}>
        <h3 className="mb-1 text-sm font-semibold text-white">{t('common.versionen.titel')}</h3>
        {versionen.length > 0 && <p className="mb-3 text-label-sm text-white/55">{t('common.versionen.hinweis')}</p>}
        <Versionsliste versionen={versionen} onWiederherstellen={(id) => void zurueckholen(id)} laeuft={holt} aufDunkel />
      </section>
    </div>
  )
}
