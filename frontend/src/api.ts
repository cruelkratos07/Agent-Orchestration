// Thin typed wrapper over the FastAPI backend. Types mirror api.py's payloads.

export type Role = 'user' | 'agent' | 'system'

export interface Message {
  role: Role
  /** "customer", "verification", "order_agent", "policy_agent", "refund_agent" */
  author: string
  content: string
}

export interface Approval {
  type: 'refund_approval'
  order_id: string
  item: string
  amount: number
  customer_id: number
  customer_name: string | null
  reason: string
  /** One-line brief for the reviewer. LLM-written when a key is set, otherwise
   *  the same mechanical string as `reason`. */
  risk_summary: string
  customer_request: string
}

export interface ThreadView {
  thread_id: string
  verified: boolean
  customer_id: number | null
  customer_name: string | null
  messages: Message[]
  awaiting_approval: Approval | null
  new_messages?: Message[]
}

export interface QueueItem extends Approval {
  thread_id: string
}

export interface ThreadSummary {
  thread_id: string
  customer_name: string | null
  verified: boolean
  message_count: number
  awaiting_approval: boolean
  last_message: string | null
}

export interface Health {
  ok: boolean
  mode: string
  /** e.g. "gemini:gemini-3.5-flash (cache: redis)" or "no model (deterministic mode)" */
  model: string
  /** "sqlite:support.db" or "postgres" */
  database: string
  cache: { backend: string; hits?: number; misses?: number; hit_rate?: number | null }
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (!response.ok) {
    const error = await response.json().catch(() => null)
    const detail = typeof error?.detail === 'string' ? error.detail : `Request failed (${response.status})`
    throw new ApiError(response.status, detail)
  }
  return response.json() as Promise<T>
}

export const api = {
  health: (signal: AbortSignal) => request<Health>('/health', { signal }),
  createThread: () => request<{ thread_id: string }>('/threads', { method: 'POST' }),
  getThread: (id: string, signal: AbortSignal) => request<ThreadView>(`/threads/${id}`, { signal }),
  send: (id: string, text: string) =>
    request<ThreadView>(`/threads/${id}/messages`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  threads: (signal: AbortSignal) => request<ThreadSummary[]>('/threads', { signal }),
  escalations: (signal: AbortSignal) => request<QueueItem[]>('/escalations', { signal }),
  decide: (id: string, approved: boolean, note: string) =>
    request<ThreadView>(`/escalations/${id}/decide`, {
      method: 'POST',
      body: JSON.stringify({ approved, note }),
    }),
}
