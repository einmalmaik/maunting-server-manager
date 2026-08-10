/**
 * Wessen Wissen ein Panel zeigt.
 *
 * Erinnerungen und Skills werden an zwei Stellen gepflegt — im eigenen Profil
 * und unter Teams — und sollen sich dort gleich anfühlen. Statt zwei Ansichten
 * nebeneinander zu bauen (die auseinanderlaufen, sobald jemand nur eine davon
 * anfasst) bekommen beide Panels diesen einen Parameter.
 *
 * `canManage` steht nur am Team, weil nur dort die Frage auftaucht: eigenes
 * Wissen darf man immer ändern, Teamwissen nur mit dem entsprechenden Schalter.
 * Ohne ihn zeigt das Panel dieselbe Liste, aber ohne Formular und ohne
 * Löschknöpfe — lesen dürfen alle Mitglieder.
 */
export type AiKnowledgeScope =
  | { kind: 'user' }
  | { kind: 'team'; teamId: number; canManage: boolean }

export function scopeCanManage(scope: AiKnowledgeScope): boolean {
  return scope.kind === 'user' || scope.canManage
}

export function scopeTeamId(scope: AiKnowledgeScope): number | undefined {
  return scope.kind === 'team' ? scope.teamId : undefined
}
