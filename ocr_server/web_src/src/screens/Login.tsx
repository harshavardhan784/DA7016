import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

declare global { interface Window { google?: { accounts: { id: { initialize: (o: unknown) => void; renderButton: (el: HTMLElement, o: unknown) => void } } } } }

function loadGoogle(): Promise<void> {
  if (window.google?.accounts) return Promise.resolve()
  return new Promise((ok, fail) => {
    const s = document.createElement('script')
    s.src = 'https://accounts.google.com/gsi/client'; s.async = true
    s.onload = () => ok(); s.onerror = () => fail(new Error('Could not load Google sign-in. Check the internet connection.'))
    document.head.appendChild(s)
  })
}

export function Login({ clientId, onSignedIn }: { clientId: string; onSignedIn: (email: string) => void }) {
  const box = useRef<HTMLDivElement>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    loadGoogle().then(() => {
      if (!alive || !box.current) return
      window.google!.accounts.id.initialize({
        client_id: clientId,
        callback: (r: { credential: string }) => api.signIn(r.credential).then((u) => onSignedIn(u.email)).catch((e: Error) => setError(e.message)),
      })
      window.google!.accounts.id.renderButton(box.current, { theme: 'outline', size: 'large', text: 'signin_with', width: 280 })
    }).catch((e: Error) => setError(e.message))
    return () => { alive = false }
  }, [clientId, onSignedIn])
  return (
    <main className="center">
      <div className="card" style={{ width: 380, textAlign: 'center', display: 'flex', flexDirection: 'column', gap: 16, alignItems: 'center' }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Infinite OCR</h1>
        <p className="muted" style={{ margin: 0 }}>One continuous, structured Word document from any PDF. Sign in with your Google account.</p>
        <div ref={box} />
        {error && <p className="err" role="alert" style={{ margin: 0 }}>{error}</p>}
      </div>
    </main>
  )
}
