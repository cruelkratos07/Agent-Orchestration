import type { Health } from './api'

/**
 * Makes the infrastructure visible.
 *
 * Which provider is answering, which database is behind it, and whether the
 * cache is shared are the three things that change behaviour and are otherwise
 * invisible. Cheap to render, and it means "is this actually using Postgres?"
 * never needs a terminal to answer.
 */
export default function StatusBar({ health }: { health: Health | null }) {
  if (!health) return <span className="chips" />

  const live = health.mode === 'LIVE_MODE'
  const model = health.model.replace(/ \(cache:.*$/, '')
  const cache = health.cache.backend
  const rate = health.cache.hit_rate

  return (
    <span className="chips">
      <span className={`chip ${live ? 'chip--live' : ''}`}>
        <i className="dot" />
        {live ? model : 'deterministic'}
      </span>
      <span className="chip">{health.database}</span>
      <span className="chip" title={rate == null ? undefined : `hit rate ${rate}`}>
        cache: {cache}
        {rate != null && <em> {Math.round(rate * 100)}%</em>}
      </span>
    </span>
  )
}
