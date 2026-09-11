import type { Health } from './api'

/** Surfaces the active model, database, and cache for operators. */
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
