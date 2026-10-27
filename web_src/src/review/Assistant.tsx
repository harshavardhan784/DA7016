import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useStore } from '../state/store'
import type { BlockInfo, Proposal, ProposalBlock } from '../types'
import { MarkupView } from './BlockView'
import { label } from './util'

const short = (m: string | null) => (m ? m.split('/').pop()!.replace(/-instruct$/, '') : '')

/** The part of a page that a block covers, cut out with CSS. */
function Crop({ url, aspect, bbox }: { url: string; aspect: number; bbox: number[] }) {
  const [x0, y0, x1, y1] = bbox
  const bw = Math.max(0.001, x1 - x0), bh = Math.max(0.001, y1 - y0)
  return (
    <div className="crop" style={{ aspectRatio: String((bw * aspect) / bh) }}>
      <img src={url} alt="Source crop" style={{ width: `${100 / bw}%`, left: `${(-100 * x0) / bw}%`, top: `${(-100 * y0) / bh}%` }} />
    </div>
  )
}

function Compare({ jobId, p, pb, onClose }: { jobId: string; p: Proposal; pb: ProposalBlock; onClose: () => void }) {
  const doc = useStore((s) => s.doc)!
  const b: BlockInfo | undefined = doc.blocks[pb.ref ?? pb.id]
  const page = pb.page ?? b?.page ?? p.new_block?.page ?? 1
  const bbox = pb.kind ? (pb.bbox ?? null) : (b?.bbox_norm ?? p.new_block?.bbox_norm ?? null)
  const aspect = doc.pages.find((x) => x.n === page)?.aspect ?? 0.7
  useEffect(() => { const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose(); window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k) }, [onClose])
  return (
    <div className="modal" role="dialog" aria-modal="true" aria-label={`Compare ${pb.title ?? label(pb.id)}`} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="card">
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
          <b>{pb.title ?? label(pb.id)}</b><span className="sp" /><button className="btn sm" autoFocus onClick={onClose}>Close</button>
        </div>
        {pb.warn && <p className="err" style={{ marginTop: 0 }}>{pb.warn}</p>}
        {pb.plain_text_fallback && <p className="err" style={{ marginTop: 0 }}>The model's answer did not pass the text check, so this is the plain text without formatting.</p>}
        <div className="cols3">
          <div><h4>Source</h4><div className="box">{bbox ? <Crop url={api.pageUrl(jobId, page)} aspect={aspect} bbox={bbox} /> : <img src={api.pageUrl(jobId, page)} alt="Page" style={{ width: '100%' }} />}</div></div>
          <div><h4>Before</h4><div className="box"><MarkupView markup={pb.before} /></div></div>
          <div><h4>After</h4><div className="box"><MarkupView markup={pb.after} /></div></div>
        </div>
      </div>
    </div>
  )
}

