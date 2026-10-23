/**
 * Der Textverlauf reist mit Manifest und Empfangsquittung.
 *
 * Bis 10/2026 schickte die Quelle ihre 200er-Teile einfach los und merkte den
 * Verlauf als übergeben, sobald sie beim Relais lagen. Ein Teil, der unterwegs
 * verloren ging, fiel niemandem auf: das neue Gerät behielt die Lücke, und ein
 * zweiter Lauf galt als „schon gesendet". Medien hatten längst Kennung,
 * Prüfsumme und ACK — der Text nicht.
 *
 * Jetzt gilt für den Text derselbe Vertrag:
 *
 * 1. Die Quelle legt je Zielgerät eine `transferId` und ein Manifest an
 *    (`history_manifest`): welche Mailbox, welche Art, wie viele Teile, wie
 *    viele Nachrichten. Plan und Teilgrenzen liegen auf der Platte, damit ein
 *    zweiter Lauf **dieselben** Teile schneidet.
 * 2. Jedes Teil (`history_sync`) trägt `transferId`, `teil` und `teile` und
 *    ist inhaltlich idempotent: dasselbe Teil zweimal ergibt keine zweite
 *    Zeile (`mischeVerlauf`).
 * 3. Das Ziel prüft Zielgerät, Unterschrift und Fenster, mischt erst danach und
 *    merkt sich den Fortschritt kontobezogen. Es quittiert jedes Teil
 *    (`history_part_ack`) und nach dem letzten `history_done`.
 * 4. Die Quelle merkt `msm:history_sent` **erst nach** `history_done`. Fehlt
 *    ein Teil, schickt der nächste Lauf genau diesen unter derselben Kennung
 *    noch einmal.
 *
 * Ein Paket ohne `transferId` stammt von einem älteren Stand und wird weiter
 * ohne Manifest angenommen — ein neues Gerät darf einen alten Absender nicht
 * aussperren.
 */

import { utf8ToBytes } from '@msdis/shield/core'
import { sha256Hex } from '@msdis/shield/integrity'

import type { E2eeGeraetItem } from '@/api/social'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { deriveBlindMailboxId, deriveUserDeviceMailboxId } from '../e2eeCrypto'
import { ladeGespraeche } from '../gespraechsListe'
import { ablage } from '../gruppen/gruppenAblage'
import { uebernimmDmGeheimnis } from '../gruppen/dmGeheimnis'
import {
  listeLokaleMailboxenStrict,
  loadLocalMessagesStrict,
  mischeVerlauf,
  parseMessageTimestamp,
  saveLocalMessages,
  type LocalStoredMessage,
  type VerlaufsArt,
} from '../messengerLocalStore'
import type { EigenesGeraet } from './geraeteAblage'
import {
  kompakteNachricht,
  MAX_TEILE,
  planeVerlaufsTeile,
  VERLAUFS_CHUNK,
  verlaufsAuswahl,
} from './verlaufsAuswahl'
import { sendeGeraeteQuittung } from './verlaufsQuittung'
import {
  MAX_POSTEN,
  MAX_TEILE_GESAMT,
  bitGesetzt,
  bitsVollstaendig,
  gesendetAn,
  istVollstaendig,
  ladeEmpfang,
  ladeQuelle,
  leereBits,
  loescheQuelle,
  merkeGesendet,
  setzeBit,
  speichereEmpfang,
  speichereQuelle,
  type PlanPosten,
  type QuellPlan,
  type VerlaufsPosten,
} from './verlaufsStand'
import { VERLAUF_MANIFEST_TYP, VERLAUF_PART_ACK_TYP, VERLAUF_DONE_TYP, VERLAUF_SYNC_TYP } from './verlaufsTypen'
import { FENSTER_MS, endung, kennungInOrdnung, sendeSigniertesPaket } from './verlaufsVersand'
import { anhaengeSammeln, type MedienAuftrag } from './verlaufsMedien'

interface DmGeheimnis {
  peerId: number
  geheimnis: string
  beleg?: Record<string, unknown>
}

interface MailboxInhalt {
  mailbox: string
  art: VerlaufsArt
  nachrichten: LocalStoredMessage[]
  dmGeheimnis?: DmGeheimnis
}

