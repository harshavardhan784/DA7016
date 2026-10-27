import { create } from 'zustand'
import type { Editor } from '@tiptap/core'
import { api } from '../api'
import type { Doc, Proposal } from '../types'
import { label } from '../review/util'

export interface Msg { id: number; role: 'user' | 'assistant'; text: string; proposalId?: string }
type Notice = { kind: 'error' | 'info'; text: string; action?: { label: string; run: () => void } } | null

interface State {
  jobId: string | null
  doc: Doc | null
  busy: string | null            // a slow thing the person is waiting for (asking the model)
  syncing: number                // saves in flight, shown as "Saving…"
  notice: Notice
  scope: 'page' | 'doc'
  page: number
  tool: 'tap' | 'drag' | 'newbox'
  selected: string[]
  active: string | null
  editor: Editor | null
  editorDirty: boolean
  toolbarOpen: boolean
  assistantOpen: boolean
  chat: Msg[]
  proposals: Record<string, Proposal>
  anchor: { id: string; from: 'source' | 'output' | 'jump'; seq: number } | null

  open: (jobId: string) => Promise<void>
  say: (kind: 'error' | 'info', text: string, action?: { label: string; run: () => void }) => void
  setScope: (s: 'page' | 'doc') => void
  goToPage: (n: number) => void
  setTool: (t: State['tool']) => void
  select: (ids: string[], how?: 'replace' | 'add' | 'toggle') => void
  setActive: (id: string | null) => void
  setEditor: (e: Editor | null) => void
  setEditorDirty: (d: boolean) => void
  setToolbarOpen: (o: boolean) => void
  setAssistantOpen: (o: boolean) => void
  setAnchor: (id: string, from: 'source' | 'output' | 'jump') => void
  saveBlock: (block: string, markup: string) => Promise<void>
  reorder: (order: string[]) => Promise<void>
  deleteBlocks: (ids: string[]) => Promise<void>
  rebuild: () => Promise<void>
  undo: () => Promise<void>
  redo: () => Promise<void>
  ask: (note: string, ids?: string[]) => Promise<void>
  addBlockAfter: (markup: string, type: string) => Promise<void>
  drawBox: (page: number, bbox: number[]) => Promise<void>
  accept: (pid: string, blocks?: string[]) => Promise<void>
  reject: (pid: string, blocks?: string[]) => Promise<void>
}

let rebuildTimer: ReturnType<typeof setTimeout> | undefined
let seq = 0
let msgId = 0
const message = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^(?:\w*Error: )+/, '')      // drop "ValueError: " style prefixes
const savedOpen = () => { try { const v = localStorage.getItem('ocr.assistantOpen'); return v === null ? window.innerWidth >= 1100 : v === '1' } catch { return true } }

