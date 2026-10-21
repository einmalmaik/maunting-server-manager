/**
 * Freigabe und Veröffentlichung des eigenen Geräts.
 *
 * Das erste Gerät eines Kontos gibt sich selbst frei, weitere Geräte werden von
 * einem freigegebenen unterschrieben. Jede dieser Aktionen räumt danach den
 * Geräte-Zwischenspeicher, damit die eigene Liste nicht der alten Wahrheit
 * nachläuft.
 */

import {
  approveEigenesGeraet,
  putEigenesGeraet,
  reapproveEigenesGeraet,
  removeEigenesGeraet,
  resetEigeneGeraete,
  type E2eeGeraetItem,
  type EntfernenNachweis,
  type ReapproveEigenesGeraetInput,
  type ResetEigeneGeraeteInput,
} from '@/api/social'
import { signiere } from '../absenderSignatur'
import { STANDARD_BEZEICHNUNG, geraeteBezeichnung } from '../geraeteBezeichnung'
import { eigenesGeraet, meinKonto, type EigenesGeraet } from './geraeteAblage'
import { entfernenDaten, freigabeDaten, setzeEigeneFreigabe } from './geraeteVertrauen'
import { vergessenGeraete } from './geraeteVerzeichnis'

let veroeffentlichtAls: string | null = null

async function veroeffentliche(
  geraet: EigenesGeraet,
  label: string,
  approvalSignature = '',
): Promise<E2eeGeraetItem> {
  return putEigenesGeraet({
    deviceId: geraet.kennung,
    publicKey: geraet.paar.publicKeyJwk,
    signingPublicKey: geraet.signaturPaar.publicKeyJwk,
    label,
    approvalSignature,
  })
}

/**
 * Die eigene Unterschrift über die eigene Freigabe — nur für das Erstgerät.
 *
 * Das erste Gerät eines Kontos gibt sich selbst frei, weil es niemanden gibt,
 * der es freigeben könnte. Ohne Unterschrift stand danach in der Liste
 * „freigegeben", während die Signaturkette „Ohne gültige Freigabe" sagte. Die
 * Unterschrift belegt nur, dass dieses Gerät seinen eigenen Schlüssel kennt —
 * ein anderes freigegebenes Gerät ersetzt sie (`gebeGeraetFrei`).
 */
async function selbstSignatur(geraet: EigenesGeraet): Promise<string> {
  const daten = await freigabeDaten(
    meinKonto(),
    geraet.kennung,
    geraet.paar.publicKeyJwk,
    geraet.signaturPaar.publicKeyJwk,
  )
  return signiere(daten, geraet.signaturPaar.privateKeyJwk)
}

/**
 * Meldet dieses Gerät beim Konto an. Idempotent und bei jedem Start fällig.
 *
 * Das erneute Melden ist kein Selbstzweck: es frischt `last_seen_at` auf, und
 * danach richtet sich der Gerätedeckel im Backend. Ein Gerät, das sich nie
 * wieder meldet, soll irgendwann Platz machen.
 *
 * **Ein Name wird nur gefüllt, nie überschrieben.** Ein gekoppeltes Gerät hat
 * seinen Namen aus der Einladung; riefe dieser Aufruf beim nächsten Start ohne
 * Argument, stünde danach `<App> · <System>` statt „Arbeitsrechner“ in der
 * Liste, denn `_auffrischen` übernimmt jedes nicht-leere Label. Deshalb wird
 * erst nachgesehen, was der Server führt, und nur ein leeres Feld bekommt die
 * abgeleitete Bezeichnung — einmal, danach ist sie da.
 *
 * Ein **generisches** Label gilt dabei wie ein leeres: der Server setzt für
 * einen Browser ohne Namen `Web-Frontend` ein, damit der Bestand nicht mehr
 * als „Unbenanntes Gerät" erscheint. Der genauere Name dieses Browsers
 * (`Web-Frontend · Chrome/Windows`) ersetzt ihn beim ersten Start; ein von
 * einem Menschen gewählter Name bleibt unangetastet.
 */