function alsDmGeheimnis(wert: unknown): DmGeheimnis | null {
  if (!wert || typeof wert !== 'object') return null
  const roh = wert as Record<string, unknown>
  const peerId = Number(roh.peerId)
  if (!Number.isInteger(peerId) || peerId <= 0) return null
  if (typeof roh.geheimnis !== 'string' || !roh.geheimnis) return null
  const beleg =
    roh.beleg && typeof roh.beleg === 'object' ? (roh.beleg as Record<string, unknown>) : undefined
  return { peerId, geheimnis: roh.geheimnis, ...(beleg ? { beleg } : {}) }
}

/**
 * Welche Mailbox zu welchem Gegenüber gehört.
 *
 * Das Chatgeheimnis liegt nicht in der Mailbox, sondern in der Ablage dieses
 * Kontos. Es muss mit: ohne es liest das neue Gerät die alte, abgeleitete
 * Mailbox und sieht keine Nachricht, die nach seiner Freigabe in der neuen
 * ankommt.
 */
async function partnerJeMailbox(konto: number): Promise<Map<string, number>> {
  const partner = new Map<string, number>()
  try {
    for (const gespraech of (await ladeGespraeche()).values()) {
      if (!gespraech?.userId) continue
      const abgeleitet = await deriveBlindMailboxId(konto, gespraech.userId)
      partner.set(gespraech.blindMailboxId || abgeleitet, gespraech.userId)
      partner.set(abgeleitet, gespraech.userId)
    }
  } catch {
    // Verschlossener Messenger: dann eben nur der Verlauf, ohne Geheimnis.
  }
  return partner
}

async function holeDmGeheimnis(peerId: number): Promise<DmGeheimnis | undefined> {
  try {
    const eintrag = await ablage.liesDmGeheimnis(peerId)
    if (!eintrag?.geheimnis) return undefined
    return {
      peerId,
      geheimnis: eintrag.geheimnis,
      ...(eintrag.beleg ? { beleg: eintrag.beleg } : {}),
    }
  } catch {
    return undefined
  }
}

/**
 * Liest den lokalen Bestand und filtert ihn auf das, was übergeben werden darf.
 *
 * `bis` ist der Beginn einer laufenden Übertragung. Ein zweiter Lauf nimmt
 * nichts dazu, was erst danach entstanden ist — sonst verschöben sich die
 * Teilgrenzen, und ein bereits quittiertes Teil trüge plötzlich anderen Inhalt.
 */
async function sammleInhalte(
  konto: number,
  bis: number | undefined,
  medien: Map<string, MedienAuftrag>,
  optionen: { beiTeilfehler?: () => void },
): Promise<{ inhalte: MailboxInhalt[]; fehlerhaft: Set<string> }> {
  const partner = await partnerJeMailbox(konto)
  const inhalte: MailboxInhalt[] = []
  const fehlerhaft = new Set<string>()
  let mailboxen: string[]
  try {
    mailboxen = (await listeLokaleMailboxenStrict()).slice().sort()
  } catch {
    optionen.beiTeilfehler?.()
    return { inhalte, fehlerhaft }
  }
  for (const mailbox of mailboxen) {
    try {
      const nachrichten = await loadLocalMessagesStrict(mailbox)
      if (nachrichten.length === 0) continue
      const peerId = partner.get(mailbox)
      const art: VerlaufsArt = peerId ? 'direkt' : 'gruppe'
      const dmGeheimnis = peerId ? await holeDmGeheimnis(peerId) : undefined
      let auswahl = verlaufsAuswahl(nachrichten, art, 'geraet')
      if (bis !== undefined) {
        auswahl = auswahl.filter((m) => {
          const zeit = parseMessageTimestamp(m.createdAt)
          return zeit === 0 || zeit <= bis
        })
      }
      if (auswahl.length === 0) continue
      anhaengeSammeln(auswahl, mailbox, medien)
      inhalte.push({ mailbox, art, nachrichten: auswahl, dmGeheimnis })
    } catch {
      // Eine kaputte Mailbox hält die übrigen nicht auf. Sie darf aber nicht
      // als „nichts mehr da" gelten: sonst gingen ihre Teile leer hinaus und
      // die Übergabe hielte die Lücke für erledigt.
      fehlerhaft.add(mailbox)
      optionen.beiTeilfehler?.()
    }
  }
  return { inhalte, fehlerhaft }
}

