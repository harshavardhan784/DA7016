import { useEffect, useState } from 'react'
import { api } from '../api'
import { useStore } from '../state/store'
import { Assistant } from '../review/Assistant'
import { OutputPane } from '../review/OutputPane'
import { SourcePane } from '../review/SourcePane'
import type { JobStatus } from '../types'

const Icon = ({ d, s = 16 }: { d: string; s?: number }) => (
  <svg aria-hidden="true" width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
)

export function Review({ jobId, email, onBack, onSignOut }: { jobId: string; email: string; onBack: () => void; onSignOut: () => void }) {
  const doc = useStore((s) => s.doc)
  const busy = useStore((s) => s.busy)
  const notice = useStore((s) => s.notice)
  const syncing = useStore((s) => s.syncing)
  const open = useStore((s) => s.assistantOpen)
  const scope = useStore((s) => s.scope)
  const [job, setJob] = useState<JobStatus | null>(null)
  const [menu, setMenu] = useState(false)
  useEffect(() => { void useStore.getState().open(jobId); api.job(jobId).then(setJob).catch(() => {}) }, [jobId])

  // Delete / Backspace removes the selected blocks, Escape clears the selection, but never while you are typing somewhere.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const st = useStore.getState()
      const typing = (e.target as HTMLElement | null)?.closest('.ProseMirror, textarea, input, select, [contenteditable="true"]')
      if (typing || document.querySelector('[role="dialog"]') || document.body.classList.contains('is-dragging')) return
      if (e.key === 'Escape' && st.selected.length) st.select([])
      else if ((e.key === 'Delete' || e.key === 'Backspace') && st.selected.length) { e.preventDefault(); void st.deleteBlocks(st.selected) }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])
  useEffect(() => {
    if (!menu) return
    const h = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest('.menu, .avatar')) setMenu(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [menu])

  if (!doc) return <div className="center">{busy ?? 'Opening…'}{notice && <div className="toast error">{notice.text}</div>}</div>
  const { setAssistantOpen, setScope } = useStore.getState()
  return (
    <div className="app">
      <header className="topbar">
        <button className="gb" onClick={onBack} aria-label="Back to documents" title="Back to documents"><Icon d="M19 12H5M12 19l-7-7 7-7" /></button>
        <span className="title" title={job?.filename}>{job?.filename ?? ''}</span>
        <span className="pill" title="How this document was read">{doc.route}</span>
        <div className="seg viewseg" role="group" aria-label="View">
          <button className={scope === 'page' ? 'on' : ''} onClick={() => setScope('page')}>This page</button>
          <button className={scope === 'doc' ? 'on' : ''} onClick={() => setScope('doc')}>Whole document</button>
        </div>
        <span className="sp" />
        <span className={`status${syncing ? ' saving' : ''}`} role="status" title={doc.dirty ? 'The Word file is rebuilt when you export' : 'The Word file is up to date'}><i />{syncing ? 'Saving…' : 'Saved'}</span>
        <a className="btn primary" href={api.downloadUrl(jobId)}>Export DOCX</a>
        <button className={`gb${open ? ' on' : ''}`} aria-pressed={open} aria-label="Assistant" title={open ? 'Close the assistant' : 'Open the assistant'} onClick={() => setAssistantOpen(!open)}>
          <Icon d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" s={15} />Assistant
        </button>
        <button className="avatar" aria-haspopup="menu" aria-expanded={menu} aria-label="Account" onClick={() => setMenu(!menu)}>{email.slice(0, 1).toUpperCase()}</button>
        {menu && <div className="menu" role="menu"><div className="who">{email}</div><button role="menuitem" onClick={onSignOut}>Sign out</button></div>}
      </header>
      <div className="body">
        <SourcePane jobId={jobId} />
        <OutputPane jobId={jobId} />
        <aside className={`assist${open ? '' : ' closed'}`} aria-label="Assistant panel">{open && <Assistant jobId={jobId} />}</aside>
        {open && (
          <div className="assist-edge">
            <button aria-label="Close assistant from the edge" title="Close assistant" onClick={() => setAssistantOpen(false)}><Icon d="M9 18l6-6-6-6" /></button>
          </div>
        )}
      </div>
      {busy && <div className="busy" role="status">{busy}</div>}
      {notice && (
        <div className={`toast ${notice.kind}`} role="status">
          {notice.text}
          {notice.action && <button onClick={() => { notice.action!.run(); useStore.setState({ notice: null }) }}>{notice.action.label}</button>}
        </div>
      )}
    </div>
  )
}
