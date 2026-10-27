import { useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { useStore } from '../state/store'
import type { BlockInfo, Page } from '../types'
import { useScrollSync } from './hooks'
import { label, leaveField, shortId } from './util'

type Rect = { page: number; x0: number; y0: number; x1: number; y1: number }
const norm = (r: Rect) => ({ x0: Math.min(r.x0, r.x1), y0: Math.min(r.y0, r.y1), x1: Math.max(r.x0, r.x1), y1: Math.max(r.y0, r.y1) })

/** The notebook's own overlap measure: the share of the smaller of the two boxes that the other one covers. */
function covered(b: number[], r: ReturnType<typeof norm>): number {
  const w = Math.min(b[2], r.x1) - Math.max(b[0], r.x0), h = Math.min(b[3], r.y1) - Math.max(b[1], r.y0)
  const small = Math.min((b[2] - b[0]) * (b[3] - b[1]), (r.x1 - r.x0) * (r.y1 - r.y0))
  return w > 0 && h > 0 && small > 0 ? (w * h) / small : 0
}
const MIN_COVER = 0.5      // a drag picks a block when it covers at least half of the smaller of the two
const DRAG_START_PX = 5    // moving less than this is a click, which toggles the block under it

function Sheet({ jobId, page, blocks }: { jobId: string; page: Page; blocks: BlockInfo[] }) {
  const selected = useStore((s) => s.selected)
  const tool = useStore((s) => s.tool)
  const [drag, setDrag] = useState<Rect | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const from = useRef<{ x: number; y: number; px: number; py: number } | null>(null)
  const placed = blocks.filter((b) => b.bbox_norm)
  const pick = (r: Rect) => { const n = norm(r); return placed.filter((b) => covered(b.bbox_norm!, n) >= MIN_COVER).map((b) => b.id) }
  const hot = drag ? new Set(tool === 'newbox' ? [] : pick(drag)) : null          // what a release would select, shown while dragging
  const forced = tool !== 'tap'                                                  // Drag box / New box always draw, which is what touch screens need

  const at = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect()
    return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) }
  }
  const down = (e: React.PointerEvent) => {
    if (e.button !== 0 || (!forced && e.pointerType === 'touch')) return      // on touch, a swipe scrolls; use the Drag box tool to select
    const p = at(e)
    from.current = { x: p.x, y: p.y, px: e.clientX, py: e.clientY }
    if (forced) { ref.current!.setPointerCapture(e.pointerId); setDrag({ page: page.n, x0: p.x, y0: p.y, x1: p.x, y1: p.y }) }
  }
  const move = (e: React.PointerEvent) => {
    const f = from.current
    if (!f) return
    const p = at(e)
    if (drag) { setDrag({ ...drag, x1: p.x, y1: p.y }); return }
    if (Math.hypot(e.clientX - f.px, e.clientY - f.py) < DRAG_START_PX) return
    ref.current!.setPointerCapture(e.pointerId)                                // from here on it is a drag, not a click
    setDrag({ page: page.n, x0: f.x, y0: f.y, x1: p.x, y1: p.y })
  }
  const up = (e: React.PointerEvent) => {
    const had = drag
    from.current = null
    setDrag(null)
    if (!had) return
    const r = norm(had)
    if (r.x1 - r.x0 < 0.01 || r.y1 - r.y0 < 0.01) return                      // too small to mean anything
    const st = useStore.getState()
    if (st.tool === 'newbox') { void st.drawBox(page.n, [r.x0, r.y0, r.x1, r.y1]); return }
    leaveField()
    st.select(pick(had), e.shiftKey || e.ctrlKey || e.metaKey ? 'add' : 'replace')
  }

  const loose = blocks.filter((b) => !b.bbox_norm)
  return (
    <>
      <div ref={ref} className={`sheet${forced ? ' tool' : ''}`} style={{ aspectRatio: String(page.aspect) }} data-page={page.n}
        onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => { from.current = null; setDrag(null) }}>
        <img src={api.pageUrl(jobId, page.n)} alt={`Page ${page.n}`} draggable={false} />
        {placed.map((b) => {
          const [x0, y0, x1, y1] = b.bbox_norm!
          const cls = hot?.has(b.id) ? 'hot' : selected.includes(b.id) ? 'sel' : b.is_picture ? 'pic' : (!b.markup.trim() || b.absorbed) ? 'ghost' : ''
          return (
            <button key={b.id} data-box={b.id} className={`box ${cls}`} aria-pressed={selected.includes(b.id)} aria-label={`Block ${label(b.id)}`} tabIndex={forced ? -1 : 0}
              title={`${label(b.id)}${b.type ? ` · ${b.type}` : ''}${b.absorbed ? ' · joined into another block' : ''}`}
              style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }}
              onClick={(e) => { if (!forced) useStore.getState().select([b.id], e.shiftKey ? 'add' : 'toggle') }}>
              <em>{shortId(b.id)}</em>
            </button>
          )
        })}
        {drag && (() => { const r = norm(drag); return <div className={`marquee${tool === 'newbox' ? ' new' : ''}`} style={{ left: `${r.x0 * 100}%`, top: `${r.y0 * 100}%`, width: `${(r.x1 - r.x0) * 100}%`, height: `${(r.y1 - r.y0) * 100}%` }} /> })()}
      </div>
      {loose.length > 0 && (
        <div className="tray" aria-label={`Blocks without a box on page ${page.n}`}>
          <span>No box:</span>
          {loose.map((b) => <button key={b.id} className={selected.includes(b.id) ? 'on' : ''} onClick={() => useStore.getState().select([b.id], 'toggle')}>{shortId(b.id)}</button>)}
        </div>
      )}
    </>
  )
}