/** Das Manifest trägt nur, was das Ziel zum Zählen braucht. */
function manifestPosten(plan: QuellPlan): VerlaufsPosten[] {
  return plan.posten.map(({ mailbox, art, teile, anzahl }) => ({ mailbox, art, teile, anzahl }))
}

/**
 * Schneidet die Teile und hält ihre Grenzen fest.
 *
 * `grenzen` ist die kumulative Nachrichtenzahl je Teil. Damit findet ein
 * zweiter Lauf dieselben Grenzen im neu gelesenen Bestand, ohne die
 * Nachrichten selbst in der Fortschrittsablage zu duplizieren.
 */
async function bauePlan(
  konto: number,
  meins: EigenesGeraet,
  ziel: E2eeGeraetItem,
  inhalte: readonly MailboxInhalt[],
): Promise<QuellPlan | null> {
  const posten: PlanPosten[] = []
  let teileGesamt = 0
  for (const inhalt of inhalte) {
    const teile = planeVerlaufsTeile(inhalt.nachrichten, {
      mailbox: inhalt.mailbox,
      art: inhalt.art,
      dmGeheimnis: inhalt.dmGeheimnis,
    })
    if (teile.length === 0 || teile.length > MAX_TEILE) return null
    teileGesamt += teile.length
    if (teileGesamt > MAX_TEILE_GESAMT) return null
    let summe = 0
    posten.push({
      mailbox: inhalt.mailbox,
      art: inhalt.art,
      teile: teile.length,
      anzahl: inhalt.nachrichten.length,
      grenzen: teile.map((teil) => (summe += teil.length)),
    })
  }
  if (posten.length === 0) return null
  const jetzt = Date.now()
  const transferId = (
    await sha256Hex(utf8ToBytes(`msm:history-text:${konto}:${meins.kennung}:${ziel.device_id}:${jetzt}`))
  ).slice(0, 40)
  return {
    v: 1,
    transferId,
    ziel: ziel.device_id,
    begonnenAm: jetzt,
    ablauf: jetzt + FENSTER_MS,
    posten,
    quittiert: Object.fromEntries(posten.map((post) => [post.mailbox, leereBits(post.teile)])),
  }
}

async function sendeManifest(
  konto: number,
  meins: EigenesGeraet,
  ziel: E2eeGeraetItem,
  plan: QuellPlan,
): Promise<void> {
  const geraeteMailbox = await deriveUserDeviceMailboxId(konto)
  await sendeSigniertesPaket({
    konto,
    meins,
    ziel,
    mailbox: geraeteMailbox,
    typ: VERLAUF_MANIFEST_TYP,
    clientUuid: `history-manifest-${endung(ziel.device_id)}-${plan.transferId.slice(0, 10)}`,
    felder: { transferId: plan.transferId, ablauf: plan.ablauf },
    innereDaten: JSON.stringify({ v: 1, posten: manifestPosten(plan) }),
  })
}

/** Die Nachrichten eines Teils aus dem neu gelesenen Bestand. */
function teilNachrichten(
  inhalt: MailboxInhalt | undefined,
  post: PlanPosten,
  teil: number,
): LocalStoredMessage[] {
  if (!inhalt) return []
  const grenzen =
    Array.isArray(post.grenzen) && post.grenzen.length === post.teile ? post.grenzen : null
  if (grenzen) {
    const start = teil === 0 ? 0 : grenzen[teil - 1]
    const ende = grenzen[teil]
    return inhalt.nachrichten.slice(start, ende)
  }
  const teile = planeVerlaufsTeile(inhalt.nachrichten, {
    mailbox: inhalt.mailbox,
    art: inhalt.art,
    dmGeheimnis: inhalt.dmGeheimnis,
  })
  return teile[teil] ?? []
}