export async function geraetVeroeffentlichen(label = ''): Promise<EigenesGeraet> {
  const geraet = await eigenesGeraet()
  if (veroeffentlichtAls === geraet.kennung) return geraet

  const eigenerName = (label || '').trim()
  let antwort = await veroeffentliche(geraet, eigenerName)
  const gespeichert = (antwort?.label || '').trim()
  if (!eigenerName && (!gespeichert || gespeichert === STANDARD_BEZEICHNUNG)) {
    const abgeleitet = geraeteBezeichnung()
    if (abgeleitet) {
      try {
        antwort = await veroeffentliche(geraet, abgeleitet)
      } catch {
        // Der Eintrag steht bereits. Der Name ist Komfort, kein Zugang.
      }
    }
  }

  // Erstgerät: die Freigabe trägt keine Unterschrift. Sie nachzureichen macht
  // `is_approved` und die Signaturkette deckungsgleich.
  if (antwort?.is_approved !== false && !(antwort?.approval_signature || '').trim()) {
    try {
      antwort = await veroeffentliche(geraet, '', await selbstSignatur(geraet))
    } catch {
      // Ohne Unterschrift bleibt das Gerät freigegeben; die Liste sagt es nur.
    }
  }

  setzeEigeneFreigabe(antwort?.is_approved !== false)
  veroeffentlichtAls = geraet.kennung
  return geraet
}

async function unterschreibeMitEigenem(daten: string): Promise<{ kennung: string; sig: string }> {
  const meins = await eigenesGeraet()
  return { kennung: meins.kennung, sig: await signiere(daten, meins.signaturPaar.privateKeyJwk) }
}

/** Gibt ein wartendes Gerät des eigenen Kontos frei — unterschrieben von diesem. */
export async function gebeGeraetFrei(geraet: E2eeGeraetItem): Promise<void> {
  const konto = meinKonto()
  const daten = await freigabeDaten(
    konto,
    geraet.device_id,
    geraet.public_key,
    geraet.signing_public_key,
  )
  const { kennung, sig } = await unterschreibeMitEigenem(daten)
  await approveEigenesGeraet(geraet.device_id, kennung, sig)
  vergessenGeraete(konto)
}

/**
 * Entfernt ein Gerät des eigenen Kontos. Ein freigegebenes verlangt die
 * Unterschrift dieses Geräts; ein wartendes nicht — es hat nie etwas bekommen.
 */
export async function entferneGeraet(
  geraet: E2eeGeraetItem,
  nachweis: EntfernenNachweis = {},
): Promise<void> {
  const konto = meinKonto()
  let unterschrift: { kennung: string; sig: string } | null = null
  if (geraet.is_approved !== false) {
    unterschrift = await unterschreibeMitEigenem(
      await entfernenDaten(konto, geraet.device_id, geraet.public_key),
    )
  }
  await removeEigenesGeraet(geraet.device_id, unterschrift?.kennung, unterschrift?.sig, nachweis)
  vergessenGeraete(konto)
}

/**
 * Holt dieses Gerät zurück, weil es das einzige ist.
 *
 * Der Fall dahinter: der Geräteschlüssel wurde neu erzeugt (verlorene Ablage,
 * neuer Browser), das Gerät wartet auf Freigabe — und es gibt kein zweites
 * freigegebenes Gerät, das unterschreiben könnte. Ohne diesen Weg bliebe nur
 * der Neuanfang, der alle Sitzungen sperrt.
 *
 * **Freigegeben ist nicht beglaubigt.** Der Server gibt die Zustellung frei,
 * aber `approved_by` bleibt leer: niemand hat diesen Schlüssel unterschrieben.
 * Die Gegenstellen sehen genau das und warnen, bis der Mensch die
 * Sicherheitsnummer verglichen hat. Deshalb ist der Nachweis hier Pflicht —
 * sonst wäre es ein Weg an der Unterschrift vorbei.
 */
export async function gibMichSelbstFrei(nachweis: ReapproveEigenesGeraetInput): Promise<void> {
  const meins = await eigenesGeraet()
  // Die eigene Unterschrift reist mit: sonst bleibt der Eintrag „freigegeben"
  // ohne Freigabe, und jedes andere Gerät zeigt genau diesen Widerspruch.
  const signatur = await selbstSignatur(meins).catch(() => '')
  await reapproveEigenesGeraet(meins.kennung, { ...nachweis, signature: signatur })
  setzeEigeneFreigabe(true)
  vergessenGeraete(meinKonto())
}

/**
 * Alle Geräte verloren: das Verzeichnis dieses Kontos leeren und neu beginnen.
 * Alle anderen Sitzungen fliegen hinaus, dieses Gerät meldet sich danach als
 * erstes wieder an, und die Kontakte bekommen eine Warnung.
 */
export async function geraeteZuruecksetzen(
  input: string | ResetEigeneGeraeteInput,
): Promise<void> {
  const konto = meinKonto()
  await resetEigeneGeraete(input)
  veroeffentlichtAls = null
  vergessenGeraete(konto)
  await geraetVeroeffentlichen()
}

/** Vergisst, dass dieses Gerät schon veröffentlicht wurde. */
export function leereFreigabeRam(): void {
  veroeffentlichtAls = null
}