function Cards({ jobId, p }: { jobId: string; p: Proposal }) {
  const { accept, reject } = useStore.getState()
  const busy = useStore((s) => s.busy)
  const [cmp, setCmp] = useState<ProposalBlock | null>(null)
  const pending = p.blocks.filter((b) => !b.resolved)
  const del = p.mode === 'delete'
  const allGone = del || (pending.length > 0 && pending.every((b) => b.kind === 'delete'))
  if (p.mode === 'ask') return null
  return (
    <>
      {p.skipped && p.skipped.length > 0 && (
        <ul className="skipped" aria-label="Skipped blocks">
          {p.skipped.map((k) => <li key={k.id}><b>{label(k.id)}</b> skipped: {k.reason}</li>)}
        </ul>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {p.blocks.map((b) => {
          const same = b.before.trim() === b.after.trim()
          const gone = del || b.kind === 'delete'
          return (
            <div key={b.id} className={`pcard${b.resolved ? ' done' : ''}`}>
              <div className="row"><b>{b.title ?? label(b.id)}</b>
                <span>{b.resolved ? b.resolved : b.label ? b.label : del ? 'to delete' : p.mode === 'newbox' ? 'new block' : same ? 'no change' : 'changed'}</span></div>
              {b.plain_text_fallback && <div className="warn">Failed the text check; plain text only.</div>}
              {b.warn && <div className="warn">{b.warn}</div>}
              <div className="acts">
                {!b.resolved && (gone
                  ? <button className="btn sm dark" disabled={!!busy} onClick={() => void accept(p.id, [b.id])}>Delete</button>
                  : <button className="btn sm primary" disabled={!!busy} onClick={() => void accept(p.id, [b.id])}>Accept</button>)}
                {!b.resolved && <button className="gb sm" disabled={!!busy} onClick={() => void reject(p.id, [b.id])}>{gone ? 'Keep' : 'Reject'}</button>}
                {!gone && <button className="gb sm" onClick={() => setCmp(b)}>Compare</button>}
              </div>
            </div>
          )
        })}
        {pending.length > 0 && (
          <div className="pfoot">
            {pending.length > 1 && <button className="btn sm dark" disabled={!!busy} onClick={() => void accept(p.id)}>{allGone ? `Delete all (${pending.length})` : `Accept all (${pending.length})`}</button>}
            {!del && <span>{short(p.model)}{p.usage.calls ? ` · $${p.usage.cost.toFixed(3)}` : ''}</span>}
          </div>
        )}
      </div>
      {cmp && <Compare jobId={jobId} p={p} pb={cmp} onClose={() => setCmp(null)} />}
    </>
  )
}

export function Assistant({ jobId }: { jobId: string }) {
  const doc = useStore((s) => s.doc)!
  const selected = useStore((s) => s.selected)
  const chat = useStore((s) => s.chat)
  const proposals = useStore((s) => s.proposals)
  const busy = useStore((s) => s.busy)
  const [text, setText] = useState('')
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [chat.length])
  const ids = doc.order.filter((i) => selected.includes(i))
  const send = () => { if (busy || !ids.length) return; const t = text.trim(); setText(''); void useStore.getState().ask(t) }

  return (
    <div className="assist-in">
      <div className="ahead">
        <b>Assistant</b><span className="sp" />
        <button className="gb" aria-label="Close assistant" title="Close assistant" onClick={() => useStore.getState().setAssistantOpen(false)}>
          <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18l6-6-6-6" /></svg>
        </button>
      </div>
      <div className="achips">
        <span>Acting on</span>
        {ids.length === 0 && <span style={{ color: 'var(--amber)' }}>nothing selected yet</span>}
        {ids.slice(0, 8).map((i) => <span key={i} className="chip">{label(i)}<button aria-label={`Remove ${label(i)}`} onClick={() => useStore.getState().select([i], 'toggle')}>×</button></span>)}
        {ids.length > 8 && <span>+{ids.length - 8} more</span>}
      </div>
      <div className="athread">
        {chat.length === 0 && <div className="muted" style={{ lineHeight: 1.5 }}>Select blocks on the page and ask for anything, in your own words: “merge these two, the sentence continues”, “add a table after this block”, “check this against the image, it is wrong, correct it”, “the brackets are missing”, “delete these”. I will tell you what I propose. Nothing changes until you accept.</div>}
        {chat.map((m) => (
          <div key={m.id} className="msg">
            <div className={`bubble${m.role === 'assistant' ? ' a' : ''}`}>{m.text}</div>
            {m.proposalId && proposals[m.proposalId] && <Cards jobId={jobId} p={proposals[m.proposalId]} />}
          </div>
        ))}
        <div ref={end} />
      </div>
      <div className="ainput">
        <div className="abox">
          <label className="sr" htmlFor="chat-input">Message the assistant</label>
          <textarea id="chat-input" rows={1} value={text} placeholder="Ask anything about the selected blocks…"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
          <button aria-label="Send" disabled={!!busy || ids.length === 0} onClick={send}>
            <svg aria-hidden="true" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
          </button>
        </div>
        <div className="ahint">Ctrl+click selects several blocks · Delete removes them</div>
      </div>
    </div>
  )
}