async function sendeTeil(
  konto: number,
  meins: EigenesGeraet,
  ziel: E2eeGeraetItem,
  plan: QuellPlan,
  postIndex: number,
  post: PlanPosten,
  teil: number,
  inhalt: MailboxInhalt | undefined,
): Promise<void> {
  const nachrichten = teilNachrichten(inhalt, post, teil)
  const dmGeheimnis = teil === 0 ? inhalt?.dmGeheimnis : undefined
  const geraeteMailbox = await deriveUserDeviceMailboxId(konto)
  await sendeSigniertesPaket({
    konto,
    meins,
    ziel,
    mailbox: geraeteMailbox,
    typ: VERLAUF_SYNC_TYP,
    clientUuid: `history-${endung(ziel.device_id)}-${plan.transferId.slice(0, 10)}-${postIndex}-${teil}`,
    felder: {
      transferId: plan.transferId,
      mailbox: post.mailbox,
      art: post.art,
      teil,
      teile: post.teile,
      ablauf: plan.ablauf,
    },
    innereDaten: JSON.stringify({
      v: 1,
      // Mailbox und Art stehen auch innen: ein älterer Empfänger kennt nur
      // dieses Paket, und der neue kann beide Angaben gegen die Außenhülle
      // prüfen. Sie sind kein Geheimnis, sondern blinde Kennungen.
      mailbox: post.mailbox,
      art: post.art,
      nachrichten,
      ...(dmGeheimnis ? { dmGeheimnis } : {}),
    }),
  })
}

/**
 * Schickt den Textverlauf an ein freigegebenes Gerät.
 *
 * Gibt die Zahl der in diesem Lauf tatsächlich abgelegten **Textteile**
 * zurück (das Manifest zählt nicht mit). Läuft bereits eine Übergabe, gehen
 * nur die noch nicht quittierten Teile hinaus; ist sie abgeschlossen, wird nur
 * noch nach Anhängen gesehen, die eigene Quittung noch nicht haben.
 */
export async function sendeTextVerlaufAnGeraet(
  ziel: E2eeGeraetItem,
  konto: number,
  meins: EigenesGeraet,
  optionen: { beiTeilfehler?: () => void } = {},
): Promise<{ gesendet: number; medien: Map<string, MedienAuftrag> }> {
  const medien = new Map<string, MedienAuftrag>()
  const fertig = gesendetAn(konto, ziel.device_id)
  let plan = fertig ? null : ladeQuelle(konto, ziel.device_id)
  if (fertig) loescheQuelle(konto, ziel.device_id)
  if (plan && (!Array.isArray(plan.posten) || plan.posten.length === 0)) {
    // Eine halb geschriebene oder beschädigte Planzeile ist kein Plan.
    loescheQuelle(konto, ziel.device_id)
    plan = null
  }
  if (plan && (!Number.isFinite(plan.ablauf) || plan.ablauf <= Date.now())) {
    // Das Fenster ist zu. Ein neuer Lauf beginnt einen neuen, ausdrücklichen
    // Transfer unter neuer Kennung — kein stilles Weiterführen eines alten.
    loescheQuelle(konto, ziel.device_id)
    plan = null
  }

  const { inhalte, fehlerhaft } = await sammleInhalte(konto, plan?.begonnenAm, medien, optionen)
  if (fertig) return { gesendet: 0, medien }

  if (!plan) {
    if (inhalte.length === 0 || fehlerhaft.size > 0) return { gesendet: 0, medien }
    plan = await bauePlan(konto, meins, ziel, inhalte)
    if (!plan) {
      // Mehr als 1000 Teile je Mailbox oder ein Posten ohne Inhalt: das ist
      // ein ausdrücklicher Teilfehler, kein vollständiger Verlauf.
      optionen.beiTeilfehler?.()
      return { gesendet: 0, medien }
    }
    speichereQuelle(konto, plan)
  }

  try {
    await sendeManifest(konto, meins, ziel, plan)
  } catch {
    optionen.beiTeilfehler?.()
    return { gesendet: 0, medien }
  }

  const jeMailbox = new Map(inhalte.map((inhalt) => [inhalt.mailbox, inhalt]))
  let gesendet = 0
  for (let postIndex = 0; postIndex < plan.posten.length; postIndex += 1) {
    const post = plan.posten[postIndex]
    if (fehlerhaft.has(post.mailbox)) continue
    const inhalt = jeMailbox.get(post.mailbox)
    for (let teil = 0; teil < post.teile; teil += 1) {
      if (bitGesetzt(plan.quittiert[post.mailbox], teil)) continue
      try {
        await sendeTeil(konto, meins, ziel, plan, postIndex, post, teil, inhalt)
        gesendet += 1
      } catch {
        optionen.beiTeilfehler?.()
        break
      }
    }
  }
  return { gesendet, medien }
}

