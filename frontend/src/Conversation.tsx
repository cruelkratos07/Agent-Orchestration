import { useEffect, useRef, useState } from 'react'
import type { Message, ThreadView } from './api'

/**
 * Which agent produced a line is the interesting part of this project, so the
 * feed labels every message by author rather than collapsing them into one
 * "assistant" voice.
 */
const LABELS: Record<string, string> = {
  customer: 'Customer',
  verification: 'Verification gate',
  order_agent: 'Order lookup',
  policy_agent: 'Policy',
  refund_agent: 'Refund',
}

/**
 * Starters that each exercise something different: the verify gate, a fuzzy
 * order reference regex cannot handle, and a two-specialist turn that ends in
 * a human approval. Clickable, because typing an example from a README is
 * friction for no reason.
 */
const STARTERS = [
  { label: 'Verify as Rohit', text: 'rohit.v@example.com' },
  { label: 'Fuzzy reference', text: 'where is my latest order?' },
  { label: 'Damage + refund', text: 'my camera ORD1003 arrived damaged, I want a refund' },
  { label: 'Policy question', text: "what's the policy on a damaged item?" },
]

function Bubble({ message }: { message: Message }) {
  return (
    <li className={`msg msg--${message.role}`}>
      <span className={`who who--${message.author}`}>
        {LABELS[message.author] ?? message.author}
      </span>
      <p>{message.content}</p>
    </li>
  )
}

interface Props {
  thread: ThreadView | null
  busy: boolean
  onSend: (text: string) => void
}

export default function Conversation({ thread, busy, onSend }: Props) {
  const [draft, setDraft] = useState('')
  const feed = useRef<HTMLUListElement>(null)

  // Keep the newest reply in view. A turn can add several messages at once, so
  // this matters more here than in a one-reply-per-turn chat.
  useEffect(() => {
    feed.current?.scrollTo({ top: feed.current.scrollHeight, behavior: 'smooth' })
  }, [thread?.messages.length, busy])

  function submit(event: React.FormEvent) {
    event.preventDefault()
    const text = draft.trim()
    if (!text || busy) return
    onSend(text)
    setDraft('')
  }

  const empty = !thread?.messages.length

  return (
    <section className="panel">
      <header className="panel__head">
        <h2>Conversation</h2>
        {thread && (
          <span className={`badge ${thread.verified ? 'badge--ok' : ''}`}>
            {thread.verified
              ? `${thread.customer_name} · #${thread.customer_id}`
              : 'not verified'}
          </span>
        )}
      </header>

      <ul className="feed" ref={feed}>
        {empty && (
          <li className="starter">
            <p>
              Identify yourself first — a customer ID, email, or phone. The
              verification gate sits in front of every specialist, so nothing
              about an order is reachable until it passes.
            </p>
            <div className="starter__row">
              {STARTERS.map((starter) => (
                <button key={starter.text} onClick={() => onSend(starter.text)}>
                  {starter.label}
                </button>
              ))}
            </div>
          </li>
        )}

        {thread?.messages.map((message, index) => <Bubble key={index} message={message} />)}

        {busy && (
          <li className="msg msg--agent">
            <span className="who">Working</span>
            <p className="thinking"><i /><i /><i /></p>
          </li>
        )}
      </ul>

      {thread?.awaiting_approval && (
        <p className="banner">
          Paused mid-turn on <strong>{thread.awaiting_approval.order_id}</strong>.
          The graph is holding at the refund step — approve or deny it in the queue.
        </p>
      )}

      <form className="composer" onSubmit={submit}>
        <input
          value={draft}
          placeholder={busy ? 'Working…' : 'Message the support agent'}
          onChange={(event) => setDraft(event.target.value)}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !draft.trim()}>Send</button>
      </form>
    </section>
  )
}