export const useStore = create<State>((set, get) => {
  /** A slow server call the person waits for (the model), with a visible label. A failure becomes a visible message. */
  const run = async <T,>(label: string, fn: () => Promise<T>): Promise<T | undefined> => {
    set({ busy: label })
    try { return await fn() } catch (e) { get().say('error', message(e)); return undefined } finally { set({ busy: null }) }
  }
  /** A quick save that happens in the background: the screen has already changed. */
  const sync = async <T,>(fn: () => Promise<T>, onFail?: () => void): Promise<T | undefined> => {
    set((s) => ({ syncing: s.syncing + 1 }))
    try { return await fn() } catch (e) { get().say('error', `Not saved: ${message(e)}`); onFail?.(); return undefined } finally { set((s) => ({ syncing: s.syncing - 1 })) }
  }
  const keep = (s: State, doc: Doc) => ({ doc, active: s.active && doc.blocks[s.active] ? s.active : null, selected: s.selected.filter((b) => doc.blocks[b]) })
  /** Reads the server's view again (fast: it is built from the saved edits, not from a rebuild). */
  const refetch = async () => {
    const id = get().jobId
    if (!id) return
    try { const doc = await api.doc(id); set((s) => keep(s, doc)) } catch (e) { get().say('error', message(e)) }
  }
  /** The slow part (notebook page-break logic and the Word file) runs quietly behind the screen; only the flags come back. */
  const refreshFlags = async () => {
    const id = get().jobId
    if (!id) return
    clearTimeout(rebuildTimer)
    try {
      const fresh = await api.rebuild(id)
      set((s) => {
        if (!s.doc) return {}
        const blocks = { ...s.doc.blocks }
        for (const [k, b] of Object.entries(blocks)) {
          const f = fresh.blocks[k]
          if (f && f.markup === b.markup) blocks[k] = { ...b, absorbed: f.absorbed, absorbed_by: f.absorbed_by, reason: f.reason }
        }
        return { doc: { ...s.doc, blocks, log: fresh.log, dirty: fresh.rev !== s.doc.rev } }
      })
    } catch { /* the next change tries again */ }
  }
  const later = (ms = 3000) => { clearTimeout(rebuildTimer); rebuildTimer = setTimeout(() => { void refreshFlags() }, ms) }
  const patchDoc = (fn: (d: Doc) => Doc) => set((s) => (s.doc ? { doc: fn(s.doc) } : {}))
  const header = (h: { rev: number; cursor: number; length: number }) => patchDoc((d) => ({ ...d, ...h, dirty: true }))

  return {
    jobId: null, doc: null, busy: null, syncing: 0, notice: null, scope: 'doc', page: 1, tool: 'tap', selected: [], active: null,
    editor: null, editorDirty: false, toolbarOpen: false, assistantOpen: savedOpen(), chat: [], proposals: {}, anchor: null,

    open: async (jobId) => {
      clearTimeout(rebuildTimer)
      set({ jobId, doc: null, selected: [], active: null, chat: [], proposals: {}, page: 1, anchor: null, editor: null, editorDirty: false })
      const doc = await run('Opening…', () => api.doc(jobId))
      if (doc) set({ doc })
    },
    say: (kind, text, action) => { set((s) => ({ notice: { kind, text, action }, chat: kind === 'error' && s.jobId ? [...s.chat, { id: ++msgId, role: 'assistant' as const, text: `Problem: ${text}` }] : s.chat })); setTimeout(() => get().notice?.text === text && set({ notice: null }), action ? 9000 : 7000) },
    // Switching view keeps you where you were: the page you were looking at, whichever view you leave.
    setScope: (scope) => set((s) => {
      const d = s.doc
      if (!d) return { scope }
      const cur = s.scope === 'page' ? s.page : d.blocks[s.anchor?.id ?? d.order[0]]?.page ?? 1
      if (scope === 'page') return { scope, page: cur }
      const first = d.order.find((i) => d.blocks[i].page === cur)
      return { scope, ...(first ? { anchor: { id: first, from: 'jump' as const, seq: ++seq } } : {}) }
    }),
    // One page control for both views: in This page it shows that page, in Whole document both panes scroll to it.
    goToPage: (n) => set((s) => {
      const d = s.doc
      if (!d) return {}
      const page = Math.max(1, Math.min(d.pages.length, n))
      if (s.scope === 'page') return { page }
      const first = d.order.find((i) => d.blocks[i].page === page)
      return first ? { anchor: { id: first, from: 'jump' as const, seq: ++seq } } : {}
    }),
    setTool: (tool) => set({ tool }),
    select: (ids, how = 'replace') => set((s) => {
      if (how === 'replace') return { selected: ids }
      const cur = new Set(s.selected)
      for (const i of ids) (how === 'toggle' && cur.has(i)) ? cur.delete(i) : cur.add(i)
      return { selected: [...cur] }
    }),
    setActive: (active) => set({ active, editorDirty: false, ...(active ? {} : { editor: null }) }),
    setEditor: (editor) => set({ editor }),
    setEditorDirty: (editorDirty) => set({ editorDirty }),
    setToolbarOpen: (toolbarOpen) => set({ toolbarOpen }),
    setAssistantOpen: (assistantOpen) => { try { localStorage.setItem('ocr.assistantOpen', assistantOpen ? '1' : '0') } catch { /* private mode */ } set({ assistantOpen }) },
    setAnchor: (id, from) => set({ anchor: { id, from, seq: ++seq } }),

    saveBlock: async (block, markup) => {
      const id = get().jobId
      const cur = get().doc?.blocks[block]
      if (!id || !cur || cur.markup === markup) return
      patchDoc((d) => ({ ...d, dirty: true, blocks: { ...d.blocks, [block]: { ...d.blocks[block], markup } } }))
      const head = await sync(() => api.editBlock(id, block, markup) as Promise<{ rev: number; cursor: number; length: number }>)
      if (head) { header(head); later() }
    },
    reorder: async (order) => {
      const id = get().jobId
      const before = get().doc?.order
      if (!id || !before) return
      patchDoc((d) => ({ ...d, order }))                                   // the blocks are already where you dropped them
      const head = await sync(() => api.reorder(id, order) as Promise<{ rev: number; cursor: number; length: number }>, () => { void refetch() })
      if (head) { header(head); later() }
    },
    deleteBlocks: async (ids) => {
      const { jobId, doc } = get()
      if (!jobId || !doc || !ids.length) return
      if (ids.length >= doc.order.length) { get().say('error', 'A document needs at least one block.'); return }
      patchDoc((d) => {
        const blocks = { ...d.blocks }
        for (const i of ids) delete blocks[i]
        return { ...d, blocks, order: d.order.filter((i) => !ids.includes(i)) }
      })
      set((s) => ({ selected: s.selected.filter((i) => !ids.includes(i)), active: s.active && ids.includes(s.active) ? null : s.active }))
      const head = await sync(() => api.deleteBlocks(jobId, ids) as Promise<{ rev: number; cursor: number; length: number }>, () => { void refetch() })
      if (head) {
        header(head); later()
        get().say('info', `Deleted ${ids.length} block${ids.length > 1 ? 's' : ''}`, { label: 'Undo', run: () => { void get().undo() } })
      }
    },
    rebuild: refreshFlags,
    undo: async () => {
      const { editor, editorDirty, jobId } = get()
      if (editor && editorDirty) { editor.commands.undo(); return }
      if (jobId && await sync(() => api.undo(jobId)) !== undefined) { await refetch(); later() }
    },
    redo: async () => {
      const { editor, editorDirty, jobId } = get()
      if (editor && editorDirty) { editor.commands.redo(); return }
      if (jobId && await sync(() => api.redo(jobId)) !== undefined) { await refetch(); later() }
    },

    ask: async (note, idsOverride) => {
      const { jobId, doc, selected } = get()
      if (!jobId || !doc) return
      const ids = idsOverride ?? doc.order.filter((b) => selected.includes(b))
      if (!ids.length) { get().say('info', 'Select at least one block on the page first.'); return }
      set((s) => ({ chat: [...s.chat, { id: ++msgId, role: 'user', text: note || 'Look at these blocks' }] }))
      const p = await run('Working on it…', () => api.review(jobId, 'auto', ids, note, null))
      if (!p) return
      const n = p.blocks.length
      const text = p.mode === 'ask' ? (p.message ?? '')
        : p.mode === 'assist' ? `${p.message ?? ''}${n ? ` ${n} proposal${n > 1 ? 's' : ''} ready${p.skipped?.length ? `, ${p.skipped.length} skipped` : ''}. Nothing changes until you accept.` : p.skipped?.length ? ` ${p.skipped.length} skipped.` : ''}`.trim()
        : p.mode === 'delete' ? `${p.understood || 'You want these blocks removed.'} Nothing is deleted until you confirm.`
        : `${p.understood ? p.understood + ' ' : ''}${n} proposal${n > 1 ? 's' : ''} ready${p.skipped?.length ? `, ${p.skipped.length} skipped` : ''}. Nothing changes until you accept.`
      set((s) => ({ proposals: { ...s.proposals, [p.id]: p }, chat: [...s.chat, { id: ++msgId, role: 'assistant', text, proposalId: p.id }] }))
      get().setAssistantOpen(true)
    },
    addBlockAfter: async (markup, type) => {
      const { jobId, doc, selected, active, anchor } = get()
      if (!jobId || !doc) return
      const after = doc.order.filter((i) => selected.includes(i)).at(-1) ?? (active && doc.blocks[active] ? active : null) ?? anchor?.id ?? doc.order[0]
      const r = await sync(() => api.addBlock(jobId, after, markup, type))
      if (!r) return
      await refetch()
      set({ selected: [r.id] })
      get().setAnchor(r.id, 'jump')
      get().say('info', `Added a new ${type.toLowerCase()} below ${label(after)}. Click in it to type.`)
      later()
    },
    drawBox: async (page, bbox) => {
      const { jobId } = get()
      set({ tool: 'tap' })
      if (!jobId) return
      set((s) => ({ chat: [...s.chat, { id: ++msgId, role: 'user', text: `Read the new box I drew on page ${page}` }] }))
      const p = await run('Reading the box…', () => api.newBox(jobId, page, bbox, ''))
      if (p) { set((s) => ({ proposals: { ...s.proposals, [p.id]: p },
        chat: [...s.chat, { id: ++msgId, role: 'assistant', text: 'New block ready. It is added only if you accept.', proposalId: p.id }] })); get().setAssistantOpen(true) }
    },
    accept: async (pid, blocks) => {
      const { jobId, proposals, doc } = get()
      const p = proposals[pid]
      if (!jobId || !p || !doc) return
      const todo = p.blocks.filter((b) => !b.resolved && (!blocks || blocks.includes(b.id)))
      // show the result at once, then confirm with the server; if it refuses, the screen goes back to what the server has
      const instant = p.mode !== 'assist' || todo.every((b) => b.kind === 'edit')       // merges, new blocks and deletes made by the assistant come back from the server instead
      if (instant) patchDoc((d) => {
        let next = { ...d, blocks: { ...d.blocks }, order: [...d.order] }
        if (p.mode === 'delete') {
          for (const b of todo) delete next.blocks[b.id]
          next.order = next.order.filter((i) => next.blocks[i])
        } else if (p.mode === 'newbox' && p.new_block) {
          const nb = p.new_block, b0 = todo[0]
          next.blocks[nb.block_id] = { id: nb.block_id, page: nb.page, type: 'Text', is_picture: false, has_image: false, bbox_norm: nb.bbox_norm,
            markup: b0.after, rendered: true, reason: null, absorbed: false, absorbed_by: null }
          next.order.splice(p.index ?? next.order.length, 0, nb.block_id)
        } else {
          for (const b of todo) { const k = b.ref ?? b.id; next.blocks[k] = { ...next.blocks[k], markup: String(b.fields.formatted_text ?? b.after),
            ...(b.fields.is_picture === false ? { is_picture: false, has_image: false } : {}) } }
        }
        return { ...next, dirty: true }
      })
      set((s) => ({ selected: s.selected.filter((i) => get().doc?.blocks[i]) }))
      const head = await sync(() => api.accept(jobId, pid, blocks) as Promise<{ rev: number; cursor: number; length: number }>, () => { void refetch() })
      if (!head) return
      header(head); later()
      if (p.mode === 'assist') {
        await refetch()
        get().say('info', `Applied ${todo.length} change${todo.length > 1 ? 's' : ''}`, { label: 'Undo', run: () => { void get().undo() } })
      }
      set((s) => {
        const cur = s.proposals[pid]
        const bl = cur.blocks.map((b) => (!b.resolved && (!blocks || blocks.includes(b.id)) ? { ...b, resolved: 'accepted' as const } : b))
        return { proposals: { ...s.proposals, [pid]: { ...cur, blocks: bl, status: bl.every((b) => b.resolved) ? 'closed' : 'ready' } } }
      })
      if (p.mode === 'delete') get().say('info', `Deleted ${todo.length} block${todo.length > 1 ? 's' : ''}`, { label: 'Undo', run: () => { void get().undo() } })
    },
    reject: async (pid, blocks) => {
      const { jobId } = get()
      if (!jobId) return
      set((s) => {
        const p = s.proposals[pid]
        const bl = p.blocks.map((b) => (!b.resolved && (!blocks || blocks.includes(b.id)) ? { ...b, resolved: 'rejected' as const } : b))
        return { proposals: { ...s.proposals, [pid]: { ...p, blocks: bl, status: bl.every((b) => b.resolved) ? 'closed' : 'ready' } } }
      })
      await sync(() => api.reject(jobId, pid, blocks))
    },
  }
})
