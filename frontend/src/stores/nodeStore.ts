/**
 * Node store — loads/manages multi-node registry for admin UI + server create.
 * Never stores agent tokens after create/update (backend encrypts; API never returns them).
 */
import { create } from 'zustand'
import { api } from '@/api/client'
import type { Node } from '@/types'

let latestFetchRequest = 0

interface NodeState {
  nodes: Node[]
  total: number
  page: number
  limit: number
  loading: boolean
  error: string | null
  fetchNodes: (page?: number, limit?: number, search?: string) => Promise<void>
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
  total: 0,
  page: 1,
  limit: 50,
  loading: false,
  error: null,

  fetchNodes: async (page, limit, search) => {
    const requestId = ++latestFetchRequest
    set({ loading: true, error: null })
    try {
      let url = '/nodes'
      const params = new URLSearchParams()
      if (page !== undefined) params.append('page', String(page))
      if (limit !== undefined) params.append('limit', String(limit))
      if (search !== undefined && search.trim() !== '') params.append('search', search.trim())

      const queryStr = params.toString()
      if (queryStr) {
        url += `?${queryStr}`
      }

      const res = await api<Node[] | { items: Node[]; total: number; page: number; limit: number }>(url)
      if (Array.isArray(res)) {
        if (requestId !== latestFetchRequest) return
        set({ nodes: res, total: res.length, page: 1, limit: res.length, loading: false })
      } else {
        if (requestId !== latestFetchRequest) return
        set({
          nodes: res.items,
          total: res.total,
          page: res.page,
          limit: res.limit,
          loading: false,
        })
      }
    } catch (err: unknown) {
      if (requestId !== latestFetchRequest) return
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
    latestFetchRequest += 1
    set({ nodes: [...get().nodes, created].slice(0, get().limit), total: get().total + 1, loading: false })
    return created
  },

  updateNode: async (id, input) => {
    const updated = await api<Node>(`/nodes/${id}`, {
      method: 'PUT',
      body: JSON.stringify(input),
    })
    latestFetchRequest += 1
    set({
      nodes: get().nodes.map((n) => (n.id === id ? updated : n)),
    })
    return updated
  },

  deleteNode: async (id) => {
    await api(`/nodes/${id}`, { method: 'DELETE' })
    latestFetchRequest += 1
    set({ nodes: get().nodes.filter((n) => n.id !== id), total: Math.max(0, get().total - 1), loading: false })
  },

  healthCheck: async (id) => {
    // GET /nodes/{id} probes agent metrics and updates status
    const fresh = await api<Node>(`/nodes/${id}`)
    set({
      nodes: get().nodes.map((n) => (n.id === id ? fresh : n)),
    })
    return fresh
  },

  clear: () => {
    latestFetchRequest += 1
    set({ nodes: [], total: 0, page: 1, loading: false, error: null })
  },
}))
