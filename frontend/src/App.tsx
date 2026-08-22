import { useCallback, useEffect, useState } from 'react'
import Conversation from './Conversation'
import Queue from './Queue'
import StatusBar from './StatusBar'
import ThreadList from './ThreadList'
import { api, type Health, type QueueItem, type ThreadSummary, type ThreadView } from './api'

/**
 * Three panes, not a router: a reviewer wants the queue, the conversation it
 * refers to, and the list of everything else on screen together.
 *
 * The queue is polled rather than pushed. A refund pauses INSIDE a graph run,
 * so there is nothing to subscribe to without adding a broker - and Phase 5
 * (Celery/Redis) stays deliberately deferred until a genuinely slow tool call
 * exists. Polling is the honest solution at this scale.
 */
const POLL_MS = 2000

const blankThread = (thread_id: string): ThreadView => ({
  thread_id,
  verified: false,
  customer_id: null,
  customer_name: null,
  messages: [],
  awaiting_approval: null,
})

export default function App() {
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
        api.escalations(),
        api.threads(),
        api.health(),
      ])
      setQueue(nextQueue)
      setThreads(nextThreads)
      setHealth(nextHealth)
    } catch (caught) {
      setError(String(caught))
    }
  }, [])

  const startThread = useCallback(async () => {
    const { thread_id } = await api.createThread()
    setThread(blankThread(thread_id))
  }, [])

  useEffect(() => {
    startThread().catch((caught) => setError(String(caught)))
  }, [startThread])

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
      setError(String(caught))
    } finally {
      setBusy(false)
    }
  }

  async function decide(threadId: string, approved: boolean, note: string) {
    setDeciding(threadId)
    setError(null)
    try {
      const updated = await api.decide(threadId, approved, note)
      if (threadId === thread?.thread_id) setThread(updated)
    } catch (caught) {
      // A 409 means another reviewer got there first. Re-reading the queue is
      // the correct response, so say so plainly rather than shouting.
      setError(
        String(caught).includes('409')
          ? 'That decision was already made by someone else. Queue refreshed.'
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
      </header>

      {error && (
        <p className="error" role="status">
          {error}
          <button className="ghost" onClick={() => setError(null)}>Dismiss</button>
        </p>
      )}

      <main className="layout">
        <ThreadList
          threads={threads}
          activeThread={thread?.thread_id ?? null}
          onOpen={open}
          onNew={() => startThread().catch((caught) => setError(String(caught)))}
        />
        <Conversation thread={thread} busy={busy} onSend={send} />
        <Queue
          items={queue}
          activeThread={thread?.thread_id ?? null}
          busy={deciding}
          onDecide={decide}
          onOpen={open}
        />
      </main>
    </div>
  )
}
