import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { AppConfig, JobStatus } from '../types'

const WORDS: Record<JobStatus['state'], string> = { queued: 'Waiting in the queue', running: 'Reading the pages', finalizing: 'Building the document', done: 'Ready', error: 'Failed' }

export function Jobs({ cfg, email, onOpen, onSignOut }: { cfg: AppConfig; email: string; onOpen: (id: string) => void; onSignOut: () => void }) {
  const [jobs, setJobs] = useState<JobStatus[]>([])
  const [msg, setMsg] = useState('')
  const [over, setOver] = useState(false)
  const [sending, setSending] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    const list = await api.jobs()
    const live = list.filter((j) => !['done', 'error'].includes(j.state))
    const detail = await Promise.all(live.map((j) => api.job(j.job_id).catch(() => j)))
    setJobs(list.map((j) => detail.find((d) => d.job_id === j.job_id) ?? j))
  }, [])
  useEffect(() => { void refresh().catch((e: Error) => setMsg(e.message)) }, [refresh])
  const polling = jobs.some((j) => !['done', 'error'].includes(j.state))
  useEffect(() => { if (!polling) return; const t = setInterval(() => void refresh().catch(() => {}), 3000); return () => clearInterval(t) }, [polling, refresh])

  const send = async (f: File | undefined) => {
    if (!f) return
    setMsg(''); setSending(true)
    try { await api.upload(f); await refresh() } catch (e) { setMsg((e as Error).message) } finally { setSending(false) }
  }
  return (
    <main className="jobs">
      <header style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Infinite OCR</h1><span className="sp" />
        <span className="muted">{email}</span><button className="btn sm" onClick={onSignOut}>Sign out</button>
      </header>
      <div className={`drop${over ? ' over' : ''}`} onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); void send(e.dataTransfer.files[0]) }}>
        <p style={{ margin: '0 0 12px' }}>Drop a PDF here, or choose one. Up to {cfg.max_mb} MB.</p>
        <input ref={input} type="file" accept="application/pdf" className="sr" aria-label="Choose a PDF" onChange={(e) => { void send(e.target.files?.[0]); e.target.value = '' }} />
        <button className="btn primary" disabled={sending} onClick={() => input.current?.click()}>{sending ? 'Uploading…' : 'Choose a PDF'}</button>
        {msg && <p className="err" role="alert" style={{ marginBottom: 0 }}>{msg}</p>}
      </div>
      {jobs.length === 0 && <p className="muted" style={{ textAlign: 'center' }}>No documents yet.</p>}
      {jobs.map((j) => (
        <div key={j.job_id} className="card" style={{ padding: 0 }}>
          <div className="job">
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{j.filename}</div>
              <div className="muted">{j.pages} pages · {new Date(j.created * 1000).toLocaleString()} · <span className={j.state === 'error' ? 'err' : ''}>{WORDS[j.state]}{j.state === 'queued' && j.queue_size ? ` (${j.queue_size} ahead)` : ''}</span></div>
              {j.state === 'error' && <div className="err" style={{ fontSize: 12 }}>{j.error}</div>}
            </div>
            {j.state === 'done' && <button className="btn primary" onClick={() => onOpen(j.job_id)}>Open</button>}
          </div>
          {j.state === 'running' && j.log_tail && <pre style={{ margin: '0 16px 14px' }}>{j.log_tail}</pre>}
        </div>
      ))}
    </main>
  )
}