export function SourcePane({ jobId }: { jobId: string }) {
  const doc = useStore((s) => s.doc)!
  const scope = useStore((s) => s.scope)
  const page = useStore((s) => s.page)
  const tool = useStore((s) => s.tool)
  const anchor = useStore((s) => s.anchor)
  const ref = useRef<HTMLDivElement>(null)
  useScrollSync(ref, 'source', 'data-box')

  const byPage = useMemo(() => {
    const m = new Map<number, BlockInfo[]>()
    for (const id of doc.order) { const b = doc.blocks[id]; (m.get(b.page) ?? m.set(b.page, []).get(b.page)!).push(b) }
    return m
  }, [doc])
  const pages = scope === 'page' ? doc.pages.filter((p) => p.n === page) : doc.pages
  const { goToPage, setTool } = useStore.getState()
  const current = scope === 'page' ? page : doc.blocks[anchor?.id ?? doc.order[0]]?.page ?? 1
  const chev = (d: string) => <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>

  return (
    <section className="source" aria-label="Source" data-anchor={anchor?.id ?? doc.order[0]}>
      <div className="shead">
        <button className="gb" aria-label="Previous page" disabled={current <= 1} onClick={() => goToPage(current - 1)}>{chev('M15 18l-6-6 6-6')}</button>
        <select aria-label="Go to page" value={current} onChange={(e) => goToPage(Number(e.target.value))}>
          {doc.pages.map((p) => <option key={p.n} value={p.n}>Page {p.n}</option>)}
        </select>
        <span className="muted" style={{ fontSize: 12 }}>of {doc.pages.length}</span>
        <button className="gb" aria-label="Next page" disabled={current >= doc.pages.length} onClick={() => goToPage(current + 1)}>{chev('M9 18l6-6-6-6')}</button>
        <span className="sp" />
        {tool !== 'newbox' && (
          <div className="seg small" role="group" aria-label="Selection tool">
            <button className={tool === 'tap' ? 'on' : ''} title="Click a block to select it; Ctrl+click adds more. Dragging also selects." onClick={() => setTool('tap')}>Select</button>
            <button className={tool === 'drag' ? 'on' : ''} title="Always drags a selection rectangle (use this on a touch screen)" onClick={() => setTool('drag')}>Drag</button>
          </div>
        )}
      </div>
      {tool === 'newbox' && (
        <div className="banner" role="status">
          <span>New box: drag a rectangle around the text the OCR missed. The result appears in the Assistant for you to accept.</span>
          <button className="gb sm" onClick={() => setTool('tap')}>Cancel</button>
        </div>
      )}
      <div className="scroll" ref={ref}>
        <div className="sheets">
          {pages.map((p, i) => (
            <div key={p.n} style={{ display: 'contents' }}>
              {scope === 'doc' && i > 0 && <div className="pg">page {p.n}</div>}
              <Sheet jobId={jobId} page={p} blocks={byPage.get(p.n) ?? []} />
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
