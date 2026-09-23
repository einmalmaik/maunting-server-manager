/**
 * Das Rechtevokabular einer Gruppe, die eingebauten Rollen — und die eine
 * Frage, die alle stellen: was darf dieses Konto hier eigentlich?
 *
 * **Warum das nicht mehr im Dialog steht.** Bis 09/2026 lagen Vokabular und
 * Systemrollen in `GroupPermissionsModal.tsx`. Der Messenger konnte sie
 * deshalb nicht benutzen und hatte für dieselbe Frage eine zweite, kürzere
 * Antwort: beim Löschen einer fremden Nachricht prüfte er `can_pin_messages` —
 * das Recht, eine Nachricht **anzuheften**. Zwei Antworten auf eine Frage sind
 * eine Gelegenheit, sie unterschiedlich falsch zu beantworten; eine
 * Rechtetabelle in einer Dialogdatei ist ausserdem für niemanden auffindbar,
 * der nicht schon weiss, dass sie dort liegt.
 *
 * **Zwei Quellen, bis Stufe 6.** Die Rechte eines Kontos stehen heute an zwei
 * Stellen, und beide werden hier zusammengeführt:
 *
 * 1. Die **Mitgliederzeile** auf dem Server: Systemrolle (`owner`, `admin`,
 *    `moderator`, `member`) und eine Rechteliste je Mitglied. Klartext, und
 *    genau deshalb auf dem Weg nach draussen.
 * 2. Der **verschlüsselte Block** der Gruppe (`gruppenKonfig.ts`): die eigenen
 *    Rollen samt Zuordnung, und Überschreibungen der eingebauten. Den liest der
 *    Server nie.
 *
 * **Vereinigt, nie abgezogen.** Was eine Quelle gibt, nimmt die andere nicht
 * weg. Ein Client, der einem Mitglied ein Recht abspräche, das der Server ihm
 * zugesteht, behauptete eine Schranke, die es nicht gibt — der Server liesse
 * die Handlung weiter zu. Ehrlicher ist: das hier sind die Rechte, für die es
 * einen Beleg gibt.
 */

import type { Gruppenzustand } from './gruppenKonfig'

/**
 * Die Rechte der Gruppe — einmal, mit Schlüsseln statt fertiger Sätze.
 *
 * Bis 09/2026 standen dieselben neun Rechte zweimal im Rechte-Dialog: einmal
 * für den Rollen-Editor, und noch einmal von Hand im Tab der Standardrechte,
 * dort mit kürzerem Wortlaut. Wer eine Beschreibung änderte, änderte sie an
 * einer Stelle.
 */
export const GRUPPEN_RECHTE = [
  { key: 'send_messages', category: 'chat' },
  { key: 'attach_media', category: 'chat' },
  { key: 'invite_members', category: 'members' },
  { key: 'start_group_calls', category: 'calls' },
  { key: 'join_group_calls', category: 'calls' },
  { key: 'share_screen', category: 'calls' },
  { key: 'mute_in_calls', category: 'moderation' },
  { key: 'kick_from_calls', category: 'moderation' },
  { key: 'kick_members', category: 'moderation' },
  { key: 'delete_messages', category: 'moderation' },
  { key: 'mention_everyone', category: 'moderation' },
  { key: 'pin_messages', category: 'moderation' },
  { key: 'manage_roles', category: 'administration' },
] as const

export type GruppenRechtKennung = (typeof GRUPPEN_RECHTE)[number]['key']

const ALLE_RECHTE: ReadonlySet<string> = new Set(GRUPPEN_RECHTE.map((r) => r.key))

/**
 * Was der Rechte-Dialog vor dem 17.09.2026 geschrieben hat.
 *
 * Dieselbe Tabelle steht in `social_service.py`. Sie wird beim **Lesen**
 * übersetzt; geschrieben wird nur noch kanonisch. Wer eine Zeile ändert,
 * ändert beide.
 */
const ALTE_NAMEN: Readonly<Record<string, readonly string[]>> = {
  call_start: ['start_group_calls'],
  call_join: ['join_group_calls'],
  call_share: ['share_screen'],
  call_moderate: ['mute_in_calls', 'kick_from_calls'],
}

/** Eine Rechteliste als Menge — alte Namen aufgelöst, Unbekanntes verworfen. */
export function leseRechte(roh: string | null | undefined): Set<string> {
  const gesetzt = new Set<string>()
  for (const teil of (roh ?? '').split(',')) {
    const name = teil.trim()
    if (!name) continue
    const ersatz = ALTE_NAMEN[name]
    if (ersatz) {
      for (const e of ersatz) gesetzt.add(e)
    } else if (ALLE_RECHTE.has(name)) {
      gesetzt.add(name)
    }
  }
  return gesetzt
}

export interface Gruppenrolle {
  id: string
  /** Bei Systemrollen ein Übersetzungsschlüssel, sonst der eingetippte Text. */
  name: string
  description: string
  is_system: boolean
  /** Nur für Systemrollen: ist `description` noch der Übersetzungsschlüssel? */
  description_is_key?: boolean
  permissions: string[]
}

/**
 * Die vier eingebauten Rollen. Name und Beschreibung sind Schlüssel — was in
 * der Oberfläche steht, holt `rollentext` daraus.
 */
