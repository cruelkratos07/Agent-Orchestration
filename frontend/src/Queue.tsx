import { useState } from 'react'
import type { QueueItem } from './api'

const rupees = (amount: number) =>
  `Rs ${amount.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`

interface Props {
  items: QueueItem[]
  activeThread: string | null
  onDecide: (threadId: string, approved: boolean, note: string) => void
  onOpen: (threadId: string) => void
}

export default function Queue({ items, activeThread, onDecide, onOpen }: Props) {
  // One note per thread, so switching between queue items doesn't lose typing.
  const [notes, setNotes] = useState<Record<string, string>>({})

  return (
    <section className="panel">
      <header className="panel__head">
        <h2>Escalation queue</h2>
        <span className="muted">{items.length} awaiting review</span>
      </header>

      {!items.length && (
        <p className="empty">
          Nothing waiting. Refunds at or above Rs 5,000, and any damage claim,
          pause here for a human.
        </p>
      )}

      <ul className="queue">
        {items.map((item) => (
          <li
            key={item.thread_id}
            className={`card${item.thread_id === activeThread ? ' card--active' : ''}`}
          >
            <div className="card__head">
              <strong>{item.order_id}</strong>
              <span>{rupees(item.amount)}</span>
            </div>
            <p className="card__item">{item.item}</p>
            <p className="muted">
              {item.customer_name} (#{item.customer_id})
            </p>
            <p className="card__reason">{item.reason}</p>
            <blockquote>“{item.customer_request}”</blockquote>

            <input
              placeholder="Note for the audit trail (optional)"
              value={notes[item.thread_id] ?? ''}
              onChange={(event) =>
                setNotes({ ...notes, [item.thread_id]: event.target.value })
              }
            />
            <div className="card__actions">
              <button
                className="approve"
                onClick={() => onDecide(item.thread_id, true, notes[item.thread_id] ?? '')}
              >
                Approve
              </button>
              <button
                className="deny"
                onClick={() => onDecide(item.thread_id, false, notes[item.thread_id] ?? '')}
              >
                Deny
              </button>
              <button className="link" onClick={() => onOpen(item.thread_id)}>
                Open conversation
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
