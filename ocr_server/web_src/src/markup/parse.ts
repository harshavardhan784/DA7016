/**
 * Tag markup (the notebook's grammar) -> editor document (ProseMirror JSON).
 *
 * Follows the notebook's own parser (`_parse_markup`) and renderer (`_render_nodes`, `_fill_cell`, ...) rule by rule.
 * Anything the editor cannot show without changing what the DOCX builder would do throws Unsupported;
 * the screen then offers the raw tags instead, so nothing is ever lost.
 */
export type PM = { type: string; attrs?: Record<string, unknown>; content?: PM[]; marks?: { type: string; attrs?: Record<string, unknown> }[]; text?: string }
export class Unsupported extends Error {}

type Attrs = Record<string, string>
interface N { tag: string; attrs: Attrs; children: (string | N)[] }

const KNOWN = 'P|STYLE|BR|BLANK|LIST|ITEM|QUESTION|OPTIONS|OPTION|TABLE|ROW|CELL|IMG|RIGHT|FORMULA|SPAN'
const TAG_RE = () => new RegExp(`<(\\/?)(${KNOWN})((?:\\s+[A-Za-z_][\\w-]*\\s*=\\s*(?:"[^"]*"|'[^']*'|[^\\s<>/"']+))*)\\s*(\\/?)>`, 'gi')
const ATTR_RE = () => /([A-Za-z_][\w-]*)\s*=\s*("[^"]*"|'[^']*'|[^\s<>/"']+)/g
const BLOCK_RE = /(?:<|&(?:amp;)*lt;)\s*\/?\s*BLOCK\b[^<>]*?(?:\/?\s*>|\/?\s*&(?:amp;)*gt;)/gi
const VOID = new Set(['BR', 'BLANK', 'IMG'])
const XML_BAD = /[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]/g

export const P_ATTRS = ['align', 'size', 'bold', 'italic', 'underline', 'font', 'color', 'heading',
  'indent_left', 'indent_first', 'space_before', 'space_after', 'line_spacing'] as const
const BOOL_MARKS = ['bold', 'italic', 'underline', 'strike', 'superscript', 'subscript'] as const
const VALUE_MARKS = ['size', 'font', 'color', 'highlight'] as const

export function unescapeHtml(s: string): string {
  if (!s.includes('&')) return s
  const t = document.createElement('textarea')
  t.innerHTML = s
  return t.value
}