export const SYSTEM_GRUPPENROLLEN: Gruppenrolle[] = [
  {
    id: 'owner',
    name: 'social.groupRoles.system.owner.name',
    description: 'social.groupRoles.system.owner.desc',
    is_system: true,
    permissions: GRUPPEN_RECHTE.map((p) => p.key),
  },
  {
    id: 'admin',
    name: 'social.groupRoles.system.admin.name',
    description: 'social.groupRoles.system.admin.desc',
    is_system: true,
    permissions: GRUPPEN_RECHTE.map((p) => p.key),
  },
  {
    id: 'moderator',
    name: 'social.groupRoles.system.moderator.name',
    description: 'social.groupRoles.system.moderator.desc',
    is_system: true,
    permissions: [
      'send_messages',
      'attach_media',
      'invite_members',
      'join_group_calls',
      'share_screen',
      'mute_in_calls',
      'kick_from_calls',
      'delete_messages',
      'mention_everyone',
      'pin_messages',
    ],
  },
  {
    id: 'member',
    name: 'social.groupRoles.system.member.name',
    description: 'social.groupRoles.system.member.desc',
    is_system: true,
    permissions: ['send_messages', 'attach_media', 'invite_members', 'join_group_calls'],
  },
]

/** Die Kennungen der eingebauten Rollen. Alles andere hat die Gruppe angelegt. */
export const SYSTEM_ROLLEN_IDS: ReadonlySet<string> = new Set(
  SYSTEM_GRUPPENROLLEN.map((r) => r.id),
)

/** `social.groupRoles.perm.<recht>.title` bzw. `.desc`. */
export function permissionTitleKey(recht: string): string {
  return `social.groupRoles.perm.${recht}.title`
}

export function permissionDescKey(recht: string): string {
  return `social.groupRoles.perm.${recht}.desc`
}

export interface RechteFrage {
  konto: number
  /** Die Systemrolle aus der Mitgliederzeile. */
  systemRolle: string | null | undefined
  /** Ob dieses Konto der Eigentümer der Gruppe ist. */
  istEigentuemer?: boolean
  /** `chat_group_members.permissions` — null heisst „nimm die Standardrechte". */
  eigeneRechte?: string | null
  /** `chat_groups.default_permissions` (@everyone). */
  standardrechte?: string | null
  /** Der entschlüsselte Gruppenblock, sofern dieses Gerät ihn lesen konnte. */
  zustand?: Gruppenzustand | null
}

/**
 * Was dieses Konto in dieser Gruppe darf.
 *
 * Der Aufbau, in dieser Reihenfolge:
 *
 * 1. **Die Mitgliederzeile.** Eigene Rechte, sonst die Standardrechte. So
 *    rechnet auch `SocialService.effective_permissions`.
 * 2. **Eigentümer und Administratoren bekommen alles.** Nicht weil sie es
 *    eingetragen hätten, sondern weil sie es sich jederzeit eintragen könnten:
 *    ihnen ein Recht abzusprechen, wäre eine Schranke, die keine ist.
 * 3. **Die Rollen aus dem verschlüsselten Block.** Eine Überschreibung einer
 *    Systemrolle gilt für alle, die diese Systemrolle tragen — sonst hätte das
 *    Bearbeiten von „Moderator" im Dialog keine Wirkung. Eine eigene Rolle gilt
 *    für die Konten in ihrer Zuordnung.
 *
 * Fehlt der Block (`zustand` ist `null`, etwa weil diesem Gerät der
 * Gruppenschlüssel noch fehlt), fallen nur die Rechte aus Schritt 3 weg. Das
 * ist der ehrliche Zustand: dieses Gerät kann die eigenen Rollen der Gruppe
 * nicht belegen, also behauptet es sie nicht.
 */
export function wirksameGruppenrechte(frage: RechteFrage): Set<string> {
  const rolle = frage.istEigentuemer ? 'owner' : (frage.systemRolle ?? 'member')

  if (rolle === 'owner' || rolle === 'admin') return new Set(ALLE_RECHTE)

  const rechte = leseRechte(
    frage.eigeneRechte !== null && frage.eigeneRechte !== undefined
      ? frage.eigeneRechte
      : frage.standardrechte,
  )

  const zustand = frage.zustand
  if (!zustand) return rechte

  for (const eigen of zustand.rollen) {
    const giltFuerMich =
      eigen.id === rolle || (zustand.zuordnung[eigen.id] ?? []).includes(frage.konto)
    if (!giltFuerMich) continue
    for (const recht of eigen.rechte) if (ALLE_RECHTE.has(recht)) rechte.add(recht)
  }

  // Eine Systemrolle ohne Überschreibung bringt ihre eingebauten Rechte mit.
  // Ohne diese Zeile hätte ein Moderator, an dem niemand etwas geändert hat,
  // weniger Rechte als einer, dessen Rolle einmal angefasst wurde.
  //
  // `member` ist ausgenommen, und zwar absichtlich: die eingebaute Liste dort
  // ist eine **Anzeige** dessen, was ein gewöhnliches Mitglied üblicherweise
  // hat, keine Zusage. Was es wirklich hat, stehen die Standardrechte — sonst
  // wäre der Reiter „@everyone" wirkungslos, weil jedes Mitglied die vier
  // Rechte der Vorlage ohnehin bekäme.
  if (!zustand.rollen.some((r) => r.id === rolle)) {
    const eingebaut = SYSTEM_GRUPPENROLLEN.find((r) => r.id === rolle)
    if (eingebaut && rolle !== 'member') {
      for (const recht of eingebaut.permissions) rechte.add(recht)
    }
  }

  return rechte
}

/** Kurzform für die eine Frage, die der Verlauf stellt. */
export function hatGruppenrecht(frage: RechteFrage, recht: GruppenRechtKennung): boolean {
  return wirksameGruppenrechte(frage).has(recht)
}
