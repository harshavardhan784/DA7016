/** The editor's document model: one node or mark for each thing the notebook's tag grammar can say. */
import { Node, Mark, type Extensions, type AnyExtension } from '@tiptap/core'
import Text from '@tiptap/extension-text'
import HardBreak from '@tiptap/extension-hard-break'
import Bold from '@tiptap/extension-bold'
import Italic from '@tiptap/extension-italic'
import Underline from '@tiptap/extension-underline'
import Strike from '@tiptap/extension-strike'
import Superscript from '@tiptap/extension-superscript'
import Subscript from '@tiptap/extension-subscript'
import { Table, TableRow, TableCell } from '@tiptap/extension-table'
import { UndoRedo } from '@tiptap/extensions'
import { Plugin } from '@tiptap/pm/state'
import katex from 'katex'

const num = (v: unknown) => { const n = parseFloat(String(v)); return Number.isFinite(n) ? n : null }
const clamp = (n: number, a: number, b: number) => Math.max(a, Math.min(b, n))
const hex = (v: unknown) => (/^#?[0-9a-fA-F]{6}$/.test(String(v ?? '')) ? '#' + String(v).replace('#', '') : null)
const truthy = (v: unknown) => ['1', 'true', 'yes', 'y', 'on'].includes(String(v ?? '').trim().toLowerCase())

export const HIGHLIGHTS: Record<string, string> = {
  yellow: '#FFF200', green: '#7CFC00', cyan: '#40E0D0', turquoise: '#40E0D0', pink: '#FFB6C1',
  red: '#FF6B6B', blue: '#8FB4FF', gray: '#D3D3D3', grey: '#D3D3D3',
}

type A = Record<string, unknown>
const css = (s: string | null): Record<string, string> => (s ? { style: s } : {})

/** Paragraph-level attributes, shared by paragraphs, list items and option items. How each looks on screen. */
const P_ATTR_DEFS: Record<string, (a: A) => Record<string, string>> = {
  align: (a) => css(['left', 'center', 'right', 'justify'].includes(String(a.align)) ? `text-align:${a.align}` : String(a.align) === 'centre' ? 'text-align:center' : null),
  size: (a) => css(num(a.size) ? `font-size:${clamp(num(a.size)!, 5, 48)}pt` : null),
  bold: (a) => css(truthy(a.bold) ? 'font-weight:700' : null),
  italic: (a) => css(truthy(a.italic) ? 'font-style:italic' : null),
  underline: (a) => css(truthy(a.underline) ? 'text-decoration:underline' : null),
  font: (a) => css(a.font ? `font-family:'${String(a.font).replace(/['"]/g, '')}',serif` : null),
  color: (a) => css(hex(a.color) ? `color:${hex(a.color)}` : null),
  heading: (a): Record<string, string> => { const m = String(a.heading ?? '').toLowerCase().replace(/^h/, ''); return ['1', '2', '3'].includes(m) ? { class: `hd hd${m}` } : {} },
  indent_left: (a) => css(num(a.indent_left) !== null ? `margin-left:${clamp(num(a.indent_left)!, -3, 8)}in` : null),
  indent_first: (a) => css(num(a.indent_first) !== null ? `text-indent:${clamp(num(a.indent_first)!, -3, 8)}in` : null),
  space_before: (a) => css(num(a.space_before) !== null ? `margin-top:${clamp(num(a.space_before)!, 0, 72)}pt` : null),
  space_after: (a) => css(num(a.space_after) !== null ? `margin-bottom:${clamp(num(a.space_after)!, 0, 72)}pt` : null),
  line_spacing: (a) => css(num(a.line_spacing) && num(a.line_spacing)! >= 0.5 && num(a.line_spacing)! <= 3 ? `line-height:${num(a.line_spacing)}` : null),
}
const pAttrs = (extra: Record<string, (a: A) => Record<string, string>> = {}) =>
  Object.fromEntries([...Object.entries(P_ATTR_DEFS), ...Object.entries(extra)].map(([k, fn]) =>
    [k, { default: null, renderHTML: (a: A) => fn(a), parseHTML: () => null }]))

const Doc = Node.create({ name: 'doc', topNode: true, content: '(paragraph | list | options | table)+' })

const Paragraph = Node.create({
  name: 'paragraph', group: 'block', content: 'inline*',
  addAttributes() { return pAttrs() },
  parseHTML() { return [{ tag: 'p' }] },
  renderHTML({ HTMLAttributes }) { return ['p', HTMLAttributes, 0] },
})

const itemNode = (name: string, tag: string, cls: string, extra: Record<string, (a: A) => Record<string, string>>) => Node.create({
  name, content: 'inline*', defining: true,
  addAttributes() { return pAttrs(extra) },
  parseHTML() { return [{ tag: `div.${cls}` }] },
  renderHTML({ HTMLAttributes }) { return [tag, { ...HTMLAttributes, class: `${cls} ${HTMLAttributes.class ?? ''}`.trim() }, 0] },
})
const ListItem = itemNode('listItem', 'div', 'li', { level: (a) => ({ style: `--lv:${clamp(num(a.level) ?? 0, 0, 4)}` }) })
const OptionItem = itemNode('optionItem', 'div', 'op', {})

const List = Node.create({
  name: 'list', group: 'block', content: 'listItem+', isolating: true,
  addAttributes() { return { type: { default: null, renderHTML: (a: A) => ({ 'data-type': String(a.type ?? 'none') }), parseHTML: () => null } } },
  parseHTML() { return [{ tag: 'div.lst' }] },
  renderHTML({ HTMLAttributes }) { return ['div', { ...HTMLAttributes, class: 'lst' }, 0] },
})
const Options = Node.create({
  name: 'options', group: 'block', content: 'optionItem+', isolating: true,
  addAttributes() {
    return {
      layout: { default: null, renderHTML: (a: A) => ({ 'data-layout': String(a.layout ?? 'vertical') }), parseHTML: () => null },
      columns: { default: null, renderHTML: (a: A) => ({ style: `--cols:${clamp(num(a.columns) ?? 2, 1, 6)}` }), parseHTML: () => null },
    }
  },
  parseHTML() { return [{ tag: 'div.ops' }] },
  renderHTML({ HTMLAttributes }) { return ['div', { ...HTMLAttributes, class: 'ops' }, 0] },
})

const Blank = Node.create({
  name: 'blank', group: 'inline', inline: true, atom: true,
  addAttributes() { return { type: { default: null }, length: { default: null } } },
  parseHTML() { return [{ tag: 'span.blank' }] },
  renderHTML({ node }) {
    const n = clamp(Math.trunc(num(node.attrs.length) ?? 10), 1, 120)
    const ch = ({ dots: '.', space: '\u00a0', line: '_', underscore: '_' } as Record<string, string>)[String(node.attrs.type ?? 'underscore')] ?? '_'
    return ['span', { class: 'blank', title: 'blank run' }, ch.repeat(Math.min(n, 40))]
  },
})
const Img = Node.create({
  name: 'img', group: 'inline', inline: true, atom: true,
  addAttributes() { return { tag: { default: 'IMG' }, bbox: { default: null } } },
  parseHTML() { return [{ tag: 'span.imgchip' }] },
  renderHTML({ node }) { return ['span', { class: 'imgchip', title: String(node.attrs.bbox) }, node.attrs.tag === 'FORMULA' ? '[formula image]' : '[image]'] },
})

/** Draws a formula into its element. Used by the editor and by the read-only view. */
export function drawMath(el: HTMLElement, latex: string, display: boolean) {
  try { katex.render(latex, el, { throwOnError: false, displayMode: display }) } catch { el.textContent = latex }
}
export function drawAllMath(root: ParentNode) {
  root.querySelectorAll<HTMLElement>('span.math[data-latex]:not([data-drawn])').forEach((el) => {
    drawMath(el, el.dataset.latex ?? '', el.dataset.display === 'true')
    el.dataset.drawn = '1'
  })
}

const MathNode = Node.create({
  name: 'math', group: 'inline', inline: true, atom: true,
  addAttributes() { return { latex: { default: '' }, display: { default: false } } },
  parseHTML() { return [{ tag: 'span.math' }] },
  renderHTML({ node }) {          // the read-only view; drawAllMath turns it into math once it is on the page
    return ['span', { class: 'math', 'data-latex': String(node.attrs.latex), 'data-display': String(!!node.attrs.display), title: 'Formula' }, String(node.attrs.latex)]
  },
  addNodeView() {
    return ({ node }) => {
      const dom = document.createElement('span')
      dom.className = 'math'
      dom.title = 'Formula. Double-click to edit it.'
      const paint = (n: typeof node) => { dom.setAttribute('data-latex', String(n.attrs.latex)); drawMath(dom, String(n.attrs.latex), !!n.attrs.display) }
      paint(node)
      return { dom, update: (n) => { if (n.type.name !== 'math') return false; paint(n); return true } }
    }
  },
  addProseMirrorPlugins() {
    return [new Plugin({ props: { handleDoubleClickOn(view, pos, node) {
      if (node.type.name !== 'math') return false
      const v = window.prompt('Edit the formula (LaTeX)', String(node.attrs.latex))
      if (v !== null && v.trim()) view.dispatch(view.state.tr.setNodeMarkup(pos, undefined, { ...node.attrs, latex: v.trim() }))
      return true
    } } })]
  },
})

const valueMark = (name: string, render: (v: string) => Record<string, string>) => Mark.create({
  name,
  addAttributes() { return { value: { default: null } } },
  parseHTML() { return [{ tag: `span[data-m="${name}"]` }] },
  renderHTML({ mark }) { return ['span', { 'data-m': name, ...render(String(mark.attrs.value ?? '')) }, 0] },
})
const Size = valueMark('size', (v) => css(num(v) ? `font-size:${clamp(num(v)!, 5, 48)}pt` : null))
const Font = valueMark('font', (v) => css(v ? `font-family:'${v.replace(/['"]/g, '')}',serif` : null))
const Color = valueMark('color', (v) => css(hex(v) ? `color:${hex(v)}` : null))
const Highlight = valueMark('highlight', (v) => css(HIGHLIGHTS[v.toLowerCase()] ? `background:${HIGHLIGHTS[v.toLowerCase()]}` : null))
const Right = Mark.create({
  name: 'right',
  parseHTML() { return [{ tag: 'span.mk-right' }] },
  renderHTML() { return ['span', { class: 'mk-right', title: 'right-aligned mark' }, 0] },
})

const rowAttrs = { header: { default: false, renderHTML: (a: A) => (a.header ? { class: 'hdr' } : {}), parseHTML: () => false } }
const strAttr = (render: (a: A) => Record<string, string>) => ({ default: null, renderHTML: render, parseHTML: () => null })

export const extensions: Extensions = [
  Doc, Paragraph, Text, HardBreak, List, ListItem, Options, OptionItem, Blank, Img, MathNode,
  Table.configure({ resizable: false }).extend({
    addAttributes() {
      return { border: strAttr((a) => ({ 'data-border': String(a.border ?? '1') })), align: strAttr((a) => ({ 'data-align': String(a.align ?? '') })) }
    },
  }),
  TableRow.extend({ content: 'tableCell+', addAttributes() { return rowAttrs } }),
  TableCell.extend({
    content: '(paragraph | list | options | table)+',
    addAttributes() {
      return {
        ...this.parent?.(),
        align: strAttr((a) => css(['left', 'center', 'right'].includes(String(a.align)) ? `text-align:${a.align}` : null)),
        bold: strAttr((a) => css(truthy(a.bold) ? 'font-weight:700' : null)),
        size: strAttr((a) => css(num(a.size) ? `font-size:${clamp(num(a.size)!, 5, 48)}pt` : null)),
      }
    },
  }),
  Bold, Italic, Underline, Strike, Superscript, Subscript, Size, Font, Color, Highlight, Right,
  UndoRedo,
] as AnyExtension[]