function parseAttrs(s: string): Attrs {
  const out: Attrs = {}
  for (const m of s.matchAll(ATTR_RE())) out[m[1].toLowerCase()] = m[2].replace(/^["']|["']$/g, '').trim()
  return out
}

function tree(text: string): N {
  text = text.replace(BLOCK_RE, '')
  const root: N = { tag: 'ROOT', attrs: {}, children: [] }
  const stack = [root]
  let pos = 0
  for (const m of text.matchAll(TAG_RE())) {
    const at = m.index as number
    if (at > pos) stack[stack.length - 1].children.push(unescapeHtml(text.slice(pos, at)))
    pos = at + m[0].length
    const name = m[2].toUpperCase()
    if (m[1]) {
      for (let k = stack.length - 1; k > 0; k--) if (stack[k].tag === name) { stack.length = k; break }
      continue
    }
    if (name === 'P' && stack[stack.length - 1].tag === 'P') stack.length -= 1     // a <P> inside an open <P>: the writer forgot </P> (the builder does the same)
    const node: N = { tag: name, attrs: parseAttrs(m[3]), children: [] }
    stack[stack.length - 1].children.push(node)
    if (VOID.has(name) || m[4]) continue
    stack.push(node)
  }
  if (pos < text.length) stack[stack.length - 1].children.push(unescapeHtml(text.slice(pos)))
  return root
}

const truthy = (v: string) => ['1', 'true', 'yes', 'y', 'on'].includes(v.trim().toLowerCase())
const isNode = (c: string | N): c is N => typeof c !== 'string'
const pAttrs = (a: Attrs) => Object.fromEntries(P_ATTRS.map((k) => [k, a[k] ?? null]))

type Marks = Map<string, Record<string, unknown> | undefined>

function styleMarks(a: Attrs, base: Marks): Marks {
  const m = new Map(base)
  for (const k of BOOL_MARKS) {
    if (!(k in a)) continue
    if (!truthy(a[k])) throw new Unsupported(`explicit ${k}="${a[k]}" on a span`)
    m.set(k, undefined)
  }
  for (const k of VALUE_MARKS) if (a[k]) m.set(k, { value: a[k] })
  return m
}

function textNode(text: string, marks: Marks): PM {
  const n: PM = { type: 'text', text }
  if (marks.size) n.marks = [...marks].map(([type, attrs]) => (attrs ? { type, attrs } : { type }))
  return n
}

// The builder's own rule for what counts as a formula (its _MATH_RE and _flatten_text): $$..$$, $..$, \\(..\\), \\[..\\];
// a lone $..$ that does not look like LaTeX is money ("$5 and $10"); an escaped \\$ is never a delimiter.
const MATH_RE = () => /\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]/gs
const LATEXY = /[\\^_{}=+\-*/<>()]|^\s*[A-Za-z]\s*$/

function splitMath(text: string): { text?: string; latex?: string; display?: boolean }[] {
  const t = text.replace(/\\\$/g, '\x01')
  const back = (x: string) => x.replace(/\x01/g, '\\$')
  const out: { text?: string; latex?: string; display?: boolean }[] = []
  let pos = 0
  for (const m of t.matchAll(MATH_RE())) {
    const body = m[1] ?? m[2] ?? m[3] ?? m[4]
    if (m[2] !== undefined && !LATEXY.test(body)) continue
    if (m.index! > pos) out.push({ text: back(t.slice(pos, m.index)) })
    out.push({ latex: back(body.trim()), display: m[1] !== undefined || m[4] !== undefined })
    pos = m.index! + m[0].length
  }
  if (pos < t.length) out.push({ text: back(t.slice(pos)) })
  return out
}

function inline(nodes: (string | N)[], marks: Marks, out: PM[]): PM[] {
  for (const n of nodes) {
    if (typeof n === 'string') {
      const t = n.replace(XML_BAD, '').replace(/\s+/g, ' ')            // words stay exactly as written
      for (const part of splitMath(t)) {
        if (part.latex !== undefined) out.push({ type: 'math', attrs: { latex: part.latex, display: !!part.display } })
        else if (part.text) out.push(textNode(part.text, marks))
      }
      continue
    }
    switch (n.tag) {
      case 'STYLE': inline(n.children, styleMarks(n.attrs, marks), out); break
      case 'SPAN': inline(n.children, marks, out); break
      case 'IMG': case 'FORMULA':
        if (n.attrs.bbox) out.push({ type: 'img', attrs: { tag: n.tag, bbox: n.attrs.bbox } })
        break
      case 'RIGHT': {
        if (n.children.some(isNode)) throw new Unsupported('formatting inside <RIGHT>')
        const t = (n.children as string[]).join('').trim()
        if (t) out.push(textNode(t, new Map(marks).set('right', undefined)))
        break
      }
      case 'BR': out.push({ type: 'hardBreak' }); break
      case 'BLANK': out.push({ type: 'blank', attrs: { type: n.attrs.type ?? null, length: n.attrs.length ?? null } }); break
      default: throw new Unsupported(`<${n.tag}> inside a paragraph`)
    }
  }
  return out
}

/** The notebook's _trim_segments: strip spaces at the start, the end and around line breaks. */
function trim(nodes: PM[]): PM[] {
  const out = nodes.map((n) => ({ ...n }))
  out.forEach((n, i) => {
    if (n.type !== 'text') return
    if (i === 0 || out[i - 1].type === 'hardBreak') n.text = n.text!.trimStart()
    if (i === out.length - 1 || out[i + 1].type === 'hardBreak') n.text = n.text!.trimEnd()
  })
  return out.filter((n) => !(n.type === 'text' && !n.text))
}

const para = (attrs: Attrs, kids: (string | N)[]): PM => ({
  type: 'paragraph', attrs: pAttrs(attrs), content: trim(inline(kids, new Map(), [])),
})

const blank = (s: string) => !s.trim()

function listLike(n: N, itemType: string, allowed: string[]): PM[] {
  const items: PM[] = []
  for (const c of n.children) {
    if (typeof c === 'string') { if (!blank(c)) throw new Unsupported(`text inside <${n.tag}>`); continue }
    if (!allowed.includes(c.tag)) throw new Unsupported(`<${c.tag}> inside <${n.tag}>`)
    const content = trim(inline(c.children, new Map(), []))
    if (!content.length) continue                                  // the builder skips empty items too
    items.push({ type: itemType, attrs: { level: c.attrs.level ?? null, ...pAttrs(c.attrs) }, content })
  }
  return items
}

function cell(c: N, header: boolean): PM {
  void header
  const paras = c.children.filter((k): k is N => isNode(k) && ['P', 'ITEM', 'OPTION'].includes(k.tag))
  let content: PM[]
  // the builder's _cell_is_complex: options, a list, a question wrapper, or text/tags beside paragraphs go through the general block renderer
  const complex = c.children.some((k) => isNode(k) && ['OPTIONS', 'LIST', 'TABLE', 'QUESTION'].includes(k.tag))
    || (paras.length > 0 && c.children.some((k) => (typeof k === 'string' ? !blank(k) : !['P', 'ITEM', 'OPTION'].includes(k.tag))))
  if (complex) {
    const out: PM[] = []
    blocks(c.children, out)
    content = out.length ? out : [para({}, [])]
  } else if (paras.length) {
    content = paras.map((p) => para(p.attrs, p.children))
  } else {
    content = [para({}, c.children)]
  }
  const span = Math.max(1, Math.min(12, Math.trunc(parseFloat(c.attrs.colspan) || 1)))
  return { type: 'tableCell', attrs: { colspan: span, rowspan: 1, colwidth: null,
    align: c.attrs.align ?? null, bold: c.attrs.bold ?? null, size: c.attrs.size ?? null }, content }
}

function table(n: N): PM {
  const rows = n.children.filter(isNode)
  if (n.children.some((k) => typeof k === 'string' ? !blank(k) : k.tag !== 'ROW') || !rows.length) {
    throw new Unsupported('table without clean rows')
  }
  const grid = rows.map((r) => {
    if (r.children.some((k) => typeof k === 'string' ? !blank(k) : k.tag !== 'CELL')) throw new Unsupported('text or tags next to cells')
    return { header: truthy(r.attrs.header ?? 'false'), cells: r.children.filter(isNode).map((c) => cell(c, truthy(r.attrs.header ?? 'false'))) }
  })
  const width = Math.max(1, ...grid.map((r) => r.cells.reduce((s, c) => s + (c.attrs!.colspan as number), 0)))
  const content = grid.map((r) => {
    const used = r.cells.reduce((s, c) => s + (c.attrs!.colspan as number), 0)
    const pad = Array.from({ length: width - used }, () => cell({ tag: 'CELL', attrs: {}, children: [] }, false))
    return { type: 'tableRow', attrs: { header: r.header }, content: [...r.cells, ...pad] }
  })
  return { type: 'table', attrs: { border: n.attrs.border ?? null, align: n.attrs.align ?? null }, content }
}

function blocks(nodes: (string | N)[], out: PM[]): void {
  let buf: (string | N)[] = []
  const flush = () => {
    if (!buf.length) return
    const p = para({}, buf)
    buf = []
    if (p.content!.length) out.push(p)
  }
  for (const n of nodes) {
    if (typeof n === 'string') {
      flush()
      for (const line of n.replace(/\\n/g, '\n').split('\n')) if (line.trim()) { const p = para({}, [line]); if (p.content!.length) out.push(p) }
      continue
    }
    if (['STYLE', 'BR', 'BLANK', 'IMG', 'FORMULA', 'RIGHT', 'SPAN'].includes(n.tag)) { buf.push(n); continue }
    flush()
    if (['P', 'ITEM', 'OPTION'].includes(n.tag)) {
      const p = para(n.attrs, n.children)
      if (p.content!.length) out.push(p)
    } else if (n.tag === 'LIST') {
      const items = listLike(n, 'listItem', ['ITEM', 'P'])
      if (items.length) out.push({ type: 'list', attrs: { type: n.attrs.type ?? null }, content: items })
    } else if (n.tag === 'OPTIONS') {
      if (!n.children.some(isNode)) throw new Unsupported('options without any option')
      const items = listLike(n, 'optionItem', ['OPTION', 'P', 'ITEM'])
      if (items.length) out.push({ type: 'options', attrs: { layout: n.attrs.layout ?? null, columns: n.attrs.columns ?? null }, content: items })
    } else if (n.tag === 'TABLE') {
      out.push(table(n))
    } else if (n.tag === 'QUESTION') {
      blocks(n.children, out)                                      // a transparent wrapper in the builder
    } else {
      throw new Unsupported(`<${n.tag}> in an unexpected place`)
    }
  }
  flush()
}

/** Throws Unsupported when the markup cannot be shown without changing the DOCX. */
export function markupToDoc(markup: string): PM {
  const out: PM[] = []
  blocks(tree(markup).children, out)
  return { type: 'doc', content: out.length ? out : [{ type: 'paragraph', attrs: pAttrs({}), content: [] }] }
}
