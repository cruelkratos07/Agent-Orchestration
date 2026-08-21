import { useState } from 'react'
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

function Bubble({ message }: { message: Message }) {
  return (
    <li className={`msg msg--${message.role}`}>
      <span className={`tag tag--${message.author}`}>
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

  function submit(event: React.FormEvent) {
    event.preventDefault()
    const text = draft.trim()
    if (!text || busy) return
    onSend(text)
    setDraft('')
  }

  return (
    <section className="panel">
      <header className="panel__head">
        <h2>Conversation</h2>
        {thread && (
          <span className="muted">
            {thread.verified
              ? `verified — ${thread.customer_name} (#${thread.customer_id})`
              : 'not verified'}
          </span>
        )}
      </header>

      <ul className="feed">
        {thread?.messages.map((message, index) => (
          <Bubble key={index} message={message} />
        ))}
        {!thread?.messages.length && (
          <li className="empty">
            Start by identifying yourself — a customer ID, email, or phone.
            Try <code>ananya.s@example.com</code>, then ask about ORD1002.
          </li>
        )}
      </ul>

      {thread?.awaiting_approval && (
        <p className="banner">
          Waiting on a reviewer for {thread.awaiting_approval.order_id}. The turn is
          paused mid-flight; approve or deny it in the queue.
        </p>
      )}

      <form className="composer" onSubmit={submit}>
        <input
          value={draft}
          placeholder={busy ? 'Working…' : 'Message the support agent'}
          onChange={(event) => setDraft(event.target.value)}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          Send
        </button>
      </form>
    </section>
  )
}
