/**
 * Der Datenexport des Kontos: ein Knopf, eine Zip-Datei.
 *
 * Der Server liefert, was er lesen kann (`POST /auth/data-export`). Dieses
 * Gerät legt dazu, was nur es öffnen kann: persönliche Notizen und Termine
 * (`sv-note-v1:`/`sv-cal-v1:`), den Messenger-Verlauf und in der App den
 * entsperrten Tresor. Das Zip entsteht hier, damit nichts davon je zum Server
 * geht. Web und App nutzen denselben Weg, nur das Speichern unterscheidet sich.
 */

import { api } from '@/api/client'
import i18n from '@/i18n'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { blobTeile, inDerAppSpeichern } from '@/lib/geraetSpeichern'
import { zipSchreiben, type ZipEintrag } from '@/lib/zipSchreiben'
import { inDerApp, type Zweitnachweis } from '@/services/passkeyService'
import {
  CALENDAR_CIPHERTEXT_PREFIX,
  NOTE_CIPHERTEXT_PREFIX,
  altschluessel,
  decryptCalendarField,
  decryptNoteContent,
  decryptNoteTitle,
  getUserNotesKey,
} from '@/services/notesCalendarCrypto'
import {
  ladeAlleEntwuerfe,
  ladeFunkenAkten,
  listeLokaleMailboxen,
  loadLocalMessages,
} from '@/services/messengerLocalStore'
import { ladeGespraeche } from '@/services/gespraechsListe'
import { ladeGruppenNamen } from '@/services/gruppenName'
import { istOffen } from '@/services/lokaleVersiegelung'

export interface ExportNachweis {
  password?: string
  otp_code?: string
  passkey?: Zweitnachweis | null
}

/** Nur in der App: der Tresor, falls entsperrt. */
export interface TresorQuelle {
  entsperrt: boolean
  eintraege: () => unknown[]
}

type Zeile = Record<string, unknown>

interface ServerPaket {
  manifest: Record<string, unknown>
  tabellen: Record<string, Zeile[]>
  dateien: { pfad: string; base64: string }[]
}

/** Was im Paket fehlt und warum; die Karte zeigt es nach dem Export an. */
export interface ExportErgebnis {
  blob: Blob
  dateiname: string
  messengerGesperrt: boolean
  tresor: 'enthalten' | 'gesperrt' | 'nicht_hier'
  ohneSchluessel: number
}

/** Chat-Einstellungen, die nur auf diesem Gerät liegen. */
const LOKALE_CHAT_SCHLUESSEL = [
  'msm:chat_pins',
  'msm:chat_archive',
  'msm:chat_mutes',
  'msm:chat_blocks',
  'msm:chat_blocked_profiles',
  'msm:chat_pinned_message',
  'msm:chat_retention',
]

const NOTIZ_FELDER = ['title', 'content'] as const
const TERMIN_FELDER = ['title', 'description', 'location', 'recurrence'] as const

async function mitSchluessel<T>(
  versuch: (schluessel: CryptoKey | undefined) => Promise<T>,
  schluessel: CryptoKey | null,
  alt: CryptoKey | null,
): Promise<T> {
  try {
    return await versuch(schluessel ?? undefined)
  } catch (fehler) {
    if (!alt) throw fehler
    return versuch(alt)
  }
}

/**
 * Ersetzt in Notizen und Terminen das Geräte-Chiffrat durch Klartext, wo
 * dieses Gerät den Schlüssel hat. Gibt zurück, wie viele Felder zu blieben.
 */
async function geraeteChiffratOeffnen(paket: ServerPaket, kontoId: number): Promise<number> {
  const schluessel = await getUserNotesKey(kontoId)
  const alt = await altschluessel().catch(() => null)
  let zu = 0

  for (const notiz of paket.tabellen.notes ?? []) {
    for (const feld of NOTIZ_FELDER) {
      const wert = notiz[feld]
      if (typeof wert !== 'string' || !wert.startsWith(NOTE_CIPHERTEXT_PREFIX)) continue
      const uid = String(notiz.note_uid)
      try {
        notiz[feld] = await mitSchluessel(
          (k) => (feld === 'title' ? decryptNoteTitle(wert, uid, k, kontoId) : decryptNoteContent(wert, uid, k, kontoId)),
          schluessel,
          alt,
        )
      } catch {
        zu++
      }
    }
  }
  for (const termin of paket.tabellen.calendar_events ?? []) {
    for (const feld of TERMIN_FELDER) {
      const wert = termin[feld]
      if (typeof wert !== 'string' || !wert.startsWith(CALENDAR_CIPHERTEXT_PREFIX)) continue
      const uid = String(termin.event_uid)
      try {
        termin[feld] = await mitSchluessel((k) => decryptCalendarField(wert, uid, feld, k, kontoId), schluessel, alt)
      } catch {
        zu++
      }
    }
  }
  return zu
}

