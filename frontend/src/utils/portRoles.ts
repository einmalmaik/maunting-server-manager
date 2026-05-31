import type { BlueprintPortDef, BlueprintPortRole } from '@/types'

const STANDARD_ROLES = new Set(['game', 'query', 'rcon', 'voice', 'web'])

export type MappedBlueprintPort = BlueprintPortDef & {
  mappedRole: string
}

export function portRoleBase(role: string): string {
  for (const base of STANDARD_ROLES) {
    if (role === base || role.startsWith(`${base}_`)) return base
  }
  return role
}

export function mapBlueprintPorts(portDefs: BlueprintPortDef[]): MappedBlueprintPort[] {
  const counts: Record<string, number> = {}
  let customIdx = 1

  return portDefs.map((port) => {
    if (port.role) {
      return { ...port, mappedRole: port.role }
    }
    if (port.name === 'custom') {
      const mappedRole = `custom_${customIdx}`
      customIdx += 1
      return { ...port, mappedRole }
    }
    const count = (counts[port.name] ?? 0) + 1
    counts[port.name] = count
    return {
      ...port,
      mappedRole: count === 1 ? port.name : `${port.name}_${count}`,
    }
  })
}

export function labelRole(role: string): BlueprintPortRole | string {
  return portRoleBase(role)
}
