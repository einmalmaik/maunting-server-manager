import { api } from '@/api/client'
import type { Node } from '@/types'

export interface NodeInstallCommand {
  command: string
}

export interface PendingNodeEnrollment {
  id: number
  display_code: string
  name: string
  host: string
  expires_at: string
}

export function getNodeInstallCommand(): Promise<NodeInstallCommand> {
  return api<NodeInstallCommand>('/nodes/install-command')
}

export function getPendingNodeEnrollments(): Promise<PendingNodeEnrollment[]> {
  return api<PendingNodeEnrollment[]>('/nodes/enrollments/pending')
}

export function approveNodeEnrollment(enrollmentId: number): Promise<Node> {
  return api<Node>(`/nodes/enrollments/${enrollmentId}/approve`, {
    method: 'POST',
  })
}
