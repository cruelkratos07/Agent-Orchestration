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
  approval_id: string
  status: string
}

export interface Session { subject: string; role: 'demo' | 'customer' | 'reviewer'; customer_id: number | null }
export interface AuditEvent {
  approval_id: string; reviewer_id: string; approved: boolean; note: string
  status: string; created_at: string; action: Approval
}
let accessToken = ''
export function setAccessToken(token: string) { accessToken = token }

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
  auth_mode: string
  mode: string
  /** e.g. "gemini:gemini-3.5-flash (cache: redis)" or "no model (deterministic mode)" */
  model: string
  /** "sqlite:support.db" or "postgres" */
  database: string
  cache: { backend: string; hits?: number; misses?: number; hit_rate?: number | null }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}), ...init?.headers },
  })
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`${response.status} ${detail}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  authConfig: () => request<{ demo: boolean }>('/auth/config'),
  me: () => request<Session>('/auth/me'),
  audit: (id: string) => request<AuditEvent[]>(`/threads/${id}/audit`),
  health: () => request<Health>('/health'),
  createThread: () => request<{ thread_id: string }>('/threads', { method: 'POST' }),
  getThread: (id: string) => request<ThreadView>(`/threads/${id}`),
  send: (id: string, text: string) =>
    request<ThreadView>(`/threads/${id}/messages`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  threads: () => request<ThreadSummary[]>('/threads'),
  escalations: () => request<QueueItem[]>('/escalations'),
  decide: (id: string, approved: boolean, note: string, approval_id: string) =>
    request<ThreadView>(`/escalations/${id}/decide`, {
      method: 'POST',
      body: JSON.stringify({ approved, note, approval_id }),
    }),
}
