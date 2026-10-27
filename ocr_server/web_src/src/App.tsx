import { useCallback, useEffect, useState } from 'react'
import { api, setUnauthorizedHandler } from './api'
import type { AppConfig } from './types'
import { Jobs } from './screens/Jobs'
import { Login } from './screens/Login'
import { Review } from './screens/Review'

const jobFromHash = () => /^#\/job\/(\w+)$/.exec(window.location.hash)?.[1] ?? null

export default function App() {
  const [cfg, setCfg] = useState<AppConfig | null>(null)
  const [email, setEmail] = useState<string | null>(null)
  const [job, setJob] = useState<string | null>(jobFromHash())
  const [error, setError] = useState('')

  useEffect(() => {
    setUnauthorizedHandler(() => setEmail(null))
    api.config().then(async (c) => {
      setCfg(c)
      if (!c.auth) { setEmail('local'); return }
      try { setEmail((await api.me()).email) } catch { setEmail(null) }
    }).catch((e: Error) => setError(e.message))
  }, [])
  useEffect(() => { const h = () => setJob(jobFromHash()); window.addEventListener('hashchange', h); return () => window.removeEventListener('hashchange', h) }, [])

  const signedIn = useCallback((e: string) => setEmail(e), [])
  const signOut = useCallback(() => { void api.signOut().finally(() => { setEmail(null); window.location.hash = '' }) }, [])

  if (error) return <main className="center"><p className="err" role="alert">{error}</p></main>
  if (!cfg) return <main className="center">Loading…</main>
  if (!email) return <Login clientId={cfg.google_client_id} onSignedIn={signedIn} />
  if (job) return <Review jobId={job} email={email} onBack={() => { window.location.hash = '' }} onSignOut={signOut} />
  return <Jobs cfg={cfg} email={email} onOpen={(id) => { window.location.hash = `#/job/${id}` }} onSignOut={signOut} />
}
