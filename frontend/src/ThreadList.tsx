import type { ThreadSummary } from './api'

interface Props {
  threads: ThreadSummary[]
  activeThread: string | null
  onOpen: (threadId: string) => void
  onNew: () => void
}

export default function ThreadList({ threads, activeThread, onOpen, onNew }: Props) {
  return (
    <aside className="rail">
      <div className="rail__head">
        <h2>Conversations</h2>
        <button className="ghost" onClick={onNew}>New</button>
      </div>

      {!threads.length && <p className="hint">No conversations yet.</p>}

      <ul className="rail__list">
        {threads.map((thread) => (
          <li key={thread.thread_id}>
            <button
              className={`rail__item${thread.thread_id === activeThread ? ' is-active' : ''}`}
              onClick={() => onOpen(thread.thread_id)}
            >
              <span className="rail__who">
                {thread.customer_name ?? 'Unverified'}
                {/* A conversation waiting on a reviewer is the one thing worth
                    interrupting the list's uniformity for. */}
                {thread.awaiting_approval && <i className="pip" title="awaiting approval" />}
              </span>
              <span className="rail__last">{thread.last_message ?? 'No messages'}</span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  )
}
