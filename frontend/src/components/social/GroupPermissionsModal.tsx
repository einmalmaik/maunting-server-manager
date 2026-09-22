import { useState, useEffect, useMemo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Dialog,
  DialogContent,
  Button,
  Badge,
  Avatar,
  Dropdown,
  Input,
  type DropdownOption,
  RechteAbschnitte,
  type RechteAbschnittDefinition,
  type RechteZeile,
} from '@/Singra/UI'
import {
  Shield,
  Users,
  UserMinus,
  Check,
  X,
  Plus,
  Pencil,
  Trash2,
  ShieldCheck,
  Sliders,
  Phone,
  ChevronDown,
  ChevronUp,
  type LucideIcon,
} from 'lucide-react'
import {
  type ChatGroupItem,
  type ChatGroupMemberItem,
  getGroupMembers,
  updateGroupMemberRole,
  kickGroupMember,
  updateGroupPermissions,
} from '@/api/social'
import { deriveGroupBlindMailboxId } from '@/services/e2eeCrypto'
import type { GruppenKontext } from '@/services/gruppenSchluessel'
import {
  aendereGruppenzustand,
  ladeGruppenzustand,
  leererGruppenzustand,
  type GruppenRolle,
  type Konfiglesung,
} from '@/services/gruppenKonfig'
import {
  GRUPPEN_RECHTE,
  SYSTEM_GRUPPENROLLEN,
  SYSTEM_ROLLEN_IDS,
  permissionDescKey,
  permissionTitleKey,
  type Gruppenrolle,
  type GruppenRechtKennung,
} from '@/services/gruppenRollen'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'

interface GroupPermissionsModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  group: ChatGroupItem | null
  currentUserId: number
  onGroupUpdated?: (group: ChatGroupItem) => void
}

/*
 * Vokabular und Systemrollen liegen seit 09/2026 in `services/gruppenRollen.ts`.
 *
 * Sie standen hier, und deshalb kam der Messenger nicht an sie heran: er
 * beantwortete die Frage „darf dieses Konto eine fremde Nachricht löschen?"
 * mit `can_pin_messages` — dem Recht, eine Nachricht **anzuheften**. Eine
 * Rechtetabelle gehört nicht in eine Dialogdatei.
 */
export type GroupRoleDefinition = Gruppenrolle
export const GROUP_PERMISSION_DEFINITIONS = GRUPPEN_RECHTE
export type GroupPermissionKey = GruppenRechtKennung

type Uebersetzer = ReturnType<typeof useTranslation>['t']

/**
 * Systemrollen tragen einen Schlüssel als Namen, selbst angelegte einen Text,
 * den jemand eingetippt hat. Der wird nicht übersetzt — er gehört der Gruppe.
 */
function rollentext(
  wert: string,
  istSystem: boolean,
  t: (schluessel: string) => string,
): string {
  return istSystem ? t(wert) : wert
}

/**
 * Was in den Standardrechten **nicht** angeboten wird.
 *
 * `manage_roles` gehört nicht dorthin: wer Rollen verwalten darf, kann sich
 * jedes andere Recht selbst geben. Ein Haken, der das für alle setzt, wäre
 * keine Einstellung, sondern die Abschaffung der Rollen.
 *
 * Das hier ist nur die Anzeige. Durchgesetzt wird es im Backend
 * (`GROUP_ROLE_ONLY_PERMISSIONS` in `social_service.py`), und zwar seit
 * 09/2026: bis dahin stand die Regel ausschließlich in dieser Zeile, und ein
 * einzelner PATCH auf `/groups/<id>/permissions` trug `manage_roles` an den
 * Standardrechten ein, ohne dass jemand widersprach. Beide Listen gehören
 * zusammen — wer eine ändert, ändert die andere mit.
 */
const NICHT_ALS_STANDARD: ReadonlySet<string> = new Set(['manage_roles'])

/** Was eine frische Gruppe mitbringt, solange der Server nichts anderes sagt. */
const STANDARD_VORGABE = ['send_messages', 'attach_media', 'invite_members'] as const

/**
 * Die Abschnitte, in denen die Gruppenrechte stehen — in **beiden** Ansichten.
 *
 * Bis 09/2026 galten sie nur für den Standardrechte-Reiter. Das Rollen-Formular
 * daneben warf dieselben Rechte in eine flache zweispaltige Liste und ignorierte
 * `category` ganz: zwei Ansichten auf dasselbe Vokabular, und nur eine zeigte
 * seine Ordnung. Wer hier einen Abschnitt ändert, ändert jetzt beide.
 *
 * Die Reihenfolge der Rechte innerhalb eines Abschnitts ist die aus
 * `GROUP_PERMISSION_DEFINITIONS` — eine zweite Sortierliste wäre wieder eine
 * Stelle, die man beim nächsten neuen Recht vergessen kann.
 *
 * `symbol` steht hier als eigenes Feld, weil der Abschnitt vorher am Titel
 * erkannt wurde (`titel === 'Moderation'`). Das war auf Deutsch richtig und
 * auf Englisch nie wahr — das Schild hing am übersetzten Text.
 */
const RECHTE_ABSCHNITTE: {
  titelKey: string
  symbol: LucideIcon
  kategorien: readonly string[]
}[] = [
  { titelKey: 'social.groupRoles.defaultsChat', symbol: Users, kategorien: ['chat', 'members'] },
  { titelKey: 'social.groupRoles.defaultsCalls', symbol: Phone, kategorien: ['calls'] },
  { titelKey: 'social.groupRoles.defaultsModeration', symbol: Shield, kategorien: ['moderation', 'administration'] },
]

/**
 * Die Rechte als Zeilen für `RechteAbschnitte`: Text nachgeschlagen, das
 * Ausgeblendete weg. `ausgeblendet` ist der einzige Unterschied zwischen den
 * beiden Ansichten — die Standardrechte lassen `manage_roles` aus, das
 * Rollen-Formular zeigt es.
 */
function rechteZeilen(t: Uebersetzer, ausgeblendet?: ReadonlySet<string>): RechteZeile[] {
  return GROUP_PERMISSION_DEFINITIONS.filter((def) => !ausgeblendet?.has(def.key)).map((def) => ({
    key: def.key,
    kategorie: def.category,
    titel: t(permissionTitleKey(def.key)),
    beschreibung: t(permissionDescKey(def.key)),
  }))
}

