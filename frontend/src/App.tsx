import { useCallback, useEffect, useRef, useState } from 'react'
import Conversation from './Conversation'
import Queue from './Queue'
import StatusBar from './StatusBar'
import ThreadList from './ThreadList'
import { api, type AuditEvent, type Session, type Health, type QueueItem, type ThreadSummary, type ThreadView } from './api'

// Polling keeps the review queue current without introducing a broker.
const POLL_MS = 2000

const blankThread = (thread_id: string): ThreadView => ({
  thread_id,
  verified: false,
  customer_id: null,
  customer_name: null,
  messages: [],
  awaiting_approval: null,
})

export default function App({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const canReview = session.role !== 'customer'
  const canChat = session.role !== 'reviewer'
  const initialized = useRef(false)
  const [audit, setAudit] = useState<AuditEvent[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [thread, setThread] = useState<ThreadView | null>(null)
  const [threads, setThreads] = useState<ThreadSummary[]>([])
  const [queue, setQueue] = useState<QueueItem[]>([])
  const [busy, setBusy] = useState(false)
  const [deciding, setDeciding] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [nextQueue, nextThreads, nextHealth] = await Promise.all([
        canReview ? api.escalations() : Promise.resolve([]),
        api.threads(),
        api.health(),
      ])
      setQueue(nextQueue)
      setThreads(nextThreads)
      setHealth(nextHealth)
    } catch (caught) {
      setError(String(caught))
    }
  }, [canReview])

  const startThread = useCallback(async () => {
    const { thread_id } = await api.createThread()
    setThread(blankThread(thread_id))
  }, [])

  useEffect(() => {
    if (canChat && !initialized.current) {
      initialized.current = true
      startThread().catch((caught) => setError(String(caught)))
    }
  }, [startThread, canChat])

  useEffect(() => {
    let cancelled = false
    setAudit([])
    if (canReview && thread?.messages.length) api.audit(thread.thread_id)
      .then(rows => { if (!cancelled) setAudit(rows) })
      .catch(caught => { if (!cancelled) setError(String(caught)) })
    return () => { cancelled = true }
  }, [thread, canReview])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, POLL_MS)
    return () => clearInterval(timer)
  }, [refresh])

  async function send(text: string) {
    if (!thread) return
    setBusy(true)
    setError(null)
    // Optimistic echo: the customer should see their own line immediately.
    setThread({
      ...thread,
      messages: [...thread.messages, { role: 'user', author: 'customer', content: text }],
    })
    try {
      setThread(await api.send(thread.thread_id, text))
      await refresh()
    } catch (caught) {
      setThread(thread)
      setError(String(caught))
    } finally {
      setBusy(false)
    }
  }

  async function decide(threadId: string, approved: boolean, note: string) {
    setDeciding(threadId)
    setError(null)
    try {
      const item = queue.find(item => item.thread_id === threadId)
      if (!item) throw new Error('Queue changed; refresh and try again.')
      const updated = await api.decide(threadId, approved, note, item.approval_id)
      if (threadId === thread?.thread_id) setThread(updated)
    } catch (caught) {
      // A 409 can mean another reviewer claimed the item or the interrupt changed.
      setError(
        String(caught).includes('409')
          ? 'This approval changed or was already claimed. Queue refreshed; inspect decision history before retrying.'
          : String(caught),
      )
    } finally {
      setDeciding(null)
      await refresh()
    }
  }

  async function open(threadId: string) {
    try {
      setThread(await api.getThread(threadId))
    } catch (caught) {
      setError(String(caught))
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="mark" aria-hidden />
          <h1>Order Support Console</h1>
        </div>
        <StatusBar health={health} />
        <span className="badge">{session.subject} · {session.role}</span>
        {session.role !== 'demo' && <button className="ghost" onClick={onSignOut}>Sign out</button>}
      </header>

      {session.role === 'demo' && <p className="banner">Local demonstration · synthetic customers · identity lookup is simulated · no real payments</p>}

      {error && (
        <p className="error" role="status">
          {error}
          <button className="ghost" onClick={() => setError(null)}>Dismiss</button>
        </p>
      )}

      <main className={`layout ${!canReview ? 'layout--customer' : ''}`}>
        <ThreadList
          threads={threads}
          activeThread={thread?.thread_id ?? null}
          onOpen={open}
          onNew={() => startThread().catch((caught) => setError(String(caught)))}
          canCreate={canChat}
        />
        <Conversation thread={thread} busy={busy} onSend={send} readOnly={!canChat} demo={session.role === 'demo'} />
        {canReview && <div className="review-pane"><Queue
          items={queue}
          activeThread={thread?.thread_id ?? null}
          busy={deciding}
          onDecide={decide}
          onOpen={open}
        />
        <section className="audit-panel" aria-label="Decision history">
          <h2>Decision history</h2>
          {!audit.length && <p className="hint">Select a reviewed conversation to inspect its decision record.</p>}
          {audit.map(event => <article className="audit-event" key={event.approval_id}>
            <strong>{event.action.order_id} · {event.approved ? 'Approved' : 'Denied'}</strong>
            <span className="badge">{event.status.replace(/_/g, ' ')}</span>
            <p>{event.reviewer_id} · {new Date(event.created_at).toLocaleString()}</p>
            {event.note && <p>{event.note}</p>}
          </article>)}
        </section></div>}
      </main>
    </div>
  )
}
