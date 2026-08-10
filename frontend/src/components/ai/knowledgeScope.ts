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

/** Für Erinnerungen: entweder die eigenen oder die eines Teams. */
export type AiKnowledgeScope =
  | { kind: 'user' }
  | { kind: 'team'; teamId: number; canManage: boolean }

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

export function scopeTeamId(scope: AiKnowledgeScope): number | undefined {
  return scope.kind === 'team' ? scope.teamId : undefined
}

/** Die `team_id`, auf die dieser Skill-Bereich zeigt — `null` heißt panelweit. */
export function skillScopeTeamId(scope: AiSkillScope): number | null {
  return scope.kind === 'team' ? scope.teamId : null
}
