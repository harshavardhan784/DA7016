/** What the toolbar buttons do. Only things the notebook's grammar can say are offered, so nothing is lost on rebuild. */
import type { Editor } from '@tiptap/core'
import { Fragment, type Node as PMNode } from '@tiptap/pm/model'
import { CellSelection } from '@tiptap/pm/tables'
import { createTable } from '@tiptap/extension-table'
import { P_ATTRS } from './parse'

const truthy = (v: unknown) => ['1', 'true', 'yes', 'y', 'on'].includes(String(v ?? '').trim().toLowerCase())
const clamp = (n: number, a: number, b: number) => Math.max(a, Math.min(b, n))
type Patch = Record<string, unknown>

export function patchTextblocks(ed: Editor, patch: Patch | ((n: PMNode) => Patch)): void {
  const { state, view } = ed
  const tr = state.tr
  for (const r of state.selection.ranges) {
    state.doc.nodesBetween(r.$from.pos, r.$to.pos, (node, pos) => {
      if (node.isTextblock) tr.setNodeMarkup(pos, undefined, { ...node.attrs, ...(typeof patch === 'function' ? patch(node) : patch) })
    })
  }
  if (tr.docChanged) view.dispatch(tr)
  view.focus()
}

export function blockAttrs(ed: Editor): Patch {
  const { $from } = ed.state.selection
  for (let d = $from.depth; d > 0; d--) if ($from.node(d).isTextblock) return $from.node(d).attrs
  return {}
}

/** Bold / italic / underline: on the selection, or on the whole paragraph when nothing is selected. */
export function toggleBasic(ed: Editor, name: 'bold' | 'italic' | 'underline'): void {
  if (ed.state.selection.empty) patchTextblocks(ed, (n) => ({ [name]: truthy(n.attrs[name]) ? null : 'true' }))
  else ed.chain().focus().toggleMark(name).run()
}
export function basicActive(ed: Editor, name: 'bold' | 'italic' | 'underline'): boolean {
  return ed.state.selection.empty ? truthy(blockAttrs(ed)[name]) : ed.isActive(name)
}

/** Strike / superscript / subscript / right-aligned mark: the selection, or the whole paragraph. */
export function toggleMarkSmart(ed: Editor, name: 'strike' | 'superscript' | 'subscript' | 'right'): void {
  const { state, view } = ed
  if (!state.selection.empty) { ed.chain().focus().toggleMark(name).run(); return }
  const { $from } = state.selection
  const [from, to] = [$from.start(), $from.end()]
  if (from === to) return
  const type = state.schema.marks[name]
  const all = state.doc.rangeHasMark(from, to, type)
  view.dispatch(all ? state.tr.removeMark(from, to, type) : state.tr.addMark(from, to, type.create()))
  view.focus()
}

/** Size / font / colour / highlight. */
export function setValue(ed: Editor, name: 'size' | 'font' | 'color' | 'highlight', value: string | null): void {
  const { state, view } = ed
  if (!state.selection.empty || name === 'highlight') {
    const range = state.selection.empty ? [state.selection.$from.start(), state.selection.$from.end()] : [state.selection.from, state.selection.to]
    const type = state.schema.marks[name]
    if (range[0] === range[1]) return
    view.dispatch(value ? state.tr.addMark(range[0], range[1], type.create({ value })) : state.tr.removeMark(range[0], range[1], type))
    view.focus()
  } else {
    patchTextblocks(ed, { [name]: value })
  }
}

export const setAlign = (ed: Editor, v: 'left' | 'center' | 'right' | 'justify') => patchTextblocks(ed, { align: v === 'left' ? null : v })
export const setHeading = (ed: Editor, v: '' | '1' | '2' | '3') => patchTextblocks(ed, { heading: v || null })
export const setLineSpacing = (ed: Editor, v: string) => patchTextblocks(ed, { line_spacing: v === '1.0' ? null : v })
export const setSpaceAfter = (ed: Editor, v: string) => patchTextblocks(ed, { space_after: v === '' ? null : v })

export function indent(ed: Editor, dir: 1 | -1): void {
  patchTextblocks(ed, (n) => {
    if (n.type.name === 'listItem') return { level: String(clamp((parseInt(String(n.attrs.level ?? 0)) || 0) + dir, 0, 4)) }
    const next = clamp((parseFloat(String(n.attrs.indent_left ?? 0)) || 0) + 0.25 * dir, 0, 8)
    return { indent_left: next === 0 ? null : String(next) }
  })
}

