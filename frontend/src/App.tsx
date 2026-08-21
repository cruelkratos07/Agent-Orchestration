import { useCallback, useEffect, useState } from 'react'
import Conversation from './Conversation'
import Queue from './Queue'
import { api, type QueueItem, type ThreadView } from './api'

/**
 * The console is deliberately two panes, not a router: a reviewer wants the
 * queue and the conversation it refers to on screen at the same time.
 *
 * The queue is polled rather than pushed. A refund pauses inside a graph run,
 * so there is nothing to subscribe to without adding a broker - and Phase 5
 * (Celery/Redis) is still deliberately deferred. Polling is the honest
 * solution at this scale.
 */
const POLL_MS = 2000

export default function App() {
  const [mode, setMode] = useState<string>('')
  const [model, setModel] = useState<string>('')
  const [thread, setThread] = useState<ThreadView | null>(null)
  const [queue, setQueue] = useState<QueueItem[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refreshQueue = useCallback(async () => {
    try {
      setQueue(await api.escalations())
    } catch (caught) {
      setError(String(caught))
    }
  }, [])

  // One thread on first load, so the composer is usable immediately.
  useEffect(() => {
    ;(async () => {
      try {
        const health = await api.health()
        setMode(health.mode)
        setModel(health.model)
        const { thread_id } = await api.createThread()
        setThread({
          thread_id,
          verified: false,
          customer_id: null,
          customer_name: null,
          messages: [],
          awaiting_approval: null,
        })
      } catch (caught) {
        setError(String(caught))
      }
    })()
  }, [])

  useEffect(() => {
    refreshQueue()
    const timer = setInterval(refreshQueue, POLL_MS)
    return () => clearInterval(timer)
  }, [refreshQueue])

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
      await refreshQueue()
    } catch (caught) {
      setError(String(caught))
    } finally {
      setBusy(false)
    }
  }

  async function decide(threadId: string, approved: boolean, note: string) {
    setError(null)
    try {
      const updated = await api.decide(threadId, approved, note)
      // Only replace the open conversation if the decision was about it.
      if (threadId === thread?.thread_id) setThread(updated)
    } catch (caught) {
      // A 409 means someone else got there first. Re-reading the queue is the
      // correct response, not an alarming error.
      setError(String(caught))
    } finally {
      await refreshQueue()
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
        <h1>Order Support Console</h1>
        <div className="topbar__meta">
          {mode && (
            <span className="pill" title={model}>
              {mode.replace('_', ' ')}
              {model && model !== 'no model (deterministic mode)' && ` · ${model}`}
            </span>
          )}
          <button
            className="link"
            onClick={async () => {
              const { thread_id } = await api.createThread()
              setThread({
                thread_id,
                verified: false,
                customer_id: null,
                customer_name: null,
                messages: [],
                awaiting_approval: null,
              })
            }}
          >
            New conversation
          </button>
        </div>
      </header>

      {error && <p className="error">{error}</p>}

      <main className="split">
        <Conversation thread={thread} busy={busy} onSend={send} />
        <Queue
          items={queue}
          activeThread={thread?.thread_id ?? null}
          onDecide={decide}
          onOpen={open}
        />
      </main>
    </div>
  )
}
