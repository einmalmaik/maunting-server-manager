/**
 * Wessen Wissen ein Panel zeigt.
 *
 * Zwei Typen statt einem, weil Erinnerungen und Skills die Frage „wem gehört
 * das?" verschieden beantworten — und der eine Typ für beide hat genau daraus
 * einen Fehler gemacht.
 *
 * Erinnerungen kennen einen echten **persönlichen** Bereich: `scope='user'`,
 * Kennung `user:{id}`, ohne Team. Skills kennen ihn nicht — ihre
 * `scope_identity` ist ausschließlich `"global"` oder `"team:{id}"`
 * (`backend/models/ai_skill.py`). Persönliche Skills liegen deshalb im
 * **Ein-Mann-Team**, das jeder Benutzer hat; das ist eine echte Teamzeile mit
 * `personal_for_user_id`, kein Sonderfall.
 *
 * Solange beide denselben Typ teilten, fiel das Skill-Panel für das persönliche
 * Team in seinen bereichslosen Modus und zeigte mitgelieferte, panelweite und
 * fremde Team-Skills in einem Topf — an zwei Stellen dieselbe Ansicht, und
 * anlegen ließ sich ein persönlicher Skill gar nicht.
 */

/**
 * Für Erinnerungen: die eigenen, die eines Teams oder die des Panels.
 *
 * `user` umfasst beides, was dem Benutzer selbst gehört — allgemeine Einträge
 * und die Notizen zu einzelnen Servern (`server:{id}:user:{uid}`). Die
 * serverbezogenen sind ebenso persönlich, hatten aber lange gar keine
 * Oberfläche: die KI schrieb sie, sie liefen in jedem Gespräch mit, und
 * niemand konnte sie sehen oder löschen.
 *
 * `panel` gehört dem Betreiber und gilt für **jeden** Benutzer. Er steht
 * deshalb in den Einstellungen und nicht im Profil.
 *
 * `server_shared` gehört der **Anlage**. Zwei Dinge unterscheiden ihn von den
 * Servernotizen unter `user`: er ist für jeden sichtbar, der den Server sehen
 * darf, und er hat keinen Besitzer — er überlebt den Kollegen, der ihn
 * aufgeschrieben hat, und verschwindet mit dem Server. Deshalb steht er auf
 * einem Reiter der Serveransicht und nicht im Profil.
 */
export type AiKnowledgeScope =
  | { kind: 'user' }
  | { kind: 'server_shared'; serverId: number; canManage: boolean }
  | { kind: 'team'; teamId: number; canManage: boolean }
  | { kind: 'panel'; canManage: boolean }

/**
 * Für Skills: entweder panelweit oder ein bestimmtes Team.
 *
 * `personal` unterscheidet das Ein-Mann-Team von einem beigetretenen. Es steuert
 * nur die Beschriftung — für das Backend sind beide dasselbe.
 */
export type AiSkillScope =
  | { kind: 'panel'; canManage: boolean }
  | { kind: 'team'; teamId: number; personal: boolean; canManage: boolean }

export function scopeCanManage(scope: AiKnowledgeScope): boolean {
  return scope.kind === 'user' || scope.canManage
}

/** Der Scope-Wert, den die Memory-API für diesen Bereich erwartet. */
export function memoryScopeName(
  scope: AiKnowledgeScope,
): 'user' | 'server_shared' | 'team' | 'panel' {
  return scope.kind
}

export function scopeTeamId(scope: AiKnowledgeScope): number | undefined {
  return scope.kind === 'team' ? scope.teamId : undefined
}

/** Die `server_id`, auf die dieser Bereich zeigt — sonst `undefined`. */
export function scopeServerId(scope: AiKnowledgeScope): number | undefined {
  return scope.kind === 'server_shared' ? scope.serverId : undefined
}

/** Die `team_id`, auf die dieser Skill-Bereich zeigt — `null` heißt panelweit. */
export function skillScopeTeamId(scope: AiSkillScope): number | null {
  return scope.kind === 'team' ? scope.teamId : null
}
