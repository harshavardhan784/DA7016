/** Editor document (ProseMirror JSON) -> tag markup, one top-level element per line, as the models write it. */
import { P_ATTRS, type PM } from './parse'

type Mark = { type: string; attrs?: Record<string, unknown> }

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const attr = (k: string, v: unknown) => (v === null || v === undefined || v === '' ? '' : ` ${k}="${String(v).replace(/"/g, '')}"`)
const attrs = (a: Record<string, unknown> | undefined, keys: readonly string[]) => keys.map((k) => attr(k, a?.[k])).join('')

const STYLE_BOOL = ['bold', 'italic', 'underline', 'strike', 'superscript', 'subscript']
const STYLE_VALUE = ['size', 'font', 'color', 'highlight']

function styleTag(marks: Mark[], inner: string): string {
  const has = (n: string) => marks.some((m) => m.type === n)
  const val = (n: string) => marks.find((m) => m.type === n)?.attrs?.value
  const a = STYLE_BOOL.filter(has).map((k) => ` ${k}="true"`).join('') + STYLE_VALUE.map((k) => attr(k, val(k))).join('')
  return a ? `<STYLE${a}>${inner}</STYLE>` : inner
}

function inline(nodes: PM[] | undefined): string {
  const list = nodes ?? []
  let out = ''
  for (let i = 0; i < list.length; i++) {
    const n = list[i]
    if (n.type === 'hardBreak') out += '<BR/>'
    else if (n.type === 'blank') out += `<BLANK${attr('type', n.attrs?.type)}${attr('length', n.attrs?.length)}/>`
    else if (n.type === 'math') out += n.attrs?.display ? `$$${esc(String(n.attrs?.latex ?? ''))}$$` : `$${esc(String(n.attrs?.latex ?? ''))}$`
    else if (n.type === 'img') out += `<${n.attrs?.tag === 'FORMULA' ? 'FORMULA' : 'IMG'}${attr('bbox', n.attrs?.bbox)}/>`
    else if (n.type === 'text') {
      const marks = n.marks ?? []
      if (marks.some((m) => m.type === 'right')) {
        // one <RIGHT> per run of right-marked text: the builder only reads plain text inside it
        let text = n.text ?? ''
        while (list[i + 1]?.type === 'text' && list[i + 1].marks?.some((m) => m.type === 'right')) text += list[++i].text ?? ''
        out += styleTag(marks, `<RIGHT>${esc(text)}</RIGHT>`)
      } else {
        out += styleTag(marks, esc(n.text ?? ''))
      }
    }
  }
  return out
}

const hasContent = (n: PM) => (n.content ?? []).some((c) => c.type !== 'text' || (c.text ?? '').trim() !== '')

function cellXml(c: PM): string {
  const ps = c.content ?? []
  const head = `<CELL${attr('colspan', (c.attrs?.colspan as number) > 1 ? c.attrs?.colspan : null)}${attrs(c.attrs, ['align', 'bold', 'size'])}>`
  if (ps.some((p) => p.type !== 'paragraph')) return `${head}${ps.map(block).join('')}</CELL>`      // options or a list in the cell
  const plain = ps.length === 1 && P_ATTRS.every((k) => !ps[0].attrs?.[k])
  if (plain) return `${head}${inline(ps[0].content)}</CELL>`
  return `${head}${ps.filter(hasContent).map((p) => `<P${attrs(p.attrs, P_ATTRS)}>${inline(p.content)}</P>`).join('')}</CELL>`
}

function block(n: PM): string {
  switch (n.type) {
    case 'paragraph':
      return hasContent(n) ? `<P${attrs(n.attrs, P_ATTRS)}>${inline(n.content)}</P>` : ''
    case 'list': case 'options': {
      const [outer, item, keys] = n.type === 'list' ? ['LIST', 'ITEM', ['type']] as const : ['OPTIONS', 'OPTION', ['layout', 'columns']] as const
      const items = (n.content ?? []).filter(hasContent)
        .map((i) => `<${item}${n.type === 'list' ? attr('level', i.attrs?.level) : ''}${attrs(i.attrs, P_ATTRS)}>${inline(i.content)}</${item}>`)
      return items.length ? `<${outer}${attrs(n.attrs, keys)}>${items.join('')}</${outer}>` : ''
    }
    case 'table': {
      const rows = (n.content ?? []).map((r) => `<ROW${r.attrs?.header ? ' header="true"' : ''}>${(r.content ?? []).map(cellXml).join('')}</ROW>`)
      return `<TABLE${attrs(n.attrs, ['border', 'align'])}>${rows.join('')}</TABLE>`
    }
    default:
      return ''
  }
}

export function docToMarkup(doc: PM): string {
  return (doc.content ?? []).map(block).filter(Boolean).join('\n')
}