/** Ein Posten aus einem Manifest, streng geprüft. */
function normalisierePosten(wert: unknown): VerlaufsPosten[] | null {
  if (!Array.isArray(wert) || wert.length === 0 || wert.length > MAX_POSTEN) return null
  const posten: VerlaufsPosten[] = []
  const gesehen = new Set<string>()
  let teileGesamt = 0
  for (const roh of wert) {
    if (!roh || typeof roh !== 'object') return null
    const eintrag = roh as Record<string, unknown>
    const mailbox = eintrag.mailbox
    if (!kennungInOrdnung(mailbox) || gesehen.has(mailbox)) return null
    const art: VerlaufsArt | null =
      eintrag.art === 'gruppe' ? 'gruppe' : eintrag.art === 'direkt' ? 'direkt' : null
    if (!art) return null
    const teile = Number(eintrag.teile)
    const anzahl = Number(eintrag.anzahl)
    if (!Number.isInteger(teile) || teile < 1 || teile > MAX_TEILE) return null
    if (!Number.isInteger(anzahl) || anzahl < 0 || anzahl > MAX_TEILE * VERLAUFS_CHUNK) return null
    teileGesamt += teile
    if (teileGesamt > MAX_TEILE_GESAMT) return null
    gesehen.add(mailbox)
    posten.push({ mailbox, art, teile, anzahl })
  }
  return posten
}

async function sendeTextDone(quellGeraet: string, transferId: string): Promise<void> {
  await sendeGeraeteQuittung(quellGeraet, VERLAUF_DONE_TYP, transferId)
}

async function sendeTextAck(
  quellGeraet: string,
  transferId: string,
  mailbox: string,
  teil: number,
  teile: number,
): Promise<void> {
  await sendeGeraeteQuittung(quellGeraet, VERLAUF_PART_ACK_TYP, transferId, {
    mailbox,
    teil,
    teile,
  })
}

/**
 * Nimmt ein Manifest an und legt den Empfangsfortschritt an.
 *
 * Ein zweites Manifest derselben Übertragung ändert nichts; es löst nur die
 * Schlussquittung erneut aus, falls die erste unterwegs verloren ging.
 */
export async function nimmtTextManifest(
  roh: Record<string, unknown>,
  vonGeraet: string,
  entsiegle: (umschlag: string) => Promise<string>,
): Promise<number> {
  const konto = angemeldetesKonto()
  if (!konto) return 0
  const transferId = roh.transferId
  if (!kennungInOrdnung(transferId)) return 0
  const ablauf = Number(roh.ablauf)
  const jetzt = Date.now()
  if (!Number.isFinite(ablauf) || ablauf < jetzt) return 0
  // Ein eigenes Gerät darf das Fenster nicht beliebig verlängern.
  if (ablauf > jetzt + FENSTER_MS + 60_000) return 0

  let paket: Record<string, unknown>
  try {
    const geparst = JSON.parse(await entsiegle(String(roh.paket || '')))
    if (!geparst || typeof geparst !== 'object') return 0
    paket = geparst as Record<string, unknown>
  } catch {
    return 0
  }
  if (Number(paket.v) !== 1) return 0
  const posten = normalisierePosten(paket.posten)
  if (!posten) return 0

  const geraet = String(roh.ziel || '')
  let stand = ladeEmpfang(konto, transferId)
  if (stand) {
    // Ein Manifest derselben Kennung darf seinen Inhalt nicht austauschen;
    // sonst könnte ein Geschwistergerät die Grenzen nachträglich verschieben.
    if (stand.von !== vonGeraet || stand.geraet !== geraet) return 0
    if (stand.ablauf !== ablauf) return 0
    if (JSON.stringify(stand.posten) !== JSON.stringify(posten)) return 0
  } else {
    stand = {
      v: 1,
      transferId,
      geraet,
      von: vonGeraet,
      begonnenAm: jetzt,
      ablauf,
      posten,
      teile: Object.fromEntries(posten.map((post) => [post.mailbox, leereBits(post.teile)])),
    }
    speichereEmpfang(konto, stand)
  }

  if (istVollstaendig(stand)) await sendeTextDone(vonGeraet, transferId)
  return 1
}

/** Übernimmt ein Chatgeheimnis, wenn das Paket eines trägt. */
async function uebernimmGeheimnis(paket: Record<string, unknown>): Promise<void> {
  const dm = alsDmGeheimnis(paket.dmGeheimnis)
  if (!dm) return
  try {
    await uebernimmDmGeheimnis(dm.peerId, dm.geheimnis, dm.beleg)
  } catch {
    // Der Verlauf darunter ist trotzdem brauchbar.
  }
}