async function messengerEintraege(): Promise<ZipEintrag[] | null> {
  if (!istOffen()) return null
  const verlauf: Record<string, unknown> = {}
  for (const mailbox of await listeLokaleMailboxen()) {
    verlauf[mailbox] = await loadLocalMessages(mailbox)
  }
  const einstellungen: Record<string, unknown> = {}
  for (const schluessel of LOKALE_CHAT_SCHLUESSEL) {
    const wert = localStorage.getItem(schluessel)
    if (wert !== null) einstellungen[schluessel] = wert
  }
  return [
    json('geraet/messenger/verlauf.json', verlauf),
    json('geraet/messenger/gespraeche.json', [...(await ladeGespraeche()).values()]),
    json('geraet/messenger/gruppen.json', Object.fromEntries(await ladeGruppenNamen())),
    json('geraet/messenger/entwuerfe.json', await ladeAlleEntwuerfe()),
    json('geraet/messenger/funken.json', await ladeFunkenAkten()),
    json('geraet/messenger/einstellungen.json', einstellungen),
  ]
}

function json(pfad: string, daten: unknown): ZipEintrag {
  return { pfad, inhalt: JSON.stringify(daten, null, 2) }
}

function ausBase64(base64: string): Uint8Array {
  const roh = atob(base64)
  const bytes = new Uint8Array(roh.length)
  for (let i = 0; i < roh.length; i++) bytes[i] = roh.charCodeAt(i)
  return bytes
}

function liesMich(ergebnis: Omit<ExportErgebnis, 'blob' | 'dateiname'>, zugangsdaten: boolean): string {
  const t = i18n.t.bind(i18n)
  const zeilen = [
    t('profile.dataExport.readme.title'),
    '',
    t('profile.dataExport.readme.intro'),
    '',
    t('profile.dataExport.readme.structure'),
    '',
    zugangsdaten ? t('profile.dataExport.readme.secretsIncluded') : t('profile.dataExport.readme.secretsMissing'),
  ]
  if (ergebnis.ohneSchluessel > 0) {
    zeilen.push('', t('profile.dataExport.readme.notesLocked', { count: ergebnis.ohneSchluessel }))
  }
  if (ergebnis.messengerGesperrt) zeilen.push('', t('profile.dataExport.readme.messengerLocked'))
  if (ergebnis.tresor === 'gesperrt') zeilen.push('', t('profile.dataExport.readme.vaultLocked'))
  if (ergebnis.tresor === 'nicht_hier') zeilen.push('', t('profile.dataExport.readme.vaultAppOnly'))
  zeilen.push('', t('profile.dataExport.readme.excluded'))
  return zeilen.join('\n') + '\n'
}

export async function exportErstellen(nachweis: ExportNachweis, tresor?: TresorQuelle): Promise<ExportErgebnis> {
  const kontoId = angemeldetesKonto()
  if (kontoId === null) throw new Error(i18n.t('profile.dataExport.errors.noAccount'))

  const paket = await api<ServerPaket>('/auth/data-export', {
    method: 'POST',
    body: JSON.stringify(nachweis),
  })
  // Zwischen Anfrage und Antwort kann ein anderes Konto angemeldet worden sein.
  // Dessen Schlüssel und Verlauf gehören nicht in dieses Paket.
  if (angemeldetesKonto() !== kontoId) throw new Error(i18n.t('profile.dataExport.errors.noAccount'))

  const ohneSchluessel = await geraeteChiffratOeffnen(paket, kontoId)
  const messenger = await messengerEintraege()
  const tresorStand: ExportErgebnis['tresor'] = !tresor ? 'nicht_hier' : tresor.entsperrt ? 'enthalten' : 'gesperrt'

  // Ortsdatum: kurz nach Mitternacht hiesse die Datei sonst nach gestern.
  const heute = new Date()
  const datum = [
    heute.getFullYear(),
    String(heute.getMonth() + 1).padStart(2, '0'),
    String(heute.getDate()).padStart(2, '0'),
  ].join('-')
  const ergebnis = { messengerGesperrt: messenger === null, tresor: tresorStand, ohneSchluessel }
  const eintraege: ZipEintrag[] = [
    { pfad: 'LIESMICH.txt', inhalt: liesMich(ergebnis, paket.manifest.zugangsdaten_enthalten === true) },
    json('manifest.json', {
      ...paket.manifest,
      geraet: {
        felder_ohne_schluessel: ohneSchluessel,
        messenger: messenger === null ? 'gesperrt' : 'enthalten',
        tresor: tresorStand,
      },
    }),
    ...Object.entries(paket.tabellen).map(([name, zeilen]) => json(`server/${name}.json`, zeilen)),
    ...paket.dateien.map((datei) => ({ pfad: datei.pfad, inhalt: ausBase64(datei.base64) })),
    ...(messenger ?? []),
  ]
  if (tresor?.entsperrt) eintraege.push(json('tresor/eintraege.json', tresor.eintraege()))

  return { blob: zipSchreiben(eintraege), dateiname: `msm-datenexport-${datum}.zip`, ...ergebnis }
}

/** Legt das Zip ab. `false` heißt: der Mensch hat den Speichern-Dialog abgebrochen. */
export async function exportSpeichern(blob: Blob, dateiname: string): Promise<boolean> {
  if (inDerApp()) {
    return inDerAppSpeichern(dateiname, blobTeile(blob))
  }
  const adresse = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = adresse
  link.download = dateiname
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(adresse), 2000)
  return true
}
