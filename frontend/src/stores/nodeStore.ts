/**
 * Node store — loads/manages multi-node registry for admin UI + server create.
 * Never stores agent tokens after create/update (backend encrypts; API never returns them).
 */
import { create } from 'zustand'
import { api } from '@/api/client'
import type { Node } from '@/types'

interface NodeState {
  nodes: Node[]
  loading: boolean
  error: string | null
  fetchNodes: () => Promise<void>
  createNode: (input: {
    name: string
    host: string
    auth_token: string
    tls_fingerprint?: string
  }) => Promise<Node>
  updateNode: (
    id: number,
    input: {
      name?: string
      host?: string
      auth_token?: string
      tls_fingerprint?: string
    },
  ) => Promise<Node>
  deleteNode: (id: number) => Promise<void>
  healthCheck: (id: number) => Promise<Node>
  clear: () => void
}

export const useNodeStore = create<NodeState>((set, get) => ({
  nodes: [],
  loading: false,
  error: null,

  fetchNodes: async () => {
    set({ loading: true, error: null })
    try {
      const nodes = await api<Node[]>('/nodes')
      set({ nodes, loading: false })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Nodes konnten nicht geladen werden'
      set({ error: message, loading: false })
      throw err
    }
  },

  createNode: async (input) => {
    const created = await api<Node>('/nodes', {
      method: 'POST',
      body: JSON.stringify(input),
    })
    set({ nodes: [...get().nodes, created] })
    return created
  },

  updateNode: async (id, input) => {
    const updated = await api<Node>(`/nodes/${id}`, {
      method: 'PUT',
      body: JSON.stringify(input),
    })
    set({
      nodes: get().nodes.map((n) => (n.id === id ? updated : n)),
    })
    return updated
  },

  deleteNode: async (id) => {
    await api(`/nodes/${id}`, { method: 'DELETE' })
    set({ nodes: get().nodes.filter((n) => n.id !== id) })
  },

  healthCheck: async (id) => {
    // GET /nodes/{id} probes agent metrics and updates status
    const fresh = await api<Node>(`/nodes/${id}`)
    set({
      nodes: get().nodes.map((n) => (n.id === id ? fresh : n)),
    })
    return fresh
  },

  clear: () => set({ nodes: [], loading: false, error: null }),
}))
