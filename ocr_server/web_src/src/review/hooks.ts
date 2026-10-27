import { useEffect, useReducer, useRef, useState, type RefObject } from 'react'
import type { Editor } from '@tiptap/core'
import { useStore } from '../state/store'

/** True while the element is near the viewport. Editors are only built for blocks you can see. */
export function useVisible(ref: RefObject<HTMLElement | null>, margin = 500): boolean {
  const [v, setV] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (typeof IntersectionObserver === 'undefined') { setV(true); return }
    const io = new IntersectionObserver(([e]) => setV(e.isIntersecting), { rootMargin: `${margin}px 0px` })
    io.observe(el)
    return () => io.disconnect()
  }, [ref, margin])
  return v
}

/** Re-renders when the editor's selection or content changes, so toolbar buttons show the right state. */
export function useTick(editor: Editor | null): void {
  const [, bump] = useReducer((n: number) => n + 1, 0)
  useEffect(() => {
    if (!editor) return
    editor.on('transaction', bump); editor.on('focus', bump); editor.on('blur', bump)
    return () => { editor.off('transaction', bump); editor.off('focus', bump); editor.off('blur', bump) }
  }, [editor])
}

const TOP = 14
/**
 * Keeps the source and output panes on the same top block while both scroll continuously.
 * Each pane reports the block at its top line; the other pane scrolls that block to its own top line.
 * A scroll caused by the sync itself is ignored, so the panes cannot chase each other.
 */
export function useScrollSync(ref: RefObject<HTMLElement | null>, who: 'source' | 'output', attr: 'data-block' | 'data-box'): void {
  const scope = useStore((s) => s.scope)
  const anchor = useStore((s) => s.anchor)
  const doc = useStore((s) => s.doc)
  const lock = useRef(0)

  useEffect(() => {
    const el = ref.current
    if (!el || scope !== 'doc') return
    let raf = 0
    const onScroll = () => {
      if (Date.now() < lock.current) return
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        const line = el.getBoundingClientRect().top + TOP
        for (const n of el.querySelectorAll<HTMLElement>(`[${attr}]`)) {
          if (n.getBoundingClientRect().bottom > line + 4) { useStore.getState().setAnchor(n.getAttribute(attr)!, who); break }
        }
      })
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => { el.removeEventListener('scroll', onScroll); cancelAnimationFrame(raf) }
  }, [ref, scope, who, attr])

  useEffect(() => {
    const el = ref.current
    if (!el || !doc || scope !== 'doc' || !anchor || anchor.from === who) return
    // the block itself, else the block that absorbed it, else the next block that is on this pane
    const order = doc.order
    const candidates = [anchor.id, doc.blocks[anchor.id]?.absorbed_by ?? '', ...order.slice(order.indexOf(anchor.id) + 1)]
    let target: HTMLElement | null = null
    for (const id of candidates) { if (id && (target = el.querySelector<HTMLElement>(`[${attr}="${CSS.escape(id)}"]`))) break }
    if (!target) return
    const delta = target.getBoundingClientRect().top - (el.getBoundingClientRect().top + TOP)
    if (Math.abs(delta) > 2) { lock.current = Date.now() + 160; el.scrollTop += delta }
  }, [anchor?.seq])   // eslint-disable-line react-hooks/exhaustive-deps
}
