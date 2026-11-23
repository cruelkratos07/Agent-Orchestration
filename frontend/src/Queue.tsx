import { useState } from 'react'
import type { QueueItem } from './api'

// "Rs" not the glyph, to match the customer-facing strings the backend writes
// ("Refund of Rs 12999..."). Mixing the two in one screen reads as careless.
const rupees = (amount: number) =>
  `Rs ${amount.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`

interface Props {
  items: QueueItem[]
  activeThread: string | null
  busy: string | null
  onDecide: (threadId: string, approved: boolean, note: string) => void
  onOpen: (threadId: string) => void
}

export default function Queue({ items, activeThread, busy, onDecide, onOpen }: Props) {
  // Keyed by thread so switching between queue items never loses typing.
  const [notes, setNotes] = useState<Record<string, string>>({})

  return (
    <section className="panel">
      <header className="panel__head">
        <h2>Escalation queue</h2>
        <span className={`badge ${items.length ? 'badge--warn' : ''}`}>
          {items.length} awaiting review
        </span>
      </header>

      {!items.length && (
        <p className="hint hint--pad">
          No refunds awaiting review.
        </p>
      )}

      <ul className="queue">
        {items.map((item) => {
          const pending = busy === item.thread_id
          return (
            <li
              key={item.thread_id}
              className={`card${item.thread_id === activeThread ? ' is-active' : ''}`}
            >
              <div className="card__top">
                <strong>{item.order_id}</strong>
                <span className="amount">{rupees(item.amount)}</span>
              </div>
              <p className="card__item">{item.item}</p>
              <p className="hint">{item.customer_name} · #{item.customer_id}</p>

              <p className="card__why">{item.reason}</p>
              {/* Identical to `reason` when no model is configured, so showing
                  both would just be noise. */}
              {item.risk_summary && item.risk_summary !== item.reason && (
                <p className="card__brief">{item.risk_summary}</p>
              )}

              <blockquote>{item.customer_request}</blockquote>

              <input
                placeholder="Reviewer note (optional)"
                aria-label={`Review note for ${item.order_id}`}
                value={notes[item.thread_id] ?? ''}
                disabled={pending}
                onChange={(event) =>
                  setNotes({ ...notes, [item.thread_id]: event.target.value })
                }
              />
              <div className="card__actions">
                <button
                  className="approve"
                  disabled={busy !== null}
                  onClick={() => onDecide(item.thread_id, true, notes[item.thread_id] ?? '')}
                >
                  {pending ? '…' : 'Approve'}
                </button>
                <button
                  className="deny"
                  disabled={busy !== null}
                  onClick={() => onDecide(item.thread_id, false, notes[item.thread_id] ?? '')}
                >
                  Deny
                </button>
                <button className="ghost" onClick={() => onOpen(item.thread_id)}>
                  Open
                </button>
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