async function mischeUndMelde(mailbox: string, nachrichten: LocalStoredMessage[], art: VerlaufsArt): Promise<void> {
  // Streng lesen: ein Lesefehler darf nicht als leerer Bestand gelten, sonst
  // überschriebe das Mischen den vorhandenen Verlauf mit nur diesem Teil.
  const vorhanden = await loadLocalMessagesStrict(mailbox)
  const zusammen = mischeVerlauf(vorhanden, nachrichten, art)
  await saveLocalMessages(mailbox, zusammen, art)
  if (typeof window !== 'undefined') {
    window.dispatchEvent(
      new CustomEvent('msm:messages-updated', { detail: { blind_mailbox_id: mailbox } }),
    )
  }
}

/**
 * Nimmt ein Textteil an.
 *
 * Ein Paket **ohne** `transferId` ist der alte Weg: es wird wie bisher direkt
 * gemischt. Ein neues Paket braucht ein angenommenes Manifest, wird erst nach
 * der Prüfung gemischt und danach quittiert; ein doppeltes Teil antwortet nur
 * noch einmal, statt dieselben Zeilen erneut zu schreiben.
 */
export async function nimmtTextTeil(
  roh: Record<string, unknown>,
  vonGeraet: string,
  entsiegle: (umschlag: string) => Promise<string>,
): Promise<number> {
  let paket: Record<string, unknown>
  try {
    const geparst = JSON.parse(await entsiegle(String(roh.paket || '')))
    if (!geparst || typeof geparst !== 'object') return 0
    paket = geparst as Record<string, unknown>
  } catch {
    return 0
  }

  const transferId = roh.transferId
  if (transferId === undefined) {
    // Alter Absender: Mailbox und Art stehen im inneren Paket.
    const mailbox = typeof paket.mailbox === 'string' ? paket.mailbox : ''
    const rohe = Array.isArray(paket.nachrichten) ? paket.nachrichten : []
    if (!mailbox || rohe.length === 0) return 0
    const art: VerlaufsArt = paket.art === 'gruppe' ? 'gruppe' : 'direkt'
    const nachrichten = rohe
      .map((m) => kompakteNachricht(m as LocalStoredMessage, 'geraet'))
      .filter((m): m is LocalStoredMessage => Boolean(m))
      .slice(0, 1000)
    if (nachrichten.length === 0) return 0
    await uebernimmGeheimnis(paket)
    await mischeUndMelde(mailbox, nachrichten, art)
    return nachrichten.length
  }

  const konto = angemeldetesKonto()
  if (!konto || !kennungInOrdnung(transferId)) return 0
  const mailbox = roh.mailbox
  const art: VerlaufsArt | null =
    roh.art === 'gruppe' ? 'gruppe' : roh.art === 'direkt' ? 'direkt' : null
  const teil = Number(roh.teil)
  const teile = Number(roh.teile)
  const ablauf = Number(roh.ablauf)
  if (!art || !kennungInOrdnung(mailbox)) return 0
  if (!Number.isInteger(teil) || !Number.isInteger(teile) || teil < 0 || teile < 1 || teil >= teile) {
    return 0
  }
  if (teile > MAX_TEILE) return 0
  if (!Number.isFinite(ablauf) || ablauf < Date.now()) return 0

  // Innen und außen müssen dasselbe sagen. Die Außenhülle ist unterschrieben;
  // eine abweichende Angabe im Chiffrat wäre ein Fehler des Absenders oder ein
  // Versuch, den Teil an eine andere Mailbox zu hängen.
  if (paket.mailbox !== undefined && paket.mailbox !== mailbox) return 0
  if (paket.art !== undefined && paket.art !== art) return 0

  const stand = ladeEmpfang(konto, transferId)
  if (!stand) return 0
  if (stand.von !== vonGeraet || stand.geraet !== String(roh.ziel || '')) return 0
  if (stand.ablauf !== ablauf) return 0
  const post = stand.posten.find((p) => p.mailbox === mailbox)
  if (!post || post.art !== art || post.teile !== teile) return 0

  if (bitGesetzt(stand.teile[mailbox], teil)) {
    // Die Quittung kann unterwegs verloren gegangen sein. Das Teil noch einmal
    // anzunehmen wäre nur ein weiterer Schreibvorgang; es antwortet erneut.
    await sendeTextAck(vonGeraet, transferId, mailbox, teil, teile)
    if (istVollstaendig(stand)) await sendeTextDone(vonGeraet, transferId)
    return 1
  }

  const rohe = Array.isArray(paket.nachrichten) ? paket.nachrichten : []
  if (rohe.length > VERLAUFS_CHUNK) return 0
  const nachrichten = rohe
    .map((m) => kompakteNachricht(m as LocalStoredMessage, 'geraet'))
    .filter((m): m is LocalStoredMessage => Boolean(m))
  // Das Chatgeheimnis hängt nicht an den Nachrichten dieses Teils: es kann
  // auch dann noch nützlich sein, wenn alle Zeilen inzwischen abgelaufen sind.
  await uebernimmGeheimnis(paket)
  // Ein Teil darf leer ankommen: zwischen zwei Läufen kann die Quelle eine
  // Nachricht gelöscht oder ihren Verfall erreicht haben. Der Absender und
  // das Ziel sind trotzdem fertig; ein Verweigern würde ewig kreisen.
  if (nachrichten.length > 0) {
    await mischeUndMelde(mailbox, nachrichten, art)
  }
  stand.teile[mailbox] = setzeBit(stand.teile[mailbox], teil, teile)
  speichereEmpfang(konto, stand)
  await sendeTextAck(vonGeraet, transferId, mailbox, teil, teile)
  if (istVollstaendig(stand)) await sendeTextDone(vonGeraet, transferId)
  return 1
}

