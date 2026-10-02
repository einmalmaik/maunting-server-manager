/**
 * Papierkorb und Archiv für einen oder viele Einträge, gleich aus welcher
 * Ansicht (Dateien, Fotos, Lichtbox).
 *
 * Die Einträge gehen nacheinander; ein Fehler hält den Rest nicht auf, er wird
 * gezählt und am Ende mit der Zahl gemeldet. Was klappte, steht im Toast mit
 * „Rückgängig“. Bis 10/2026 gab es das zweimal: der Dateibereich hörte beim
 * ersten Fehler auf, die Galerie machte weiter.
 */
import i18n from '@/i18n'
import { toast } from '@/stores/toastStore'
import { useVaultStore } from './vaultStore'

/** Stand einer laufenden Sammelaktion für die Fortschrittsleiste; `null`: keine läuft. */
export type SammelStand = { text: string; anteil: number | null } | null

type Arbeit = (id: string) => Promise<void>

/** Führt `arbeit` für jede Kennung aus, meldet den Fortschritt und zählt, was scheitert. */
async function fuerAlle(ids: string[], text: string, arbeit: Arbeit, fortschritt: (stand: SammelStand) => void) {
  const geschafft: string[] = []
  let fehler = 0
  fortschritt({ text, anteil: 0 })
  try {
    for (const [nr, id] of ids.entries()) {
      try {
        await arbeit(id)
        geschafft.push(id)
      } catch {
        fehler += 1
      }
      fortschritt({ text, anteil: (nr + 1) / ids.length })
    }
  } finally {
    fortschritt(null)
  }
  return { geschafft, fehler }
}

/** Nimmt eine Aktion für `ids` zurück. Ein Fehler hält den Rest nicht auf; gemeldet wird einmal. */
export async function zuruecknehmen(ids: string[], arbeit: Arbeit) {
  let fehler = 0
  for (const id of ids) {
    try {
      await arbeit(id)
    } catch {
      fehler += 1
    }
  }
  if (fehler > 0) toast.error(i18n.t('mss.vault.fotos.fehler.rueckgaengig'))
}

/** Erfolg mit „Rückgängig“ für die erledigten, Fehler mit der Zahl der übrigen. */
function melden(geschafft: string[], fehler: number, erfolg: string, misserfolg: string, zurueck: Arbeit) {
  if (geschafft.length > 0) {
    toast.success(erfolg, { label: i18n.t('common.undo'), ausfuehren: () => void zuruecknehmen(geschafft, zurueck) })
  }
  if (fehler > 0) toast.error(misserfolg)
}

const ohneAnzeige = () => {}

/** Legt `ids` in den Papierkorb und gibt die zurück, bei denen es klappte. */
export async function inDenPapierkorb(ids: string[], fortschritt: (stand: SammelStand) => void = ohneAnzeige) {
  const { trashItem, restoreItem } = useVaultStore.getState()
  const { geschafft, fehler } = await fuerAlle(ids, i18n.t('mss.vault.fotos.sammel.papierkorb', { count: ids.length }), trashItem, fortschritt)
  melden(
    geschafft,
    fehler,
    geschafft.length === 1 ? i18n.t('mss.vault.inPapierkorbGelegt') : i18n.t('mss.vault.dateien.papierkorbMehrere', { count: geschafft.length }),
    i18n.t('mss.vault.fotos.fehler.papierkorb', { count: fehler }),
    restoreItem,
  )
  return geschafft
}

/** Legt `ids` ins Archiv und gibt die zurück, bei denen es klappte. */
export async function insArchiv(ids: string[], fortschritt: (stand: SammelStand) => void = ohneAnzeige) {
  const { setArchived } = useVaultStore.getState()
  const { geschafft, fehler } = await fuerAlle(
    ids,
    i18n.t('mss.vault.fotos.sammel.archiv', { count: ids.length }),
    (id) => setArchived(id, true),
    fortschritt,
  )
  melden(
    geschafft,
    fehler,
    geschafft.length === 1 ? i18n.t('mss.vault.archiviert') : i18n.t('mss.vault.dateien.archiviertMehrere', { count: geschafft.length }),
    i18n.t('mss.vault.fotos.fehler.archiv', { count: fehler }),
    (id) => setArchived(id, false),
  )
  return geschafft
}