function uebersetzteAbschnitte(t: Uebersetzer): RechteAbschnittDefinition[] {
  return RECHTE_ABSCHNITTE.map((abschnitt) => ({
    titel: t(abschnitt.titelKey),
    symbol: abschnitt.symbol,
    kategorien: abschnitt.kategorien,
  }))
}

/** Wird im Bauteil mit `t()` befüllt — hier stehen nur die Werte. */
const ROLE_OPTION_IDS = ['admin', 'moderator', 'member'] as const

interface GroupRoleFormProps {
  initial: GroupRoleDefinition | null
  onSubmit: (name: string, description: string, permissions: string[]) => Promise<void>
  onCancel: () => void
  disabled?: boolean
}

function GroupRoleForm({ initial, onSubmit, onCancel, disabled }: GroupRoleFormProps) {
  const { t } = useTranslation()

  const isSystemRole = Boolean(initial?.is_system)
  const isOwnerRole = initial?.id === 'owner'
  /*
   * Angezeigt wird, was auch in der Liste steht — nicht der Übersetzungs-
   * schlüssel. Eine Systemrolle trägt als `name` und `description` einen
   * Schlüssel wie `social.groupRoles.system.moderator.desc`; stand der im
   * Eingabefeld, las man ihn dort und speicherte ihn beim nächsten Klick als
   * Text ab. Solange die Rollen nur im Arbeitsspeicher lagen, fiel das beim
   * Schliessen des Dialogs wieder weg.
   */
  const [name, setName] = useState(
    initial ? rollentext(initial.name, initial.is_system, t) : '',
  )
  const [description, setDescription] = useState(
    initial
      ? rollentext(
          initial.description,
          initial.is_system && initial.description_is_key !== false,
          t,
        )
      : '',
  )
  const [selectedPerms, setSelectedPerms] = useState<Set<string>>(
    new Set(initial?.permissions ?? ['send_messages', 'attach_media'])
  )
  const [saving, setSaving] = useState(false)

  const togglePerm = (key: string) => {
    if (isOwnerRole) return
    setSelectedPerms((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const handleSelectAll = () => {
    if (isOwnerRole) return
    setSelectedPerms(new Set(GROUP_PERMISSION_DEFINITIONS.map((p) => p.key)))
  }

  const handleDeselectAll = () => {
    if (isOwnerRole) return
    setSelectedPerms(new Set())
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    setSaving(true)
    try {
      await onSubmit(name.trim(), description.trim(), Array.from(selectedPerms))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5 rounded-2xl border border-outline-variant/30 bg-surface-container/60 p-5">
      <div className="flex items-center justify-between border-b border-outline-variant/20 pb-3">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-primary" />
          <h3 className="font-headline text-body-md font-bold text-primary">
            {initial
              ? t('social.groupRoles.editRole', { name: rollentext(initial.name, initial.is_system, t) })
              : t('social.groupRoles.newRole')}
          </h3>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="p-1 rounded-md text-on-surface-variant hover:text-on-surface transition-colors"
          aria-label={t('common.close')}
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {isOwnerRole && (
        <div className="p-3 rounded-xl bg-status-warning/10 border border-status-warning/30 text-xs text-status-warning">
          {t('social.groupRoles.ownerLocked')}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block text-xs font-semibold text-on-surface-variant mb-1.5 uppercase tracking-wider">
            {t('social.groupRoles.nameLabel')}
          </label>
          <Input
            type="text"
            value={name}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setName(e.target.value)}
            placeholder={t('social.groupRoles.namePlaceholder')}
            disabled={isSystemRole || disabled}
            required
            className="text-xs h-9"
          />
        </div>
        <div>
          <label className="block text-xs font-semibold text-on-surface-variant mb-1.5 uppercase tracking-wider">
            {t('social.groupRoles.descLabel')}
          </label>
          <Input
            type="text"
            value={description}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDescription(e.target.value)}
            placeholder={t('social.groupRoles.descPlaceholder')}
            disabled={disabled}
            className="text-xs h-9"
          />
        </div>
      </div>

      {/* Permissions Picker */}
      <div className="space-y-3 pt-2">
        <div className="flex items-center justify-between">
          <span className="block text-xs font-semibold text-on-surface-variant uppercase tracking-wider">
            {t('social.groupRoles.permissionsCount', {
              selected: selectedPerms.size,
              total: GROUP_PERMISSION_DEFINITIONS.length,
            })}
          </span>
          {!isOwnerRole && (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleSelectAll}
                className="text-label-sm font-medium text-primary hover:underline"
              >
                {t('social.groupRoles.selectAll')}
              </button>
              <span className="text-on-surface-variant/40">•</span>
              <button
                type="button"
                onClick={handleDeselectAll}
                className="text-label-sm font-medium text-on-surface-variant hover:text-on-surface"
              >
                {t('social.groupRoles.selectNone')}
              </button>
            </div>
          )}
        </div>

        {/* Dieselben Abschnitte wie im Standardrechte-Reiter. `manage_roles`
            bleibt hier stehen — eine Rolle darf es tragen, die Standardrechte
            aller nicht (siehe NICHT_ALS_STANDARD). */}
        <RechteAbschnitte
          rechte={rechteZeilen(t)}
          abschnitte={uebersetzteAbschnitte(t)}
          gesetzt={selectedPerms}
          onToggle={(key) => togglePerm(key)}
          disabled={isOwnerRole || disabled}
          zeilenBeschriftung={(titel) => t('social.groupRoles.allow', { name: titel })}
        />
      </div>

      <div className="flex justify-end gap-2.5 pt-3 border-t border-outline-variant/20">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={saving}>
          {t('common.cancel')}
        </Button>
        <Button
          type="submit"
          variant="primary"
          size="sm"
          disabled={saving || isOwnerRole || disabled || !name.trim()}
          className="gap-1.5"
        >
          <Check className="w-4 h-4" />
          <span>{saving ? t('common.saving') : t('social.groupRoles.saveRole')}</span>
        </Button>
      </div>
    </form>
  )
}

export function GroupPermissionsModal({
  open,
  onOpenChange,
  group,
  currentUserId,
  onGroupUpdated,
}: GroupPermissionsModalProps) {
  const { t } = useTranslation()

  const [activeTab, setActiveTab] = useState<'members' | 'roles' | 'permissions'>('members')
  const [members, setMembers] = useState<ChatGroupMemberItem[]>([])
  const [loading, setLoading] = useState(false)
  const [savingPermissions, setSavingPermissions] = useState(false)

  /**
   * Die Rollen der Gruppe, wie sie im verschlüsselten Block stehen.
   *
   * Eine Kennung, die auch eine Systemrolle trägt (`admin`, `moderator`, …),
   * ist eine Überschreibung: Rechte und Beschreibung kommen dann von hier, der
   * Name bleibt der eingebaute. Alles andere ist eine eigene Rolle.
   */
  const [eigeneRollen, setEigeneRollen] = useState<GruppenRolle[]>([])
  const [rollenZuordnung, setRollenZuordnung] = useState<Record<string, number[]>>({})
  const [konfigLage, setKonfigLage] = useState<
    'laedt' | 'bereit' | { art: Konfiglesung['art'] }
  >('laedt')
  const [rollenSpeichern, setRollenSpeichern] = useState(false)
  const [editingRole, setEditingRole] = useState<GroupRoleDefinition | null>(null)
  const [isCreatingRole, setIsCreatingRole] = useState(false)
  const [expandedRoleDescriptions, setExpandedRoleDescriptions] = useState<Record<string, boolean>>({})

  /**
   * Die Standardrechte, als Menge der gesetzten Schlüssel.
   *
   * Vorher stand hier je Recht ein eigenes `useState`, dreimal wiederholt —
   * beim Anlegen, beim Laden und beim Speichern. Zwei neue Rechte kamen ins
   * Vokabular und fehlten hier still: der Dialog zeigte zehn Schalter, das
   * Backend kannte zwölf. Deshalb kommt die Liste jetzt aus
   * `GROUP_PERMISSION_DEFINITIONS` und nirgendwo sonst.
   */
  const [standardrechte, setStandardrechte] = useState<Set<string>>(new Set(STANDARD_VORGABE))

  const rollenAuswahl: DropdownOption[] = ROLE_OPTION_IDS.map((id) => ({
    value: id,
    label: t(`social.groupRoles.system.${id}.name`),
  }))

  const isOwner = group?.owner_user_id === currentUserId
  const currentUserRole = group?.role || (isOwner ? 'owner' : 'member')
  const canManage = isOwner || currentUserRole === 'admin'

  /**
   * Die Rollen, wie sie auf dem Bildschirm stehen: die eingebauten, und darüber
   * gelegt, was die Gruppe selbst festgelegt hat.
   *
   * Der Name einer Systemrolle bleibt der eingebaute Schlüssel — „Eigentümer"
   * soll in jeder Sprache „Eigentümer" heissen und in keiner Gruppe etwas
   * anderes bedeuten. Rechte und Beschreibung darf die Gruppe überschreiben.
   */
  const roles = useMemo<GroupRoleDefinition[]>(() => {
    const ueberschrieben = new Map(eigeneRollen.map((r) => [r.id, r]))
    const system = SYSTEM_GRUPPENROLLEN.map((vorlage) => {
      const eigen = ueberschrieben.get(vorlage.id)
      if (!eigen) return vorlage
      ueberschrieben.delete(vorlage.id)
      return {
        ...vorlage,
        description: eigen.beschreibung || vorlage.description,
        description_is_key: !eigen.beschreibung,
        permissions: eigen.rechte,
      }
    })
    const eigene = eigeneRollen
      .filter((r) => ueberschrieben.has(r.id))
      .map<GroupRoleDefinition>((r) => ({
        id: r.id,
        name: r.name,
        description: r.beschreibung,
        is_system: false,
        permissions: r.rechte,
      }))
    return [...system, ...eigene]
  }, [eigeneRollen])

  /**
   * Wer diesen Block geschrieben haben darf.
   *
   * Dieselbe Quelle wie im Backend (`darf_gruppenzustand_schreiben`): die
   * Mitgliederzeile. Sie ist Klartext und fällt mit Stufe 6 weg — bis dahin
   * prüfen beide Seiten dasselbe, statt zwei Wahrheiten zu pflegen. Den Block
   * gegen sich selbst zu prüfen wäre wertlos: er behauptet dann genau das, was
   * ihn beglaubigen soll.
   */
  const darfSchreiben = useCallback(
    (konto: number) => {
      if (konto === group?.owner_user_id) return true
      const mitglied = members.find((m) => m.user_id === konto)
      if (!mitglied) return false
      if (mitglied.role === 'owner' || mitglied.role === 'admin') return true
      return (mitglied.permissions || '')
        .split(',')
        .map((p) => p.trim())
        .includes('manage_roles')
    },
    [group?.owner_user_id, members],
  )

  const gruppenKontext = useCallback(async (): Promise<GruppenKontext | null> => {
    if (!group) return null
    return {
      groupId: group.id,
      blindMailboxId: await deriveGroupBlindMailboxId(group.id),
      eigeneId: currentUserId,
      mitglieder: members.map((m) => m.user_id),
    }
  }, [group, currentUserId, members])

  useEffect(() => {
    if (open && group) {
      void loadMembers()
      const gesetzt = (group.default_permissions || STANDARD_VORGABE.join(','))
        .split(',')
        .map((p) => p.trim())
        .filter(Boolean)
      setStandardrechte(new Set(gesetzt))
    }
  }, [open, group?.id, group?.default_permissions])

  /**
   * Den verschlüsselten Rollenblock holen — erst, wenn die Mitglieder da sind.
   *
   * Die Reihenfolge ist keine Vorsicht, sondern nötig: ohne Mitgliederliste
   * könnte `darfSchreiben` niemanden bestätigen, und ein gültiger Block käme
   * als „unbefugt" an.
   */
  useEffect(() => {
    if (!open || !group || members.length === 0) return
    let abgebrochen = false

    void (async () => {
      setKonfigLage('laedt')
      try {
        const kontext = await gruppenKontext()
        if (!kontext || abgebrochen) return
        const lesung = await ladeGruppenzustand(kontext, darfSchreiben)
        if (abgebrochen) return

        if (lesung.art === 'zustand') {
          setEigeneRollen(lesung.zustand.rollen)
          setRollenZuordnung(lesung.zustand.zuordnung)
          setKonfigLage('bereit')
        } else if (lesung.art === 'leer') {
          setEigeneRollen([])
          setRollenZuordnung({})
          setKonfigLage('bereit')
        } else {
          // Nichts anzeigen, was nicht geprüft ist. Ein Block, der nicht
          // aufgeht oder nicht beglaubigt ist, wird nicht „so gut es geht"
          // dargestellt — dann stünde eine Rechtetabelle auf dem Schirm, von
          // der niemand sagen kann, wer sie geschrieben hat.
          setEigeneRollen([])
          setRollenZuordnung({})
          setKonfigLage({ art: lesung.art })
        }
      } catch {
        if (!abgebrochen) setKonfigLage({ art: 'unlesbar' })
      }
    })()

    return () => {
      abgebrochen = true
    }
  }, [open, group?.id, members, darfSchreiben, gruppenKontext])

  const loadMembers = async () => {
    if (!group) return
    setLoading(true)
    try {
      const data = await getGroupMembers(group.id)
      setMembers(data)
    } catch {
      // Fallback zu den bereits in group vorhandenen Mitgliedern
      if (group.members) setMembers(group.members)
    } finally {
      setLoading(false)
    }
  }

  const handleRoleChange = async (member: ChatGroupMemberItem, newRole: string) => {
    if (!group) return
    try {
      const updated = await updateGroupMemberRole(
        group.id,
        member.user_id,
        newRole as 'admin' | 'moderator' | 'member'
      )
      setMembers((prev) =>
        prev.map((m) => (m.user_id === member.user_id ? { ...m, role: updated.role } : m))
      )
      toast.success(
        t('social.groupRoles.memberRoleChanged', {
          name: member.username,
          role: t(`social.groupRoles.system.${newRole}.name`),
        }),
      )
    } catch (err: any) {
      toast.error(err?.message || t('social.groupRoles.memberRoleFailed'))
    }
  }

  const handleKickMember = async (member: ChatGroupMemberItem) => {
    if (!group) return
    const ok = await confirm({
      title: t('social.groupRoles.kickTitle'),
      message: t('social.groupRoles.kickMessage', { name: member.username }),
      confirmText: t('common.remove'),
      danger: true,
    })
    if (!ok) return

    try {
      await kickGroupMember(group.id, member.user_id)
      setMembers((prev) => prev.filter((m) => m.user_id !== member.user_id))
      toast.success(t('social.groupRoles.kicked', { name: member.username }))
    } catch (err: any) {
      toast.error(err?.message || t('social.groupRoles.kickFailed'))
    }
  }

  const handleSaveDefaultPermissions = async () => {
    if (!group) return
    setSavingPermissions(true)
    // In der Reihenfolge des Vokabulars, und nur, was hier auch angeboten
    // wurde: ein Recht, das der Dialog nie zeigt, darf er auch nicht schreiben.
    const permString = GROUP_PERMISSION_DEFINITIONS.filter(
      (d) => !NICHT_ALS_STANDARD.has(d.key) && standardrechte.has(d.key),
    )
      .map((d) => d.key)
      .join(',')
    try {
      const updated = await updateGroupPermissions(group.id, permString)
      toast.success(t('social.groupRoles.defaultsSaved'))
      if (onGroupUpdated) onGroupUpdated(updated)
    } catch (err: any) {
      toast.error(err?.message || t('social.groupRoles.defaultsFailed'))
    } finally {
      setSavingPermissions(false)
    }
  }

  /**
   * Eine Änderung an den Rollen: lesen, ändern, verschlüsselt zurückschreiben.
   *
   * Die Änderung wird als Funktion übergeben und nicht als fertiger Stand, weil
   * `aendereGruppenzustand` sie bei einem Konflikt auf den frischen Stand
   * anwenden muss. Ein „ich hatte da eben noch etwas anderes gesehen"
   * überschreibt sonst die Rolle, die ein anderes Gerät gerade angelegt hat.
   */
  const speichereRollen = async (
    aendere: (vorher: { rollen: GruppenRolle[]; zuordnung: Record<string, number[]> }) => {
      rollen: GruppenRolle[]
      zuordnung: Record<string, number[]>
    },
    erfolg: string,
  ): Promise<boolean> => {
    const kontext = await gruppenKontext()
    if (!kontext) return false

    setRollenSpeichern(true)
    try {
      const ergebnis = await aendereGruppenzustand(kontext, darfSchreiben, (vorher) => ({
        ...leererGruppenzustand(),
        ...aendere(vorher),
      }))

      if (ergebnis.art === 'gespeichert') {
        // Aus der Quelle lesen statt den eigenen Stand fortzuschreiben: was
        // angezeigt wird, ist dann immer das, was auch geschrieben wurde.
        const frisch = await ladeGruppenzustand(kontext, darfSchreiben)
        if (frisch.art === 'zustand') {
          setEigeneRollen(frisch.zustand.rollen)
          setRollenZuordnung(frisch.zustand.zuordnung)
          setKonfigLage('bereit')
        }
        toast.success(erfolg)
        return true
      }

      if (ergebnis.art === 'konflikt') {
        toast.error(t('social.groupRoles.saveConflict'))
      } else if (ergebnis.art === 'nicht-unterschreibbar') {
        toast.error(t('social.groupRoles.saveUnsigned'))
      } else {
        toast.error(t('social.groupRoles.saveUnreadable'))
      }
      return false
    } catch (err: any) {
      toast.error(err?.message || t('social.groupRoles.saveFailed'))
      return false
    } finally {
      setRollenSpeichern(false)
    }
  }

  const handleCreateRole = async (name: string, description: string, permissions: string[]) => {
    // Zufällig statt fortlaufend: zwei Geräte, die gleichzeitig eine Rolle
    // anlegen, dürfen sich keine Kennung teilen — sonst stünde die eine Rolle
    // in der Zuordnung der anderen.
    const id = `custom_${crypto.randomUUID()}`
    const angelegt = await speichereRollen(
      (vorher) => ({
        ...vorher,
        rollen: [
          ...vorher.rollen,
          { id, name, beschreibung: description, rechte: permissions },
        ],
      }),
      t('social.groupRoles.created', { name }),
    )
    if (angelegt) setIsCreatingRole(false)
  }

  const handleUpdateRole = async (
    role: GroupRoleDefinition,
    name: string,
    description: string,
    permissions: string[]
  ) => {
    // Eine Systemrolle hat noch keinen Eintrag im Block, solange sie unverändert
    // ist. Die erste Änderung legt ihn an — unter derselben Kennung, damit die
    // Verschmelzung sie als Überschreibung erkennt und nicht als neue Rolle.
    //
    // Die Beschreibung einer Systemrolle wird nur abgelegt, wenn sie wirklich
    // geändert wurde. Sonst fröre ein blosser Haken sie in der Sprache ein, in
    // der er gesetzt wurde: der Moderator hiesse für alle künftigen Mitglieder
    // auf Deutsch, was vorher in elf Sprachen dastand. Leer heisst „nimm die
    // eingebaute".
    const eingebaut = SYSTEM_GRUPPENROLLEN.find((s) => s.id === role.id)
    const beschreibung =
      role.is_system && eingebaut && description.trim() === t(eingebaut.description).trim()
        ? ''
        : description

    const geaendert = await speichereRollen(
      (vorher) => {
        const vorhanden = vorher.rollen.some((r) => r.id === role.id)
        const neu: GruppenRolle = {
          id: role.id,
          name: role.is_system ? role.id : name,
          beschreibung,
          rechte: permissions,
        }
        return {
          ...vorher,
          rollen: vorhanden
            ? vorher.rollen.map((r) => (r.id === role.id ? neu : r))
            : [...vorher.rollen, neu],
        }
      },
      t('social.groupRoles.updated', {
        name: rollentext(role.name, role.is_system, t),
      }),
    )
    if (geaendert) setEditingRole(null)
  }

  const handleDeleteRole = async (role: GroupRoleDefinition) => {
    if (role.is_system) return
    const ok = await confirm({
      title: t('social.groupRoles.deleteTitle'),
      message: t('social.groupRoles.deleteMessage', { name: role.name }),
      confirmText: t('common.delete'),
      danger: true,
    })
    if (!ok) return

    await speichereRollen(
      (vorher) => {
        // Auch die Zuordnung mitnehmen: eine Rollenkennung ohne Rolle wäre eine
        // Liste von Konten, die auf nichts mehr zeigt — und beim nächsten
        // Anlegen mit derselben Kennung plötzlich wieder gälte.
        const { [role.id]: _entfernt, ...rest } = vorher.zuordnung
        return { rollen: vorher.rollen.filter((r) => r.id !== role.id), zuordnung: rest }
      },
      t('social.groupRoles.deleted', { name: role.name }),
    )
  }

  /** Die selbst angelegten Rollen — die eingebauten trägt die Mitgliederzeile. */
  const zusatzRollen = useMemo(
    () => eigeneRollen.filter((r) => !SYSTEM_ROLLEN_IDS.has(r.id)),
    [eigeneRollen],
  )

  /**
   * Eine eigene Rolle an einem Mitglied an- oder abschalten.
   *
   * Die Zuordnung steht im verschlüsselten Block und nicht in
   * `chat_group_members`. Genau das ist der Unterschied zur Systemrolle: „Konto
   * 42 ist Moderator in Gruppe 7" weiss der Server, „Konto 42 trägt
   * Nachtaufsicht" nicht.
   */
  const handleToggleMemberRole = async (
    member: ChatGroupMemberItem,
    rolle: GruppenRolle,
    an: boolean,
  ) => {
    await speichereRollen(
      (vorher) => {
        const bisher = vorher.zuordnung[rolle.id] ?? []
        const neu = an
          ? [...new Set([...bisher, member.user_id])]
          : bisher.filter((k) => k !== member.user_id)
        return { ...vorher, zuordnung: { ...vorher.zuordnung, [rolle.id]: neu } }
      },
      an
        ? t('social.groupRoles.memberRoleGiven', { name: member.username, role: rolle.name })
        : t('social.groupRoles.memberRoleTaken', { name: member.username, role: rolle.name }),
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="w-[96vw] max-w-5xl xl:max-w-6xl p-0 overflow-hidden bg-surface border-outline-variant/30 flex flex-col max-h-[92vh] sm:max-h-[88vh] shadow-2xl rounded-2xl"
      >
        {/* Header with generous vertical padding */}
        <div className="px-4 sm:px-6 py-4 sm:py-5 border-b border-outline-variant/20 bg-surface-container/70 flex items-center justify-between gap-2 shrink-0">
          {/*
           * `min-w-0 flex-1` ist hier keine Feinheit, sondern der Unterschied
           * zwischen „passt" und „Dialog kaputt".
           *
           * Titel und Untertitel tragen seit jeher `truncate`, und der innere
           * Kasten `min-w-0` — trotzdem lief der Kopf bei 375 px auf 383 px
           * auf. Grund: ein Flex-Kind hat `min-width: auto` und schrumpft
           * nicht unter seine Inhaltsbreite. Das `truncate` weiter innen kam
           * nie zum Zug, weil dieser Kasten hier gar nicht erst schmaler
           * wurde.
           *
           * Die Folge war mehr als ein abgeschnittener Titel: `DialogContent`
           * traegt `overflow-hidden`, und der Klick auf einen Reiter loest ein
           * `scrollIntoView` aus. Das setzte `scrollLeft` auf 90 — der ganze
           * Dialoginhalt stand danach links ausserhalb, der Titel las sich als
           * „uppen-Rollen & Rechte", und ohne Scrollleiste kam man nicht
           * zurueck. Nur Schliessen und Neuoeffnen half.
           */}
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <div className="w-10 h-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center shadow-sm shrink-0">
              <ShieldCheck className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <h2 className="font-headline text-body-lg font-bold text-primary truncate">
                {t('social.groupRoles.title')}
              </h2>
              <p className="text-xs text-on-surface-variant truncate">
                {t('social.groupRoles.subtitle', {
                  group: group?.name || t('social.groupRoles.groupFallback'),
                  count: members.length,
                })}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="p-2 rounded-xl text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high transition-colors shrink-0"
            aria-label={t('common.close')}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Tab Switcher - Responsive Segmented Controls on Mobile, Classic Tabs on Desktop */}
        <div className="border-b border-outline-variant/15 px-2.5 sm:px-6 pt-2 sm:pt-3.5 pb-2 sm:pb-0 bg-surface-container-low/60 shrink-0">
          <div className="grid grid-cols-3 sm:flex gap-1 sm:gap-6 w-full p-1 sm:p-0 rounded-xl sm:rounded-none bg-surface-container-high/40 sm:bg-transparent">
            <button
              type="button"
              onClick={() => {
                setActiveTab('members')
                setIsCreatingRole(false)
                setEditingRole(null)
              }}
              className={`py-2 sm:pb-3.5 px-1 sm:px-3 text-label-sm sm:text-sm font-semibold flex flex-col sm:flex-row items-center justify-center gap-1 sm:gap-2 transition-all rounded-lg sm:rounded-none sm:border-b-2 text-center select-none ${
                activeTab === 'members'
                  ? 'bg-primary/15 sm:bg-transparent text-primary sm:border-primary shadow-sm sm:shadow-none'
                  : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60 sm:hover:bg-transparent sm:border-transparent'
              }`}
            >
              <Users className="w-4 h-4 shrink-0" />
              <span className="leading-tight">
                <span className="sm:hidden">{t('social.groupRoles.tabMembersShort')}</span>
                <span className="hidden sm:inline">{t('social.groupRoles.tabMembers', { count: members.length })}</span>
              </span>
            </button>

            <button
              type="button"
              onClick={() => {
                setActiveTab('roles')
                setIsCreatingRole(false)
                setEditingRole(null)
              }}
              className={`py-2 sm:pb-3.5 px-1 sm:px-3 text-label-sm sm:text-sm font-semibold flex flex-col sm:flex-row items-center justify-center gap-1 sm:gap-2 transition-all rounded-lg sm:rounded-none sm:border-b-2 text-center select-none ${
                activeTab === 'roles'
                  ? 'bg-primary/15 sm:bg-transparent text-primary sm:border-primary shadow-sm sm:shadow-none'
                  : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60 sm:hover:bg-transparent sm:border-transparent'
              }`}
            >
              <Shield className="w-4 h-4 shrink-0" />
              <span className="leading-tight">
                <span className="sm:hidden">{t('social.groupRoles.tabRolesShort')}</span>
                <span className="hidden sm:inline">{t('social.groupRoles.tabRoles', { count: roles.length })}</span>
              </span>
            </button>

            <button
              type="button"
              onClick={() => {
                setActiveTab('permissions')
                setIsCreatingRole(false)
                setEditingRole(null)
              }}
              className={`py-2 sm:pb-3.5 px-1 sm:px-3 text-label-sm sm:text-sm font-semibold flex flex-col sm:flex-row items-center justify-center gap-1 sm:gap-2 transition-all rounded-lg sm:rounded-none sm:border-b-2 text-center select-none ${
                activeTab === 'permissions'
                  ? 'bg-primary/15 sm:bg-transparent text-primary sm:border-primary shadow-sm sm:shadow-none'
                  : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high/60 sm:hover:bg-transparent sm:border-transparent'
              }`}
            >
              <Sliders className="w-4 h-4 shrink-0" />
              <span className="leading-tight">
                <span className="sm:hidden">{t('social.groupRoles.tabDefaultsShort')}</span>
                <span className="hidden sm:inline">{t('social.groupRoles.tabDefaults')}</span>
              </span>
            </button>
          </div>
        </div>

        {/* Scrollable Tab Content with generous vertical spacing */}
        <div className="p-4 sm:p-6 flex-1 overflow-y-auto space-y-6">
          {/* TAB 1: MEMBERS */}
          {activeTab === 'members' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div>
                  <h3 className="font-headline text-body-sm font-bold text-primary">
                    {t('social.groupRoles.membersHeading')}
                  </h3>
                  <p className="text-xs text-on-surface-variant/80">
                    {t('social.groupRoles.membersHint')}
                  </p>
                </div>
                <Badge variant="default" className="text-xs px-2.5 py-0.5 font-medium">
                  {t('social.groupRoles.participantCount', { count: members.length })}
                </Badge>
              </div>

              {loading ? (
                <div className="py-16 text-center text-xs text-on-surface-variant">
                  {t('social.groupRoles.membersLoading')}
                </div>
              ) : (
                <div className="divide-y divide-outline-variant/20 rounded-2xl border border-outline-variant/30 bg-surface-container/70 overflow-hidden shadow-sm">
                  {members.map((member) => {
                    const isMemberOwner = member.role === 'owner' || member.user_id === group?.owner_user_id
                    const isSelf = member.user_id === currentUserId
                    const canEditThisMember =
                      canManage &&
                      !isMemberOwner &&
                      (isOwner || member.role !== 'admin')

                    return (
                      <div
                        key={`grp-mem-${member.user_id}`}
                        className="p-3.5 sm:p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4 hover:bg-surface-container-high/50 transition-colors"
                      >
                        <div className="flex items-center gap-3.5 min-w-0">
                          <Avatar src={member.avatar_url} name={member.username} size="md" />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-xs sm:text-sm font-bold text-primary truncate">
                                {member.username}
                              </span>
                              {isSelf && (
                                <span className="text-label-sm text-on-surface-variant/70 font-normal">
                                  {t('social.groupRoles.you')}
                                </span>
                              )}
                            </div>
                            <div className="flex items-center gap-2 mt-1 flex-wrap">
                              <Badge
                                variant={
                                  isMemberOwner
                                    ? 'warning'
                                    : member.role === 'admin'
                                    ? 'info'
                                    : member.role === 'moderator'
                                    ? 'default'
                                    : 'default'
                                }
                                className="text-label-sm py-0 px-2 font-medium"
                              >
                                {isMemberOwner
                                  ? t('social.groupRoles.system.owner.name')
                                  : member.role === 'admin'
                                  ? t('social.groupRoles.system.admin.name')
                                  : member.role === 'moderator'
                                  ? t('social.groupRoles.system.moderator.name')
                                  : t('social.groupRoles.system.member.name')}
                              </Badge>
                            </div>
                            {/*
                              Die eigenen Rollen dieser Gruppe. Anklickbar für
                              wen verwalten darf, sonst nur sichtbar — wer eine
                              Rolle trägt, soll das sehen können, auch ohne sie
                              ändern zu dürfen.
                            */}
                            {zusatzRollen.length > 0 && (
                              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                                {zusatzRollen.map((rolle) => {
                                  const traegt = (rollenZuordnung[rolle.id] ?? []).includes(
                                    member.user_id,
                                  )
                                  if (!canEditThisMember) {
                                    return traegt ? (
                                      <span
                                        key={`mem-rolle-${member.user_id}-${rolle.id}`}
                                        className="text-label-sm px-2 py-0.5 rounded-md bg-primary/15 text-primary font-semibold"
                                      >
                                        {rolle.name}
                                      </span>
                                    ) : null
                                  }
                                  return (
                                    <button
                                      key={`mem-rolle-${member.user_id}-${rolle.id}`}
                                      type="button"
                                      role="switch"
                                      aria-checked={traegt}
                                      disabled={rollenSpeichern}
                                      onClick={() =>
                                        void handleToggleMemberRole(member, rolle, !traegt)
                                      }
                                      aria-label={t('social.groupRoles.memberRoleToggleAria', {
                                        role: rolle.name,
                                        name: member.username,
                                      })}
                                      /*
                                        Auf dem Handy 44 px hoch, am Schreibtisch
                                        der schmale Chip. Als Anzeige reichten
                                        21 px; als Schalter, den man mit dem
                                        Daumen trifft, nicht. Die Höhe steckt im
                                        Knopf selbst und nicht in einem
                                        unsichtbaren Feld darüber — sonst
                                        überlappten sich zwei umgebrochene
                                        Reihen.
                                      */
                                      className={`text-label-sm inline-flex items-center px-3 sm:px-2 min-h-[44px] sm:min-h-0 sm:py-0.5 rounded-md font-semibold transition-colors disabled:opacity-50 ${
                                        traegt
                                          ? 'bg-primary/15 text-primary hover:bg-primary/25'
                                          : 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest'
                                      }`}
                                    >
                                      {rolle.name}
                                    </button>
                                  )
                                })}
                              </div>
                            )}
                          </div>
                        </div>

                        {/* Controls - always visible and touch-friendly */}
                        <div className="flex items-center gap-2.5 justify-end w-full sm:w-auto shrink-0 pt-2 sm:pt-0 border-t sm:border-t-0 border-outline-variant/10">
                          {canEditThisMember && (
                            <>
                              <div className="flex-1 sm:w-48 sm:flex-none">
                                <Dropdown
                                  value={member.role}
                                  onChange={(val) => void handleRoleChange(member, val)}
                                  options={rollenAuswahl}
                                />
                              </div>
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                onClick={() => void handleKickMember(member)}
                                className="h-9 w-9 p-0 text-error hover:bg-error/10 rounded-xl shrink-0"
                                title={t('social.groupRoles.kickTitle')}
                                aria-label={t('social.groupRoles.kickAria', { name: member.username })}
                              >
                                <UserMinus className="w-4 h-4" />
                              </Button>
                            </>
                          )}
                          {!canEditThisMember && isMemberOwner && (
                            <span className="text-xs text-on-surface-variant/70 font-medium px-3 py-1 bg-surface-container rounded-lg">
                              {t('social.groupRoles.groupLead')}
                            </span>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {/* TAB 2: ROLES MANAGEMENT (Roles.tsx style table & forms) */}
          {activeTab === 'roles' && (
            <div className="space-y-5">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div>
                  <h3 className="font-headline text-body-sm font-bold text-primary">
                    {t('social.groupRoles.rolesHeading')}
                  </h3>
                  <p className="text-xs text-on-surface-variant/80">
                    {t('social.groupRoles.rolesHint')}
                  </p>
                </div>
                {canManage && !isCreatingRole && !editingRole && (
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    disabled={konfigLage !== 'bereit'}
                    onClick={() => setIsCreatingRole(true)}
                    className="gap-1.5 rounded-xl h-8 px-3"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>{t('social.groupRoles.createRole')}</span>
                  </Button>
                )}
              </div>

              {/*
                Der Zustand des verschlüsselten Blocks, offen benannt.

                Ein Gerät, dem der Gruppenschlüssel fehlt, sieht die Rollen
                nicht — und soll das auch lesen können, statt vor einer Liste zu
                stehen, in der nur die eingebauten vier stehen und die eigenen
                fehlen. Dasselbe gilt für einen Block, der nicht beglaubigt ist:
                lieber „nicht prüfbar" als eine Rechtetabelle, von der niemand
                sagen kann, wer sie geschrieben hat.
              */}
              {konfigLage !== 'bereit' && (
                <div
                  className={`px-3.5 py-3 rounded-2xl border text-xs ${
                    konfigLage === 'laedt'
                      ? 'border-outline-variant/30 bg-surface-container/60 text-on-surface-variant'
                      : 'border-status-warning/40 bg-status-warning/10 text-status-warning'
                  }`}
                  role={konfigLage === 'laedt' ? undefined : 'alert'}
                >
                  {konfigLage === 'laedt'
                    ? t('social.groupRoles.configLoading')
                    : konfigLage.art === 'kein-schluessel'
                    ? t('social.groupRoles.configNoKey')
                    : konfigLage.art === 'unbefugt'
                    ? t('social.groupRoles.configUnsigned')
                    : t('social.groupRoles.configUnreadable')}
                </div>
              )}

              {/* Create Role Form */}
              {isCreatingRole && (
                <GroupRoleForm
                  initial={null}
                  onSubmit={handleCreateRole}
                  onCancel={() => setIsCreatingRole(false)}
                  disabled={rollenSpeichern}
                />
              )}

              {/* Edit Role Form */}
              {editingRole && (
                <GroupRoleForm
                  initial={editingRole}
                  onSubmit={(n, d, p) => handleUpdateRole(editingRole, n, d, p)}
                  onCancel={() => setEditingRole(null)}
                  disabled={rollenSpeichern}
                />
              )}

              {/* Roles List - Fully responsive card rows that work seamlessly on small windows and mobile */}
              <div className="space-y-3">
                {roles.map((r) => {
                  const isExpanded = Boolean(expandedRoleDescriptions[r.id])
                  return (
                    <div
                      key={`role-item-${r.id}`}
                      className="p-3.5 sm:p-4 rounded-2xl border border-outline-variant/35 bg-surface-container/75 hover:bg-surface-container/95 transition-colors shadow-sm space-y-2.5"
                    >
                      {/* Top Row: Role Name & Badges + Action Buttons */}
                      <div className="flex items-center justify-between gap-3 flex-wrap">
                        <div className="flex items-center gap-2.5 min-w-0 flex-wrap">
                          {r.is_system ? (
                            <Shield className="w-4 h-4 text-status-warning shrink-0" />
                          ) : (
                            <Shield className="w-4 h-4 text-primary shrink-0" />
                          )}
                          <span className="text-xs sm:text-sm font-bold text-on-surface truncate">
                            {rollentext(r.name, r.is_system, t)}
                          </span>
                          {r.is_system ? (
                            <span className="text-label-sm px-2 py-0.5 rounded-md bg-status-warning/15 text-status-warning font-semibold shrink-0">
                              {t('social.groupRoles.badgeSystem')}
                            </span>
                          ) : (
                            <span className="text-label-sm px-2 py-0.5 rounded-md bg-primary/15 text-primary font-semibold shrink-0">
                              {t('social.groupRoles.badgeCustom')}
                            </span>
                          )}
                          <Badge variant="default" className="text-label-sm px-2 py-0.5 font-medium shrink-0">
                            {t('social.groupRoles.rightsCount', { count: r.permissions.length })}
                          </Badge>
                        </div>

                        {/* Always visible, prominent Action Buttons */}
                        {canManage && (
                          <div className="flex items-center gap-1.5 shrink-0 ml-auto">
                            <button
                              type="button"
                              disabled={konfigLage !== 'bereit' || rollenSpeichern}
                              onClick={() => {
                                setIsCreatingRole(false)
                                setEditingRole(r)
                              }}
                              className="px-2.5 py-1.5 rounded-xl bg-surface-container-high hover:bg-primary/15 text-primary text-xs font-medium flex items-center gap-1.5 transition-colors disabled:opacity-50"
                              title={t('social.groupRoles.edit')}
                              aria-label={t('social.groupRoles.editAria', { name: rollentext(r.name, r.is_system, t) })}
                            >
                              <Pencil className="w-3.5 h-3.5" />
                              <span className="hidden xs:inline">{t('common.edit')}</span>
                            </button>
                            {!r.is_system && (
                              <button
                                type="button"
                                disabled={konfigLage !== 'bereit' || rollenSpeichern}
                                onClick={() => void handleDeleteRole(r)}
                                className="p-1.5 rounded-xl bg-surface-container-high hover:bg-error/15 text-error transition-colors disabled:opacity-50"
                                title={t('social.groupRoles.deleteTitle')}
                                aria-label={t('social.groupRoles.deleteAria', { name: r.name })}
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            )}
                          </div>
                        )}
                      </div>

                      {/* Middle: Description (collapsible on small text / short by default) */}
                      {r.description && (
                        <div>
                          <p
                            className={`text-xs text-on-surface-variant leading-relaxed ${
                              isExpanded ? '' : 'line-clamp-2 sm:line-clamp-none'
                            }`}
                          >
                            {rollentext(
                              r.description,
                              r.is_system && r.description_is_key !== false,
                              t,
                            )}
                          </p>
                          {r.description.length > 70 && (
                            <button
                              type="button"
                              onClick={() =>
                                setExpandedRoleDescriptions((prev) => ({
                                  ...prev,
                                  [r.id]: !isExpanded,
                                }))
                              }
                              className="sm:hidden text-label-sm font-medium text-primary hover:underline mt-1 flex items-center gap-1"
                            >
                              {isExpanded ? (
                                <>
                                  <span>{t('social.groupRoles.showLess')}</span>
                                  <ChevronUp className="w-3 h-3" />
                                </>
                              ) : (
                                <>
                                  <span>{t('social.groupRoles.showDescription')}</span>
                                  <ChevronDown className="w-3 h-3" />
                                </>
                              )}
                            </button>
                          )}
                        </div>
                      )}

                      {/* Permission Chips Preview */}
                      <div className="pt-2 border-t border-outline-variant/15 flex items-center gap-1.5 flex-wrap">
                        {r.permissions.slice(0, 4).map((pk) => {
                          const def = GROUP_PERMISSION_DEFINITIONS.find((p) => p.key === pk)
                          return (
                            <span
                              key={`role-chip-${r.id}-${pk}`}
                              className="text-label-sm px-2 py-0.5 rounded-md bg-surface-container-high text-on-surface-variant border border-outline-variant/20 font-medium"
                            >
                              {def ? t(permissionTitleKey(def.key)) : pk}
                            </span>
                          )
                        })}
                        {r.permissions.length > 4 && (
                          <span className="text-label-sm text-on-surface-variant/80 font-medium px-1">
                            {t('social.groupRoles.moreRights', { count: r.permissions.length - 4 })}
                          </span>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* TAB 3: DEFAULT PERMISSIONS (@everyone) */}
          {activeTab === 'permissions' && (
            <div className="space-y-5">
              <div className="p-4 rounded-2xl bg-surface-container/75 border border-outline-variant/35 text-xs text-on-surface-variant space-y-1.5">
                <span className="font-bold text-primary block text-body-sm">
                  {t('social.groupRoles.defaultsHeading')}
                </span>
                <p className="text-xs leading-relaxed">
                  {t('social.groupRoles.defaultsHint')}
                </p>
              </div>

              {/* Dasselbe Bauteil wie im Rollen-Formular, nur ohne
                  `manage_roles`: wer Rollen verwalten darf, kann sich jedes
                  andere Recht selbst geben. */}
              <RechteAbschnitte
                rechte={rechteZeilen(t, NICHT_ALS_STANDARD)}
                abschnitte={uebersetzteAbschnitte(t)}
                gesetzt={standardrechte}
                onToggle={(key, an) =>
                  setStandardrechte((vorher) => {
                    const neu = new Set(vorher)
                    if (an) neu.add(key)
                    else neu.delete(key)
                    return neu
                  })
                }
                disabled={!canManage}
                zeilenBeschriftung={(titel) => t('social.groupRoles.allow', { name: titel })}
              />


              {canManage && (
                <div className="flex justify-end pt-2">
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    disabled={savingPermissions}
                    onClick={() => void handleSaveDefaultPermissions()}
                    className="gap-2 rounded-xl px-4 py-2"
                  >
                    <Check className="w-4 h-4" />
                    <span>{savingPermissions ? t('common.saving') : t('social.groupRoles.saveDefaults')}</span>
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
