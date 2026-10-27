import { Fragment, useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../state/store'
import { BlockView } from './BlockView'
import { Toolbar } from './Toolbar'
import { useScrollSync } from './hooks'
import { label } from './util'

interface Drag { group: string[]; page: number; x: number; y: number; left: number; offY: number; width: number; text: string }

const plain = (markup: string) => markup.replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/\s+/g, ' ').trim()
const EDGE = 64            // px from the top or bottom of the page area where a drag starts scrolling it
const MOVE_MS = 160

export function OutputPane({ jobId }: { jobId: string }) {
  const doc = useStore((s) => s.doc)!
  const scope = useStore((s) => s.scope)
  const page = useStore((s) => s.page)
  const anchor = useStore((s) => s.anchor)
  const selected = useStore((s) => s.selected)
  const scrollRef = useRef<HTMLDivElement>(null)
  const colRef = useRef<HTMLDivElement>(null)
  useScrollSync(scrollRef, 'output', 'data-block')

  const [drag, setDrag] = useState<Drag | null>(null)
  const [live, setLive] = useState<string[] | null>(null)        // the dragged page's blocks, in the order they would land
  const liveRef = useRef<string[] | null>(null)
  const animating = useRef(false)
  const tops = useRef(new Map<string, number>())

  const shown = useMemo(() => (scope === 'page' ? doc.order.filter((i) => doc.blocks[i].page === page) : doc.order), [doc.order, doc.blocks, scope, page])
  const ids = useMemo(() => {
    if (!drag || !live) return shown
    const out: string[] = []
    let put = false
    for (const id of shown) {
      if (doc.blocks[id].page !== drag.page) out.push(id)
      else if (!put) { out.push(...live); put = true }
    }
    return out
  }, [shown, drag, live, doc.blocks])
  const absorbers = useMemo(() => new Set(Object.values(doc.blocks).map((b) => b.absorbed_by).filter(Boolean) as string[]), [doc.blocks])

  // Blocks that change place slide there instead of jumping (only while moving blocks, so editing never animates).
  useLayoutEffect(() => {
    const col = colRef.current
    if (!col) return
    const next = new Map<string, number>()
    col.querySelectorAll<HTMLElement>('[data-block]').forEach((el) => next.set(el.dataset.block!, el.offsetTop))
    if (animating.current) {
      next.forEach((top, id) => {
        const was = tops.current.get(id)
        if (was === undefined || was === top) return
        const el = col.querySelector<HTMLElement>(`[data-block="${CSS.escape(id)}"]`)
        if (!el) return
        el.style.transition = 'none'
        el.style.transform = `translateY(${was - top}px)`
        void el.offsetHeight
        el.style.transition = `transform ${MOVE_MS}ms ease`
        el.style.transform = ''
      })
    }
    tops.current = next
  })

  const settle = () => { setTimeout(() => { animating.current = false }, MOVE_MS + 120) }
  const fullOrder = (pg: number, pageOrder: string[]) => {
    const d = useStore.getState().doc!
    const out: string[] = []
    let put = false
    for (const id of d.order) {
      if (d.blocks[id].page !== pg) out.push(id)
      else if (!put) { out.push(...pageOrder); put = true }
    }
    return out
  }

  const onGrip = useCallback((e: React.PointerEvent, id: string) => {
    if (e.button !== 0) return
    e.preventDefault()
    const st = useStore.getState()
    const d = st.doc!
    const pg = d.blocks[id].page
    const sameSelected = st.selected.filter((i) => d.blocks[i]?.page === pg)
    const group = st.selected.includes(id) && sameSelected.length > 1 ? d.order.filter((i) => sameSelected.includes(i)) : [id]   // a selection moves together
    const start = { x: e.clientX, y: e.clientY }
    const pointer = { y: e.clientY, dir: 0 }                              // dir: the way the pointer last moved, -1 up, 1 down
    const col = colRef.current!, scroller = scrollRef.current!
    const original = d.order.filter((i) => d.blocks[i].page === pg)
    let started = false, raf = 0

    const reflow = () => {
      const current = liveRef.current ?? original
      const others = current.filter((i) => !group.includes(i))
      const colTop = col.getBoundingClientRect().top                      // layout positions, so the sliding animation cannot upset the maths
      let k = others.length
      for (let n = 0; n < others.length; n++) {
        const el = col.querySelector<HTMLElement>(`[data-block="${CSS.escape(others[n])}"]`)
        if (el && colTop + el.offsetTop + el.offsetHeight / 2 > pointer.y) { k = n; break }
      }
      const next = [...others.slice(0, k), ...group, ...others.slice(k)]
      if (next.join() !== current.join()) { liveRef.current = next; setLive(next) }
    }
    const loop = () => {
      const r = scroller.getBoundingClientRect()
      // near an edge AND heading towards it: picking a block up near the top must not make the page run away
      const speed = pointer.y < r.top + EDGE && pointer.dir < 0 ? -14 : pointer.y > r.bottom - EDGE && pointer.dir > 0 ? 14 : 0
      if (speed) { scroller.scrollTop += speed; reflow() }
      raf = requestAnimationFrame(loop)
    }
    const move = (ev: PointerEvent) => {
      if (ev.clientY !== pointer.y) pointer.dir = ev.clientY > pointer.y ? 1 : -1
      pointer.y = ev.clientY
      if (!started) {
        if (Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 4) return
        started = true
        animating.current = true
        document.body.classList.add('is-dragging')
        const el = col.querySelector<HTMLElement>(`[data-block="${CSS.escape(id)}"]`)!
        const r = el.getBoundingClientRect()
        const text = group.length > 1 ? `${group.length} blocks` : plain(d.blocks[id].markup).slice(0, 90)
        setDrag({ group, page: pg, x: ev.clientX, y: ev.clientY, left: r.left, offY: start.y - r.top, width: r.width, text })
        raf = requestAnimationFrame(loop)
      }
      setDrag((p) => (p ? { ...p, x: ev.clientX, y: ev.clientY } : p))
      reflow()
    }
    const end = (commit: boolean) => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', cancel); window.removeEventListener('keydown', key)
      cancelAnimationFrame(raf)
      setTimeout(() => document.body.classList.remove('is-dragging'), 0)       // after the same Escape press has been seen by everyone
      const final = liveRef.current
      liveRef.current = null
      if (started && commit && final && final.join() !== original.join()) void useStore.getState().reorder(fullOrder(pg, final))
      setDrag(null); setLive(null)
      settle()
    }
    const up = () => end(true)
    const cancel = () => end(false)
    const key = (ev: KeyboardEvent) => { if (ev.key === 'Escape') end(false) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', cancel); window.addEventListener('keydown', key)
  }, [])

  const onNudge = useCallback((id: string, dir: -1 | 1) => {
    const d = useStore.getState().doc!
    const pg = d.blocks[id].page
    const pageIds = d.order.filter((i) => d.blocks[i].page === pg)
    const k = pageIds.indexOf(id), j = k + dir
    if (j < 0 || j >= pageIds.length) return
    const next = [...pageIds];
    [next[k], next[j]] = [next[j], next[k]]
    animating.current = true
    void useStore.getState().reorder(fullOrder(pg, next))
    settle()
    requestAnimationFrame(() => colRef.current?.querySelector<HTMLElement>(`[data-block="${CSS.escape(id)}"] .grip`)?.focus())
  }, [])

  const clearOnBackground = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget || e.target === colRef.current) useStore.getState().select([])
  }

  return (
    <section className="output" aria-label="Output" data-anchor={anchor?.id ?? doc.order[0]}>
      <Toolbar />
      <div className="docscroll" ref={scrollRef} onMouseDown={clearOnBackground}>
        <div className="doccol" ref={colRef}>
          {ids.map((id, i) => {
            const b = doc.blocks[id]
            const first = i === 0 || doc.blocks[ids[i - 1]].page !== b.page
            return (
              <Fragment key={id}>
                {first && (scope === 'doc' || i === 0) && <div className="divider">page {b.page}{scope === 'doc' && i > 0 ? ' starts' : ''}</div>}
                <BlockView block={b} jobId={jobId} continues={absorbers.has(id)} dragging={!!drag?.group.includes(id)} onGrip={onGrip} onNudge={onNudge} />
              </Fragment>
            )
          })}
          {!ids.length && <div className="muted">No blocks on this page.</div>}
        </div>
      </div>
      {drag && (
        <div className="floatcard" aria-hidden="true" style={{ left: drag.left + 10, top: drag.y - drag.offY - 4, width: drag.width }}>
          <b>{drag.group.length > 1 ? `${drag.group.length}` : label(drag.group[0]).split(' · ')[1]}</b>{drag.text}
        </div>
      )}
      {selected.length > 0 && !drag && (
        <div className="selbar" role="toolbar" aria-label="Selected blocks">
          <span>{selected.length} selected</span>
          <button className="del" onClick={() => void useStore.getState().deleteBlocks(selected)}>
            <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></svg>Delete
          </button>
          <button onClick={() => useStore.getState().select([])}>Clear</button>
        </div>
      )}
    </section>
  )
}