/** Ein Teil-ACK der Gegenseite: gilt für den laufenden Plan dieses Ziels. */
export async function quittiereTextTeil(
  roh: Record<string, unknown>,
  vonGeraet: string,
): Promise<number> {
  const konto = angemeldetesKonto()
  if (!konto) return 0
  const transferId = roh.transferId
  const mailbox = roh.mailbox
  const teil = Number(roh.teil)
  const teile = Number(roh.teile)
  if (!kennungInOrdnung(transferId) || !kennungInOrdnung(mailbox)) return 0
  if (!Number.isInteger(teil) || teil < 0 || !Number.isInteger(teile) || teile < 1 || teil >= teile) {
    return 0
  }
  const plan = ladeQuelle(konto, vonGeraet)
  if (!plan || plan.transferId !== transferId) return 0
  const post = plan.posten.find((p) => p.mailbox === mailbox)
  if (!post || post.teile !== teile) return 0
  plan.quittiert[mailbox] = setzeBit(plan.quittiert[mailbox], teil, post.teile)
  speichereQuelle(konto, plan)
  return 1
}

/** Hat das Ziel jeden Teil des Plans einzeln bestätigt? */
function planVollstaendig(plan: QuellPlan): boolean {
  return plan.posten.every((post) => bitsVollstaendig(plan.quittiert[post.mailbox], post.teile))
}

/**
 * `history_done`: jetzt erst gilt der Verlauf als übergeben.
 *
 * Bewusst **nach** dieser Nachricht und nicht nach dem Absenden. Kommt sie
 * nie an, bleibt der Plan offen und der nächste Lauf schickt das Manifest
 * erneut; das Ziel antwortet dann mit einer neuen Schlussquittung.
 *
 * Die Schlussquittung allein genügt nicht: erst wenn **jeder** Teil auch
 * einzeln bestätigt ist, gilt der Plan als erledigt. Gehen einzelne ACKs
 * verloren, holt der nächste Lauf die Teile nach; das Ziel antwortet auf ein
 * doppeltes Teil erneut, und die dann folgende Schlussquittung trägt.
 */
export async function quittiereTextFertig(
  roh: Record<string, unknown>,
  vonGeraet: string,
): Promise<number> {
  const konto = angemeldetesKonto()
  if (!konto) return 0
  const transferId = roh.transferId
  if (!kennungInOrdnung(transferId)) return 0
  const plan = ladeQuelle(konto, vonGeraet)
  if (!plan || plan.transferId !== transferId || plan.ziel !== vonGeraet) return 0
  if (!planVollstaendig(plan)) return 0
  merkeGesendet(konto, vonGeraet)
  loescheQuelle(konto, vonGeraet)
  return 1
}
