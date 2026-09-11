import { useEffect, useState } from 'react'
import { api, setAccessToken, type Session } from './api'
import App from './App'

export default function Access() {
  const [session, setSession] = useState<Session | null>(null)
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    api.authConfig().then(async ({ demo }) => {
      if (demo) setSession(await api.me())
    }).catch(() => setError('The support service is unavailable. Please try again.'))
      .finally(() => setLoading(false))
  }, [])
  async function signIn(event: React.FormEvent) {
    event.preventDefault()
    setLoading(true)
    setError('')
    setAccessToken(token.trim())
    try { setSession(await api.me()); setToken('') }
    catch { setAccessToken(''); setError('Access could not be verified. Check your token or contact your administrator.') }
    finally { setLoading(false) }
  }
  if (session) return <App session={session} onSignOut={() => {
    setAccessToken(''); setSession(null)
  }} />
  return <main className="access-page">
    <form className="access-card" onSubmit={signIn}>
      <span className="eyebrow">ORDER SUPPORT</span>
      <h1>Sign in</h1>
      <p>Use your assigned token to open the customer or reviewer workspace.</p>
      <label htmlFor="access-token">Your access token</label>
      <input id="access-token" type="password" autoComplete="off" value={token}
        onChange={event => setToken(event.target.value)} required />
      <button disabled={loading || !token.trim()}>{loading ? 'Connecting…' : 'Open workspace'}</button>
      {error && <p role="alert" className="access-error">{error}</p>}
      <p className="hint">The token stays in memory and is cleared when this page reloads.</p>
    </form>
  </main>
}