const LABEL = /^\s*(?:\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|[•·◦▪➤*–—-])\s/

/** Bullets / numbering. The builder never numbers anything, so the label characters are written into the text. */
export function toggleList(ed: Editor, type: 'bullet' | 'number'): void {
  const { state, view } = ed
  const { $from, $to } = state.selection
  const s = state.schema
  const first = $from.depth ? $from.index(0) : 0                         // select-all has no depth
  const last = $to.depth ? $to.index(0) : state.doc.childCount - 1
  const kids: PMNode[] = []
  for (let i = first; i <= last; i++) kids.push(state.doc.child(i))
  let start = 0
  for (let i = 0; i < first; i++) start += state.doc.child(i).nodeSize
  const end = start + kids.reduce((n, k) => n + k.nodeSize, 0)
  const keep = (n: PMNode) => Object.fromEntries(P_ATTRS.map((k) => [k, n.attrs[k] ?? null]))

  if (kids.every((k) => k.type.name === 'list')) {                         // already lists: switch type, or turn back into paragraphs
    const out: PMNode[] = []
    const same = kids.every((k) => k.attrs.type === type)
    for (const list of kids) {
      if (!same) { out.push(list.type.create({ ...list.attrs, type }, list.content)); continue }
      list.forEach((item) => out.push(s.nodes.paragraph.create(keep(item), item.content)))
    }
    view.dispatch(state.tr.replaceWith(start, end, out)); view.focus(); return
  }
  if (!kids.every((k) => k.type.name === 'paragraph')) return
  const items = kids.map((p, i) => {
    const label = type === 'bullet' ? '• ' : `${i + 1}. `
    const body = LABEL.test(p.textContent) ? p.content : Fragment.from(s.text(label)).append(p.content)
    return s.nodes.listItem.create({ level: '0', ...keep(p) }, body)
  })
  view.dispatch(state.tr.replaceWith(start, end, s.nodes.list.create({ type }, items)))
  view.focus()
}

export function insertTable(ed: Editor, rows: number, cols: number): void {
  const { state, view } = ed
  const pos = state.selection.$from.depth >= 1 ? state.selection.$from.after(1) : state.doc.content.size
  const t = createTable(state.schema, rows, cols, false)
  const tr = state.tr.insert(pos, t)
  tr.setNodeMarkup(pos, undefined, { ...t.attrs, border: '1' })
  tr.setNodeMarkup(pos + 1, undefined, { ...t.firstChild!.attrs, header: true })
  view.dispatch(tr.insert(pos + t.nodeSize, state.schema.nodes.paragraph.create()))
  view.focus()
}

export function toggleHeaderRow(ed: Editor): void {
  const { state, view } = ed
  const { $from } = state.selection
  for (let d = $from.depth; d > 0; d--) {
    const n = $from.node(d)
    if (n.type.name === 'tableRow') { view.dispatch(state.tr.setNodeMarkup($from.before(d), undefined, { ...n.attrs, header: !n.attrs.header })); view.focus(); return }
  }
}
export function headerRowActive(ed: Editor): boolean {
  const { $from } = ed.state.selection
  for (let d = $from.depth; d > 0; d--) if ($from.node(d).type.name === 'tableRow') return !!$from.node(d).attrs.header
  return false
}

/** The builder can merge cells across a row only, so a selection that spans rows may not be merged. */
export function canMergeAcross(ed: Editor): boolean {
  const s = ed.state.selection
  return s instanceof CellSelection && s.$anchorCell.pos !== s.$headCell.pos && s.$anchorCell.start() === s.$headCell.start()
}
export const mergeAcross = (ed: Editor) => { if (canMergeAcross(ed)) ed.chain().focus().mergeCells().run() }

export const insertBlank = (ed: Editor) =>
  ed.chain().focus().insertContent({ type: 'blank', attrs: { type: 'underscore', length: '20' } }).run()

export function clearFormatting(ed: Editor): void {
  ed.chain().focus().unsetAllMarks().run()
  patchTextblocks(ed, Object.fromEntries(P_ATTRS.map((k) => [k, null])))
}

/** A blank table as tags, for inserting it as a block of its own (header row first). */
export function emptyTableMarkup(rows: number, cols: number): string {
  const row = (header: boolean) => `<ROW${header ? ' header="true"' : ''}>${'<CELL></CELL>'.repeat(cols)}</ROW>`
  return `<TABLE border="1">${Array.from({ length: rows }, (_, i) => row(i === 0)).join('')}</TABLE>`
}
